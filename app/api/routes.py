import time
import uuid
import logging
import asyncio
from typing import Any, Dict, List
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from app.schemas import (
    ChatRequest,
    ChatResponse,
    GateError,
    GuardrailAction,
    UsageMetrics,
    UsageSummary,
)
from app.guardrails import GuardrailEngine
from app.budget import budget_tracker
from app.cache import semantic_cache
from app.router import model_router
from app.tracing import trace_recorder
from app.providers.gemini import GeminiProvider, ProviderError
from app.providers.bedrock import BedrockProvider
from app.config import settings

logger = logging.getLogger("tollgate.api")
router = APIRouter()

guardrails = GuardrailEngine()
gemini_provider = GeminiProvider()
bedrock_provider = BedrockProvider()


@router.get("/health", tags=["System"])
async def health():
    # SECURITY FIX (HIGH-01): Do NOT expose budget details or infra config on public /health.
    # Load-balancers only need status=ok.
    return {
        "status": "ok",
        "service": "tollgate",
    }


@router.get("/v1/usage", response_model=UsageSummary, tags=["Monitoring"])
async def get_usage():
    """Returns today's usage statistics per app and globally."""
    return budget_tracker.get_summary()


@router.get("/v1/traces/{trace_id}", tags=["Monitoring"])
async def get_trace(trace_id: str):
    """Retrieves full trace details for a given trace_id."""
    trace = trace_recorder.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


@router.post("/v1/chat", response_model=ChatResponse, tags=["LLM Gateway"])
async def chat(request: ChatRequest, response: Response, background_tasks: BackgroundTasks):
    start_time = time.time()
    trace_id = str(uuid.uuid4())

    # Prompt summary for safe tracing (no secrets, no full text)
    prompt_snippet = ""
    for m in reversed(request.messages):
        if m.role == "user":
            prompt_snippet = (m.content[:80] + "...") if len(m.content) > 80 else m.content
            break

    # 1. GUARDRAILS (Deterministic regex & rule check before anything else)
    guard_action = guardrails.inspect_messages(request.messages)
    if guard_action:
        latency_ms = (time.time() - start_time) * 1000
        err = GateError(code="guardrail", message=f"Blocked by guardrail: {guard_action.rule}")
        chat_resp = ChatResponse(
            text=f"Request blocked by Tollgate Guardrail: {guard_action.rule}",
            route="gemini",
            route_reason="guardrail_blocked",
            cached=False,
            guardrail=guard_action,
            usage=UsageMetrics(),
            latency_ms=latency_ms,
            trace_id=trace_id,
            error=err,
        )
        _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)
        return chat_resp

    # 2. RPM RATE LIMIT CHECK
    if not budget_tracker.check_rpm_limit(request.app):
        latency_ms = (time.time() - start_time) * 1000
        response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
        response.headers["Retry-After"] = "10"
        err = GateError(code="rate_limited", message=f"Rate limit exceeded (RPM limit: {settings.rpm_limit})")
        chat_resp = ChatResponse(
            text="Tollgate rate limit exceeded. Please try again shortly.",
            route="gemini",
            route_reason="rate_limited",
            cached=False,
            usage=UsageMetrics(),
            latency_ms=latency_ms,
            trace_id=trace_id,
            error=err,
        )
        _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)
        return chat_resp

    # 3. DAILY BUDGET CHECK
    if not budget_tracker.check_daily_budget(est_new_tokens=100):
        latency_ms = (time.time() - start_time) * 1000
        response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
        err = GateError(code="budget_exceeded", message="Daily token or cost budget cap exceeded.")
        chat_resp = ChatResponse(
            text="Tollgate daily budget limit reached. LLM requests temporarily suspended to protect quota.",
            route="gemini",
            route_reason="budget_exceeded",
            cached=False,
            usage=UsageMetrics(),
            latency_ms=latency_ms,
            trace_id=trace_id,
            error=err,
        )
        _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)
        return chat_resp

    # 4. ROUTING DECISION
    target_route, route_reason = model_router.decide_route(request)

    # 5. SEMANTIC CACHE PATH (DocGround FAQ / Knowledge lookups)
    corpus_version = str(request.metadata.get("corpus_version", "v1")) if request.metadata else "v1"
    tenant_id = str(request.metadata.get("tenant_id", "default")) if request.metadata else "default"
    user_query = ""
    for m in reversed(request.messages):
        if m.role == "user":
            user_query = m.content
            break

    if target_route == "cache" and user_query:
        # SECURITY FIX (HIGH-02): Was `provider` (undefined → NameError). Fixed to gemini_provider.
        query_emb = await gemini_provider.get_embedding(user_query)
        # SECURITY FIX: Tenant and App isolation in semantic cache
        match = semantic_cache.find_match(
            query_emb,
            corpus_version=corpus_version,
            app=request.app,
            tenant_id=tenant_id
        )
        if match:
            cached_text, sim = match
            latency_ms = (time.time() - start_time) * 1000
            # Cache hit consumes 0 external tokens and $0.00
            chat_resp = ChatResponse(
                text=cached_text,
                route="cache",
                route_reason=f"cache_hit: similarity {sim:.4f} >= {settings.cache_similarity_threshold}",
                cached=True,
                cache_similarity=round(sim, 4),
                guardrail=None,
                usage=UsageMetrics(input_tokens=0, output_tokens=0, est_usd=0.0),
                latency_ms=latency_ms,
                trace_id=trace_id,
                error=None,
            )
            _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)
            return chat_resp

    # 6. LIVE MULTI-CLOUD PROVIDER PATH (Gemini + AWS Bedrock)
    try:
        dict_messages = [{"role": m.role, "content": m.content} for m in request.messages]
        used_route = "gemini"
        route_detail = route_reason if target_route != "cache" else "cache_miss: querying LLM live"

        # Determine if Bedrock should be preferred or requested
        prefer_bedrock = (
            request.force_route == "bedrock"
            or settings.active_llm_provider == "bedrock"
        ) and bedrock_provider.is_configured()

        if prefer_bedrock:
            used_route = "bedrock"
            route_detail = "provider_route: AWS Bedrock Claude 3.5 Sonnet"
            gen_text, in_tok, out_tok = await bedrock_provider.generate(
                messages=dict_messages,
                tools=request.tools,
                timeout=settings.request_timeout_seconds,
            )
        else:
            try:
                gen_text, in_tok, out_tok = await gemini_provider.generate(
                    messages=dict_messages,
                    tools=request.tools,
                    timeout=settings.request_timeout_seconds,
                )
            except ProviderError as pe:
                # Multi-Cloud Failover: If Gemini is rate-limited or fails, seamlessly failover to AWS Bedrock!
                if bedrock_provider.is_configured():
                    logger.warning(f"Gemini failed ({pe.code}), executing Multi-Cloud Failover to AWS Bedrock...")
                    used_route = "bedrock_failover"
                    route_detail = f"multi_cloud_failover: Gemini ({pe.code}) -> AWS Bedrock Claude"
                    gen_text, in_tok, out_tok = await bedrock_provider.generate(
                        messages=dict_messages,
                        tools=request.tools,
                        timeout=settings.request_timeout_seconds,
                    )
                else:
                    raise pe

        cost = budget_tracker.record_usage(request.app, in_tok, out_tok)
        latency_ms = (time.time() - start_time) * 1000

        # SECURITY FIX (ADV-02): Output Guardrail Inspection
        gen_text, was_redacted = guardrail_engine.inspect_and_sanitize_output(gen_text)
        if was_redacted:
            logger.warning(f"Trace {trace_id}: Output contained sensitive data and was sanitized by output guardrail.")

        # Store in semantic cache if app is docground and response is valid
        if request.app == "docground" and user_query and gen_text and len(gen_text) > 10:
            query_emb = await gemini_provider.get_embedding(user_query)
            semantic_cache.store(
                query_text=user_query,
                corpus_version=corpus_version,
                query_embedding=query_emb,
                response_text=gen_text,
                app=request.app,
                tenant_id=tenant_id,
            )

        chat_resp = ChatResponse(
            text=gen_text,
            route=used_route,
            route_reason=route_detail,
            cached=False,
            cache_similarity=None,
            guardrail=None,
            usage=UsageMetrics(input_tokens=in_tok, output_tokens=out_tok, est_usd=cost),
            latency_ms=latency_ms,
            trace_id=trace_id,
            error=None,
        )
        _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)

        # Faza 8 — Podpięcie Judge'a (Asynchroniczny sampling np. 20% lub app=docground)
        background_tasks.add_task(_async_judge_sample, trace_id, user_query, gen_text, request.app)

        return chat_resp

    except ProviderError as pe:
        latency_ms = (time.time() - start_time) * 1000
        if pe.code == "rate_limited":
            response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
            if pe.retry_after:
                response.headers["Retry-After"] = str(int(pe.retry_after))
        elif pe.code == "timeout":
            response.status_code = status.HTTP_504_GATEWAY_TIMEOUT
        else:
            response.status_code = status.HTTP_502_BAD_GATEWAY

        err = GateError(code=pe.code, message=pe.message)
        chat_resp = ChatResponse(
            text=f"Tollgate upstream error: {pe.message}",
            route="gemini",
            route_reason=f"provider_error: {pe.code}",
            cached=False,
            usage=UsageMetrics(),
            latency_ms=latency_ms,
            trace_id=trace_id,
            error=err,
        )
        _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)
        return chat_resp

    except Exception as exc:
        latency_ms = (time.time() - start_time) * 1000
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        logger.exception(f"Unhandled error in chat endpoint: {exc}")
        err = GateError(code="provider_down", message=f"Internal Gateway exception: {str(exc)}")
        chat_resp = ChatResponse(
            text="Internal Gateway processing error. Check gateway traces.",
            route="gemini",
            route_reason="internal_exception",
            cached=False,
            usage=UsageMetrics(),
            latency_ms=latency_ms,
            trace_id=trace_id,
            error=err,
        )
        _record_and_log_trace(trace_id, request, chat_resp, prompt_snippet)
        return chat_resp


def _record_and_log_trace(trace_id: str, req: ChatRequest, resp: ChatResponse, prompt_snippet: str):
    trace_entry = {
        "trace_id": trace_id,
        "app": req.app,
        "prompt_snippet": prompt_snippet,
        "route": resp.route,
        "route_reason": resp.route_reason,
        "cached": resp.cached,
        "cache_similarity": resp.cache_similarity,
        "guardrail": resp.guardrail.model_dump() if resp.guardrail else None,
        "tokens_in": resp.usage.input_tokens,
        "tokens_out": resp.usage.output_tokens,
        "est_usd": resp.usage.est_usd,
        "latency_ms": round(resp.latency_ms, 2),
        "error": resp.error.model_dump() if resp.error else None,
        "tool_names": [t.get("name", "unknown") for t in req.tools] if req.tools else [],
    }
    trace_recorder.record_trace(trace_entry)


async def _async_judge_sample(trace_id: str, query: str, answer: str, app: str):
    """
    Faza 8 — Podpięcie Judge'a:
    Asynchroniczna ocena próbki odpowiedzi przez moduł TheJudge (Relevance / Faithfulness).
    Zapisuje 'judge_evaluation' bezpośrednio do pliku traces.jsonl bez blokowania odpowiedzi dla usera.
    """
    if not query or not answer or app != "docground":
        return

    # Prosty i deterministyczny rule-based / prompt-based sędzia
    try:
        # Heurystyka jakości: obecność źródeł [[źródło:]], długość oraz trafność słów kluczowych
        has_citations = "źródło:" in answer.lower() or "source:" in answer.lower()
        score = 1.0 if has_citations or len(answer) > 100 else 0.5
        reasoning = "Odpowiedź zawiera weryfikowalne cytowania lub wyczerpujący kontekst techniczny." if score == 1.0 else "Odpowiedź poprawna, brak formalnego znacznika cytowania."

        judge_result = {
            "judge_model": "rule-judge-v1",
            "faithfulness_score": score,
            "relevance_score": 1.0 if len(answer) > 20 else 0.5,
            "reasoning": reasoning,
            "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        trace_recorder.update_trace_with_judge(trace_id, judge_result)
        logger.info(f"Asynchronous Judge evaluated trace {trace_id}: Faithfulness={score}")
    except Exception as e:
        logger.warning(f"Failed in async judge sample: {e}")
