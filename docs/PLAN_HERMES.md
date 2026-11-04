# Hermes implementation plan — Unified Marketplace Watch

## Mission

Build a **free, open-source, self-hosted marketplace alert system** on Ubuntu Linux that monitors **new listings** matching user-defined searches on **Subito.it, Vinted, Wallapop, and (if technically and contractually viable) Facebook Marketplace**, sending concise push notifications to an Android phone.

**User intent:** alerts for new matching listings, not AI pricing analysis, arbitrage, auto-buying, or automatic seller messaging. **No LLM, MCP server, cloud subscription, paid proxy, or paid API is required at runtime.** Hermes is a *development assistant only*.

**Foundation:** https://github.com/Kianda/subitoo (GPLv3)

**Implementation references (inspect, do not blindly depend on or copy):**
- Wallapop: https://github.com/Duccioo/WallaPy (Apache-2.0), https://github.com/jaimeberdejo/wallapop-mcp
- Facebook: https://github.com/ethanashi/fbm-sniper-community (MIT)

The foundation already offers per-search scheduling, SQLite, a plugin system for site adapters, first-run seeding, ntfy notifications, and development Docker Compose. Its `CONTRIBUTING.md` shows the `BaseSite` + `@register` extension pattern. Verify **current code and behavior**, not just the README.

## Scope and priorities

### P0 — Essential
- Running monitoring for Subito and Vinted, with successful baseline and fresh-listing notifications.
- Add Wallapop as a third working adapter.
- Android push through **ntfy** (public free server with an unguessable topic, or self-hosted later).
- Multiple enabled/disabled saved searches, per-site URL configuration, sensible schedules, reliable deduplication.
- Headless operation after setup and persistence across restarts.
- Fully testable without running Hermes, Pi, Ollama, llama.cpp, or local GPU models.

### P1 — Unified experience
- A **watch group** with a friendly name (e.g. `Sony A6400`), shared include/exclude words and min/max price, plus a separate source-specific search URL per enabled platform.
- Group-wide notification format; one useful alert per newly observed `(marketplace, listing_id)` across overlapping searches and different schedules, with clear semantics if groups overlap.
- Minimal CLI or declarative YAML/JSON is enough; **no GUI** in the initial release.

### P2 — Conditional
- Facebook Marketplace read-only monitoring using **manual user login** and local session persistence, if it works on the user's Linux host and does not require bypassing restrictions.
- If Facebook is not viable, keep the other three operational and clearly document the actual limitation. Never claim four-platform support without a working live test.

### Explicit non-goals
- AI item ranking, value estimation, seller contact, automatic purchases, account creation, CAPTCHA solving/bypass, stealth/evasion, rotating identities/proxies, mobile apps, remote dashboard, microservices or multi-user features.

## Workflow rules for Hermes

1. Work **in the current project directory**, inspect its contents before making changes. If it is not the correct repository, clone upstream to an isolated workspace and create a feature branch such as `feature/unified-marketplace-watch`.
2. Read `README.md`, `CONTRIBUTING.md`, `SPEC.md`, `LICENSE`, `.env.example`, `docker-compose*.yml`, the adapter code, storage/migration code, notifier code, and tests. Inspect each third-party repository and its licensing. Capture upstream commit SHAs in `docs/RESEARCH.md`.
3. Write/maintain `PLAN.md` with checkpoints and an evidence log (`docs/VALIDATION.md`). Mark each gate passed/failed/blocked; continue to next independent task where possible.
4. Before changing functionality, **run the current tests** and record failures. Add tests for each change, run them before marking a phase complete. Favor small commits with meaningful descriptions; don't push, force-push, or modify unrelated repositories/services.
5. Prioritize **small, maintainable extensions** of Subitoo rather than reimplementing the scheduler, storage, browser integration, or notifications.
6. Network access to marketplace services is uncertain. Never turn a 403, CAPTCHA, login wall, rate limit, markup/API change, parse failure, or network timeout into an empty successful result. Log a diagnostic and keep the service healthy for the other marketplaces.
7. Never claim functionality is working without a verified live fetch **and** fixture-based regression tests. Don't mistake a mock or a green unit test for verified production access.
8. Keep secrets, cookies, authentication state and ntfy topic private, in local files excluded from Git (`chmod 600` when appropriate); redact logs. Do not ask to paste credentials into the chat or commit them.
9. Use conservative polling, backoff, jitter where appropriate, and respect site restrictions/terms. Do not implement CAPTCHAs bypass, account-protection evasion, or access-control circumvention.
10. The local host already runs other tooling. Do not uninstall, reconfigure, or interfere with **Hermes, Pi, Ollama, llama.cpp, SSH, Tailscale, or user system services**. Avoid unnecessary system-level changes or elevated privileges.
11. At the end of each phase, summarize files changed, test results, actual limitations, and next steps in `docs/VALIDATION.md`. Do not stop to ask routine design questions; make pragmatic choices, document them, and proceed. Ask only if credentials/manual Facebook login or a potentially destructive/privileged operation is unavoidable.

## Phase 0 — Technical audit and feasibility baseline

**Tasks**
- Verify Python version, existing dependencies, Docker/Compose availability, `uv`, and free disk space, without requiring changes to user services.
- Review the exact Subitoo adapter contract, database semantics and current test suite.
- Verify what exists already: ntfy notifications, sorting assumptions, first-run baseline, cross-query deduplication behavior, per-query cron, and URL support. Note that Subitoo documents some duplication across **different** schedules; measure it instead of assuming fixed.
- Examine WallaPy and FBM Sniper code for usable functionality, runtime requirements, and fragility. Inspect **Facebook search support** (not merely car/electronics examples).
- Review service permissions: the upstream Ofelia container uses the Docker socket; assess security implications and whether to preserve it in a personal trusted deployment or document a safer scheduling alternative.
- Save architecture notes and exact upstream commits in `docs/RESEARCH.md`.

**Gate G0:** Baseline tests run and are recorded; honest capability matrix for all 4 platforms; no credentials or existing services harmed.

## Phase 1 — Working upstream MVP (Subito + Vinted + ntfy)

**Tasks**
- Configure **source-built** development deployment, not prebuilt upstream-only images (use `./start.sh --dev` after code review or the equivalent `docker-compose.dev.yml`).
- Configure ntfy via `.env`; use a high-entropy topic and validate an Android-receivable test notification.
- Add at least one valid saved search each for Subito and Vinted using *search URLs generated by their websites*, preferably sorted by newest.
- Validate listing parsing, metadata, and silent first-run initialization. Test fresh item notifications with saved fixtures or controlled injections; a genuinely observed live new listing is ideal but not assumed available immediately.
- Ensure the polling service runs independently after Hermes exits, and data persists through restart.

**Gate G1:** Subito + Vinted live fetches work; ntfy test reaches the configured channel; seeded results don't trigger notification flood; restart doesn't lose tracked listings.

## Phase 2 — Wallapop adapter

**Tasks**
- Implement `app/subitoo/sites/wallapop.py`, subclass `BaseSite`, registered under `wallapop`, following actual current code conventions.
- Prefer a pasted **Wallapop search URL** (with native site filters/region) when feasible. Alternative: small adapter-defined structured search configuration, but do not invent unsupported filter semantics.
- Inspect whether WallaPy's nonofficial API is still functional; borrow ideas or use the library only if maintained, compatible, and permitted. Do not assume PyPI stability.
- Produce normalized `Listing` objects with durable site-specific identifiers, titles, numerical price, currency where available, direct URL, image, and safe missing-field behavior.
- Respect newest-first ordering/pagination where actually supported; bound page counts and HTTP requests.
- Add offline fixture + contract test following `app/tests/test_subito.py`; failure tests for 403/429, parse errors and empty searches.
- Run a limited live test and record output (redacted of any personal data).

**Gate G2:** Wallapop adapter registers, passes contract/unit tests, and live-search verification succeeds; other two marketplaces still work.

## Phase 3 — Reliable unified watch groups

**Tasks**
- Add the smallest possible user-facing abstraction: `watch group` name, include/exclude keywords, min/max price, enabled platforms, and **one platform-specific search URL/config per platform**. Avoid pretending that every site has identical location/category capabilities.
- Reuse existing core search/filters/scheduler. A watch group may simply create/track ordinary per-platform queries; implement a migration only if necessary.
- Expose lightweight operations: add, list, show, enable, disable, edit, remove, test/dry-run. Keep backward compatibility with existing `subitoo query` commands and DB entries.
- Define a **single global new-listing event** for `(marketplace, listing_id)` with explicit group-matching semantics. Prevent duplicate pushes even when a listing matches several queries on different cron schedules. Avoid masking a genuinely new match due to a previously failed scan.
- New-listing-only notifications must be default. Price-change alerts should be optional/off by default because the user asked specifically for *new* listings.
- First-run seeding must be scoped correctly when a new group or platform is added; no old-listing notification storms.
- Notifications contain platform, title, price, matching watch name, thumbnail (when supported), and a clickable direct link.
- Configure `TZ=Europe/Rome`, logging, and predictable scheduler interval; default to conservative site-specific polling (initially 15–30 min; Facebook, if added, 30–60 min). Do not promise instantaneous notifications.

**Gate G3:** One watch group searches all three supported platforms; a simulated new item sends one notification, no matter how many searches match; next run sends zero; persistence survives restart; existing standalone searches remain usable.

## Phase 4 — Facebook Marketplace feasibility spike and optional adapter

**Time-box the research effort**: treat Facebook as an independent, high-risk integration rather than blocking the stable 3-platform release.

**Tasks**
- Inspect `fbm-sniper-community` specifically for search queries, location handling, cookies/session handling, dependency stack, and whether it requires unsupported protections/evasion. Do not blindly copy stealth components.
- Examine whether current user-accessible Facebook Marketplace pages can be loaded **with manual user login**, via the existing Subitoo browser integration or a minimal compatible Playwright/Camoufox context.
- Prefer a search URL supplied by the user in a browser. Keep the account session on disk, outside version control, with restricted permissions. Never expose session tokens in logs or notifications.
- If authorized and workable, implement `facebook` adapter, normalized listings, fresh-item deduplication, session-expiry diagnostics, rate limiting, and offline fixture tests.
- If blocked by login/CAPTCHA/access restrictions, stop automated interaction; surface `UNSUPPORTED` or `AUTH_REQUIRED` accurately and document a manual/natively-notified fallback. Do not attempt CAPTCHA bypass or rotating proxy identities.
- Avoid dependence on a visible desktop session after setup if possible; if not feasible, document the limitation and leave Facebook disabled by default.

**Gate G4:** Either (a) Facebook real search + repeatable fetch + offline tests + ntfy notification have all been verified, OR (b) Facebook is explicitly disabled with an honest limitation and 3-platform functionality is unaffected. No false green status.

## Phase 5 — Quality, security, operational deployment

**Tests and acceptance scenarios**
1. First successful scan seeds the existing matches silently, with no notification flood; an HTTP/parse error never counts as a successful baseline.
2. Second run with same items: zero notifications.
3. Fixture adds item: exactly one push containing correct platform/title/price/URL.
4. Overlapping searches, different schedules: no duplicate push for a global listing event.
5. One platform returns HTTP 429/403 or schema error: no fake empty success, no false baseline reset; other platforms still run.
6. Temporary network outage and process restart: database state preserved, no redundant push flood, failures observable.
7. Expired Facebook session: meaningful paused/auth-required state, no credential leak.
8. Invalid search URL/domain and missing price/image: correct validation and graceful handling.
9. Per-platform filters, timezone, sorting and event timestamps: tested, with limitations documented.
10. Existing upstream tests still pass; each new adapter has fixture-based contract tests and limited, opt-in live smoke test.

**Deliverables**
- Source code on an isolated feature branch, with small commits (no push unless asked).
- `PLAN.md`, `docs/RESEARCH.md`, `docs/VALIDATION.md` with a truthful platform status matrix.
- `README.md` setup/use instructions, `.env.example`, saved-search/watch-group examples, `docker-compose.dev.yml` updates if needed.
- Commands to start, stop, run doctor, test notifications, show searches, tail logs, back up SQLite, and restore data.
- Clear notes on Facebook constraints, unofficial APIs, privacy/ToS, rate limiting, Docker socket permissions, session expiry, and future maintenance.

**Final definition of done**
- For each platform marked `WORKING`, recorded evidence of real listing retrieval, deterministic adapter test, and end-to-end notification test.
- Routine operation without paid services and without any LLM.
- No secrets committed, no breakage to existing Subito/Vinted behavior, no repeated notifications on restart.
- If Facebook remains `BLOCKED`, the release remains acceptable as a stable three-platform tool, but **never label it a four-platform solution**.

## Example watch definition (illustrative, adapt to real CLI/data model)

```yaml
watches:
  - name: sony-a6400
    enabled: true
    min_price_eur: 0
    max_price_eur: 550
    title_include: ["a6400"]
    title_exclude: ["guasto", "solo scatola", "broken"]
    notify_on_price_change: false
    sources:
      subito:
        search_url: "<paste actual Subito search URL sorted newest>"
      vinted:
        search_url: "<paste actual Italian Vinted search URL>"
      wallapop:
        search_url: "<paste actual Wallapop search URL>"
      facebook:
        enabled: false  # enable only after verified and manual login
        search_url: "<paste actual Facebook Marketplace search URL>"
```

**The URLs are intentionally placeholders.** Do not hardcode imagined URLs or treat this illustrative YAML as already supported by upstream.

## Start instruction for Hermes

> Execute this plan incrementally, beginning with Phase 0. Work autonomously on non-destructive steps, keep an updated checklist and evidence log, and implement each phase only after establishing the previous phase's test baseline. Provide a concise phase summary after each gate. Never report an unverified marketplace as working. Prefer working, tested three-platform monitoring over a brittle four-platform demo. Stop only when a genuine manual-login requirement, system privilege escalation, or external blocker makes autonomous safe progress impossible.
