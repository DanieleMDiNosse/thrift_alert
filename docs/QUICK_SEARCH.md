# Ricerca semplice: prodotto + fascia di prezzo

## Correzione dell'esperienza richiesta

Il modulo principale non richiede più URL, né un nome separato: basta «Cosa vuoi
cercare?» e minimo/massimo EUR facoltativi. Le ricerche native vengono costruite
internamente. Mercatini, frequenza, esclusioni e URL personalizzati sono avanzati.
Le vecchie ricerche con URL restano personalizzate per non riscriverne i filtri.

## Implementazione

- `core/searches.py`: input validato, query Unicode codificata con urlencode,
  sorgenti Subito/Vinted/Wallapop, prezzi anche nei parametri nativi quando supportati.
- `Watch.search_text` facoltativo nel JSON esistente: nessuna migrazione SQLite.
- `/api/watches/quick-save` riusa le protezioni e il salvataggio del core.
- Tutte le parole del prodotto sono incluse nel filtro comune del titolo;
  minimo/massimo restano applicati dal core e non solo dai siti.
- Wallapop richiede coordinate: punto di riferimento 42.5,12.5 nell'Italia centrale,
  senza filtro di raggio. Non è geolocalizzazione dell'utente, né garanzia che gli
  annunci siano italiani o spedibili in Italia. Limite dichiarato nelle opzioni e guida.
- Checkbox dei marketplace preservate in modifica; una sorgente assente non viene
  abilitata silenziosamente. Impostazioni personalizzate pregresse mantenute.

## Evidenza

- RED: 10 nuovi test API/builder fallivano (modulo/route assenti), poi GREEN.
- RED browser: campo prodotto assente; ora prodotto+prezzi bastano per creare tre
  query senza vedere o compilare URL. Modifica prodotto aggiorna anche i filtri.
- RED regressione modifica: mercatini non scelti apparivano selezionati; corretto.
- RED testo visibile: nota tecnica mostrava ancora «URL»; ora la vista semplice
  contiene solo spiegazioni d'uso, dettagli tecnici nelle opzioni avanzate.
- Suite dedicata completa (HTTP/builder/Firefox, incluse regressioni GUI):
  **29 passed**, 20.19 s.
- Suite completa: **235 passed, 9 skipped**, 7.18 s. Rete/browser opt-in.
- Live, sola lettura, «Sony a6400», EUR100–550, URL generati dal builder:
  Subito 30 recuperati / 1 match; Vinted 92 / 3 match; Wallapop 2 / 0 match.
  Zero match filtrati è un risultato valido, non una certificazione di copertura.
- Verificato nel browser del sito che l'URL Subito generato usa il sort nativo
  `datedesc`; Vinted newest_first e Wallapop newest impostati esplicitamente.
- Nessun watch reale creato/attivato o notifica inviata durante queste prove.
- GUI riavviata e GET stato HTTP200; anteprima del modulo semplice salvata in
  data/gui-simple-search.png (privata, ignorata da Git).
- Revisione indipendente APPROVED: specifiche e qualità conformi, nessun difetto
  importante individuato nel perimetro esaminato. Il revisore ha verificato 16 test
  mirati/browser e 23 test GUI/core; nessuna attivazione o invio esterno.
