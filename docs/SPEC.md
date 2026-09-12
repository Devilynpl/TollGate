# Tollgate Specification & Architecture Contract

## Overview
Tollgate to lekki, deterministyczny LLM Gateway zbudowany w oparciu o **FastAPI**, dedykowany do obsługi aplikacji **DocGround** (RAG) oraz **BriefAgent** (Multi-step Agent).

## Endpointy

### 1. `POST /v1/chat`
Główny punkt wejściowy dla wszystkich aplikacji portfolio.

**Nagłówki:**
- `Content-Type: application/json`

**Kody odpowiedzi:**
- `200 OK`: Sukces (zapytanie zrealizowane przez Gemini lub Semantic Cache, ewentualnie blokada guardrail z czytelnym komunikatem).
- `429 Too Many Requests`: Przekroczenie limitu RPM lub dziennego budżetu tokenów/USD. Zawiera nagłówek `Retry-After`.
- `502 Bad Gateway`: Błąd zewnętrznego dostawcy Gemini po wyczerpaniu puli retry.
- `504 Gateway Timeout`: Upłynięcie limitu czasu zapytania (30 s).

### 2. `GET /health`
Liveness probe zwracający stan serwera, aktywny model oraz pozostały limit tokenów.

### 3. `GET /v1/usage`
Audyt bieżącego zużycia:
- Licznik RPM,
- Liczba zapytań, tokeny in/out oraz estymowany koszt $ per aplikacja (`docground`, `briefagent`) oraz globalnie,
- Pozostały dzienny limit tokenów.

### 4. `GET /v1/traces/{trace_id}`
Pobranie pełnego rekordu audytowego z pliku `logs/traces.jsonl` dla celów diagnostycznych.

## Zasady Routingu
1. `force_route`: Jeśli podano `"gemini"` lub `"cache"`, żądanie natychmiast kierowane jest wskazaną trasą.
2. `briefagent`: Zawsze kierowany do `"gemini"`. Pamięć podręczna jest wyłączona ze względu na dynamiczny, wieloetapowy charakter agenta.
3. `docground`: W pierwszej kolejności sprawdzany jest `SemanticCache` (jeśli `corpus_version` się zgadza i podobieństwo cosinusowe $\ge 0.92$). Przy braku dopasowania – kierowany do `"gemini"`.

## Bezpieczeństwo i Guardrails
- Blokada przed modelem na poziomie regex (PII: e-mail, telefon, PESEL).
- Wykrywanie wstrzyknięć poleceń (Prompt Injection: ignore previous instructions, reveal system prompt, DAN mode).
- Ograniczenie maksymalnej długości tekstu wejściowego (`MAX_INPUT_CHARS=16000`).
