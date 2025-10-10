# GasCast-BBS

Un **BBS APRS in Python** con supporto **APRS-IS**, pensato per offrire una semplice cassetta postale (Bulletin Board System) per radioamatori su rete APRS e backbone internet.

> **Stato del progetto:** prototipo attivo / in evoluzione. Feedback e PR sono benvenuti!

---

## ✨ Caratteristiche

- Ricezione e pubblicazione di messaggi APRS tramite **APRS-IS**.
- Bacheca messaggi (BBS) locale con **persistenza su database**.
- Architettura minimale in **Python**, facile da leggere ed estendere.
- Licenza **GPL-3.0** (software libero).

> Nota: la funzionalità RF diretta (TNC/RTX) non è attualmente prevista in questo repository; l’accesso avviene via backbone APRS-IS.

---

## 🧭 Struttura del repository

- `aprs_bbs.py` – entrypoint dell’applicazione BBS e logica APRS-IS.
- `aprs_bbs_db.py` – accesso ai dati e funzioni di persistenza (es. SQLite).
- `aprs_bbs_old.py` – versione precedente / codice storico.
- `LICENSE` – termini della licenza (GPL-3.0).

> I nomi dei file sono indicativi della responsabilità del modulo; controlla il sorgente per i dettagli implementativi.

---

## 🚀 Requisiti

- **Python** 3.10+ (consigliato 3.11 o superiore)
- Moduli Python:
  - `aprslib` (per interfacciarsi con APRS-IS)
  - eventuali moduli standard (`sqlite3`, `logging`, ecc.)

Installa le dipendenze con:

```bash
pip install -U aprslib
```

> Se il progetto introduce un `requirements.txt` o `pyproject.toml`, preferisci quelli.

---

## 🔧 Configurazione

Configura le credenziali e i parametri di connessione APRS-IS (callsign, passcode, server) tramite **variabili d’ambiente** o un file `.env` (se usi `python-dotenv`). Esempio con variabili d’ambiente:

```bash
export APRS_CALLSIGN="N0CALL-10"   # il tuo nominativo-SSID
export APRS_PASSCODE="12345"       # passcode APRS-IS per il tuo nominativo
export APRS_SERVER="rotate.aprs2.net"
export APRS_PORT="14580"
```

> Suggerimento: `rotate.aprs2.net:14580` fornisce un bilanciatore per i server APRS-IS. Imposta filtri server (es. area/portatori) secondo le tue esigenze.

---

## ▶️ Avvio

Con la configurazione pronta:

```bash
python aprs_bbs.py
```

> Alcuni ambienti richiedono `python3` invece di `python`.

Per usare un file `.env`:

```bash
python -m pip install python-dotenv
cp .env.example .env   # se presente nel repo
# poi avvia normalmente
python aprs_bbs.py
```

---

## 📨 Utilizzo (idee d’interazione)

- Inserisci/ricevi **messaggi BBS** (bulletins / private messages) via APRS-IS.
- Definisci **comandi semplici** (es. HELP, LIST, READ, SEND) gestiti dal BBS.
- Regola i **filtri APRS-IS** (ad es. area/radius) per ridurre il traffico in ingresso.
- Personalizza il **prefisso** o il **formato** dei messaggi BBS per distinguerli nel feed APRS.

> Verifica nel codice i comandi già implementati e il formato delle UI string (APRS).

---

## 💾 Persistenza dati

Il modulo `aprs_bbs_db.py` fornisce funzioni per persistere i messaggi (tipicamente **SQLite** in locale). Il file del DB può essere versionato/ignorato a seconda della tua policy. Per rigenerarlo, elimina il file e riavvia l’applicazione (se lo schema è creato a runtime).

---

## 🧪 Sviluppo

Clona il repo ed esegui l’app in locale con un server APRS-IS di test/rotazione. Consigli:
- attiva **logging** a livello `INFO/DEBUG` per ispezionare il traffico;
- proteggi il passcode tramite variabili d’ambiente o un **.env** escluso da git;
- aggiungi test su funzioni di parsing/serializzazione frame APRS.

Esempio di esecuzione con logging verboso:

```bash
export LOG_LEVEL=DEBUG
python aprs_bbs.py
```

---

## 🛣️ Roadmap (proposte)

- [ ] Comandi BBS documentati e help integrato
- [ ] Gestione **messaggi privati** con ACL basilari
- [ ] Filtri APRS-IS configurabili da CLI o file YAML
- [ ] Esportazione/backup messaggi (JSON/CSV)
- [ ] Contenitore **Docker** (immagine leggera + healthcheck)
- [ ] Integrazione RF (TNC KISS) opzionale

Contribuzioni e idee sono benvenute!

---

## 🤝 Contribuire

1. Forka il progetto e crea un branch feature:
   ```bash
   git checkout -b feature/nome-feature
   ```
2. Fai commit chiari, aggiungi test se possibile.
3. Apri una **Pull Request** descrivendo obiettivi e cambi.

Per bug e richieste, usa le **Issues**.

---

## 📝 Licenza

Distribuito con licenza **GPL-3.0**. Vedi il file `LICENSE` per i dettagli.

---

## 📬 Contatti

- Autore: @vash
- Repo: https://github.com/vash909/GasCast-BBS

Se usi GasCast-BBS on‑air, fammi sapere come va e cosa vorresti migliorare. 73!

