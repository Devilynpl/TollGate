from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatRequest(BaseModel):
    app: Literal["docground", "briefagent"]
    messages: List[ChatMessage]
    tools: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    force_route: Optional[Literal["gemini", "cache"]] = None


class GuardrailAction(BaseModel):
    rule: str
    action: Literal["block"] = "block"


class UsageMetrics(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    est_usd: float = 0.0


class GateError(BaseModel):
    code: Literal[
        "rate_limited",
        "timeout",
        "budget_exceeded",
        "guardrail",
        "provider_down",
        "invalid_request",
    ]
    message: str


class ChatResponse(BaseModel):
    text: str
    route: Literal["cache", "gemini"]
    route_reason: str
    cached: bool = False
    cache_similarity: Optional[float] = None
    guardrail: Optional[GuardrailAction] = None
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    latency_ms: float = 0.0
    trace_id: str
    error: Optional[GateError] = None


class AppUsage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    est_usd: float = 0.0


class UsageSummary(BaseModel):
    global_usage: AppUsage
    by_app: Dict[str, AppUsage]
    rpm_current: int
    daily_token_cap: int
    daily_usd_cap: float
    token_cap_remaining: int
