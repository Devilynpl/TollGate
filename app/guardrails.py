import re
import unicodedata
from typing import Optional, Tuple
from app.config import settings
from app.schemas import GuardrailAction


# Regex for PII
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE)
PHONE_REGEX = re.compile(r"(?<!\d)(?:\+?48\s?)?(?:[1-9]\d{2}[\s-]?(?:\d{3}[\s-]?\d{3}|\d{2}[\s-]?\d{2}[\s-]?\d{2}))(?!\d)")
PESEL_REGEX = re.compile(r"\b\d{11}\b")

# SECURITY FIX (MEDIUM-01): Extended Prompt Injection patterns.
# Applied AFTER unicode normalization to resist leetspeak, homoglyph, and multi-language bypass.
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"forget\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"reveal\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"show\s+(?:me\s+)?(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"print\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)", re.IGNORECASE),
    re.compile(r"what\s+(?:are\s+)?(?:your\s+)?(?:system\s+)?instructions", re.IGNORECASE),
    re.compile(r"wyjmij\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"poka[zż]\s+(?:mi\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"zignoruj\s+(?:wszystkie\s+)?(?:poprzednie\s+)?instrukcje", re.IGNORECASE),
    re.compile(r"bypass\s+(?:all\s+)?(?:rules|filters|safety|guardrails)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+DAN\s+mode", re.IGNORECASE),
    re.compile(r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:an?\s+)?(?:unrestricted|jailbroken|DAN)", re.IGNORECASE),
    re.compile(r"pretend\s+(?:you\s+(?:are|have\s+no))\s+(?:restrictions?|rules?|filters?)", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    # Token smuggling patterns
    re.compile(r"<\s*/?(?:system|instruction|prompt)\s*>", re.IGNORECASE),
    re.compile(r"\[INST\]|\[/?SYS\]", re.IGNORECASE),
]


def _normalize_for_inspection(text: str) -> str:
    """
    SECURITY FIX (MEDIUM-01): Normalize input before regex inspection.
    Defends against: Unicode homoglyphs, zero-width chars, excessive whitespace, null bytes.
    """
    # Remove null bytes and control characters (except standard whitespace)
    text = "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf") or c in "\n\r\t ")
    # NFKC normalization: converts homoglyphs (ｉｇｎｏｒｅ → ignore, ℐ → I, etc.)
    text = unicodedata.normalize("NFKC", text)
    # Collapse multiple whitespace to single space
    text = re.sub(r"\s+", " ", text)
    return text


class GuardrailEngine:
    """
    Deterministic regex & rule-based pre-model guardrail.
    Zero LLM latency, zero token cost.
    Evaluates:
    - Input length limits
    - PII (email, phone, PESEL)
    - Prompt injection attempts (with unicode normalization)
    """

    def __init__(self, max_chars: int = settings.max_input_chars):
        self.max_chars = max_chars

    def inspect_text(self, text: str) -> Optional[GuardrailAction]:
        # 1. Max input length check (on raw text)
        if len(text) > self.max_chars:
            return GuardrailAction(
                rule=f"max_length_exceeded: text length {len(text)} > {self.max_chars}",
                action="block",
            )

        # Normalize text for injection/PII checks to resist bypass attempts
        normalized = _normalize_for_inspection(text)

        # 2. Prompt injection patterns (on normalized text)
        for pattern in INJECTION_PATTERNS:
            if pattern.search(normalized):
                return GuardrailAction(
                    rule=f"prompt_injection_detected: matched pattern '{pattern.pattern}'",
                    action="block",
                )

        # 3. PII Detection (Email) — on normalized text
        if EMAIL_REGEX.search(normalized):
            return GuardrailAction(
                rule="pii_detected: email address found",
                action="block",
            )

        # 4. PII Detection (PESEL - 11 digits)
        if PESEL_REGEX.search(normalized):
            return GuardrailAction(
                rule="pii_detected: PESEL identifier found",
                action="block",
            )

        # 5. PII Detection (Phone)
        if PHONE_REGEX.search(normalized):
            return GuardrailAction(
                rule="pii_detected: phone number found",
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
        # 1. Check valid month encoding in PESEL (months 01-12, 21-32, etc.)
        month = int(pesel[2:4])
        day = int(pesel[4:6])
        valid_month = (1 <= month <= 12) or (21 <= month <= 32) or (41 <= month <= 52) or (61 <= month <= 72) or (81 <= month <= 92)
        valid_day = 1 <= day <= 31
        if not (valid_month and valid_day):
            return False

        # 2. Checksum verification (SECURITY FIX MEDIUM-03: removed unconditional `return True` fallback)
        weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
        s = sum(int(pesel[i]) * weights[i] for i in range(10))
        control = (10 - (s % 10)) % 10
        return control == int(pesel[10])

    def inspect_and_sanitize_output(self, output_text: str) -> Tuple[str, bool]:
        """
        SECURITY FIX (ADV-02): Output Guardrails & Sensitive Data Leakage Prevention (OWASP LLM06).
        Inspects generated LLM output for:
        - Accidental leaks of API keys (Gemini, AWS, Bearer tokens, GitHub)
        - Exposed PESEL / sensitive identifiers
        Returns (sanitized_text, was_redacted).
        """
        if not output_text:
            return output_text, False

        redacted = False
        text = output_text

        # 1. API Keys and Secrets patterns
        patterns = [
            (re.compile(r"AIzaSy[A-Za-z0-9_-]{25,45}"), "[REDACTED_GEMINI_KEY]"),
            (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
            (re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"), "aws_secret_access_key=[REDACTED]"),
            (re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,255}"), "[REDACTED_GITHUB_TOKEN]"),
            (re.compile(r"Bearer\s+[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_+/=]*"), "Bearer [REDACTED_JWT]"),
        ]

        for pat, repl in patterns:
            if pat.search(text):
                text = pat.sub(repl, text)
                redacted = True

        # 2. Redact PESEL numbers in output
        for m in PESEL_REGEX.finditer(text):
            candidate = m.group()
            text = text.replace(candidate, "[REDACTED_PESEL]")
            redacted = True

        return text, redacted

