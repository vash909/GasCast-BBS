# GasCast-BBS (Script-Only)

A **simple APRS Bulletin Board System (BBS)** implemented with **only Python’s standard library**.  
It connects to **APRS‑IS** over TCP, logs in with your callsign, listens for APRS message frames addressed to the BBS callsign, and executes **simple text commands**. It also keeps **mailboxes** for delayed delivery of private messages and supports **chat groups**. Messages are **persisted to SQLite** so queued mail survives restarts.

> This README documents the functionality present in the single Python script you shared. It does not assume other files or third‑party packages.

---

## ✨ Features

- **APRS‑IS connectivity** via TCP (defaults to `rotate.aprs2.net:14580`) with a standards‑compliant login line.
- **Automatic passcode generation** from your base callsign (or use `--passcode` / `APRS_PASSCODE`).
- **APRS message parsing** (TNC2 format): processes only text messages addressed to your BBS callsign.
- **Private mailboxes** with automatic delivery upon `login`.
- **Chat groups**: create/join/leave groups and broadcast within them.
- **Acknowledgement tracking**: outgoing messages can request ACKs using APRS `{NN` sequence numbers (00–99 wrap).
- **SQLite persistence** for queued private messages (`aprs_bbs.db` by default; path can be overridden).
- **Optional APRS Object Beacon**: periodically announce the BBS on the map with name/position/symbol/comment.
- **Verbose logging** with `-v/--verbose`.

---

## 🧰 Requirements

- **Python 3.10+** (standard library only: `socket`, `threading`, `argparse`, `sqlite3`, `logging`, `time`, `os`).
- A valid **amateur radio callsign** (base callsign, without SSID, e.g. `N0CALL`).

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
  - `callsign` – your base callsign (e.g. `N0CALL`). *Do not include SSID.*
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

1. Ensure you have Python 3.10+.
2. Pick your base callsign (e.g., `N0CALL`).  
   - Optionally set `APRS_PASSCODE` or pass `--passcode`. If neither is provided, the script **computes** the standard APRS‑IS passcode from your base callsign.
3. Run:
   ```bash
   python3 aprs_bbs.py N0CALL -v
   ```
   Or with custom server/port:
   ```bash
   python3 aprs_bbs.py N0CALL --server rotate.aprs2.net --port 14580
   ```

> The script connects to APRS‑IS, sends the login line, and starts a background receiver thread. Press `Ctrl+C` to stop.

---

## 📨 Using the BBS

All interactions are APRS **text messages** addressed to **your BBS callsign**. The script recognizes these commands:

- `login` – Register with the BBS and receive any **pending private mail**.
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

## 🔐 Notes & good practice

- Use the **base callsign** (no SSID) for passcode generation.
- A **negative passcode** indicates receive‑only on APRS‑IS; servers will ignore outbound messages.
- Keep your passcode out of source control; prefer **environment variables**.
- Run behind a stable internet connection; the script uses the `TCPIP*` path as an internet client.
- Use `--filter` to narrow APRS‑IS traffic (defaults to messages to your callsign).

---

## 🛠️ Logging

Enable verbose logs for troubleshooting:
```bash
python3 aprs_bbs.py N0CALL --verbose
```
Log format: `TIMESTAMP LEVEL LOGGER: message`.

---

## 🚧 Limitations

- RF (TNC/KISS) is **not** handled directly—this script speaks **APRS‑IS only**.
- Mailboxes and groups are **in‑memory** for the current session; private messages are persisted, group membership is not persisted across restarts.
- APRS message body length is constrained by the APRS spec and gateways.

---

## 🧪 Development tips

- Test locally against `rotate.aprs2.net:14580` with a filter limiting traffic to your callsign.
- Add application‑level filters or rate limits if connecting from a noisy feed.
- Consider extending the command set (e.g., listing inbox, deleting messages, listing groups).

---

## 📄 License

This script is intended for use within the GasCast‑BBS repository. License terms should match the repo’s license (e.g., GPL‑3.0). Update this section if needed.

---

## 🙌 Credits

Authored by **@vash**. Cheers and 73!
