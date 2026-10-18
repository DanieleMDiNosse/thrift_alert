# Validazione GUI locale

## Baseline e perimetro

- Branch esistente `feature/unified-marketplace-watch`, working tree inizialmente pulito.
- Baseline prima delle modifiche: 212 test passati, 3 smoke live esclusi.
- Nessun nuovo framework, pacchetto runtime, servizio cloud o scheduler.
- La GUI usa core.watches e lo stesso SQLite; nessuna modifica alle regole di dedup.
- Watch dimostrativo reale rimasto disabilitato; nessuna ricerca personale arbitraria.

## Test e verifiche osservati

- RED iniziale: nuovo modulo GUI e comando assenti (test HTTP/CLI falliti).
- GREEN API: CRUD con database reale temporaneo, validazione campi/URL, ID assenti,
  JSON malformato/payload limitato, bind loopback, Host/Origin/CSRF, asset allowlist,
  nessun raw payload/topic nelle risposte; ripristino solo per query in errore.
- Test notifiche con sender sostituito: azione esplicita protetta e limitata a un
  tentativo ogni 30 secondi; nessun invio reale dai test GUI.
- RED browser: dopo Attiva i pulsanti restavano disabilitati fino al polling successivo.
  Corretto il ripristino delle azioni dopo le mutazioni; regressione Firefox verde.
- Browser reale: aggiungi→modifica→attiva→pausa→elimina; verifica mobile 390 px senza
  overflow orizzontale; stringhe HTML salvate trattate come testo, non eseguite.
- Lista compatta: 8 annunci iniziali e Mostra tutti/Mostra meno; messaggio italiano
  per modulo senza URL; limite nome coerente con il core (120 caratteri).
- Test browser aggiuntivo: cron personalizzato, max_pages e fonti disabilitate non
  editabili preservati; polling con modulo aperto non cancella il testo in modifica.
- Ultima suite esplicita GUI HTTP+Firefox: **17 passed**, 14.94 s.
- Suite completa finale: **225 passed, 7 skipped**. Smoke marketplace e test browser
  restano opt-in; i test browser sono stati eseguiti esplicitamente nel comando sopra.
- Wheel costruito con uv: contiene gui.py, index.html, app.js e style.css.

## Revisione

- Revisione backend indipendente: protezioni e riuso del core verificati; trovato
  un difetto di lettura durante eliminazione concorrente. Riprodotto in RED con
  seconda connessione che elimina tra lettura gruppi e query: RemoteDisconnected.
- Correzione: transazione di sola lettura WAL per ogni stato, commit prima di inviare
  la risposta. Regressione verde; test retry pending/running/completed senza mutazioni.
- Revisione integrata finale indipendente APPROVED: specifiche PASS, qualità/sicurezza
  PASS, correzione snapshot confermata. Nessun difetto importante residuo nel perimetro
  esaminato. Prova aggiuntiva del revisore: toggle fallito ripristina i controlli e il
  successivo tentativo riesce senza errori pagina.

## Operatività e limiti

- Server avviato su http://127.0.0.1:8787, GET stato verificato HTTP 200;
  un watch disabilitato, 40 annunci osservati, dispatcher già esistente rilevato attivo.
- Anteprime desktop/editor salvate in data/ (ignorato da Git); controllo visivo senza
  sovrapposizioni della pagina principale. Metadati aumentati per leggibilità e lista
  ridotta per evitare una pagina inutilmente lunga.
- Nessuna immagine remota caricata automaticamente, nessun CDN/font esterno.
- hermes verify --json resta BLOCCATO prima del build: Docker non installato.
  Questo non è un esito del percorso locale e non viene riportato come gate passato.
- Firefox desktop/mobile sono le verifiche browser effettuate; altri browser non
  certificati qui. GUI personale locale, non pannello remoto/multiutente.
