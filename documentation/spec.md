Faza 0 — Kontrakt i granice
Czas: pół wieczoru

jeden endpoint, z którego korzystają obie apki: POST /v1/chat
wejście: app (docground | briefagent), messages, tools?, metadata
wyjście: text, usage, route, cached, trace_id, guardrail
twarde limity: max znaków, max tool-rounds, max $ / dzień, timeout
co nie wchodzi: własny wektorowy RAG w gatewayu, UI agenta, fine-tune

Done: spec na 1 stronę + przykładowy request/response JSON.

Faza 1 — Szkielet API
Czas: 1 wieczór

FastAPI, POST /v1/chat, GET /health
adapter GeminiProvider (jedna funkcja: messages in → text + tokens out)
.env: klucz, model (gemini-flash albo to, czego już używasz)
structured logs: trace_id, app, latency_ms
docker-compose albo przynajmniej uvicorn + README „4 komendy”

Na tym etapie gateway tylko przekazuje do Gemini. Zero magii.
Done: DocGround potrafi strzelić w /v1/chat zamiast w SDK. Odpowiedź ta sama.

Faza 2 — Idempotencja i błędy dostawcy
Czas: 1 wieczór
Free tier umrze. To ma być feature.

retry z backoffem na 429 / 503 (2–3 próby)
mapowanie błędów: rate_limited, timeout, provider_down, invalid_request
klient dostaje czytelny JSON, nie traceback
twardy timeout (np. 30 s)
header Retry-After gdy się da

Done: odcinasz internet / mockujesz 429 → UI pokazuje „spróbuj za chwilę”, proces żyje.

Faza 3 — Budżet i limity
Czas: 1 wieczór

licznik tokenów i szacunek $ per request (nawet jeśli free: liczy się jakby płatne)
limit dzienny per app i globalny
limit RPM in-memory (Redis nie jest wymagany na start; dict + TTL wystarczy)
gdy cap: 402 / 429 z powodem budget_exceeded
endpoint GET /v1/usage — ile poszło dziś

Done: po N requestach gateway odmawia. Liczba w /usage się zgadza.

Faza 4 — Guardrails
Czas: 1–2 wieczory
Warstwa przed modelem, tania i deterministyczna:

max długość inputu
proste filtry: PII (email, telefon, PESEL-ish), oczywisty prompt injection (ignore previous, wyjmij system prompt)
allowlist app + opcjonalnie allowed_tools
po modelu: obcięcie zbyt długiej odpowiedzi

Na start reguły + regex, nie drugi LLM. Drugi model jako sędzia zostawiasz Judge’owi.
Done: 10 złośliwych promptów w tests/test_guardrails.py — 8+ zablokowanych z powodem.

Faza 5 — Semantic cache
Czas: 1–2 wieczory
To ratuje free tier i wygląda dobrze na demo.

cache tylko dla docground (pytania FAQ-podobne)
klucz: embedding pytania + id korpusu / wersja indeksu
próg podobieństwa (zacznij od 0.92, zmierz)
nie cache’uj: tool-loop agenta, requestów z PII, odpowiedzi nie wiem jeśli nie jesteś pewien
w odpowiedzi flaga cached: true i cache_similarity

Store: SQLite + wektor lokalny albo po prostu JSON + embeddings Gemini/lokalne. Nie ciągnij Pinecone’a.
Done: to samo pytanie 2× → drugie <100 ms, 0 tokenów, w logu cache_hit.

Faza 6 — Routing modeli
Czas: 1–2 wieczory
Prosty router, nie orkiestra.
Ścieżki:

cache jeśli hit
local albo gemini-flash jeśli pytanie krótkie / klasyfikacja / niski stake
gemini (Twój obecny) default
później: „upgrade” gdy Judge score < próg — na razie zostaw hook

Heurystyki v1 (wystarczą):

długość pytania
czy są tools (briefagent → zawsze pełny Gemini)
ręczny header X-Route: force-gemini

Loguj route_reason.
Done: tabela 20 pytań: która trasa, dlaczego. BriefAgent nigdy nie idzie w ślepy cache.

Faza 7 — Tracing
Czas: 1 wieczór
Każdy request ma trace_id i zapis:

app, route, cached, guardrail
tokeny in/out, latency, error
skrót promptu (nie pełne sekrety)
dla agenta: lista tool names (nie całe payloady)

Sink v1: JSONL logs/traces.jsonl + GET /v1/traces/{id}.

Langfuse / Phoenix dopiero gdy JSONL działa — inaczej utoniesz w SDK.
Done: z ID z odpowiedzi odtwarzasz cały przebieg w 10 sekund.

Faza 8 — Podpięcie Judge’a
Czas: 1 wieczór
Nie wstawiaj Judge’a na każdy request (drogo i wolno).

sample: np. 10% albo tylko gdy app=docground i nie cache
asynchronicznie: request wraca, ocena dopisuje się do trace
pola: faithfulness / relevance + model sędziego
alert w logu gdy score < próg (nie blokuj usera na v1)

Done: w trace widać judge_score. 30 requestów demo → masz mini-tabelę jakości vs cache vs raw Gemini.

Faza 9 — Demo i twardnienie
Czas: 1 dzień
UI minimalne (Gradio albo 1 strona):

wybór app: DocGround / BriefAgent / raw chat
pytanie + 3 przykłady
panel po prawej: route, cached, tokens, $ est., guardrail, trace_id
scenariusz nagrania 90 s:
pytanie RAG → cytat, route=gemini
to samo pytanie → cache hit
injection → zablokowane
seria requestów → zbliżasz się do capu

Testy: guardrails, budget, cache hit/miss, 429 mock.

README jak spec: problem, diagram, limity Gemini free, tabela (cache hit, p95, $/1k est.).
Done: link live albo wideo. Recenzent rozumie system bez czytania kodu.

Faza 10 — Portfolio freeze
Czas: pół dnia

pinned repo modelgate
diagram: DocGround / BriefAgent → ModelGate → Gemini (→ local)
sekcja trade-offów: dlaczego nie LangChain gateway, dlaczego nie LLM-as-guardrail na starcie, dlaczego cache tylko na RAG
znane limity free tiera
backlog „nie zrobione”: Redis, płatny Pro, auto-upgrade po Judge, A/B modeli

Nie dodawaj już ficzerów. Zatrzymaj się i opisz.
Done: 3 repo tworzą jedną historię. ModelGate jest klejem, nie czwartym chatbotem.
