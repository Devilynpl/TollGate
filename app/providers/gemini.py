import abc
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import settings

logger = logging.getLogger("tollgate.provider")


class ProviderError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


class BaseProvider(abc.ABC):
    @abc.abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 30.0,
    ) -> Tuple[str, int, int]:
        """Returns (response_text, input_tokens, output_tokens)."""
        pass

    @abc.abstractmethod
    async def get_embedding(self, text: str) -> List[float]:
        """Returns embedding vector for text."""
        pass


class GeminiProvider(BaseProvider):
    """
    Direct Gemini API Provider using HTTP REST (v1beta).
    Includes automatic exponential backoff retry for 429/503 errors.
    """

    def __init__(
        self,
        api_key: str = settings.gemini_api_key,
        model: str = settings.gemini_model,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    async def generate(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = settings.request_timeout_seconds,
    ) -> Tuple[str, int, int]:
        if not self.api_key:
            raise ProviderError("provider_down", "GEMINI_API_KEY is not configured", retryable=False)

        # Build Gemini contents structure
        contents = []
        system_instruction = None

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                # Gemini accepts system_instruction or prepend to user
                system_instruction = {"parts": [{"text": content}]}
            elif role in ("user", "tool"):
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})

        if not contents:
            contents.append({"role": "user", "parts": [{"text": "Hello"}]})

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"

        # Exponential backoff retry loop
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})

                    if resp.status_code == 200:
                        data = resp.json()
                        text = ""
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts)

                        usage_metadata = data.get("usageMetadata", {})
                        in_tokens = usage_metadata.get("promptTokenCount", 0)
                        out_tokens = usage_metadata.get("candidatesTokenCount", 0)

                        if in_tokens == 0:
                            in_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
                        if out_tokens == 0:
                            out_tokens = len(text) // 4

                        return text, in_tokens, out_tokens

                    elif resp.status_code in (429, 503):
                        retry_after = 2.0 * (2 ** attempt)
                        logger.warning(
                            f"Gemini API returned {resp.status_code}. Retry {attempt + 1}/{self.max_retries} "
                            f"in {retry_after}s..."
                        )
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise ProviderError(
                            "rate_limited" if resp.status_code == 429 else "provider_down",
                            f"Gemini API rate limit or service unavailable ({resp.status_code}): {resp.text}",
                            retryable=True,
                            retry_after=retry_after,
                        )

                    elif resp.status_code >= 400 and resp.status_code < 500:
                        raise ProviderError(
                            "invalid_request",
                            f"Gemini API client error ({resp.status_code}): {resp.text}",
                            retryable=False,
                        )
                    else:
                        raise ProviderError(
                            "provider_down",
                            f"Gemini API server error ({resp.status_code}): {resp.text}",
                            retryable=False,
                        )

            except httpx.TimeoutException as te:
                logger.warning(f"Request timeout on attempt {attempt + 1}: {te}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
                raise ProviderError("timeout", f"Request to Gemini timed out after {timeout}s", retryable=False)
            except httpx.RequestError as re:
                logger.error(f"HTTP request error: {re}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
                raise ProviderError("provider_down", f"Connection error: {str(re)}", retryable=True)

        raise ProviderError("provider_down", "Exhausted all retries without success", retryable=False)

    async def get_embedding(self, text: str) -> List[float]:
        """Gets embedding vector for text using Gemini text-embedding-004 or local fallback."""
        if not self.api_key:
            return self._hash_embedding(text)

        url = f"{self.base_url}/models/text-embedding-004:embedContent?key={self.api_key}"
        payload = {
            "model": "models/text-embedding-004",
            "content": {"parts": [{"text": text[:2000]}]},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("embedding", {}).get("values", [])
        except Exception as e:
            logger.warning(f"Failed to fetch Gemini embedding: {e}. Using deterministic embedding fallback.")

        return self._hash_embedding(text)

    @staticmethod
    def _hash_embedding(text: str, dim: int = 64) -> List[float]:
        """Deterministic hashing fallback embedding when API key is unavailable."""
        import hashlib
        vals = []
        for i in range(dim):
            h = hashlib.sha256(f"{text}_{i}".encode("utf-8")).hexdigest()
            vals.append((int(h[:8], 16) / 0xFFFFFFFF) * 2 - 1)
        # Normalize
        norm = sum(v * v for v in vals) ** 0.5 or 1.0
        return [v / norm for v in vals]
