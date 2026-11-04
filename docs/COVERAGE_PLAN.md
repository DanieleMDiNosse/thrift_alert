# Deeper marketplace scans and alternative phrases — implementation checkpoints

## Current state after resuming 20261018_152431_8502c8

Implementation and local deployment verified. Source remains uncommitted on
feature/unified-marketplace-watch, based on dcfe8c8; no push or host service changes.
The pre-existing working-tree implementation was preserved and completed, not reset.
No ownership conflict or other project process was observed before deployment.

| Unit | State | Evidence |
|---|---|---|
| Alternative semantics | verified | One phrase per line, max five, OR between phrases, all words within; shared price/include/exclude filters |
| Native URLs and dedup | verified | Same-platform URL validation, one query/site, listing IDs deduplicated across phrases |
| Bounded pagination | verified | Budgets Subito30/Vinted10/Wallapop25 divided among phrases; native end/page/site limits and cursor loops tested |
| Baseline upgrade | verified | Existing quick watches silently expand once; later-phrase errors cannot seed partial success; existing prices refreshed |
| GUI and coverage | verified | Multiline form and last-successful completed/partial status; seven isolated Firefox tests passed |
| Previous live scans | historical evidence retained | data/deep-live-validation.json read back; see below, not rerun during this recovery |
| Local runtime | verified | GUI HTTP200, /api/state monitor_running=true; PID/cwd/command identity confirmed |
| Local commit | not performed | Changes intentionally left inspectable in working tree; no remote write |

## Resume corrections and verification

The final independent review in the old session withheld approval for two P2 bugs:
1. Silent expanded baseline only touched existing rows, leaving old prices that could
   trigger a false delayed price-change alert on the next identical scan.
2. Vinted short-page inference overrode explicit metadata advertising more pages.

Added regressions, observed five intended failures, then applied minimal fixes:
- pipeline.py updates the observed price in the baseline transaction.
- vinted.py respects explicit total_pages even for short/empty intermediate pages;
  page length infers end only without total_pages. Native page bounds still apply.
- Corrected the synthetic explicit-empty fixture to advertise zero results rather
  than a contradictory three-page result set.

Verification from repository root, project Python3.12:
  env -u PYTHONPATH -u PYTHONHOME app/.venv/bin/python -m pytest -q app/tests
  => 307 passed, 13 skipped (22.23 s).
  env -u PYTHONPATH -u PYTHONHOME MARKETWATCH_BROWSER=1 app/.venv/bin/python -m pytest -q app/tests/test_phrase_browser.py app/tests/test_quick_search_browser.py app/tests/test_gui_browser.py --tb=short
  => 7 passed (16.76 s), isolated databases, no real push or marketplace calls.
  git diff --check => passed.

An initial test invocation inherited the agent's Python3.14 PYTHONPATH and failed
imports. Python -E fixed the parent but not wrapper subprocesses; clearing both
PYTHONPATH/PYTHONHOME fixed the full suite. No dependency install/change was needed.
OPERATIONS.md documents this environment boundary.

Two local review passes completed: specification review of phrase/filter semantics,
coverage versus inventory completeness, baseline/notification rules; regression-risk
review of validation, migrations, manual URL compatibility, UI rendering and runtime
identity. The two previously reported review findings have executable regressions.
No new independent reviewer approval is claimed for this resumed patch.

## Historical live evidence (old session, not fresh results)

Read-only three-phrase EUR40–100 scan:
- Subito: 467 distinct listings, 52 matching, 28 pages, partial/page_limit.
- Vinted: 708 distinct listings, 17 matching, 9 pages, partial/page_limit.
- Wallapop: 3 distinct listings, zero matching, 7 pages, native pagination end.
These counts precede the resumed corrections and are not current inventory counts.

The old session also silently persisted broader baselines for the existing single
phrase 'casse attive': Subito35/Vinted13/Wallapop0 matches, zero notifications.
Do not repeat the expansion or add alternative phrases without user intent.

## Deployment and preserved state

Before restart, SQLite integrity_check=ok, all three saved sources seeded/pending,
three coverage rows, original 'casse attive' enabled watch and phrase unchanged.
Created private consistent backup:
  backups/resume-deep-scan-1792437962391055393.sqlite (0600, integrity_check=ok).
Doctor: schema v6, local browser dependency available, ntfy configured, all critical
checks passed; dependency readiness is not proof of a new network fetch.

Restarted only the project's stopped processes with sanitized Python environment:
- monitor PID368227, data/monitor.pid and append-only data/monitor.log
- GUI PID368228, data/gui.pid and append-only data/gui.log
Both detached with a new session and verified alive with this project's cwd and
subitoo.cli command. Dashboard at http://127.0.0.1:8787/ returns HTTP200;
/api/state confirms monitor_running=true and notifier configured. Normal scheduling
has resumed: at read-back Subito was running, other sources pending. That ongoing
scan was not represented as a completed fresh live validation.

No test notification sent during recovery; normal enabled monitoring may send real
new-listing alerts. No new system/autostart service was installed: reboot still
requires restarting these local processes. Physical Android receipt remains unverified.
GUI.md now documents phrases, budgets, baseline expansion and partial coverage.

## Scope and next action

Requested implementation is operational; no further automatic edit or expensive
live replay is required. User may open the GUI and choose alternative phrases.
Keep current source changes unless a separate commit is desired. Preserve prior
outputs and private backups; do not expose .env, ntfy topic or cached personal data.
No promise that a public marketplace exposes its complete inventory: native limits,
regional scope/ranking and fuzzy search remain. No CAPTCHA bypass, request flood,
unbounded retries, paid proxy or Facebook automation.
