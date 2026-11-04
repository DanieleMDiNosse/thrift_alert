# “Recensisci” — brevissima recensione del prodotto

## Utilizzo

Accanto a ciascun annuncio il pulsante **Recensisci** sostituisce **Ne vale la pena?**.
Il clic apre un pannello inline e, se manca una recensione salvata, accoda una sola
richiesta per quell'annuncio. La recensione è in italiano, in 2–3 frasi, con massimo
60 parole e 600 caratteri: punto di forza, limite principale e uso consigliato.

Il modello è fissato a **gpt-6.1-sol**, reasoning **low**, per il task `review`,
indipendentemente dalle impostazioni di modello/reasoning degli altri task AI.
Richiede il provider Hermes autenticato (`AI_PROVIDER=hermes`) oppure OpenAI con credenziali (`AI_PROVIDER=openai`).
Se si utilizza Hermes, le credenziali vengono risolte localmente dal file `auth.json` del profilo configurato in `AI_HERMES_HOME`: nessuna API key manuale necessaria. Per dettagli su configurazione e portabilità tra macchine vedi [GPT_RUNTIME.md](GPT_RUNTIME.md#configurazione-su-un-altro-pc-o-con-un-altro-utente-hermes).

Si tratta di una sintesi AI del prodotto, non di una prova personale o di una
ricerca web verificata. Questa distinzione è visibile nel pannello. GPT riceve titolo
e descrizione disponibile; non riceve il prezzo per fare confronti economici.
Se modello o variante non sono chiari, deve dichiararlo senza indovinarli.
Niente punteggi, classifica, prezzo del nuovo o consiglio di convenienza.

Il risultato resta nel database. Riaprire il pannello o ricaricare la pagina non
richiede automaticamente un'altra inferenza. **Aggiorna recensione** richiede una
nuova generazione. Gli snapshot cambiati o la cache scaduta sono segnalati come
non aggiornati; una generazione fallita non cancella il risultato precedente.

## Architettura e compatibilità

- `web/worth.js` e `worth.css` mantengono il pannello inline, errori e polling limitato.
- `POST /api/ai/review` accetta `{site, id, refresh?}`, con i controlli Host/Origin/CSRF
  della GUI. Titolo e descrizione sono recuperati dal backend, non dal browser.
- `GET /api/ai/review?site=...&id=...` legge soltanto, senza accodare lavoro.
- `ai/review.py` valida la brevità e prepara il contesto; nessuna ricerca web o
  inferenza separata di identificazione viene eseguita. Una generazione usa una
  singola chiamata al modello, salvo i normali tentativi successivi in caso di errore.
- La migrazione additiva `0012_listing_reviews.sql` crea `ai_listing_reviews`.
  `ai_listing_worth` e i giudizi economici storici non vengono modificati o riutilizzati
  come recensioni. Gli endpoint `/api/ai/worth` restano compatibili per uso legacy.
- Si riusano snapshot immutabili, fingerprint, token di aggiornamento e coda durevole
  esistenti. I job `worth` con `purpose=review` eseguono il nuovo task di inferenza.
- Il modello non ha strumenti autonomi. Il testo viene renderizzato come testo, non HTML.

`AI_WORKFLOW=on_demand` mantiene il funzionamento manuale. `AI_ENABLED=false`
continua a sospendere il vecchio flusso autonomo, non i clic espliciti. Il monitor
marketplace resta indipendente dal modello. La validità della cache usa
`AI_WORTH_TTL_HOURS` (default 24 ore), condivisa con il flusso precedente.

## Avvio e verifica

Dalla directory del progetto:

```sh
./start-local.sh
# oppure, per processi già attivi:
./start-local.sh restart
```

Interfaccia: http://127.0.0.1:8787

Verifica ripetibile:

```sh
hermes verify --json
```

Verifica eseguita: compilazione riuscita, 553 test superati e 3 saltati, GUI isolata
pronta con HTTP 200. Coperti clic esplicito, integrazione HTTP/browser con worker,
cache separata, deduplicazione, limiti di lunghezza, modello/reasoning fissati,
aggiornamento del risultato, rendering sicuro e assenza di lavoro automatico.

Prova reale aggiuntiva, separata dai test con inferenza simulata: su un annuncio
osservato di Sony SS-SCD1, clic nel renderer reale e inferenza Hermes/GPT-6.1-sol low
hanno prodotto una recensione di 44 parole e 292 caratteri, salvata e riletta dal
browser senza errori JavaScript. La prova ha usato un database isolato e non ha
modificato annunci, ricerche, coda o risultati del database operativo.
