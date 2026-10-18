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

1. Premi «Nuova ricerca» e assegna un nome riconoscibile.
2. Apri ogni marketplace, imposta la ricerca e copia il suo URL; preferisci l'ordine
   dal più recente. Incolla un URL distinto per Subito, Vinted e/o Wallapop.
3. Imposta, se servono, prezzo minimo/massimo in euro e parole incluse/escluse,
   una voce per riga. Tutte le parole incluse sono richieste; basta una esclusa per
   scartare il titolo. Nessuna conversione automatica di valuta.
4. Salva. Usa «Attiva» o «Metti in pausa» per controllare le scansioni. La prima
   scansione riuscita crea una baseline silenziosa, senza inviare tutti gli annunci.
5. «Modifica» riapre lo stesso modulo. Cambiare URL o filtri ricrea una baseline
   silenziosa per le sorgenti modificate; una semplice pausa conserva lo storico.
6. «Elimina» chiede conferma. Cancella il gruppo e le relative query, non il registro
   globale di deduplicazione.

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
