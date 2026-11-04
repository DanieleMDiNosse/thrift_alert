# Local AI product research and shopping assistant

The active workflow is now per-listing **Recensisci** (brief product review), without automatic
rankings or scoring. Follow [WORTH_ASSISTANT.md](WORTH_ASSISTANT.md). The ranking/chat
instructions below describe the retained legacy workflow, not the default UI.

Current deployment: GPT-6.1 Sol with reasoning low, not local Saluki. See
[GPT_RUNTIME.md](GPT_RUNTIME.md) for the active Hermes Codex OAuth configuration,
cost/privacy implications and validation boundary. Local-model sections below are
retained as optional fallback documentation, not the current provider selection.

## Architecture and scope

This is an additive extension, not a monitoring rewrite. Existing marketplace
adapters, scheduler, notification delivery and raw listing records remain intact.
The existing loopback Python HTTP GUI serves the new plain-JavaScript workspace.
SQLite remains the only database; no Redis, Celery or vector database is required.

Data flow:

1. The separate AI worker discovers persisted listings once per minute.
2. It queues exact model/variant identification with validated JSON.
3. Confident manufacturer/model/variant identities share a product. Unknown variants
   or low confidence stay separate. Generation is retained in the identity key;
   missing generation is not guessed. Matching remains conservative, not fuzzy.
4. Product research is queued once and reused. DDGS searches for sources; a separate
   extractor checks robots and retrieves bounded public text.
5. The configured model synthesizes retrieved passages and category-specific criterion
   scores. Source IDs must exist; invented citation IDs reject the response.
6. Deterministic Python computes quality, value and asking-price discount rankings.
7. Chat jobs retrieve products, sources, listings, rankings and persisted conversation
   turns. Replies and citations are stored back into SQLite and polled by the UI.

The worker has no shell, filesystem tools, arbitrary agent actions or code execution.
LLM output is data, never tool instructions. Monitoring does not import the AI package
and continues if the worker, network or model is unavailable.

## Install and launch on this machine

The verified repository root is:

```sh
cd /home/danielemdn/Documents/repositories/thrift_alert/subitoo
```

The existing Python environment is `app/.venv` (Python 3.12). Optional free search
adds DDGS and its dependencies; the existing base lock is preserved. The extended
lock pins those additions while constraining existing dependencies:

```sh
uv pip sync --python app/.venv/bin/python requirements-ai.lock
uv pip install --python app/.venv/bin/python --no-deps -e ./app
```

Do not replace your existing private `.env`. Add the `AI_*` options from
`.env.example`; keep the existing notification/search settings. Relevant defaults:

```ini
AI_ENABLED=false
AI_ENDPOINT=http://127.0.0.1:8080/v1
AI_MODEL=underdog-saluki-27b-iq2
AI_TIMEOUT=300
AI_MAX_TOKENS=2500
AI_MAX_CONTEXT_CHARS=48000
AI_SEARCH_BACKEND=ddgs
AI_RESEARCH_TTL_DAYS=90
AI_LOCK_PATH=./data/ai-inference.lock
```

Enable AI in the dashboard settings (persisted in SQLite), or set AI_ENABLED=true
before the first explicit dashboard override. Endpoint/model/secrets remain
server-side environment configuration. UI changes to enabled/weights override their
initial defaults; model selection never depends on the development model.

Avvio con un solo comando dalla directory del repository:

```sh
./start-local.sh
```

Avvia monitor, GUI e worker AI in background, riutilizzando i processi dello stesso
progetto già attivi. Puoi chiudere il terminale. Se trova la vecchia GUI senza API AI,
la riavvia per caricare il pannello aggiornato; il monitor attivo non viene riavviato.
La configurazione privata `.env` non viene modificata. Il worker usa automaticamente
lo stesso DB_PATH dell'applicazione. Il provider attivo usa il login Codex di Hermes;
lo script non avvia server GPU. Se il modello non risponde, il monitor resta attivo.

```sh
./start-local.sh status
./start-local.sh stop
./start-local.sh restart
```

Apri http://127.0.0.1:8787 . I log sono in `data/local-stack/monitor.log`,
`gui.log` e `worker.log`. Per un'altra porta usa `MARKETWATCH_PORT=8790 ./start-local.sh`
e mantieni lo stesso valore nei comandi di stato/arresto. L'arresto riguarda i servizi
verificati di questo progetto, anche se già attivi prima dello script; non termina
llama.cpp o processi di altri progetti. I PID salvati vengono verificati prima di
inviare segnali. Un avvio fallito arresta solo i servizi creati da quel tentativo.
Non installa servizi di sistema e non configura l'avvio automatico dopo il riavvio.

Per diagnostica è ancora possibile avviare i singoli componenti in primo piano:

```sh
env -u PYTHONPATH -u PYTHONHOME ./marketwatch serve
env -u PYTHONPATH -u PYTHONHOME ./marketwatch gui --no-open-browser
env -u PYTHONPATH -u PYTHONHOME app/.venv/bin/python -m subitoo.ai.worker --db ./data/subitoo.db
```

The worker's `--once` performs discovery and processes at most one job, then exits;
it is useful for diagnostics, not a complete research run.

### Local model

A running llama.cpp instance was discovered at port 8080 with alias
`underdog-saluki-27b-iq2`. A real JSON identification call succeeded. Do not start a
second server when this one is running. The executable and model paths observed are:

```
/home/danielemdn/.local/opt/Bonsai-demo/bin/cuda/llama-server
/home/danielemdn/models/underdog-saluki-27b/Underdog-Saluki-27B-1.0-IQ2-mix.gguf
```

Only if no model server is running, a conservative single-slot startup using the
installed server's observed flags is:

```sh
/home/danielemdn/.local/opt/Bonsai-demo/bin/cuda/llama-server \
  -m /home/danielemdn/models/underdog-saluki-27b/Underdog-Saluki-27B-1.0-IQ2-mix.gguf \
  --alias underdog-saluki-27b-iq2 --host 127.0.0.1 --port 8080 \
  -c 8192 -np 1 -ngl auto --fit on -ctk q4_0 -ctv q4_0 --jinja
```

For the smaller 8192-token startup above, also set AI_MAX_CONTEXT_CHARS=12000
and AI_MAX_TOKENS=1800 to reserve room for the system schema and output.
The character cap is a conservative approximation, not an exact tokenizer count.

This exact new startup configuration was not launched: the existing working GPU
server was intentionally left alone. Its observed configuration uses one slot,
automatic fitting/offload and quantized KV cache. Actual performance depends on
CPU offload and available VRAM; successful identification took about 102 seconds.
A separate whole-pipeline smoke attempt timed out during identification at a
150-second timeout. A second 300-second-budget attempt returned no schema-valid
identification after about 243 seconds and remained queued for retry. Therefore
live full research/chat completion is not certified.
The default timeout is consequently 300 seconds. Keep prompts and concurrency small.

All application inference requests share an advisory file lock. A second worker
on the same DB is rejected by a separate worker lock. These locks coordinate this
application, not unrelated Hermes/Ollama clients; the model server should also have
one inference slot. The application never launches GPU servers.

### Search backends and Hermes decision

DDGS is the optional default free search backend and worked in a real search.
Hermes on this host also supports DDGS, but its extraction tool reported that the
configured search-only backend cannot extract pages. The app uses the same small search library with its own controlled extractor,
independently of the configured inference provider.

Alternatively configure an existing trusted SearXNG instance:

```ini
AI_SEARCH_BACKEND=searxng
AI_SEARXNG_URL=http://127.0.0.1:8888
```

That is an example port, not an installed service. The operator must enable JSON
search output on their instance. SearXNG is optional and not installed here.
Do not expose Hermes API directly to webapp users. The optional Hermes OAuth
inference adapter calls only the provider client, not an agent loop or tool server.
See GPT_RUNTIME.md; direct OpenAI-compatible llama.cpp remains available explicitly.

## Database and jobs

Migration 0007 adds ai_products, ai_listing_products, ai_sources, ai_settings,
ai_jobs, ai_conversations and ai_messages. Migration 0008 adds independent research
cache timestamps and optional human-verified listing facts, preserving source
versions for older conversation citations. Migration 0009 keys verified facts by
marketplace/native advertisement ID, so overlapping saved searches cannot lose
them when the newest raw observation changes. Existing conflicting facts migrate
with newest-save-wins ordering (raw row ID breaks ties). Migrations run transactionally through
the existing migrator. Back up the DB with SQLite's backup API before first startup;
see OPERATIONS.md. Original listing tables/data are not rewritten.

Research TTL defaults to 90 days, configurable through AI_RESEARCH_TTL_DAYS.
Ready products are reused; expired products show needs_refresh and discovery queues
one refresh. The UI also supports manual refresh. Sources retain URL/title/passages,
retrieval and available publication times, source kind and provisional reliability.
Single-domain research confidence is capped at 0.5; multiple websites do not by
themselves prove independent evidence or failure rates. Source quality assessment
is model-assisted and explicitly labelled, not a certification.

The queue is bounded (default 1000 pending jobs), deduplicates active kind/target
jobs, claims atomically and recovers stale running jobs. Discovery fills available
capacity without rolling back useful work; successful jobs atomically release their
slot before follow-up research is enqueued. Chat and research take priority over
large identification backlogs (no preemption of a running inference request). Offline inference is
requeued after 60 seconds without consuming the permanent-error retry budget.
Invalid outputs/other failures have bounded retries with backoff and become failed.
Research can be retried via Refresh research; failed identification currently needs
an operator retry (documented limitation). Chat is queued, not a blocking HTTP
inference call. Clearing chat increments a revision and cancels pending replies so
an old running reply cannot resurrect cleared history.

## Rankings and missing data

These are transparent heuristics, not scientifically objective measurements.
Category rubrics exist for camera, audio, bicycle, computer and phone. Unsupported
categories retain knowledge/chat but no default quality rank. Weights are defined
in ai/ranking.py and can be changed through the guarded settings endpoint.

Quality: weighted criterion ratings on a 0–100 rubric. Each included criterion must
cite a retrieved source. Missing criteria contribute zero to the full-weight sum;
less than 50% supported weight is ineligible. Price does not enter this score.

Value: quality × condition factor × 100 / known total purchase cost. Factors:
new 1, like_new .98, good .90, fair .75, poor .50, unknown .60. Shipping and fees
must both be explicitly known; unknown costs produce no value score, not zero-cost
assumptions. Scores have units of heuristic quality points per EUR100 (or per 100
of the listing currency); different currencies are not pooled in the value rank.

Deal: 100 × (comparable median asking price − listing asking price) / median.
Requires at least five OTHER distinct recent listings with the same normalized
product/variant, condition and currency. A listing is excluded from its own
reference sample. Hypothetical offers change only the evaluated scenario: reference
market statistics always use original observed asking prices. The lookback is 90 days,
with median/IQR/sample size reported. Weight normalization and price quantiles avoid
overflow; unrepresentable arithmetic is unavailable, not an infinite score.
No completed-sale data is claimed. Asking prices can differ substantially from
realized sale prices. Unknown variants/conditions or small samples are ineligible.

The UI keeps categories separate and exposes quality/value/deal independently.
Hypothetical prices are separate calculation inputs and never overwrite listing
prices. An explicitly separate asking-price-only quality/value indicator is shown
when a category has no complete-cost value ranking; it excludes shipping/fees and
must not be presented as the best total-cost purchase. Enter hypothetical offers
through the price fields; arbitrary offer amounts embedded only
in conversational prose are not automatically parsed into a deterministic scenario. Rank tie ordering is deterministic. Quality ratings are generated by a
model, so independent fresh model runs need not be identical; deterministic ranking
means the same stored ratings, policy and listing snapshot yield the same order.

## UI and API usage

The AI workspace lives below the existing observed listings. Enable AI, run the
worker and click Analizza annunci. Select a category or saved search, then choose
one of the three rankings. Product details show criteria, strengths/weaknesses,
reported issues, specifications and sources. Select at least two products for the
comparison table. Create a new conversation with the selected products/search to
bind its context; changing a later UI selection does not silently rewrite old chats.

Chat history and citations persist in SQLite; localStorage remembers only the
selected conversation ID. New conversation and clear controls are included. The
small Markdown renderer supports text, paragraphs, bold and inline code without
HTML execution. Clickable citations come from validated stored source records,
not generated URLs. Output is polled rather than streamed. Check Ricerca nuove
fonti to request on-demand research; the actual question informs the search query,
including Reddit targeting when requested. Explicit search imperatives such as
“Cerca su Reddit …” or “Search …” also trigger research without the checkbox; ordinary
follow-ups use cached research. Focused research supplements existing supported
assessments rather than erasing them, and does not refresh the broad-research TTL.
Use the hypothetical-price section for offers without changing monitored prices.

Condition and fees are not guessed from marketplace prose. Explicit native Subito
condition/shipping and Vinted condition/service-fee fields are now reused with
marketplace provenance; seller declarations are not independent inspections.
Manual confirmed facts override these values. In product details,
open Dati verificati annuncio and enter confirmed condition/shipping/fees. Blank
means unknown and explicit 0 means confirmed zero. Additional accessories, age and
warranty fields are supported by the facts API; none alter original raw records.

Key endpoints (all POSTs require the existing CSRF header and local Host/Origin):

- GET /api/ai/state?category=camera&watch_id=1
- POST /api/ai/settings {enabled, weights?}
- POST /api/ai/sync {}
- POST /api/ai/products/{id}/refresh {}
- POST /api/ai/compare {product_ids, hypothetical?}
- POST /api/ai/listings/{id}/facts {condition, shipping_cost, fees, ...}
- POST /api/ai/conversations/new {product_ids, watch_id?}
- POST /api/ai/conversations/{id}/messages {message, research?, hypothetical?}
- POST /api/ai/conversations/{id}/clear {}

## Security and tests

GUI remains loopback-only, single trusted local user; it is not a multi-user hosted
service. Existing Host/Origin/CSRF checks also guard AI endpoints. Privileged endpoint
and credentials cannot be changed from the browser. Keys are never sent to the UI
or stored in error text. Do not expose the GUI or inference services publicly.

External retrieval rejects private/reserved addresses and mixed private/public DNS,
pins a validated IP while TLS checks the original hostname, disables redirects,
limits document size and time, and obeys robots or fails closed if unavailable.
Inference redirects are also rejected so bearer credentials cannot be forwarded
to an unexpected origin.
No authentication, CAPTCHA, paywall or JavaScript browser bypass is attempted.
Only public HTTP(S) text on standard ports is fetched. Search sends product queries
to external engines; local inference does not make web search private.

Prompts treat ads/reviews as untrusted data; no tool execution exists. This limits
prompt-injection consequences but cannot guarantee the semantic accuracy of a
model's synthesis. A citation proves retrieved provenance, not that the prose
faithfully interprets it. Review important purchase advice against the passages.

```sh
env -u PYTHONPATH -u PYTHONHOME app/.venv/bin/python -m pytest -q app/tests
MARKETWATCH_BROWSER=1 env -u PYTHONPATH -u PYTHONHOME app/.venv/bin/python -m pytest -q app/tests
hermes verify --json
```

The saved verification recipe builds Python sources, runs the full browser-enabled
suite and boots an isolated GUI DB on port 18787; it checks /api/ai/state and tears
it down. It does not start monitoring, send notifications or consume live inference.

## Remaining limitations

- No vision execution: original image associations remain; text-only works without it.
- No general Hermes agent/tool runtime; only a restricted OAuth inference adapter.
- No automatic marketplace condition/accessory extraction or completed-sale feeds.
- Variant detection is cautious text inference, not guaranteed product identification.
- Evidence search is bounded; no guarantee that every official/reliability source is found.
- Source classification is provisional; no automatic independent-report deduplication.
- One research TTL rather than separate specification/reliability TTLs.
- Failed identification retry and extensive preference editing lack dedicated UI controls.
- Chat retrieval keeps the latest question intact, sends each history turn once,
  budgets older excerpts and evidence, and does not delete stored history. This is
  bounded retrieval, not semantic long-term memory summarization. An individually
  oversized newest question/scenario can still exceed a deliberately small context cap.
  Historical sources/chat/jobs currently have no retention policy; database/query pagination should precede high-volume deployment.
- Live search/extraction and live model identification succeeded separately; the full
  real-model research-to-chat pipeline remains to be confirmed under local performance.
- Monitoring regressions are covered by deterministic tests; no new live marketplace
  scans or notifications were launched as part of AI verification.

## Manual live smoke and verification record

The repeatable manual smoke script uses a temporary DB and synthetic listing,
then real configured search and inference. It never notifies or scans marketplaces:

```sh
env -u PYTHONPATH -u PYTHONHOME app/.venv/bin/python scripts/ai_live_smoke.py
```

Do not run it concurrently with other heavy inference. It exits nonzero and reports
phase/job status if actual inference or evidence cannot complete. Fixture integration
tests are deliberately separate from this opt-in live check.

Independent security and quantitative review findings were reproduced before fixes:
redirect credential forwarding, full-queue follow-up rollback, unbounded duplicate
chat history, hypothetical-price contamination, duplicate-observation fact loss and
overflowing finite weights. Dedicated regression tests now cover all six, plus
capacity-limited discovery, extreme prices and focused-research preservation.
