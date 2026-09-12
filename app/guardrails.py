import re
from typing import Optional, Tuple
from app.config import settings
from app.schemas import GuardrailAction


# Regex for PII
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE)
PHONE_REGEX = re.compile(r"(?:\+?48\s?)?(?:[1-9]\d{2}[\s-]?\d{3}[\s-]?\d{3}|[1-9]\d{1}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2})")
PESEL_REGEX = re.compile(r"\b\d{11}\b")

# Regex / patterns for Prompt Injection
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions?", re.IGNORECASE),
    re.compile(r"reveal\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"show\s+(?:me\s+)?(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"wyjmij\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"pokaż\s+(?:mi\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"zignoruj\s+(?:wszystkie\s+)?(?:poprzednie\s+)?instrukcje", re.IGNORECASE),
    re.compile(r"bypass\s+all\s+(?:rules|filters|safety)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+DAN\s+mode", re.IGNORECASE),
]


class GuardrailEngine:
    """
    Deterministic regex & rule-based pre-model guardrail.
    Zero LLM latency, zero token cost.
    Evaluates:
    - Input length limits
    - PII (email, phone, PESEL)
    - Prompt injection attempts
    """

    def __init__(self, max_chars: int = settings.max_input_chars):
        self.max_chars = max_chars

    def inspect_text(self, text: str) -> Optional[GuardrailAction]:
        # 1. Max input length check
        if len(text) > self.max_chars:
            return GuardrailAction(
                rule=f"max_length_exceeded: text length {len(text)} > {self.max_chars}",
                action="block",
            )

        # 2. Prompt injection patterns
        for pattern in INJECTION_PATTERNS:
            if pattern.search(text):
                return GuardrailAction(
                    rule=f"prompt_injection_detected: matched pattern '{pattern.pattern}'",
                    action="block",
                )

        # 3. PII Detection (Email)
        if EMAIL_REGEX.search(text):
            return GuardrailAction(
                rule="pii_detected: email address found",
                action="block",
            )

        # 4. PII Detection (Phone)
        if PHONE_REGEX.search(text):
            return GuardrailAction(
                rule="pii_detected: phone number found",
                action="block",
            )

        # 5. PII Detection (PESEL - 11 digits)
        # Check standard PESEL checksum or format
        pesel_matches = PESEL_REGEX.findall(text)
        for m in pesel_matches:
            if self._is_plausible_pesel(m):
                return GuardrailAction(
                    rule="pii_detected: PESEL identifier found",
                    action="block",
                )

        return None

    def inspect_messages(self, messages) -> Optional[GuardrailAction]:
        for msg in messages:
            res = self.inspect_text(msg.content)
            if res:
                return res
        return None

    @staticmethod
    def _is_plausible_pesel(pesel: str) -> bool:
        if len(pesel) != 11 or not pesel.isdigit():
            return False
        # Optional checksum verification
        weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
        s = sum(int(pesel[i]) * weights[i] for i in range(10))
        control = (10 - (s % 10)) % 10
        return control == int(pesel[10])
