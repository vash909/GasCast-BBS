# GasCast-BBS

A **simple APRS Bulletin Board System (BBS)** implemented with **only Python’s standard library**.  
It connects to **APRS‑IS** over TCP, logs in with your callsign, listens for APRS message frames addressed to the BBS callsign, and executes **simple text commands**. It also keeps **mailboxes** for delayed delivery of private messages and supports **chat groups**. Messages are **persisted to SQLite** so queued mail survives restarts.

> This README documents the functionality present in the single Python script you shared. It does not assume other files or third‑party packages.

---

## ✨ Features

- **APRS‑IS connectivity** via TCP (defaults to `rotate.aprs2.net:14580`) with a standards‑compliant login line.
- **APRS message parsing** (APRS 1.2c format): processes only text messages addressed to your BBS callsign.
- **Private mailboxes** with automatic delivery upon `login`.
- **Chat groups**: create/join/leave groups and broadcast within them.
- **Acknowledgement tracking**: outgoing messages can request ACKs using APRS `{NN` sequence numbers (00–99 wrap).
- **SQLite persistence** for queued private messages (`aprs_bbs.db` by default; path can be overridden).
- **Optional APRS Object Beacon**: periodically announce the BBS on the map with name/position/symbol/comment.
- **Verbose logging** with `-v/--verbose`.

---

## 🧰 Requirements

- **Python 3.7+** (standard library only: `socket`, `threading`, `argparse`, `sqlite3`, `logging`, `time`, `os`).
- A valid **amateur radio license and callsign** (base callsign, e.g. `N0CALL`).

No external pip packages are required.

---

## ⚙️ Configuration

You can configure connection and behavior via CLI flags and/or environment variables.

### Environment variables

- `APRS_PASSCODE` – APRS‑IS passcode for your **base** callsign (optional; if omitted, the script computes it).
- `APRS_BBS_DB_PATH` – path to the SQLite database file (optional; defaults to `aprs_bbs.db` next to the script).

### Command‑line options

Run `python3 aprs_bbs.py --help` to see all flags. Summary:

- **Positional**
  - `callsign` – your base callsign and SSID (e.g. `N0CALL`).
- **Connection**
  - `--server` – APRS‑IS host (default: `rotate.aprs2.net`)
  - `--port` – APRS‑IS TCP port (default: `14580` – user filter port)
  - `--filter` – custom javAPRFilters expression (defaults to `filter m/<YOURCALL>`)
  - `--passcode` – APRS‑IS passcode (overrides env/auto‑computed value)
- **Beaconing (optional)**
  - `--object-name` – APRS object name (max 9 chars) to beacon
  - `--lat` / `--lon` – object coordinates in APRS formats (`DDMM.mmN/S`, `DDDMM.mmE/W`)
  - `--comment` – object comment (default: `-APRS BBS disponibile`)
  - `--object-interval` – seconds between object beacons (default: `900`)
  - `--symbol-table` – `'/'` primary or `'\'` secondary (default: `'/'`)
  - `--symbol-code` – single‑char icon code (default: `'-'` house)
- **Misc**
  - `-v/--verbose` – enable debug logging

---

## 🚀 Quick start

1. Ensure you have Python 3 installed;
2. Set your parameter as explained above;  
3. Run (if you have environment variables already setup):
   ```bash
   python3 aprs_bbs.py N0CALL 
   ```
   Or with custom server/port:
   ```bash
   python3 aprs_bbs.py N0CALL-SSID --server rotate.aprs2.net --port 14580 --passcode $pwd --object-name $name --lat DDMM.mmN/S --lon DDDMM.mmE/W --comment $comment --object-interval $seconds --symbol-table / --symbol-code -
   ```

> The script connects to APRS‑IS, sends the login line, and starts a background receiver thread.

---

## 📨 Using the BBS
An example of a running instance is available at [GasCast BBS](https://aprs.fi/info/a/GasCast): send `login` to IU1BOT-10 to get started!

All interactions are APRS **text messages** addressed to **your ham radio callsign** (not the object name!). The script recognizes these commands:

- `login` – Register with the BBS and receive any **pending private messages**.
- `help` – Show a short help message.
- `msg CALLSIGN MESSAGE` – Store a **private message** for `CALLSIGN`.
- `group create NAME` – Create a chat group `NAME` and join it.
- `group join NAME` – Join an existing group.
- `group leave NAME` – Leave a group.
- `group msg NAME MESSAGE` – Send `MESSAGE` to all members of `NAME`.

### Example APRS chat session

1. A user sends to **YOURCALL**:  
   `login`  
   → The BBS replies with queued messages or `No new messages.`

2. A user sends:  
   `msg IZ1ABC Hello from the BBS`  
   → Stored for delivery when `IZ1ABC` logs in.

3. Create and use a group:  
   ```
   group create hikers
   group msg hikers Net tonight at 20:30 local
   ```

> Outgoing messages typically request acknowledgements. Sequence numbers (`{NN`) are tracked per‑recipient and wrap after 99.

---

## 🗂️ Data persistence

- Private messages are stored in **SQLite** and mirrored in memory.
- Default DB path: `aprs_bbs.db` (next to the script) or override with `APRS_BBS_DB_PATH`.
- On startup, **undelivered** messages are loaded; after delivery, rows are marked as `delivered=1`.
- Delivery on `login` sends each message and awaits APRS ACKs; even if DB writes fail, in‑memory delivery proceeds and errors are logged.

---

## 📡 APRS Object Beacon (optional)

You can periodically beacon an **APRS object** to announce your BBS on the map:

```bash
python3 aprs_bbs.py N0CALL \
  --object-name GASBBS \
  --lat 4540.00N --lon 00911.00E \
  --comment "-APRS BBS available" \
  --object-interval 900 \
  --symbol-table / \
  --symbol-code -
```

- The beacon runs in a **daemon thread** and stops automatically on exit.
- The timestamp uses UTC `HHMMSSz`. Object name is padded/truncated to 9 chars.

---

## 🛠️ Logging

Enable verbose logs for troubleshooting:
```bash
python3 aprs_bbs.py N0CALL -v
```
Log format: `TIMESTAMP LEVEL LOGGER: message`.

Open an issue and attach logs if any unexpected behavior is noticed.

---

## 🚧 Limitations

- RF (TNC/KISS) is **not** handled directly—this script speaks **APRS‑IS only**.
- While private messages are persisted, group membership is saved in volatile memroy and not persisted across restarts.
- APRS message body length is constrained by the APRS protocol specs and gateways.
- We accept requests and suggestions for new features. Don't be shy!! 

---

## 📄 License

- Distributed under **GPL-3.0** license. See the `LICENSE` file for details.

---

## 🙌 Credits

- Author: Lorenzo Vash IU1BOT - iu1bot@xzgroup.net | Matteo IU6HMO ham@iu6hmo.com
- Contributors: Maurizio IK1WHN

**Any comment is welcome, feel free to drop an email for whatever reason!**