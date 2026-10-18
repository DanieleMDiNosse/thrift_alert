# Validation evidence

## Final gate summary

| Gate/platform | Status | Evidence / remaining boundary |
|---|---|---|
| G0 audit | PASS | Baseline107, pinned refs/licenses, architecture/security audit |
| Subito | LIVE + OFFLINE + NTFY VERIFIED | 30 real listings; fixture regression; controlled remote push read back |
| Vinted | LIVE + OFFLINE + NTFY VERIFIED | 96 real listings; fixture regression; controlled remote push read back |
| Wallapop / G2 | LIVE + OFFLINE + NTFY VERIFIED | 39 real listings; synthetic contract/error fixture; controlled push read back |
| G1 Android end device | HUMAN BLOCKER | Topic configured privately; user subscription/physical receipt unverified |
| G3 watch/dedup | PASS | Three-site saved group, silent seeds, global overlap/restart/concurrency regressions |
| G4 Facebook | PASS disabled alternative | Unsupported, no adapter/login/session; native manual fallback documented |
| G5 source-local | PASS with stated boundaries | Offline212, live3, two review passes, migration retry, backup/restart, docs |
| Docker / hermes verify | BLOCKED | Docker executable absent; no build/runtime certification |

No genuine newly published listing was waited for: new-event E2E evidence is a
controlled injection through the real notifier, explicitly labeled, not an organic
listing observation. No physical Android receipt, exhaustive coverage, ToS permission
or remote exactly-once guarantee is inferred from these checks.

The only persisted demonstration watch is disabled; meaningful personal search URLs
and phone subscription remain operator setup, not silently chosen shopping intent.

## Phase 0 / G0
- Initial directory contained only the user plan, not a Git repository.
- Cloned Kianda/subitoo into `subitoo`, feature branch; no upstream writes.
- Host: Python default 3.14.7; system Python 3.12.3 selected in app/.venv.
- uv available; Docker/Compose absent. 552 GiB free as reported by df.
- Before functionality changes: `cd app && .venv/bin/python -m pytest -q`: **107 passed**.
- Dependency install uses existing pyproject editable extras, no global/system changes.

## Phase 1 / G1 (partial, Android receipt unverified)
- Vinted homepage HTTP 200. Found native `/catalog/1206-outerwear` link in HTML.
- Live existing Vinted adapter retrieved **96 listings** from that exact native link;
  first listing ID `10424401951`. This is a connectivity test, not the user's saved search.
- Original Subito browser health failed: Docker hostname unavailable.
- Added optional `BROWSER_BACKEND=local`: ordinary headless Playwright Firefox,
  no Camoufox/stealth additions, remote browser compatibility retained.
- Installed upstream-pinned Playwright 1.53.0 in local venv, Firefox 139 build 1488
  in user cache; no privileged dependency install.
- Live Subito adapter retrieved **30 listings** using native homepage link
  `https://www.subito.it/annunci-italia/vendita/usato`.
- Generated a private high-entropy ntfy topic in ignored `.env` (0600); never printed.
- `app/.venv/bin/subitoo notify test` succeeded. Exact topic read-back HTTP 200,
  one message and matching test title verified. Android subscription/receipt needs user action.
- No actual user search URLs were provided. Generic connectivity searches must not
  be silently presented as meaningful user-configured watches.

## Phase 3 / G3 (offline work)
- RED: durable event suite initially failed 3 assertions: separate schedules doubled
  notifications, a global baseline was reannounced, and price changes were on by default.
- GREEN: 21 event/pipeline tests passed after implementation.
- RED: watch suite initially failed all 5 tests (missing module/CLI).
- GREEN: 32 watch/event/pipeline/query-JSON tests passed.
- New queries default to new-only. Existing DB queries retain historical price-change
  opt-in during migration. Upstream price-change regression now explicitly opts in;
  upstream documented cross-heartbeat duplication test now requires suppression.
- Group keywords are literal case-insensitive substrings: ALL includes, ANY excludes.
- New/changed sources seed independently and silently. Overlapping groups use the first
  successful already-seeded scanning group as notification owner; no retroactive push
  for a later group, and no assertion that unscanned groups match.
- Global event key is (site, listing ID), persisted independently of query removal.
- SQLite IMMEDIATE transaction serializes new-event check/send/commit. Normal restarts
  are deduplicated; remote acceptance followed by process crash before SQLite commit
  may duplicate (at-least-once; ntfy has no transactional exactly-once guarantee).
- Pending send failure cannot be converted into a silent baseline by a newly added group.

## Safety regression work
- RED: 10/13 schema/URL tests failed against upstream behavior.
- Subito now requires explicit ads/results lists, not `{}` as empty success; Vinted
  malformed wrappers/identities fail instead of being silently discarded.
- URL validation rejects non-HTTP schemes, embedded credentials, foreign hosts and ports.
- Local plugin settings read ignored .env with environment override; upstream only read
  os.environ for plugins, which worked in Compose but not source-local operation.

## Remaining gates
- Wallapop implementation/live evidence and reference research in progress separately.
- Facebook remains disabled/unimplemented pending bounded feasibility research.
- Full integration/regression/security review, runnable persistent scheduling, source
  documentation and final local commits still pending.

## Verification follow-up
- Latest focused suite: 84 passed, including new atomic-baseline rollback/retry test.
- Source disable/reenable now preserves query identity and seeded history (RED then GREEN).
- Local wrapper/scheduler/watch suite: 10 passed. `./marketwatch doctor` succeeds
  with database schema v4, installed adapters, local browser dependency, ntfy config.
  Browser dependency check is not a live fetch and must not imply a remote WS connection.
- `hermes verify --json` at repo root selected Docker and failed: executable absent.
- Source-local `hermes verify` detected Python but its generic bootstrap invoked bare
  pip outside app/.venv; test then mixed inherited Python paths and failed importing
  pydantic_core. Removed the five freshly installed packages plus editable subitoo
  from that unintended Python tool installation to undo the bootstrap side effect.
  No pre-existing package was upgraded (installer reported each added package).
- Attempt to save an explicit project-local verification recipe was denied by the
  protected-file approval boundary. No retry or bypass attempted. Root saved manifest
  remains the detected Docker recipe. Verification automation remains BLOCKED;
  explicit app/.venv Python test commands are the valid evidence instead.
- Native website search UI generated actual URLs for `sony a6400` on Subito and Vinted;
  these are connectivity/examples only, not an instruction to monitor this product.

## Phase 2 / G2 — PASS (bounded live compatibility)
- Parent independently repeated Wallapop adapter fetch with project HTTP context:
  39 normalized listings, first ID `3zlmq4opm4jx`, EUR. No alternate transport needed.
- All Wallapop tests pass after rejecting unverified closest sort and supporting
  absent optional price/image/location safely. Synthetic fixture explicitly labeled.
- Reference commits/licenses documented in WALLAPOP.md and indexed in RESEARCH.md.

## Integrated live and ntfy validation
- Stored `connectivity-demo` watch (disabled) with all three actual source configs.
- Manual first scans: Subito 30, Vinted 96, Wallapop 39; all status pending, seeded true,
  zero notifications. Reopened SQLite: all three baselines and disabled state preserved.
- Independent opt-in live test harness: `MARKETWATCH_LIVE=1 app/.venv/bin/python -m
  pytest -m live -q -s app/tests`: 3 passed, 203 deselected, 33.21 s; counts 30/96/39.
- Controlled fixture-to-real-ntfy test used temporary isolated DB, parsed fixtures for
  each platform, empty baseline then one listing, actual notifier, then repeat run.
  Each platform: exactly one send and zero repeat. Titles explicitly CONTROLLED TEST.
- Exact private-channel read-back verified three controlled messages with correct
  platform, title, price, group and HTTPS click URL. Initial since=10m read-back
  returned no matches; since=all on this newly generated topic returned all messages
  and assertions passed. No additional sends were made to compensate for that poll.
- Android display/receipt remains UNVERIFIED until user subscribes locally; do not
  claim physical phone delivery from server publication/read-back.

## Phase 4 / G4 — PASS disabled alternative
- Facebook reference declares endpoint broken, no usable manual persistent-login
  bridge. No login/session collection attempted, no Facebook adapter registered.
- Disabled Facebook config accepted; enabling rejected explicitly as unsupported.
  Other marketplaces unaffected. This is not four-platform support.

## Phase 5 — current operational evidence
- Full suite: **204 passed, 3 skipped** (network tests explicitly opt-in), 0.61 s.
- Doctor succeeds; local browser dependency check correctly labeled, not remote WS.
- SQLite online backup generated under ignored backups/, integrity_check=ok, one watch.
- Local dispatcher launched independently with persist_on_release; log confirms ready,
  heartbeat 60 s. Actual Python PID recorded privately in data/monitor.pid.
  The only demonstration watch is disabled, so no unintended search notifications.
- New tests protect atomic baseline rollback, source pause/resume history, EUR bounds
  excluding foreign currency, and configured-secret/URL-credential diagnostic redaction.
- No Docker install, existing service changes, Facebook login, LLM runtime, or push.
- Specification review found and reproduced two issues: later baselined group could
  strand a pending failed send if the original group was disabled; watch test contacted
  disabled sources. Added RED regressions (three failing cases), repaired pending-event
  handling with existing-row update and disabled-source skip; full suite passed.
- Added migration v2→v4 compatibility and simultaneous two-connection delivery tests,
  and owner-only new DB/directory creation through local wrapper.
- Latest full suite: **210 passed, 3 skipped**, 0.90 s. Dependency compatibility check:
  all 29 installed packages compatible. Graceful dispatcher stop/restart verified with
  all three seeded/disabled query states unchanged.
- Specification re-review PASS: independent targeted tests 3 passed, both findings repaired.
- Quality review found two additional concurrency/recovery defects: stale scheduler
  enabled snapshot could claim a disabled source; interrupted ALTER migration left
  an unversioned column and duplicate-column failure on retry. RED regressions
  reproduced both. Atomic claim now checks enabled=1; each migration and version
  stamp commits atomically with explicit rollback. Latest full suite **212 passed,
  3 skipped**, 0.87 s; compileall and diff-check pass. Independent bounded quality
  re-review APPROVED; 4 targeted tests pass, no remaining issue within reviewed scope.
- Reproducibility: built/installed a non-editable package in a fresh scratch Python3.12
  venv using requirements.lock; `cd app && <fresh-python> -m pytest -q` returned
  **210 passed, 3 skipped**. Installed package creates schema v4 from bundled migrations.
  First root-level non-editable test invocation failed to resolve the upstream `tests`
  namespace; running from app (upstream documented working directory) resolved it.
  Existing local runtime/environment were not modified by this scratch verification.


