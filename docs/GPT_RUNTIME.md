# GPT runtime: Hermes Codex OAuth, gpt-6.1-sol, reasoning low

## Active deployment

The active workflow is `AI_WORKFLOW=on_demand`: use the listing's **Recensisci**
button, not the legacy **Riprendi AI** control described later. See
[WORTH_ASSISTANT.md](WORTH_ASSISTANT.md). Provider/authentication details here still
apply; automatic discovery/scoring is disabled and the old workspace is hidden.

The application now reuses the existing **faraday Hermes Codex login**. It does not
require an OpenAI API key and does not copy access/refresh tokens into `.env`, the
application database, frontend, logs or command arguments. Hermes owns resolution
and token refresh. The provider is explicitly selected; no silent provider fallback.

The existing `.env` contains operator-only, non-secret routing settings:

```ini
AI_PROVIDER=hermes
AI_HERMES_HOME=/home/danielemdn/.hermes/profiles/faraday
AI_MODEL=gpt-6.1-sol
AI_REASONING_EFFORT=low
AI_ENABLED=false
AI_RETRIES=0
```

`AI_HERMES_PYTHON` is also configured to the installed Hermes runtime interpreter.
It must be an absolute executable path, not the application interpreter. This
installation uses managed Hermes environments, not the older repository `venv`
(which lacked dependencies when checked). A verified way to inspect the active
interpreter from this Hermes CLI environment is:

```sh
python -c 'import sys; print(sys.executable)'
```

After a managed Hermes update, check this setting if its old environment is removed.
The application does not install or upgrade Hermes. The actual interpreter used
for the successful integration check was:

```text
/home/danielemdn/.hermes/installs/a1951cb19b5543ea/environments/de2bf34ca2d04a6c99ff44d4865f8225/venv/bin/python
```

`AI_ENDPOINT` and `AI_API_KEY` are not used for Hermes requests. Leave secrets out
of commands and chat. The existing private `.env` remains owner-only (`0600`).
The settings boolean `credential_configured` means the interpreter and profile auth
container exist, not a live assertion that the account is authenticated or entitled.
A live successful request is the stronger check.

## Configurazione su un altro PC o con un altro utente Hermes

Se trasferisci l'applicazione su un altro computer o desideri utilizzare un altro utente/profilo loggato su Hermes:

1. **Aggiorna i percorsi nel file `.env`:** non mantenere i percorsi dell'host precedente, poiché sono assoluti e legati al filesystem locale.
2. **Individua il profilo Hermes (`AI_HERMES_HOME`):**
   ```sh
   ls -la ~/.hermes/auth.json             # se usa il profilo principale
   ls -la ~/.hermes/profiles/*/auth.json  # se usa un profilo specifico
   ```
   Imposta `AI_HERMES_HOME` con il percorso assoluto alla cartella contenente `auth.json` (es. `/home/nuovoutente/.hermes` o `/home/nuovoutente/.hermes/profiles/nomeprofilo`).
3. **Individua l'interprete Python di Hermes (`AI_HERMES_PYTHON`):**
   Dall'ambiente Hermes attivo su quella macchina:
   ```sh
   python -c 'import sys; print(sys.executable)'
   ```
   Imposta `AI_HERMES_PYTHON` con l'eseguibile restituito.
4. **Riavvia i servizi locali:**
   ```sh
   ./start-local.sh restart
   ```
   Il worker e la GUI useranno immediatamente il nuovo profilo; Hermes risolverà e rinnoverà i token OAuth per il nuovo account, addebitando le recensioni sulla quota associata.

## Start and use

```sh
cd /home/danielemdn/Documents/repositories/thrift_alert/subitoo
./start-local.sh
./start-local.sh status
```

Open http://127.0.0.1:8787 . Select **Riprendi AI** when ready. AI remains paused
through this configuration change; the existing SQLite queue is preserved.
Resuming can process previously queued work as well as new listings. There is no
application-level spending or subscription-quota cap. The selected account's plan,
provider terms, usage limits and data policy apply; OAuth reuse is not a promise of
unlimited or free inference. No local GPU model server is required or launched.

After editing inference settings, use `./start-local.sh restart` to reload all app
components. That command also restarts the marketplace monitor; ordinary start
reuses existing processes and will not reload an already running worker.

## Security boundary and data flow

1. The app constructs bounded task prompts and strict JSON schemas.
2. Its existing cross-process inference lock serializes requests.
3. `hermes_bridge.py` invokes a fixed `hermes_runner.py` path using the configured
   interpreter with Python `-I`. The working directory/PYTHONPATH cannot substitute
   arbitrary modules. Paths can only be configured server-side, not through HTTP.
4. The runner imports Hermes' `resolve_provider_client` with explicit provider
   `openai-codex` and `raw_codex=True`, so Hermes resolves/refreshes its own login.
5. It uses the native streaming Responses API with `store=false`, reasoning low and
   strict `text.format` JSON schema. It does not instantiate an agent or register,
   forward or execute tool calls. Shell/filesystem/web tools are not available to
   the model. No Hermes API or new listening service is exposed to the browser.
6. Streamed text is bounded, requires successful completion, and is validated again
   by the application's domain and citation validators before persistence.

Only required environment settings are forwarded. Application API keys, inherited
Python module paths and API-route overrides are not forwarded. Child stderr is
not logged; error bodies are replaced with fixed safe codes. OAuth errors and
network failures keep pending work without consuming malformed-output retry budget.
Missing local runtime/profile configuration blocks activation and job claims.

This is process/module isolation, **not an OS sandbox**: the configured interpreter
and installed Hermes code are trusted and the process runs as the same local user.
The model has no tool interface. No changes were made to other Hermes profiles.
Hermes' installed subscription proxy only supported Nous when checked, so it was
not used for Codex. The app does not expose an unrestricted Hermes agent.

Codex does not accept the API-key route's `max_completion_tokens`/Responses
`max_output_tokens` cap. Hermes mode instead enforces the configured wall deadline,
context-character budget, source count, strict schema and output-byte limit. It
must not be described as having a hard generated-token/monetary cap. Normal
API-key mode retains `AI_MAX_TOKENS`; it is ignored by the Codex endpoint.
`store=false` does not guarantee zero retention by the provider.

Search/retrieval, source persistence, cache, queue and numeric rankings remain
application-controlled. Web search uses DDGS or the configured search backend;
GPT does not acquire browsing access. Listing/review text remains untrusted data.
Content included in prompts is sent to the account's cloud provider.

## Other supported modes

To use a separate billed OpenAI API key instead, set:

```ini
AI_PROVIDER=openai
AI_ENDPOINT=https://api.openai.com/v1
AI_MODEL=gpt-6.1-sol
AI_REASONING_EFFORT=low
```

Set `AI_API_KEY` privately on the backend. This mode uses HTTPS Chat Completions,
strict JSON output, `max_completion_tokens` and `store=false`. Missing keys and
plaintext HTTP block requests. To use llama.cpp explicitly, select
`AI_PROVIDER=local` and configure its endpoint/model; there is no automatic fallback.
No database migration or additional dependency was needed for the Hermes adapter.

## Verification

Deterministic tests `test_ai_hermes_provider.py` and `test_ai_hermes_runner.py` use
fake SDK responses, no live login. They cover strict native Responses requests,
no tools/secret forwarding, invalid/incomplete output, bounded timeouts, safe error
handling, missing configuration and preserved job attempts. They also cover
structured errors inside SSE streams: rate-limit/server failures and
authentication failures retain queued jobs without consuming attempts; refusals
and unknown codes remain rejected. The final browser-enabled suite returned
446 passed, 3 skipped. API-key/local tests
remain in place. A real native Codex structured-output probe succeeded, followed
by a real call through application `LLMClient`: Sony A6400, body only, validated
JSON, 4.2 seconds on this machine. This is a measured example, not a latency SLA
or proof that research conclusions are always accurate.

Run deterministic tests without live requests:

```sh
env -u PYTHONPATH -u PYTHONHOME app/.venv/bin/python -m pytest -q app/tests
hermes verify --json
```

The adapter depends on the installed Hermes Python client API. An incompatible
future Hermes update can require a narrow adapter update; failures are isolated
from marketplace monitoring. General agent features are intentionally not exposed.
