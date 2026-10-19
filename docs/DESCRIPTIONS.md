# Descrizioni espandibili — evidenze

## Perimetro

Anteprima di tre righe sotto il titolo, «Mostra tutto» / «Mostra meno» solo se il
testo eccede l'anteprima. Testo letterale tramite textContent, niente HTML remoto,
immagini o script. Lo stato espanso sopravvive al polling finché la pagina resta aperta.

Subito e Wallapop: campo body/description già osservato nelle risposte reali di
ricerca, nessuna richiesta in più. Vinted: catalogo senza descrizione; caricamento
esplicito al clic, cache persistente condivisa per (site,id). Nessun fan-out automatico.
Cache al primo recupero, non promessa di aggiornamento continuo del testo del venditore.

## Validazione

- Baseline working tree pulito sul commit della ricerca semplice.
- RED: 8 test fallivano per API/campi/parser mancanti. GREEN dopo implementazione.
- RED Firefox: anteprima e pulsante di caricamento assenti. GREEN: 2 browser journey.
- Schema5: piccola tabella cache descrizioni, migrazioni atomiche esistenti riutilizzate.
  Test upgrade ora confrontano l'ultima migrazione disponibile, non v4 hardcoded.
- HTTP: cache dopo recupero, nessuna richiesta da GET stato, CSRF richiesto, ID solo
  su annunci già noti nel DB, nessun URL fornito dal client, errori senza cache vuota.
- Parser Vinted cerca il plugin con item_id esatto, non raccomandazioni o meta SEO.
- URL dettaglio: HTTPS, dominio Vinted validato, stesso itemID, senza credenziali.
- Live: Vinted ha risposto307 per un redirect stesso host e stesso articolo con query
  aggiunta. RED nuovo test; implementato un solo redirect verificato, mai verso altri
  host, articoli o login. Nessun retry su403/429 o bypass. Limiti25s per richiesta.
- Recupero live Vinted riuscito: 39 caratteri, cache riletta identica; contenuto non
  stampato. Precedente pagina live verificata con plugin descrizione796 caratteri.
- Subito rawbody177 caratteri; Wallapop rawdescription138 caratteri osservati live.
- Suite descrizioni esplicita HTTP/parser/Firefox: **14 passed**, 6.09 s.
- Suite completa: **247 passed, 11 skipped**, 8.79 s. Browser/live restano opt-in.
- GUI riavviata solo su127.0.0.1:8787; stato reale HTTP200, 5 annunci osservati,
  uno con descrizione in cache visibile nel browser. Nessun watch attivato o push.
- Revisione indipendente APPROVED: flusso raw→stato→DOM, caricamento esplicito,
  cache e protezioni verificati; 25 test passati nella revisione. La successiva
  estensione del singolo redirect Vinted sullo stesso host/articolo è coperta da
  test aggiuntivi positivi e negativi e dalla prova live descritta sopra.

## Limiti

Il catalogo potrebbe troncare una descrizione nativa senza esporre tale informazione;
si mostra tutto il testo effettivamente ricevuto, non si inventano parti mancanti.
Dettagli rimossi, cambiamenti del formato o restrizioni sono errori osservabili, non
una descrizione vuota riuscita. Payload Vinted controllato dopo ricezione (4 MiB),
non un download streaming limitato; ambiente personale locale, non endpoint remoto.
