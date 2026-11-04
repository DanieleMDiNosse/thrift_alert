# Thrift Alert

Sistema self-hosted per il monitoraggio e la notifica di nuovi annunci su **Subito.it, Vinted e Wallapop**, dotato di dashboard web locale in italiano e assistenza AI per recensioni tecniche del prodotto su richiesta.

> **Riconoscimenti & Base del progetto:**  
> Questo progetto nasce ed è sviluppato come evoluzione e fork del software open-source [subitoo](https://github.com/Kianda/subitoo) di **Kianda** (rilasciato sotto licenza GNU GPLv3).  
> Ringraziamo l'autore originale per l'ottima architettura iniziale (adattatori, scheduler, notifiche). Rispetto al progetto originario, **Thrift Alert** introduce:
> - Supporto multisito unificato (**Subito.it**, **Vinted**, **Wallapop**)
> - Ricerche aggregate multisito (**Watch Groups**) con deduplicazione globale durevole
> - Dashboard web locale (**Marketwatch**) a `http://127.0.0.1:8787` con tema scuro e galleria immagini
> - Filtri avanzati sul testo della descrizione e recupero asincrono schede
> - Assistenza AI on-demand per recensioni tecniche oggettive (**"Recensisci"**) con supporto per Hermes Codex OAuth, OpenAI e modelli locali.

---

## Indice

- [Caratteristiche principali](#caratteristiche-principali)
- [Avvio Rapido](#avvio-rapido)
- [Interfaccia Grafica e Gestione Ricerche](#interfaccia-grafica-e-gestione-ricerche)
- [Assistenza AI e Recensioni Prodotto ("Recensisci")](#assistenza-ai-e-recensioni-prodotto-recensisci)
- [Configurazione Notifiche (ntfy / Pushover)](#configurazione-notifiche)
- [Filtri e Sintassi](#filtri-e-sintassi)
- [Interfaccia a riga di comando (CLI)](#interfaccia-a-riga-di-comando-cli)
- [Domande Frequenti (FAQ)](#domande-frequenti-faq)
- [Licenza e Riconoscimenti](#licenza-e-riconoscimenti)

---

## Caratteristiche principali

- **Monitoraggio multisito contemporaneo:** Controlla automaticamente annunci su **Subito.it**, **Vinted** e **Wallapop**.
- **Frequenza personalizzabile:** Pianificazione su misura per ciascuna ricerca salvata (tramite espressioni cron).
- **Notifiche push immediate:** Avvisi su smartphone (Android/iOS) con foto dell'annuncio, prezzo e link diretto.
- **Deduplicazione durevole:** Nessuna doppia notifica se lo stesso articolo compare in ricerche o pianificazioni sovrapposte.
- **Filtri universali:** Fascia di prezzo, spedizione presente/assente, parole obbligatorie o vietate nel titolo (tramite regex) e parole richieste nella descrizione.
- **Dashboard web locale:** Interfaccia grafica completa per visualizzare gli annunci, gestire le ricerche e consultare le recensioni.
- **Recensioni AI on-demand:** Ottieni con un solo clic una scheda tecnica, la stima del nuovo e un riassunto dei pregi/difetti del modello.

---

## Avvio Rapido

### Installazione ed esecuzione locale (Consigliata)

1. **Clona il repository e prepara l'ambiente Python:**
   ```bash
   cd subitoo
   uv venv --python 3.12 app/.venv
   uv pip sync --python app/.venv/bin/python requirements-ai.lock
   uv pip install --python app/.venv/bin/python --no-deps -e ./app
   app/.venv/bin/python -m playwright install firefox
   ```

2. **Configura le variabili d'ambiente:**
   ```bash
   cp .env.example .env
   chmod 600 .env
   ```
   *Modifica il file `.env` per inserire il tuo canale di notifica (es. topic `ntfy`) ed eventualmente le impostazioni AI.*

3. **Avvia il sistema con un unico comando:**
   ```bash
   ./start-local.sh
   ```
   Questo script avvia in background il monitor delle ricerche, l'interfaccia web e il worker AI.

4. **Apri la dashboard nel browser:**
   Vai su **http://127.0.0.1:8787** per visualizzare gli annunci e gestire le ricerche.

Comandi di gestione rapida dello stack:
```bash
./start-local.sh status    # Controlla lo stato dei processi (monitor, GUI, worker)
./start-local.sh stop      # Ferma tutti i servizi
./start-local.sh restart   # Riavvia tutti i servizi ricaricando le configurazioni
```

---

## Interfaccia Grafica e Gestione Ricerche

L'interfaccia grafica (Marketwatch) ti permette di gestire tutto senza dover modificare file JSON:

1. Premi **"＋ Nuova ricerca"**.
2. Inserisci il nome della ricerca e cosa desideri cercare (è possibile inserire più varianti, una per riga).
3. Imposta facoltativamente la fascia di prezzo minima e massima.
4. Nelle **Opzioni avanzate** puoi selezionare quali marketplace includere, impostare parole da includere/escludere nel titolo e parole obbligatorie nella descrizione.
5. Al salvataggio, la ricerca esegue una prima scansione silenziosa per stabilire la base di partenza; riceverai notifiche solo per i nuovi annunci trovati successivamente.

Per chi preferisce la riga di comando, sono disponibili i comandi di `./marketwatch`:
```bash
./marketwatch doctor                                    # Diagnostica salute database, browser e notifiche
./marketwatch watch add --from-json /percorso/file.json  # Aggiunge una ricerca da file JSON
./marketwatch watch list                                # Elenca le ricerche attive
./marketwatch watch test 1                              # Esegue una prova a secco senza inviare notifiche
./marketwatch serve                                     # Avvia il monitor in primo piano
```

---

## Assistenza AI e Recensioni Prodotto ("Recensisci")

Accanto a ciascun annuncio nella dashboard è disponibile il pulsante **"Recensisci"**:
- **Completamente on-demand:** Nessuna chiamata automatica in background. L'AI viene interpellata unicamente quando fai clic sul pulsante, evitando consumi indesiderati.
- **Sintesi tecnica e oggettiva:** Effettua una veloce ricerca web di specifiche tecniche tramite DuckDuckGo (`ddgs`) ed elabora tramite **GPT-6.1-sol** (reasoning `low`) una recensione concisa (2–3 frasi, max 60 parole) con punto di forza, limite principale e destinatario ideale.
- **Dati tecnici strutturati:** Riconosce il modello esatto, l'anno di uscita, la stima del prezzo di listino / nuovo e, per le biciclette, cambio, guarnitura e rapporto di trasmissione più agile.
- **Cache su database:** Le recensioni vengono salvate nel database locale SQLite e rimangono valide per 24 ore (`AI_WORTH_TTL_HOURS=24`).
- Documentazione tecnica dettagliata: consultare [docs/WORTH_ASSISTANT.md](docs/WORTH_ASSISTANT.md) e [docs/GPT_RUNTIME.md](docs/GPT_RUNTIME.md).

### Configurazione del Provider AI (nel file `.env`)

L'applicazione supporta tre modalità di inferenza:

#### 1. Provider Hermes Codex OAuth (Consigliato se usi Hermes CLI)
Non richiede alcuna chiave API a pagamento né costi diretti: riutilizza l'accesso OAuth Codex già autenticato su Hermes:
```ini
AI_PROVIDER=hermes
AI_MODEL=gpt-6.1-sol
AI_REASONING_EFFORT=low

# Percorso assoluto alla cartella del profilo Hermes dove si trova auth.json
# Es. profilo principale: /home/<utente>/.hermes
# Es. profilo specifico:  /home/<utente>/.hermes/profiles/<nome_profilo>
AI_HERMES_HOME=/home/<utente>/.hermes/profiles/<nome_profilo>

# Percorso assoluto all'interprete Python dell'installazione Hermes
AI_HERMES_PYTHON=/home/<utente>/.hermes/.../venv/bin/python
```
> **Come trovare i percorsi corretti:**
> - `AI_HERMES_HOME`: verifica dove si trova `auth.json` con `ls -la ~/.hermes/auth.json` o `ls -la ~/.hermes/profiles/*/auth.json`.
> - `AI_HERMES_PYTHON`: dall'ambiente Hermes, esegui `python -c 'import sys; print(sys.executable)'`.

#### 2. Provider OpenAI API Diretto
Se desideri utilizzare una chiave sviluppatore OpenAI tradizionale:
```ini
AI_PROVIDER=openai
AI_ENDPOINT=https://api.openai.com/v1
AI_MODEL=gpt-6.1-sol
AI_REASONING_EFFORT=low
AI_API_KEY=sk-...  # La tua chiave API da https://platform.openai.com
```

#### 3. Modello Locale (llama.cpp / Ollama)
Per eseguire un modello in locale senza connessione a servizi cloud:
```ini
AI_PROVIDER=local
AI_ENDPOINT=http://127.0.0.1:8080/v1
AI_MODEL=underdog-saluki-27b-iq2  # O l'alias del tuo modello locale
```

---

## Configurazione Notifiche

Puoi scegliere di ricevere le notifiche tramite **ntfy** (consigliato per semplicità) o **Pushover**. Quando viene individuato un nuovo annuncio, la notifica include il titolo, il prezzo, il mercatino di provenienza, l'immagine di anteprima e il link diretto all'annuncio.

### Opzione 1: ntfy (Gratuito, nessun account richiesto)
1. Installa l'app **ntfy** sul tuo smartphone ([Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy) o [App Store](https://apps.apple.com/app/ntfy/id1625396347)).
2. Scegli un nome per il tuo topic segreto (genera una stringa lunga e casuale, es. con `openssl rand -hex 16`).
3. Iscriviti a quel topic all'interno dell'app.
4. Configura il file `.env`:
   ```ini
   DEFAULT_CHANNEL=ntfy
   NTFY_TOPIC=il_tuo_topic_segreto_e_casuale
   NTFY_URL=https://ntfy.sh
   ```
   > ⚠️ **Attenzione:** Sul server pubblico `ntfy.sh`, chiunque conosca il nome del topic può leggere gli avvisi. Usa un nome lungo e difficile da indovinare.

### Opzione 2: Pushover
1. Recupera la tua **USER_KEY** dalla home page di [Pushover](https://pushover.net).
2. Crea una nuova applicazione su Pushover per ottenere un **APPLICATION_TOKEN**.
3. Configura il file `.env`:
   ```ini
   DEFAULT_CHANNEL=pushover
   PUSHOVER_USER=la_tua_user_key
   PUSHOVER_TOKEN=il_tuo_token_applicazione
   ```

Verifica il corretto invio con:
```bash
./marketwatch notify test
```

---

## Filtri e Sintassi

### Filtri sul Titolo (Espressioni Regolari - Regex)
I filtri `title_include` e `title_exclude` usano espressioni regolari Python sul titolo dell'annuncio:
- **Non fanno distinzione tra maiuscole e minuscole** (case-insensitive) e verificano la presenza del testo in qualsiasi punto del titolo.
- `title_include`: mantiene solo gli annunci il cui titolo corrisponde al pattern.
- `title_exclude`: scarta gli annunci che corrispondono al pattern.
- Se un titolo soddisfa entrambi i criteri, **l'esclusione ha sempre la precedenza**.

**Esempio pratico:**  
Cercare modelli specifici di NAS Synology escludendo la versione "j" meno potente:
- Includi: `DS ?124|DS ?220|DS ?223|DS ?218`
- Escludi: `DS218j`

*Suggerimento: puoi testare i tuoi pattern regex su [regex101.com](https://regex101.com/) (selezionando Python come motore).*

### Filtri sulla Descrizione
È possibile definire parole o frasi che devono comparire obbligatoriamente all'interno della descrizione dell'annuncio. Su Vinted, la descrizione completa viene recuperata e memorizzata in cache in modo asincrono.

---

## Interfaccia a riga di comando (CLI)

Se utilizzi l'ambiente a riga di comando:

```bash
# Gestione delle ricerche
./marketwatch query add          # Creazione guidata interattiva di una ricerca
./marketwatch query list         # Elenco delle ricerche salvate
./marketwatch query show <id>    # Dettagli di una ricerca
./marketwatch query edit <id>    # Modifica filtri o frequenza cron
./marketwatch query enable <id>  # Abilita una ricerca
./marketwatch query disable <id> # Disabilita/mette in pausa una ricerca
./marketwatch query test <id>    # Prova a secco (mostra i risultati senza inviare notifiche)
./marketwatch query rm <id>      # Elimina una ricerca

# Monitoraggio e diagnostica
./marketwatch doctor             # Verifica configurazione, database e notifiche
./marketwatch listings <id>      # Mostra gli annunci memorizzati per una ricerca
./marketwatch runs <id>          # Storico delle esecuzioni passate
./marketwatch notify test        # Invia una notifica di prova
```

---

## Domande Frequenti (FAQ)

**Perché non ricevo notifiche al primo avvio?**  
Al primo avvio di una nuova ricerca, il sistema memorizza gli annunci esistenti come "stato iniziale" (baseline) in modo silenzioso. Inizierai a ricevere notifiche a partire dalle scansioni successive, solo quando compaiono annunci effettivamente nuovi o quando il prezzo varia (se abilitato).

**Se due ricerche diverse trovano lo stesso annuncio, ricevo due notifiche?**  
No. Il sistema adotta una deduplicazione globale durevole basata sulla coppia `(marketplace, id_annuncio)`. La prima ricerca che rileva l'annuncio invia la notifica; le altre ricerche registrano il riferimento senza generare notifiche duplicate.

**Cosa succede se una scansione richiede più tempo del previsto?**  
Ogni ricerca è protetta da un lock interno nel database: le esecuzioni della stessa ricerca non si sovrappongono mai.

**Dove vengono salvati i dati?**  
Tutti i dati, la cronologia degli annunci e le recensioni AI sono memorizzati nel database locale SQLite in `./data/subitoo.db`. Per eseguire un backup è sufficiente copiare il database quando i processi sono fermi, oppure consultare [docs/OPERATIONS.md](docs/OPERATIONS.md) per i comandi di backup a caldo.

---

## Licenza e Riconoscimenti

Questo software estende il progetto open-source [subitoo](https://github.com/Kianda/subitoo) di Kianda ed è distribuito sotto licenza **GNU General Public License v3.0** (GPLv3).  
Per consultare i termini e le condizioni complete di distribuzione, vedi il file [`LICENSE`](./LICENSE).
