import os
from pathlib import Path
from pydantic import BaseModel
TOLLGATE_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    env_path = TOLLGATE_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    pass


class Settings(BaseModel):
    port: int = int(os.getenv("PORT", "8000"))
    host: str = os.getenv("HOST", "0.0.0.0")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    # AWS Bedrock Multi-Cloud Configuration
    aws_access_key_id: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_secret_access_key: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    aws_region: str = os.getenv("AWS_REGION", "eu-central-1")
    aws_bedrock_model_id: str = os.getenv(
        "AWS_BEDROCK_MODEL_ID",
        "arn:aws:bedrock:eu-central-1:832191487769:inference-profile/eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    active_llm_provider: str = os.getenv("ACTIVE_LLM_PROVIDER", "hybrid_failover")

    daily_token_cap: int = int(os.getenv("DAILY_TOKEN_CAP", "200000"))
    daily_usd_cap: float = float(os.getenv("DAILY_USD_CAP", "2.00"))
    rpm_limit: int = int(os.getenv("RPM_LIMIT", "60"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30.0"))

    max_input_chars: int = int(os.getenv("MAX_INPUT_CHARS", "16000"))

    cache_similarity_threshold: float = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92"))
    cache_db_path: Path = TOLLGATE_ROOT / os.getenv("CACHE_DB_PATH", "data/cache.db")
    traces_log_path: Path = TOLLGATE_ROOT / "logs" / "traces.jsonl"


settings = Settings()
