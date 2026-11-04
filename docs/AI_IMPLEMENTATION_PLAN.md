# Incremental AI extension

Baseline: source-local Python 3.12, stdlib ThreadingHTTPServer loopback GUI,
plain JavaScript/CSS, sqlite3 WAL with SQL migrations, existing scheduler/marketplace
adapters retained. Repository is nested at `subitoo/`; existing uncommitted monitoring
and pagination changes must remain untouched. Baseline offline tests: 307 passed,
13 skipped.

Implementation ownership:
1. New ai package: validated identities, product knowledge, SQLite queue, local
   OpenAI-compatible inference, application-controlled research and persistent chat.
2. Public-only pinned-IP source fetcher and optional DDGS / trusted SearXNG search.
3. Deterministic category-specific quality, complete-cost value and asking-price
   discount calculations with explicit missingness/sample-size policies.
4. Separate frontend assets integrated into existing GUI with existing CSRF/Host guards.
5. Deterministic service/network/ranking/browser tests, existing regression suite,
   real local model smoke if available, user-facing configuration/operations docs.

No Redis, separate vector database, paid API, new GPU process or unrestricted Hermes
runtime. A dedicated worker keeps online research/inference away from monitoring.
Model observed available at 127.0.0.1:8080 with alias underdog-saluki-27b-iq2.
