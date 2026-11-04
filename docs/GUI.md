# Interfaccia grafica locale

## Brevissima recensione GPT del prodotto

Premi **Recensisci** accanto all'annuncio. Il pannello mostra una recensione del
prodotto in massimo 60 parole, generata da GPT-6.1-sol low, senza ricerca web o confronto prezzi.
Il risultato resta salvato; **Aggiorna recensione** richiede una nuova generazione. Non
serve avviare classifiche o analizzare tutti gli annunci: il lavoro parte solo su
richiesta. Dettagli e limiti in `WORTH_ASSISTANT.md`.

## Annunci di tutti i marketplace

La panoramica alterna i mercatini presenti, ordinando gli annunci per ultimo
incontro all'interno di ciascun sito. Mostra i conteggi completi dell'archivio,
non solo quelli della pagina corrente. Usa "Ricerca annunci" per scegliere una
ricerca salvata (anche in pausa), oppure "Tutte le ricerche". Puoi combinarla con
"Marketplace annunci" per selezionare
Subito, Vinted o Wallapop e "Pagina precedente/successiva" per accedere a tutti i
risultati (40 per pagina). "Espandi pagina" passa dagli otto elementi compatti
all'intera pagina: non significa che l'archivio finisca lì.

Quando cambi ricerca, la lista torna alla prima pagina mantenendo il marketplace.
Il selettore "Ordina annunci" offre "Panoramica multisito", "Prezzo crescente"
e "Prezzo decrescente". L'ordine per prezzo vale su tutti i risultati filtrati,
prima della paginazione, non solo sugli annunci già visualizzati. I prezzi assenti
restano in fondo in entrambe le direzioni. Si usano prezzi richiesti, senza sommare
spedizione o commissioni; valute diverse sono raggruppate e non convertite.
Cambiare ordine riporta alla prima pagina e mantiene ricerca e marketplace.
Conteggi e pagine si riferiscono alla ricerca selezionata; "Recensisci"
continua a funzionare sugli annunci filtrati. Una ricerca eliminata viene rimossa
dal selettore al successivo aggiornamento e la vista torna a tutte le ricerche.

L'API GET `/api/state` accetta `watch_id`, `listing_site`, `listing_offset` e
`listing_order` (`balanced_recency`, `price_asc`, `price_desc`) e restituisce
`listing_page` con totale, limite, offset, presenza di altre pagine e conteggi per
sito. La selezione della ricerca precede deduplicazione e limite: un annuncio
presente in più ricerche resta raggiungibile in ciascuna. La lettura usa uno snapshot SQLite.
La panoramica è bilanciata, non una classifica cronologica globale. Per la diagnosi
dei campi AI e dei limiti di raccolta vedi `AI_CATALOG_VALIDATION.md`.

Il filtro ricerca è coperto da test HTTP/browser su ricerche sovrapposte, più
pagine, combinazione con marketplace e rimozione della ricerca selezionata.
Verifica completa dopo l'integrazione: 538 test passati, 3 saltati; compilazione,
GUI isolata HTTP 200, Ruff e controlli whitespace superati. Nessuna migrazione DB
o modifica alle scansioni è necessaria per questo filtro.

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

### Parole obbligatorie nella descrizione

In «Nuova ricerca» o «Modifica», apri «Opzioni avanzate» e compila
«Parole obbligatorie nella descrizione»: una parola o frase letterale per riga.
Devono comparire **tutte nella descrizione**, anche su righe diverse, senza
distinzione tra maiuscole e minuscole. Sono sottostringhe letterali, non regex né
ricerca semantica: «garanzia» corrisponde anche a «senza garanzia».
Il titolo non soddisfa questo filtro; descrizioni assenti o vuote non corrispondono.
Lascia il campo vuoto per mantenere il comportamento precedente.

Subito e Wallapop usano la descrizione ricevuta nei risultati. Su Vinted, soltanto
con questo filtro attivo, il monitor recupera il dettaglio degli annunci che
superano prima titolo e prezzo, con richieste distanziate e cache persistente
condivisa con «Carica descrizione». La cache conserva la prima copia osservata,
non garantisce il testo aggiornato; una prima scansione può richiedere più tempo.
Il monitor legge al massimo cinque nuove descrizioni per tentativo, distanziate
di dieci secondi, e riprende dopo cinque minuti conservando catalogo e cache.
Una baseline viene stabilita solo dopo aver verificato tutti i candidati del
catalogo osservato. HTTP 429 impone una pausa condivisa da tutto il mercatino
di almeno un'ora, rispettando eventuali attese maggiori richieste dal server.
Errori di accesso 401/403 o di parsing sospendono la fonte senza falsa baseline.
Il comando di test legge la cache ma non ne salva di nuova.

Il filtro vale per le scansioni e le notifiche, non come filtro temporaneo della
lista già archiviata. Cambiarlo ricrea una baseline silenziosa delle fonti
modificate, come gli altri cambiamenti ai criteri; non invia notifiche in massa.
È salvato come `description_include` (lista di stringhe nel JSON della ricerca).

### Creazione e gestione

1. Premi «Nuova ricerca».
2. Scrivi cosa vuoi cercare, ad esempio «Sony a6400». Puoi inserire fino a cinque
   frasi alternative, una per riga: ad esempio «casse attive», «cassa attiva» e
   «diffusori attivi». Basta una delle frasi; dentro ciascuna devono comparire tutte
   le parole nel titolo. Le parole aggiuntive e quelle escluse valgono per tutte le alternative.
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

Le ricerche semplici ora sfogliano più pagine: budget massimo per scansione e
mercatino di 30 pagine Subito, 10 Vinted e 25 Wallapop, diviso fra le frasi.
Per esempio, tre frasi ricevono rispettivamente 10, 3 e 8 pagine ciascuna; il
resto non viene redistribuito. Le richieste sono distanziate, non parallele.
La pagina mostra «Paginazione completata» quando termina la paginazione esposta,
oppure «Scansione parziale» quando si raggiunge un limite. Non è una garanzia
che il sito esponga tutti gli annunci: ranking, area geografica e limiti propri
rimangono. Lo stato di copertura riguarda l'ultima scansione riuscita.
Il primo ampliamento di una ricerca esistente crea una baseline silenziosa:
non notifica in massa gli annunci già presenti e aggiorna i prezzi osservati.
Le ricerche manuali con URL mantengono il loro numero di pagine configurato.

Gli «Annunci osservati» includono la baseline e non significano necessariamente
«appena pubblicati» o «già notificati». La pagina legge SQLite e si aggiorna senza
interrogare le ricerche dei marketplace. I link aprono gli annunci nel sito originale.
Le foto appaiono accanto al testo e vengono caricate dal browser dai CDN dei mercatini
quando entrano nella pagina. Un clic sulla foto la apre più grande in una nuova scheda.
Foto assenti, non valide o non raggiungibili mostrano un segnaposto; nessun proxy
immagini sul server locale. Il CDN riceve la richiesta del browser (e quindi il suo
indirizzo IP), ma non il riferimento alla pagina locale: viene usato no-referrer.

Il tema scuro è predefinito e comprende schede, annunci, modulo, campi e messaggi.

Sotto il titolo appare la descrizione, quando disponibile, limitata a tre righe.
«Mostra tutto» espande il testo completo e «Mostra meno» lo richiude. I testi brevi
non mostrano pulsanti inutili. Subito e Wallapop usano il testo già ricevuto dalla
ricerca. Vinted non lo include nel catalogo: «Carica descrizione» apre una richiesta
al dettaglio soltanto quando premi il pulsante, poi conserva il risultato in cache (oppure durante le
scansioni con parole obbligatorie nella descrizione, come spiegato sopra).
La cache Vinted è una copia del testo al momento del primo caricamento, non una
verifica aggiornata a ogni visita. Annunci rimossi o blocchi del sito possono impedirne
il recupero; in quel caso puoi aprire il link originale. Nessuna richiesta aggiuntiva
per descrizioni viene eseguita durante il polling automatico della pagina.

DNS, connessioni interrotte, timeout e indisponibilità temporanee vengono ritentati
automaticamente dopo 5, 10, 20 minuti e così via, fino a un'attesa massima di 6 ore.
Il dashboard distingue questi casi da un accesso negato e mostra la ripresa prevista.
Un 429 è «Limite richieste», non «Accesso negato»: la pausa vale per tutte le
ricerche del mercatino, anche per il caricamento manuale delle descrizioni.
Il pulsante di ripristino non può aggirarla. Dopo 401/403, CAPTCHA o risposte non
valide resta invece necessaria una verifica manuale; non si aggirano blocchi.
Storico, baseline e identità degli annunci vengono conservati durante i recuperi.
«Limite pagine raggiunto» resta un'indicazione di copertura parziale, non un errore
di rete. Facebook resta non supportato.

Il test notifiche è un'azione esplicita. Il suo successo indica pubblicazione al
servizio configurato, non prova della ricezione fisica sul telefono. Il topic ntfy
e gli eventuali token restano in `.env`, mai visualizzati dalla GUI. L'iscrizione
Android iniziale va fatta localmente come descritto in OPERATIONS.md.

## Sicurezza e limiti

- Server limitato a 127.0.0.1, non esposto alla LAN o a Internet.
- Host/Origin e token anti-CSRF sulle modifiche; nessuno script, font o CSS esterno.
- Immagini HTTPS limitate ai CDN dei marketplace; credenziali URL e host arbitrari
  rifiutati. I contenuti testuali continuano a essere mostrati come testo, non HTML.
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
