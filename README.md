# Thrift Alert

Sistema self-hosted per il monitoraggio e la notifica di nuovi annunci su **Subito.it, Vinted e Wallapop**, con dashboard web locale e assistenza AI per recensioni tecniche on-demand.

> **Riconoscimenti & Base del progetto:**
> Questo progetto nasce ed è sviluppato come evoluzione del software open-source [subitoo](https://github.com/Kianda/subitoo) di **Kianda** (rilasciato sotto licenza GNU GPLv3).
> Ringraziamo l'autore originale per l'ottima architettura iniziale (adattatori, scheduler, notifiche). Rispetto al progetto originario, **Thrift Alert** introduce:
> - Supporto multisito unificato (**Subito.it**, **Vinted**, **Wallapop**)
> - Gruppi di ricerca aggregati (**Watch Groups**) con deduplicazione globale durevole
> - Dashboard web locale (**Marketwatch**) con supporto tema scuro e galleria foto
> - Filtri avanzati sul corpo della descrizione e recupero asincrono
> - Assistenza AI on-demand per recensioni tecniche oggettive (**"Recensisci"**) con supporto per Hermes Codex OAuth, OpenAI e modelli locali.

## Assistenza AI e Recensioni Prodotto ("Recensisci")

Nella dashboard web, accanto a ciascun annuncio, è presente il pulsante **"Recensisci"**:
- **On-Demand:** Nessuna analisi automatica a tappeto. L'AI viene interpellata **solo su richiesta dell'utente** con un clic, preservando token e chiamate.
- **Sintesi tecnica e oggettiva:** Esegue una rapida ricerca di specifiche via DuckDuckGo (`ddgs`) ed elabora con **GPT-6.1-sol** (reasoning `low`) una recensione concisa (2–3 frasi, max 60 parole) con punto di forza, limite principale e per chi è adatto, identificando modello esatto, anno di uscita, stima del prezzo di listino/nuovo e (per le bici) trasmissione/guarnitura/rapporti.
- **Cache locale:** I risultati vengono salvati nel database SQLite e conservati per 24 ore (`AI_WORTH_TTL_HOURS=24`).
- Documentazione completa: [docs/WORTH_ASSISTANT.md](docs/WORTH_ASSISTANT.md) e [docs/GPT_RUNTIME.md](docs/GPT_RUNTIME.md).

### Configurazione Provider AI (nel file `.env`)

L'app supporta tre modalità di inferenza configurabili in `.env`:

#### 1. Provider Hermes Codex OAuth (Consigliato se usi Hermes CLI)
Non richiede chiavi API a pagamento né costi diretti a consumo, riutilizzando l'accesso OAuth Codex già autenticato in Hermes:
```ini
AI_PROVIDER=hermes
AI_MODEL=gpt-6.1-sol
AI_REASONING_EFFORT=low

# Percorso alla cartella del profilo Hermes dove si trova auth.json
# Es. profilo principale: /home/<utente>/.hermes
# Es. profilo specifico:  /home/<utente>/.hermes/profiles/<nome_profilo>
AI_HERMES_HOME=/home/<utente>/.hermes/profiles/<nome_profilo>

# Percorso all'interprete Python dell'installazione Hermes
AI_HERMES_PYTHON=/home/<utente>/.hermes/.../venv/bin/python
```
> **Come trovare i percorsi su un nuovo PC:**
> - `AI_HERMES_HOME`: verifica dove risiede `auth.json` con `ls -la ~/.hermes/auth.json` oppure `ls -la ~/.hermes/profiles/*/auth.json`.
> - `AI_HERMES_PYTHON`: dall'ambiente Hermes, esegui `python -c 'import sys; print(sys.executable)'`.

#### 2. Provider OpenAI API Diretto
Se preferisci usare una chiave API standard OpenAI:
```ini
AI_PROVIDER=openai
AI_ENDPOINT=https://api.openai.com/v1
AI_MODEL=gpt-6.1-sol
AI_REASONING_EFFORT=low
AI_API_KEY=sk-...  # La tua chiave API da https://platform.openai.com
```

#### 3. Modello Locale (llama.cpp / Ollama)
Per eseguire un modello in locale senza connessione cloud:
```ini
AI_PROVIDER=local
AI_ENDPOINT=http://127.0.0.1:8080/v1
AI_MODEL=underdog-saluki-27b-iq2  # O l'alias del tuo modello
```

A self-hosted, **modular** marketplace scraper.

## Unified Marketplace Watch (this source branch)

Subito, Vinted and Wallapop were live-fetched on this host; offline regression
tests and controlled fixture-to-ntfy pushes are recorded in [docs/VALIDATION.md](docs/VALIDATION.md).
Facebook is explicitly unsupported: no account/session automation is enabled.
Android receipt still requires subscribing to the private topic and user confirmation.

Prefer a graphical interface? Run `./marketwatch gui` to open the simple Italian
local dashboard at http://127.0.0.1:8787. Manage watches without editing JSON; see
[docs/GUI.md](docs/GUI.md). The GUI reuses the existing monitor, not a second scheduler.

Use [docs/OPERATIONS.md](docs/OPERATIONS.md) for the verified source-local path,
watch JSON schema/examples, start/stop, doctor, logs, backup and restore.
Docker is optional and was unavailable on the validation host. No LLM or paid service
is needed at runtime. Existing Docker commands below are retained for upstream users.

```sh
./marketwatch doctor
./marketwatch watch add --from-json /path/to/watch.json
./marketwatch watch list
./marketwatch watch test 1
./marketwatch serve
```

New queries default to **new-listing-only**; price changes are explicit opt-in via
`notify_on_price_change`. Legacy queries retain their prior setting on migration.
New-listing dedup is now durable by `(site, listing_id)` across different cron schedules.
The first successfully scanning seeded group owns the alert; later groups record
silently. Remote acceptance followed by a pre-commit crash remains at-least-once,
not exactly-once. This supersedes the original same-heartbeat limitation below.

> 🛠️ Add a site or channel: [`CONTRIBUTING.md`](./CONTRIBUTING.md) · 📐 Design & rationale:
> [`SPEC.md`](./SPEC.md)

## Requirements

- [Docker](https://docs.docker.com/get-docker/)
- For phone notifications — optional, and pick one: [ntfy](https://ntfy.sh) (no account, just install the app) or a [Pushover](https://pushover.net) account

## Features

- Watch multiple marketplace sites — **Subito.it**, **Vinted** and **AutoScout24** (more coming soon...)
- Per-query schedules (each saved search has its own cron)
- Alerts on **new** matches and **price changes**
- Universal filters: price range, shipping present/absent, title include/exclude (regex)
- One notification per listing, with image + link
- Overlapping searches are de-duplicated so you're pinged **once**, not twice

## Quick start

```bash
cp .env.example .env      # optional: set up notifications (see below)
./start.sh                # pull the images + start everything, wait until healthy
./subitoo query add       # create a search (interactive wizard)
./subitoo query list      # see your searches
./stop.sh                 # stop (your data in ./data is always preserved)
```

`./start.sh` pulls pre-built images (`kianda/*` on Docker Hub), so every install runs the
same build. Pin a version with `SUBITOO_VERSION` in `.env` (default `latest`). Once up,
the scheduler runs due searches automatically.

| Script | What it does |
|---|---|
| `./start.sh` | Pull images + start, wait until healthy. Add `--dev` to build from source. |
| `./stop.sh` | Stop the stack; your data in `./data` is kept. |
| `./subitoo …` | Run any CLI command inside the container (`./subitoo query list`, etc.). |

**Tip:** symlink the wrapper onto your PATH — `ln -s "$PWD/subitoo" ~/.local/bin/subitoo` —
then just `subitoo query list` from anywhere.

## Create a search

`./subitoo query add` walks you through it:

1. **Pick a site** — `subito`, `vinted` or `autoscout24`.
2. **Paste the search URL.** Set up the keywords, category, region and sorting on the site
   itself, then copy the URL from your browser's address bar. (Result pages to scan defaults
   to 1 — that's 30 listings on Subito, 96 on Vinted, 20 on AutoScout24.)
3. **Add filters** — price range, shipping, title include/exclude (regex). You're only
   asked for the ones that apply to the site you picked — see below.
4. **Set the schedule** — a cron expression (default: every 4 hours).

The search runs once immediately to seed a silent baseline (this also confirms the URL
works); you start getting alerts from the next run.

### Which filters go where

The sites draw the line in different places, because Subito's own price filter only
appears once you pick a category while Vinted and AutoScout24 let you set everything up
front:

| | Subito.it | Vinted | AutoScout24 |
|---|---|---|---|
| **Price** | Subitoo's `price_min` / `price_max` | on Vinted — it rides along in the URL | on AutoScout24 — in the URL |
| **Shipping** | Subitoo's `shipping` filter | n/a, every Vinted item ships | n/a, cars aren't shipped |
| **Category, brand, size, condition** | in the pasted URL | in the pasted URL | make, model, year, km, gearbox, radius… in the pasted URL |
| **Title regex** | Subitoo's `title_include` / `title_exclude` | same — the one thing neither site can express | same, matched on make + model + version |

The wizard knows this and only asks what applies, so on Vinted and AutoScout24 it goes
straight to the title regex and tells you what it skipped. Subitoo asks Vinted for the very page your URL
describes, so whatever filters it carries are applied by Vinted itself and your results
always match what the URL says.

Vinted's search is fuzzy, though: on a narrow query it pads the results with loosely
related items (a pair of boots in a "meta quest" search). Those are genuine results — your
own browser shows you the same page — so `title_include` is how you cut them out.

On AutoScout24, sort the search by **most recent** before copying the URL: listings carry
no publication date, so that sort order is what keeps new cars on the first page. Paste the
URL from the address bar rather than typing one — AutoScout24 silently drops parameters it
doesn't recognise.

Need a bound tighter than your URL's — €150 under a URL that says €200? The core still
applies every filter on every site, so set it with `query add --from-json`.

### Title filters (regex)

`title_include` / `title_exclude` are Python regexes matched against the listing **title**:

- **Case-insensitive**, and matched **anywhere in the title** — `iphone` matches
  "Apple iPhone 13". Anchor with `^` / `$` if you need the whole title.
- `title_include` keeps only matching listings; `title_exclude` drops matching ones.
  If both match, **exclude wins**.
- Blank = filter off. An invalid pattern is rejected when you create the search.

Example (on Subito, where the price filter is Subitoo's) — Synology NAS between €75 and
€150, only the models you want, minus the cut-down one:

```
  price_min             75
  price_max             150
  title_include (regex) DS ?124|DS ?220|DS ?223|DS ?218
  title_exclude (regex) DS218j
```

`|` is "or", `DS ?124` allows an optional space ("DS124" and "DS 124" both match). Note
that `DS ?218` also matches "DS218j" — the exclude is what filters it back out.

**Tip:** test a pattern on [regex101.com](https://regex101.com/) (flavor: Python, flag
`i`) before saving the search.

## CLI

Run via the wrapper (`./subitoo <cmd>`):

```
query add [--from-json f]    # create a search (wizard or JSON)
query edit <id>              # change filters, cron, name or pacing (not the URL)
query list | show <id>       # inspect
query enable|disable <id>    # pause switch
query retry <id>             # clear an 'error' latch -> pending
query rm <id>                # delete
query test <id>              # dry run: fetch + filter, no notify/persist
run <id> [--dry-run]         # run one search now
runs <id> | listings <id>    # history / seen items
sites | notifiers            # list installed plugins
doctor                       # health check (DB, plugins, browser, channel)
notify test                  # send a test notification
```

`subitoo --help` documents everything.

## Notifications

**Pushover** and **ntfy** ship in-box. One channel is active at a time — pick it with
`DEFAULT_CHANNEL` in `.env` and verify with `./subitoo notify test`. Adding another is a
single file (see [`CONTRIBUTING.md`](./CONTRIBUTING.md)).

Either way an alert carries the listing's image, and tapping it opens the listing.

### Pushover

Grab your **USER_KEY** from the [Pushover homepage](https://pushover.net) and an
**APPLICATION_TOKEN** from a [new Pushover app](https://pushover.net/apps/build), then put
them in `.env`:

```ini
DEFAULT_CHANNEL=pushover
PUSHOVER_USER=your_user_key
PUSHOVER_TOKEN=your_app_token
```

### ntfy

Install [ntfy](https://ntfy.sh) on your phone, subscribe to a topic, and name that topic
in `.env`. No account needed:

```ini
DEFAULT_CHANNEL=ntfy
NTFY_TOPIC=subitoo-3f9c1ab74e2d
```

⚠️ **The topic name is the password.** On the public ntfy.sh server anyone who knows it
can read your alerts and post to them, so use a long random one — `NTFY_TOPIC=nas` is
effectively public. Self-hosting instead? Set `NTFY_URL` to your server, and `NTFY_TOKEN`
if the topic is access-controlled.

## FAQ

**Why no notifications on the first run?** By design — the first run just records the
current matches as a baseline. You get alerts from the next run onward, when something is
new or changes price.

**Will overlapping searches double-notify me?** New-listing events are now persisted
globally by marketplace and listing ID, including across different schedules and normal
restarts. The first successfully scanning seeded group sends; later matches are silent.
Price-change opt-in keeps upstream per-query semantics.

**What if the scheduler fires while a previous run is still going?** It's skipped — a
built-in lock means a search never overlaps itself.

**Where is my data?** In `./data` (SQLite DB). Back it up to avoid losing your history.

## Contributing & design

- **Add a site or notification channel:** [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **Architecture, decisions, and the browser/Playwright version pin:** [`SPEC.md`](./SPEC.md).
- **Cutting a release (maintainer):** push a semver tag (`git tag v2.0.0 && git push origin
  v2.0.0`); CI builds and publishes the images. Details in [`SPEC.md`](./SPEC.md) §12; the
  workflow needs the `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` repo secrets.

## Licenza e Riconoscimenti

Questo progetto estende [subitoo](https://github.com/Kianda/subitoo) di Kianda ed è distribuito sotto licenza **GNU General Public License v3.0** (GPLv3) — consulta il file [`LICENSE`](./LICENSE) per i termini completi.
