# AI extension validation

Current workflow: per-listing on-demand judgments, not ranking/scoring. See
WORTH_ASSISTANT.md. Latest complete verification: 528 passed, 3 skipped, compile
and isolated GUI readiness HTTP 200 successful; Ruff and whitespace checks passed.
The sections below retain earlier verification checkpoints.

Latest dashboard/data correction: see AI_CATALOG_VALIDATION.md. Final verification
returned 471 passed, 3 skipped, successful compile/readiness HTTP 200 and whitespace
checks. The provider-specific verification below is an earlier checkpoint.

## Current GPT provider: existing Hermes Codex OAuth

The application is now configured for `gpt-6.1-sol` with reasoning `low`; see
GPT_RUNTIME.md. `hermes verify --json`: 446 passed, 3 skipped; compile and
isolated GUI readiness HTTP 200 succeeded. Ruff and `git diff --check` passed.
Live GUI/API read-back confirmed provider=hermes, model/reasoning, configured login
metadata and no missing-key warning or JavaScript errors. Real native Responses
strict-JSON inference succeeded through Hermes' Codex login. Application LLMClient
then identified Sony A6400 body-only in 4.2 seconds and returned a valid contextual
chat reply in 10.8 seconds. These are examples, not a latency guarantee or full
online-research quality certification. No API key or OAuth token was copied into
the app. The queue remains paused and preserved;
the running marketplace monitor and shared local model server were not restarted.
The remaining sections describe earlier local-provider verification, not a current
GPT model-quality or account-access certification.

The final independent adapter review found one retry-classification defect:
structured SSE `error`/`response.failed` events were all considered permanent.
Regression tests reproduced it, then the runner was corrected to classify known
rate-limit/server and OAuth codes without exposing provider messages. Worker-level
tests now verify queued status, unchanged attempts across repeated failures beyond
the permanent-error budget, delayed retry and no persisted product output. Unknown
errors and refusals remain rejected. All 28 focused Hermes tests pass.

## Final source-local verification

Run from `/home/danielemdn/Documents/repositories/thrift_alert/subitoo`:

```
ruff check app/subitoo/ai app/tests/test_ai_*.py scripts/ai_live_smoke.py app/tests/conftest.py
hermes verify --json
git diff --check
```

Observed final results:

- Ruff: all checks passed for the new AI modules/tests/smoke script and shared fixture.
- Python compileall: exit 0.
- Full existing + new suite, Firefox enabled: 384 passed, 3 skipped in 75.34 seconds.
- Isolated GUI startup: readiness HTTP 200 at http://127.0.0.1:18787/api/ai/state .
- Verification process teardown completed; no monitor or model server launched.
- Git whitespace checks: exit 0.

The saved local `.hermes/environment.json` recipe runs the explicit project Python,
clears inherited PYTHONPATH/PYTHONHOME and uses a temporary verification DB, rather
than trying the old auto-detected Docker recipe. This manifest is locally ignored
by Git; retain it locally or deliberately include it when preparing a later commit.

A separate run with deliberately conflicting AI_ENABLED/AI_MODEL/AI_ENDPOINT
values passed all 32 selected service, review-regression and enhancement tests.
AI test fixtures ignore the operator's private `.env` and inherited AI settings.
No automated tests require a real model, live search or marketplace access.

## Browser-to-database integration

`test_ai_integrated.py` exercises Firefox against the actual GUI HTTP server and
SQLite database. Only inference and external source retrieval are deterministic
fixtures. It verifies:

- Existing raw listings → normalized shared products → persisted source research.
- Three independent ranking outputs with sufficient comparable asking-price support.
- Technical comparison table built from actual backend results.
- Queued contextual chat processed by the worker.
- Clickable stored-source citations and history after a browser reload.

Other browser tests cover mobile layout, source-link/XSS handling, selection,
settings, error states, explicit costs and hypothetical-price payloads.

## Independent review and fixes

Both an independent security/integration review and a quantitative/specification
review were performed. Every reported finding received a failing reproduction and
was fixed:

1. Inference redirects could forward bearer credentials: all inference redirects
   now fail, and a two-origin HTTP regression proves the target is not contacted.
2. Capacity-one queues rolled back completed identification when creating research:
   successful jobs release capacity atomically before follow-up enqueueing.
3. Conversation data duplicated full message text and exceeded prompt limits:
   the prompt builder budgets history/evidence and preserves the newest question.
4. Hypothetical prices polluted reference medians: observed asking-price statistics
   are frozen independently of scenario offers.
5. Verified costs/condition were attached to one duplicate raw row: migration 0009
   persists them under marketplace/native advert identity instead.
6. Extreme finite weights overflowed scoring: safe scale normalization preserves
   score invariance and finite JSON results.

Additional regressions cover discovery filling only available queue capacity,
extreme price arithmetic and focused research preserving earlier supported criteria.

## Real external service probes

These are distinct from deterministic test evidence:

- Existing llama.cpp /v1/models responded with alias underdog-saluki-27b-iq2.
- Real local JSON inference identified Sony A6400, variant body only, with an unknown
  generation preserved as null. One successful measured call took about 102 seconds.
- A real DDGS query returned public camera review URLs. The app extractor retrieved
  readable text from https://expertphotography.com/sony-a6400-review and
  https://lensespro.org/sony-a6400-review/ after its access/robots checks.
- A full live worker smoke with a synthetic isolated listing did not finish
  identification successfully: an earlier attempt hit a 150-second timeout, and a
  later 300-second-budget attempt yielded no valid identification after about 243
  seconds and left the job queued. No complete live product-research/chat run is
  claimed. The model's response to all production prompt shapes remains unverified.

`scripts/ai_live_smoke.py` preserves the reproducible manual path for a later live
check. It creates/removes a temporary SQLite DB and never sends notifications or
contacts marketplaces. The real production database and running model server were
not reconfigured or restarted during verification.

## Remaining boundaries

See AI_ASSISTANT.md for startup and limitations. In particular: text-only runtime,
no Hermes runtime agent adapter, no verified completed-sale feed, conservative
variant matching, manually confirmed costs/condition, explicit hypothetical-price
fields, no automated retention policy, and model-assisted (not certified) evidence
interpretation. This remains a trusted single-user loopback application, not a
public multi-user deployment.
