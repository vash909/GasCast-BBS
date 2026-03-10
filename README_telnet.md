<img width="604" height="205" alt="image" src="https://github.com/user-attachments/assets/54ab9da5-19ae-4e49-9b14-c94a3d49aedb" />

# GasCast Linux Telnet BBS

GasCast is a multi-user ANSI BBS for Linux.
It can be used from:
- Telnet clients
- APRS-IS (RF-facing bridge)

## Architecture

- `Telnet side`: interactive BBS UI, channels, boards, notes, mail, ham tools.
- `APRS-IS side`: short packet commands, direct messages, groups, ACK handling.
- `Bridge`: APRS-IS messages can reach Telnet users/channels and vice versa.
- `Storage`: SQLite in `./data/bbs.sqlite3`.

## Requirements

- Linux
- Python 3.11+
- Telnet client (`telnet` or `netcat`)
- APRS-IS connectivity and valid ham callsign/passcode (if APRS-IS bridge enabled)

## Quick Start

```bash
cp config.example.toml config.toml
./run_bbs.sh
```

Or:

```bash
python3 main.py --host 0.0.0.0 --port 2323 --data-dir ./data
```

## Telnet Login

Connect:

```bash
telnet 127.0.0.1 2323
```

At prompt enter only your callsign (no password).
Examples: `IU1BOT`, `IU1BOT-10`.

## Telnet UI (classic fixed screen)

- GasCast now uses a fixed-screen dashboard style:
  - logo/header always on top
  - status panel
  - content window
  - menu window
- The screen is re-rendered instead of continuously scrolling.
- You can select menu shortcuts with digits:
  - `0` help
  - `1` channels
  - `2` notes
  - `3` mail
  - `4` boards
  - `5` APRS-IS
  - `6` main refresh

## APRS-IS Config

`config.toml` example:

```toml
[aprs]
enabled = true
callsign = "IU1BOT-10"
server = "rotate.aprs2.net"
port = 14580
filter = "filter m/IU1BOT-10"
passcode = **********

object_name = "GasCast"
object_lat = "4540.00N"
object_lon = "00911.00E"
object_comment = "-GasCast BBS Bridge"
position_interval = 900
```

Notes:
- `position_interval` is the interval (seconds) for APRS-IS position/object packets.
- `object_interval` is still supported for compatibility.
- `object_lat` / `object_lon` accept:
  - APRS format: `DDMM.mmN` and `DDDMM.mmE`
  - Decimal degrees: `44.3107N`, `9.3320E`, `44.3107`, `-9.3320`
  - Decimal values are auto-converted to APRS format at startup.

## Telnet Command Guide

### Core

- `help`
  - Show command summary.
- `cls`
  - Redraw the main screen.
- `quit`
  - Disconnect.

### Presence and channels

- `who`
  - List online users.
- `users`
  - List registered users (callsign-based entries are auto-created).
- `channels`
  - List channels and user count.
- `join <channel>`
  - Join or create a channel.

Example:
```text
join dx
```

### Realtime chat

- `say <message>`
  - Send message to current channel.
- `dm <user> <message>`
  - Direct message. If user is offline, message can be queued (note/APRS-IS).

Examples:
```text
say cq test from telnet
dm IU1ABC ping when online
```

### Notes (offline short messages)

- `note send <user>`
  - Create multiline note (end with single `.` line).
- `note list`
  - List notes.
- `note read <id>`
  - Read a note.

### Mail (longer async messages)

- `mail compose <user>`
  - Compose multiline mail.
- `mail inbox`
  - List inbox.
- `mail read <id>`
  - Read mail.

### Boards

- `board list`
  - List boards.
- `board ls <board>`
  - List posts in board.
- `board post <board>`
  - Create post (multiline).
- `board read <id>`
  - Read post.

### Ham tools

- `ham bands`
  - HF band table.
- `ham prop <band>`
  - Offline propagation estimate (example: `ham prop 20m`).
- `ham qcode <QCODE>`
  - Q-code dictionary.
- `ham grayline`
  - Grayline hint.
- `ham sun`
  - Estimated solar snapshot.

### APRS-IS bridge control from Telnet

- `aprs-is status`
  - Bridge status.
- `aprs-is groups`
  - APRS-IS known groups.
- `aprs-is msg <call> <text>`
  - Queue/send APRS-IS direct message.
- `aprs-is gmsg <group> <text>`
  - Send from Telnet to a specific APRS-IS group.
- `aprs-is gread <group> [limit]`
  - Read stored APRS-IS group messages for that group.
- `aprs-is group msg <group> <text>`
  - Same as `gmsg` (syntax-compatible form).

Aliases accepted: `aprs`, `rf`.

Examples:
```text
aprs-is status
aprs-is msg IU1XYZ hello from gascast
aprs-is gmsg net hello all
aprs-is gread net 30
aprs-is group msg net hello all
```

## APRS-IS Command Guide (over APRS message)

Send APRS text message **to your BBS callsign** (`[aprs].callsign`):

- `login`
  - Register activity and fetch pending APRS-IS messages.
- `help`
  - Show short APRS command list.
- `msg CALL TEXT`
  - Store/send private message.
- `group create NAME`
  - Create and join group.
- `group join NAME`
  - Join group.
- `group leave NAME`
  - Leave group.
- `group msg NAME TEXT`
  - Send group message.

Examples:
```text
login
msg IU1ABC test via aprs-is
group create net
group msg net check in 5m
```

## Bridge Behavior

- APRS-IS group messages appear in same-name Telnet channel.
- Telnet `say` in channel relays to APRS-IS group with same name.
- APRS-IS/Telnet group traffic is stored and can be reviewed with `aprs-is gread`.
- APRS-IS direct messages can be delivered to online Telnet users.
- Offline APRS-IS messages are queued in SQLite.

## Troubleshooting

- `APRS rx stopped: timed out`
  - Fixed in current code by blocking read mode and timeout handling.
- If you still receive duplicate `login` replies:
  - Verify only one GasCast/APRS bot is running with that callsign.
  - Current code suppresses repeated `NO MSG` within a short holdoff window.

## Security/ops

- Restrict server exposure with firewall/VPN/reverse proxy.
- Keep APRS packet text short to reduce airtime usage.
