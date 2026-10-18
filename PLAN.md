# Unified Marketplace Watch checkpoints

Work confined to this clone, branch `feature/unified-marketplace-watch`.
No pushes, privileged installs, protection bypass or changes to existing user services.
A new project-local dispatcher process is running independently, with demo disabled.

- [x] Inspect initial folder and clone upstream in isolated subdirectory.
- [x] Upstream baseline: 107 passed, app/.venv Python 3.12.
- [x] G0 PASS: pinned source/license/architecture audit; capability evidence recorded.
- [ ] G1 PARTIAL / HUMAN BLOCKER: local source-built Subito/Vinted and ntfy publish+
  read-back verified, silent baseline and persistence verified; actual Android receipt
  needs user subscription/confirmation. Docker-specific deployment NOT VERIFIED.
- [x] G2 PASS: Wallapop registered, fixture/error tests and parent live fetch verified.
- [x] G3 PASS: three-platform watch, durable global new events, controlled notifications,
  overlap/different schedules/restart/concurrency, new-only default, standalone queries.
- [x] G4 PASS disabled alternative: Facebook unsupported, no login or adapter; source
  audit found broken endpoint and unsuitable session bootstrap; native manual fallback.
- [x] G5 PASS for source-local implementation: docs, dependency lock, private config,
  backup, dispatcher restart, tests, specification and code-quality re-reviews complete.
  Docker/Android remain the explicit external limits above.

## Current verification checkpoint

- Offline: 212 passed, 3 live tests skipped by default.
- Explicit live smoke: 3 passed, Subito 30 / Vinted 96 / Wallapop 39 listings.
- Controlled fixture-to-real-ntfy: three platform messages read back, one each,
  correct price/title/group/link, repeated scans zero pushes; phone receipt unverified.
- `doctor`, compileall, git diff --check, dependency compatibility passed.
- Specification review initially found two defects; RED regressions added, fixed,
  independent bounded re-review PASS (3 targeted tests). Quality review found and
  repaired atomic scheduler-disable and migration-retry defects (RED then GREEN);
  bounded quality re-review APPROVED (4 focused tests).
- `hermes verify --json`: BLOCKED by missing Docker. Generic Python bootstrap was
  unsuitable for this venv and its unintended installation was undone. Protected
  verification-recipe edit denied; no bypass/retry attempted.
- Local commits: `162ff90` Wallapop adapter/tests/research; `d7988da` durable watches,
  migrations, local monitoring and regressions. Operational docs/wrapper packaged in
  the final commit; no pushes.

## Operator boundary

The only saved watch is a disabled connectivity example, not a personal shopping
request. Configure website-generated URLs and subscribe to the private local ntfy
topic before enabling meaningful monitoring. Do not expose the topic in chat/logs.
No claims of four-platform support, physical phone delivery, Docker build success,
reboot autostart, exhaustive search coverage, platform permission, or remote
exactly-once delivery. See docs/VALIDATION.md and docs/OPERATIONS.md.
