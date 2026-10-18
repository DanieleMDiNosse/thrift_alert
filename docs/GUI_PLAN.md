# Interfaccia locale — piano e criteri

- [x] Baseline: 212 test passati, 3 smoke live esclusi; working tree pulito.
- [x] API HTTP loopback senza nuove dipendenze; riuso core.watches/SQLite.
- [x] Pagina italiana responsive: ricerche, nuovo/modifica, attiva/pausa/elimina,
      stato delle sorgenti, ripristino dopo errore, ultimi annunci osservati.
- [x] Modulo con nome, prezzi, parole incluse/escluse, URL distinti e intervalli
      semplici; impostazioni avanzate non necessarie restano nascoste.
- [x] Protezioni: bind solo127.0.0.1, Host/Origin, token CSRF, payload limitato,
      escaping DOM, nessun segreto o raw payload nelle risposte/log.
- [x] Test RED→GREEN HTTP/CRUD/validazione/privacy e browser end-to-end con DB isolato.
- [x] Revisione specifiche e regressioni; documentazione e avvio GUI locale verificato.

Non cambiare il dispatcher né attivare il watch dimostrativo. La GUI gestisce lo
stesso database, non crea un secondo scheduler; nessun framework, cloud o LLM runtime.
