# GasCast-BBS

A **Python APRS BBS** with **APRS-IS support**, designed to provide a simple Bulletin Board System (BBS) for radio amateurs over the APRS network and the internet backbone.

> **Project status:** active prototype / under development. Feedback and PRs are welcome!

---

## ✨ Features

- Receive and publish APRS messages via **APRS-IS**.
- Local message board (BBS) with **database persistence**.
- Minimal architecture in **Python**, easy to read and extend.
- **GPL-3.0 License** (free software).

> Note: direct RF functionality (TNC/RTX) is not currently implemented in this repository; access is via the APRS-IS backbone.

---

## 🧭 Repository Structure

- `aprs_bbs.py` – main entrypoint and APRS-IS logic.
- `aprs_bbs_db.py` – data access and persistence functions (e.g., SQLite).
- `aprs_bbs_old.py` – previous version / legacy code.
- `LICENSE` – license terms (GPL-3.0).

> File names are indicative of module responsibilities; check the source for implementation details.

---

## 🚀 Requirements

- **Python** 3.10+ (3.11 or higher recommended)
- Python modules:
  - `aprslib` (for interfacing with APRS-IS)
  - standard modules (`sqlite3`, `logging`, etc.)

Install dependencies with:

```bash
pip install -U aprslib
```

> If the project introduces a `requirements.txt` or `pyproject.toml`, prefer using those.

---

## 🔧 Configuration

Set up your APRS-IS credentials and connection parameters (callsign, passcode, server) using **environment variables** or a `.env` file (if you use `python-dotenv`). Example with environment variables:

```bash
export APRS_CALLSIGN="N0CALL-10"   # your callsign-SSID
export APRS_PASSCODE="12345"       # APRS-IS passcode for your callsign
export APRS_SERVER="rotate.aprs2.net"
export APRS_PORT="14580"
```

> Tip: `rotate.aprs2.net:14580` provides a load-balanced APRS-IS entrypoint. Adjust server filters (e.g., area/portions) as needed.

---

## ▶️ Run

With configuration ready:

```bash
python aprs_bbs.py
```

> Some systems may require `python3` instead of `python`.

To use a `.env` file:

```bash
python -m pip install python-dotenv
cp .env.example .env   # if provided in the repo
# then run normally
python aprs_bbs.py
```

---

## 📨 Usage (interaction ideas)

- Insert/receive **BBS messages** (bulletins / private messages) via APRS-IS.
- Define **simple commands** (e.g., HELP, LIST, READ, SEND) handled by the BBS.
- Adjust **APRS-IS filters** (e.g., area/radius) to reduce incoming traffic.
- Customize **prefix/format** of BBS messages to distinguish them in the APRS feed.

> Check the code for currently implemented commands and APRS UI string formats.

---

## 💾 Data Persistence

The `aprs_bbs_db.py` module provides functions to persist messages (typically **SQLite** locally). The DB file may be versioned/ignored depending on your policy. To regenerate it, delete the file and restart the application (if schema is auto-created).

---

## 🧪 Development

Clone the repo and run locally against a test/rotating APRS-IS server. Recommendations:
- enable **logging** at `INFO/DEBUG` to inspect traffic;
- protect the passcode via environment variables or a **.env** excluded from git;
- add tests for APRS frame parsing/serialization functions.

Example run with verbose logging:

```bash
export LOG_LEVEL=DEBUG
python aprs_bbs.py
```

---

## 🛣️ Roadmap (proposals)

- [ ] Documented BBS commands and integrated help
- [ ] **Private messages** support with basic ACL
- [ ] Configurable APRS-IS filters (CLI/YAML)
- [ ] Message export/backup (JSON/CSV)
- [ ] **Docker container** (lightweight image + healthcheck)
- [ ] Optional RF integration (TNC KISS)

Contributions and ideas are welcome!

---

## 🤝 Contributing

1. Fork the project and create a feature branch:
   ```bash
   git checkout -b feature/your-feature
   ```
2. Write clear commits, add tests if possible.
3. Open a **Pull Request** describing goals and changes.

For bugs and requests, use **Issues**.

---

## 📝 License

Distributed under **GPL-3.0** license. See the `LICENSE` file for details.

---

## 📬 Contact

- Author: @vash
- Repo: https://github.com/vash909/GasCast-BBS

If you use GasCast-BBS on‑air, let me know how it works and what you’d like improved. 73!

