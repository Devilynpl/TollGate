"""AWS Bedrock Provider for Enterprise Multi-Cloud LLM Gateway.

Supports:
- Anthropic Claude 3.5 / 4.5 Sonnet via Bedrock European / Global Inference Profiles
- Meta Llama 3 / 3.1
- Mistral Large
- High-resilience automatic retry and fallback integration
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger("tollgate.provider.bedrock")


class BedrockProvider:
    """AWS Bedrock Provider using standard AWS SDK (boto3) Converse API."""

    def __init__(
        self,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
        model_id: Optional[str] = None,
    ):
        self.aws_access_key_id = aws_access_key_id or settings.aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key or settings.aws_secret_access_key
        self.region_name = region_name or settings.aws_region or "eu-central-1"
        raw_model = model_id or settings.aws_bedrock_model_id or "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
        self.model_id = self._normalize_model_id(raw_model, self.region_name)
        self._client = None

    @staticmethod
    def _normalize_model_id(mid: str, region: str) -> str:
        """Ensures Bedrock gets an inference profile ID or ARN if raw foundation model ID is passed."""
        mid = mid.strip()
        if mid.startswith("arn:aws:bedrock:") or mid.startswith("eu.") or mid.startswith("us.") or mid.startswith("global."):
            return mid
        if mid.startswith("anthropic.claude-"):
            prefix = "eu." if "eu-" in region else "global."
            return f"{prefix}{mid}"
        return mid

    def is_configured(self) -> bool:
        return bool(self.aws_access_key_id and self.aws_secret_access_key and self.model_id)

    def _get_client(self):
        if self._client is None:
            import boto3

            session = boto3.Session(
                aws_access_key_id=self.aws_access_key_id or None,
                aws_secret_access_key=self.aws_secret_access_key or None,
                region_name=self.region_name,
            )
            self._client = session.client("bedrock-runtime")
        return self._client

    def generate_sync(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = settings.request_timeout_seconds,
    ) -> Tuple[str, int, int]:
        """Invoke Claude / Llama via Bedrock Converse API synchronously."""
        client = self._get_client()

        bedrock_messages = []
        system_prompts = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_prompts.append({"text": content})
            elif role == "assistant":
                bedrock_messages.append({"role": "assistant", "content": [{"text": content}]})
            else:
                bedrock_messages.append({"role": "user", "content": [{"text": content}]})

        if not bedrock_messages:
            bedrock_messages = [{"role": "user", "content": [{"text": "Hello"}]}]

        kwargs: Dict[str, Any] = {
            "modelId": self.model_id,
            "messages": bedrock_messages,
            "inferenceConfig": {
                "maxTokens": 2048,
                "temperature": 0.2,
            },
        }
        if system_prompts:
            kwargs["system"] = system_prompts

        response = client.converse(**kwargs)

        text = ""
        output_msg = response.get("output", {}).get("message", {})
        for c in output_msg.get("content", []):
            if "text" in c:
                text += c["text"]

        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        logger.info(
            f"AWS Bedrock ({self.model_id[:35]}...) tokens: in={input_tokens}, out={output_tokens}"
        )
        return text, input_tokens, output_tokens

    async def generate(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = settings.request_timeout_seconds,
    ) -> Tuple[str, int, int]:
        """Invoke Claude / Llama via Bedrock Converse API asynchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.generate_sync(messages, tools, timeout))

    async def get_embedding(self, text: str) -> List[float]:
        """Fallback to local hash/mock or Gemini embedding."""
        from app.providers.gemini import GeminiProvider

        return await GeminiProvider().get_embedding(text)
