# Interfaccia grafica locale

## Aprire l'app

Dalla cartella del progetto:

```sh
./marketwatch gui
```

Si apre il browser su http://127.0.0.1:8787. Se preferisci aprirlo tu:

```sh
./marketwatch gui --no-open-browser
```

Porta occupata? Usa `./marketwatch gui --port 8788`. Per chiudere la GUI in primo
piano premi Ctrl-C. Il monitor resta un processo separato: chiudere il browser o
la GUI non arresta le scansioni già avviate con `./marketwatch serve`.
La pagina mostra se il dispatcher locale sta effettivamente girando; non avvia un
secondo scheduler. Non sono stati installati nuovi servizi di sistema.

## Uso quotidiano

1. Premi «Nuova ricerca».
2. Scrivi cosa vuoi cercare, ad esempio «Sony a6400».
3. Indica il prezzo minimo e/o massimo in euro, se desideri un limite.
4. Premi «Salva ricerca». L'app crea automaticamente le ricerche su Subito, Vinted
   e Wallapop: non devi aprire i siti o incollare URL.
5. Usa «Attiva» / «Metti in pausa» per controllare le scansioni. La prima scansione
   riuscita crea una baseline silenziosa, senza inviare tutti gli annunci esistenti.
6. «Modifica» riapre prodotto e prezzi. Cambiare prodotto o filtri ricrea una baseline
   silenziosa; una semplice pausa conserva lo storico. «Elimina» chiede conferma.

Le «Opzioni avanzate» sono facoltative: scelta dei mercatini, frequenza, parole da
escludere e, soltanto se serve, URL personalizzati. Le ricerche precedenti create
con URL restano in modalità personalizzata per non alterarne i filtri originali.

La ricerca semplice richiede tutte le parole del prodotto nel titolo, per limitare
risultati vaghi o riempitivi dei siti. I prezzi sono applicati anche dal filtro comune;
non viene fatta conversione di valuta. Wallapop richiede tecnicamente un punto di
riferimento: viene usata l'Italia centrale senza raggio imposto, non la tua posizione.
Possono comparire annunci esteri; non viene garantita la spedizione in Italia.

Gli «Annunci osservati» includono la baseline e non significano necessariamente
«appena pubblicati» o «già notificati». La pagina legge SQLite e si aggiorna senza
interrogare i marketplace. I link aprono gli annunci nel sito originale; non vengono
caricate immagini remote automaticamente.

Le sorgenti in errore restano sospese per evitare richieste ripetute. Prima di
riprovare controlla URL, accesso e restrizioni della piattaforma. Il ripristino
riabilita la normale pianificazione; non aggira CAPTCHA o blocchi e non forza una
scansione immediata. Facebook resta non supportato.

Il test notifiche è un'azione esplicita. Il suo successo indica pubblicazione al
servizio configurato, non prova della ricezione fisica sul telefono. Il topic ntfy
e gli eventuali token restano in `.env`, mai visualizzati dalla GUI. L'iscrizione
Android iniziale va fatta localmente come descritto in OPERATIONS.md.

## Sicurezza e limiti

- Server limitato a 127.0.0.1, non esposto alla LAN o a Internet.
- Host/Origin e token anti-CSRF sulle modifiche; nessun CDN, font esterno o framework.
- Stesso database e stesse regole della CLI; nessun LLM o nuovo servizio cloud.
- Nessuna gestione credenziali o sessioni Facebook nel browser.
- È un'applicazione personale locale, non un pannello multiutente autenticato.
- In caso di scansione in corso o database occupato, attendi e riprova la modifica.

## Verifica

```sh
app/.venv/bin/python -m pytest -q app/tests/test_gui.py
MARKETWATCH_BROWSER=1 app/.venv/bin/python -m pytest -q app/tests/test_gui_browser.py
```

I test browser usano Firefox e database temporanei isolati. Non inviano notifiche
reali e non contattano i marketplace. Per i test di rete dei marketplace restano
validi i comandi separati in OPERATIONS.md.
