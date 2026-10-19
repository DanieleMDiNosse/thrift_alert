# Immagini e tema scuro — validazione

- Baseline branch pulito; nessuna modifica al monitor, ai watch o alle notifiche.
- Miniature da image_url già presente in SQLite, caricamento lazy nel browser,
  dimensioni riservate, object-fit contain, apertura foto più grande in scheda separata.
- Mancanza/errore immagine: segnaposto, senza elementi rotti o loop di retry JS.
- HTTPS e allowlist di suffissi CDN, niente credenziali o porte arbitrarie; niente
  proxy server-side. CSP ammette solo domini immagini noti, script/CSS restano locali.
- referrerpolicy=no-referrer; il browser contatta comunque il CDN (IP visibile al CDN).
- Tema scuro predefinito: pagina, schede, modale, input, selezioni, messaggi e footer.

## Test e revisioni locali

- RED: 12 test stato immagine fallivano, campo assente; browser foto/tema assenti.
- GREEN: URL immagini validati, payload e DOM/CSP aggiornati, palette/layout scuri.
- Una fixture PNG iniziale aveva CRC errato (Firefox Image corrupt/truncated):
  sostituita con PNG valido costruito con struct/zlib, nessun workaround in produzione.
- Suite completa: **259 passed, 12 skipped**, 15.20 s; da ripetere al commit.
- Suite browser/mirata: **21 passed**, 30.63 s. Copre foto decodificata, caricamento
  differito, no-referrer, fallback errore, tema di pagina/modale/input, mobile e
  regressioni CRUD/ricerca semplice/descrizione espandibile.
- Revisione specifiche: immagini vere dal DB, fallback, tema scuro e layout coerenti.
- Revisione regressioni/sicurezza: diff ispezionato, link noopener/noreferrer,
  allowlist e CSP coerenti, nessun raw payload/segreto esposto, nessun fetch proxy.
- Prova runtime reale: stato HTTP200, 5 URL immagini; prima foto Vinted decodificata
  nel browser a larghezza310px, nessun errore JS. Non sono dati inventati di esempio.
- Controllo visivo screenshot desktop e modale: nessun difetto importante rilevato.
  Anteprime private in data/gui-dark-images.png e data/gui-dark-editor.png.
- Riavviata solo la GUI su127.0.0.1:8787; ricerche e dispatcher invariati.

Docker resta un percorso separato non disponibile sull'host; nessuna installazione
privilegiata o dipendenza aggiuntiva è stata necessaria per questa modifica locale.
