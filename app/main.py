import os
import logging
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.routes import router as api_router
from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("tollgate.main")

app = FastAPI(
    title="Tollgate • Lean LLM Gateway",
    description=(
        "Production-grade LLM Gateway for DocGround & BriefAgent. "
        "Provides single contract /v1/chat, retry/backoff, daily budget caps, "
        "regex guardrails, semantic cache, routing, and JSONL tracing."
    ),
    version="0.1.0",
    # Disable public OpenAPI docs in production to avoid information disclosure
    docs_url="/docs" if os.getenv("TOLLGATE_ENV", "production") == "development" else None,
    redoc_url=None,
)

# SECURITY FIX (CRITICAL-01): Restrict CORS to known internal origins only.
# TollGate is an internal service — it should NOT be callable from arbitrary browsers.
_allowed_origins = [
    o.strip()
    for o in os.getenv(
        "TOLLGATE_ALLOWED_ORIGINS",
        "http://localhost:8501,http://127.0.0.1:8501,http://localhost:8000",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-Tollgate-Key"],
)

# SECURITY FIX (INFO-01): Standard HTTP Security Headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# SECURITY FIX (CRITICAL-02): Internal API key authentication middleware.
# Every request to /v1/* must carry the X-Tollgate-Key header matching TOLLGATE_INTERNAL_KEY env var.
_INTERNAL_KEY = os.getenv("TOLLGATE_INTERNAL_KEY", "")


@app.middleware("http")
async def enforce_internal_auth(request: Request, call_next):
    """
    Enforce shared-secret authentication on all /v1/ endpoints.
    The /health endpoint is intentionally left public for load-balancer probes,
    but is stripped of sensitive budget details (see routes.py).
    """
    path = request.url.path

    # Public path whitelist — only /health is unauthenticated
    if path in ("/health", "/"):
        return await call_next(request)

    # All other paths require X-Tollgate-Key header
    if not _INTERNAL_KEY:
        # If TOLLGATE_INTERNAL_KEY is not set, log a warning but allow through (dev mode)
        logger.warning("TOLLGATE_INTERNAL_KEY is not set — auth enforcement is DISABLED. Set it for production!")
        return await call_next(request)

    provided_key = request.headers.get("X-Tollgate-Key", "")
    if not provided_key or provided_key != _INTERNAL_KEY:
        logger.warning(f"Unauthorized request to {path} from {request.client.host}")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Unauthorized: missing or invalid X-Tollgate-Key"},
        )

    return await call_next(request)


app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
