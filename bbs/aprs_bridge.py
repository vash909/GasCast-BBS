"""APRS-IS bridge for GasCast (RF <-> Telnet)."""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from typing import Callable

from .db import BBSDatabase

MAX_RF_TEXT = 67


def generate_passcode(callsign: str) -> int:
    base_call = callsign.split("-")[0].upper()
    hash_val = 0x73E2
    for i in range(0, len(base_call), 2):
        c1 = ord(base_call[i])
        hash_val ^= c1 << 8
        if i + 1 < len(base_call):
            hash_val ^= ord(base_call[i + 1])
    return abs(hash_val)


class APRSBridge:
    """Bridge APRS messages/groups into GasCast and vice versa."""

    def __init__(
        self,
        db: BBSDatabase,
        callsign: str,
        *,
        server_host: str = "rotate.aprs2.net",
        port: int = 14580,
        filter_string: str | None = None,
        passcode: int | None = None,
        software_name: str = "GASCAST",
        software_version: str = "1.0",
        object_name: str | None = None,
        object_lat: str | None = None,
        object_lon: str | None = None,
        object_comment: str = "-GasCast RF",
        object_interval: int = 900,
        object_symbol_table: str = "/",
        object_symbol_code: str = "-",
        on_private: Callable[[str, str, str, int], None] | None = None,
        on_group: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.logger = logging.getLogger("gascast.aprs")
        self.db = db
        self.callsign = callsign.upper()
        self.server_host = server_host
        self.port = int(port)
        self.passcode = int(passcode) if passcode is not None else generate_passcode(self.callsign)
        self.filter_string = filter_string or f"filter m/{self.callsign}"
        self.login_line = (
            f"user {self.callsign} pass {self.passcode} "
            f"vers {software_name} {software_version} {self.filter_string}"
        )

        self.object_name = object_name
        self.object_lat = self._normalize_coordinate(object_lat, is_lat=True)
        self.object_lon = self._normalize_coordinate(object_lon, is_lat=False)
        if object_lat and not self.object_lat:
            self.logger.warning("Invalid APRS object_lat '%s' - beacon disabled until fixed", object_lat)
        if object_lon and not self.object_lon:
            self.logger.warning("Invalid APRS object_lon '%s' - beacon disabled until fixed", object_lon)
        self.object_comment = object_comment
        self.object_interval = max(30, int(object_interval))
        self.object_symbol_table = (object_symbol_table or "/")[0]
        self.object_symbol_code = (object_symbol_code or "-")[0]

        self.on_private = on_private
        self.on_group = on_group

        self.chat_groups: dict[str, set[str]] = {}
        self.active_calls: set[str] = set()
        self.next_seq: dict[str, int] = {}
        self.pending_acks: dict[tuple[str, str], str] = {}
        self.last_login_reply: dict[str, float] = {}
        self.login_reply_holdoff = 20.0
        self.seen_message_ids: dict[tuple[str, str], tuple[float, str]] = {}
        self.message_id_holdoff = 180.0

        self._sock: socket.socket | None = None
        self._sock_file = None
        self._running = False
        self._recv_thread: threading.Thread | None = None
        self._beacon_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._running and self._sock_file is not None

    def start(self) -> None:
        self.logger.info("APRS connect %s:%s as %s", self.server_host, self.port, self.callsign)
        self._sock = socket.create_connection((self.server_host, self.port), timeout=15)
        self._sock_file = self._sock.makefile("rwb", buffering=0)

        # Read APRS-IS banner if present.
        try:
            _ = self._sock_file.readline()
        except Exception:
            pass

        self._sock_file.write((self.login_line + "\r\n").encode("utf-8"))
        self._sock_file.flush()
        # After login keep the stream blocking to avoid idle read timeout warnings.
        self._sock.settimeout(None)

        self._running = True
        self._recv_thread = threading.Thread(target=self._receive_loop, name="gascast_aprs_recv", daemon=True)
        self._recv_thread.start()

        if self.object_name and self.object_lat and self.object_lon:
            self._send_object()
            self._start_object_beacon()

    def stop(self) -> None:
        self._running = False
        if self._sock_file:
            try:
                self._sock_file.close()
            except Exception:
                pass
            self._sock_file = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _receive_loop(self) -> None:
        assert self._sock_file is not None
        while self._running:
            try:
                line_bytes = self._sock_file.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith("#"):
                    continue
                self._handle_packet(line)
            except socket.timeout:
                # Keep bridge alive on transient read timeout.
                continue
            except Exception as exc:
                self.logger.warning("APRS rx stopped: %s", exc)
                break
        self.stop()

    def _handle_packet(self, packet: str) -> None:
        if ":" not in packet:
            return
        header, data = packet.split(":", 1)
        if ">" not in header:
            return
        src, _ = header.split(">", 1)
        src = src.upper()

        if not data.startswith(":") or len(data) < 11:
            return
        dest_field = data[1:10].strip().upper()
        remainder = data[10:]
        if not remainder.startswith(":"):
            return
        message_text = remainder[1:]

        seq_num: str | None = None
        if "{" in message_text:
            body, seq_part = message_text.split("{", 1)
            digits = ""
            for ch in seq_part:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if digits:
                seq_num = digits
            message_text = body

        if dest_field != self.callsign:
            return

        lower = message_text.strip().lower()
        if lower.startswith("ack") or lower.startswith("rej"):
            seq = ""
            for ch in lower[3:]:
                if ch.isdigit():
                    seq += ch
                else:
                    break
            if seq:
                self.pending_acks.pop((src, seq), None)
            return

        clean_text = message_text.strip()
        now = time.monotonic()
        if seq_num:
            self._prune_seen_message_ids(now)
            key = (src, seq_num)
            cached = self.seen_message_ids.get(key)
            if cached and cached[1] == clean_text:
                # Duplicate retransmission: ACK again but do not execute command twice.
                self._send_message(src, f"ack{seq_num}", expect_ack=False)
                return
            self.seen_message_ids[key] = (now, clean_text)

        self.active_calls.add(src)
        self._process_command(src, clean_text)

        if seq_num:
            self._send_message(src, f"ack{seq_num}", expect_ack=False)

    def _prune_seen_message_ids(self, now: float) -> None:
        stale = [k for k, v in self.seen_message_ids.items() if (now - v[0]) > self.message_id_holdoff]
        for key in stale:
            del self.seen_message_ids[key]

    def _process_command(self, from_call: str, text: str) -> None:
        if not text:
            return
        parts = text.split()
        if not parts:
            return

        cmd = parts[0].lower()
        if cmd == "login":
            self._handle_login(from_call)
            return
        if cmd == "help":
            self._handle_help(from_call)
            return

        if cmd in {"msg", "send"}:
            if len(parts) < 3:
                self._send_message(from_call, "ERR msg", expect_ack=False)
                return
            to_call = parts[1].upper()
            body = " ".join(parts[2:])
            self._store_private_message(from_call, to_call, body)
            return

        if cmd == "group" and len(parts) >= 2:
            sub = parts[1].lower()
            if sub == "create" and len(parts) >= 3:
                self._create_group(from_call, parts[2].lower())
                return
            if sub == "join" and len(parts) >= 3:
                self._join_group(from_call, parts[2].lower())
                return
            if sub == "leave" and len(parts) >= 3:
                self._leave_group(from_call, parts[2].lower())
                return
            if sub == "msg" and len(parts) >= 4:
                self._group_message(from_call, parts[2].lower(), " ".join(parts[3:]))
                return
            self._send_message(from_call, "ERR group", expect_ack=False)
            return

        self._send_message(from_call, "ERR cmd", expect_ack=False)

    def _store_private_message(self, from_call: str, to_call: str, message: str) -> None:
        msg_id = self.db.queue_rf_message(from_call, to_call, message)
        self._send_message(from_call, f"OK {to_call}", expect_ack=False)

        if to_call in self.active_calls:
            self._send_message(to_call, self._compact_private(from_call, message), expect_ack=True)
            self.db.mark_rf_message_delivered(msg_id)

        if self.on_private:
            self.on_private(from_call, to_call, message, msg_id)

    def _handle_login(self, callsign: str) -> None:
        self.active_calls.add(callsign)
        now = time.monotonic()
        pending = self.db.list_pending_rf_messages(callsign)
        if not pending:
            last = self.last_login_reply.get(callsign)
            if last and (now - last) < self.login_reply_holdoff:
                return
            self._send_message(callsign, "NO MSG", expect_ack=False)
            self.last_login_reply[callsign] = now
            return
        for row in pending:
            self._send_message(callsign, self._compact_private(row["from_call"], row["message"]), expect_ack=True)
            self.db.mark_rf_message_delivered(int(row["id"]))
        self.last_login_reply[callsign] = now

    def _handle_help(self, callsign: str) -> None:
        lines = [
            "login help",
            "msg CALL TEXT",
            "group create N",
            "group join N",
            "group leave N",
            "group msg N T",
        ]
        for line in lines:
            self._send_message(callsign, line, expect_ack=False)

    def _create_group(self, caller: str, name: str) -> None:
        members = self.chat_groups.get(name)
        if members is not None:
            self._send_message(caller, "EXISTS", expect_ack=False)
            return
        self.chat_groups[name] = {caller}
        self._send_message(caller, f"JOIN {name}", expect_ack=False)

    def _join_group(self, caller: str, name: str) -> None:
        members = self.chat_groups.get(name)
        if not members:
            self._send_message(caller, "NO GROUP", expect_ack=False)
            return
        if caller in members:
            self._send_message(caller, "ALREADY", expect_ack=False)
            return
        members.add(caller)
        self._send_message(caller, f"JOIN {name}", expect_ack=False)

    def _leave_group(self, caller: str, name: str) -> None:
        members = self.chat_groups.get(name)
        if not members or caller not in members:
            self._send_message(caller, "NOT IN", expect_ack=False)
            return
        members.remove(caller)
        self._send_message(caller, f"LEFT {name}", expect_ack=False)
        if not members:
            del self.chat_groups[name]

    def _group_message(self, caller: str, group: str, message: str) -> None:
        members = self.chat_groups.get(group)
        if not members or caller not in members:
            self._send_message(caller, "NOT IN", expect_ack=False)
            return

        payload = self._compact_group(group, caller, message)
        for member in members:
            if member != caller:
                self._send_message(member, payload, expect_ack=True)

        if self.on_group:
            self.on_group(group, caller, message)

        self._send_message(caller, "SENT", expect_ack=False)

    def relay_group_from_bbs(self, group: str, from_call: str, message: str) -> int:
        members = self.chat_groups.get(group.lower()) or self.chat_groups.get(group)
        if not members:
            return 0
        payload = self._compact_group(group, from_call, message)
        sent = 0
        for member in members:
            if member.upper() == from_call.upper():
                continue
            self._send_message(member, payload, expect_ack=True)
            sent += 1
        return sent

    def send_direct_from_bbs(self, from_call: str, to_call: str, message: str) -> None:
        text = self._compact_private(from_call, message)
        self._send_message(to_call.upper(), text, expect_ack=True)

    def _compact_private(self, from_call: str, message: str) -> str:
        return self._fit(f"{from_call[:8]}> {message}")

    def _compact_group(self, group: str, from_call: str, message: str) -> str:
        return self._fit(f"[{group[:8]}]{from_call[:8]}:{message}")

    def _fit(self, text: str) -> str:
        clean = " ".join(text.split())
        if len(clean) <= MAX_RF_TEXT:
            return clean
        return clean[: MAX_RF_TEXT - 3] + "..."

    def _send_message(self, to_call: str, message: str, *, expect_ack: bool = True) -> None:
        if not self._sock_file:
            return
        out = self._fit(message)

        if expect_ack:
            seq_num = self.next_seq.get(to_call.upper(), 1)
            seq = f"{seq_num:02d}"
            self.next_seq[to_call.upper()] = (seq_num % 99) + 1
            self.pending_acks[(to_call.upper(), seq)] = out
            out = f"{out}{{{seq}"

        addressee = to_call.upper().ljust(9)[:9]
        payload = f":{addressee}:{out}"
        packet = f"{self.callsign}>APRS,TCPIP*:{payload}\r\n"

        with self._send_lock:
            try:
                self._sock_file.write(packet.encode("utf-8"))
                self._sock_file.flush()
            except Exception as exc:
                self.logger.warning("APRS tx error to %s: %s", to_call, exc)

    def _send_object(self) -> None:
        if not self._sock_file or not self.object_name or not self.object_lat or not self.object_lon:
            return
        objname = self.object_name.ljust(9)[:9]
        ts = time.strftime("%H%M%Sz", time.gmtime())
        payload = (
            f";{objname}*{ts}{self.object_lat}{self.object_symbol_table}"
            f"{self.object_lon}{self.object_symbol_code}{self.object_comment}"
        )
        packet = f"{self.callsign}>APRS,TCPIP*:{payload}\r\n"
        with self._send_lock:
            try:
                self._sock_file.write(packet.encode("utf-8"))
                self._sock_file.flush()
            except Exception as exc:
                self.logger.warning("APRS object tx error: %s", exc)

    def _start_object_beacon(self) -> None:
        def beacon_loop() -> None:
            while self._running:
                time.sleep(self.object_interval)
                if not self._running:
                    break
                self._send_object()

        self._beacon_thread = threading.Thread(target=beacon_loop, name="gascast_aprs_object", daemon=True)
        self._beacon_thread.start()

    def _normalize_coordinate(self, raw: str | None, *, is_lat: bool) -> str | None:
        if raw is None:
            return None
        value = raw.strip().upper()
        if not value:
            return None

        if is_lat and re.fullmatch(r"\d{4}\.\d{2}[NS]", value):
            return value
        if (not is_lat) and re.fullmatch(r"\d{5}\.\d{2}[EW]", value):
            return value

        match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*([NSEW])?", value)
        if not match:
            return None

        num = float(match.group(1))
        hemi = match.group(2)

        if is_lat:
            if hemi and hemi not in {"N", "S"}:
                return None
            if not (-90.0 <= num <= 90.0):
                return None
            if hemi is None:
                hemi = "N" if num >= 0 else "S"
            return self._to_aprs_coord(abs(num), hemi, deg_width=2)

        if hemi and hemi not in {"E", "W"}:
            return None
        if not (-180.0 <= num <= 180.0):
            return None
        if hemi is None:
            hemi = "E" if num >= 0 else "W"
        return self._to_aprs_coord(abs(num), hemi, deg_width=3)

    def _to_aprs_coord(self, degrees_decimal: float, hemi: str, *, deg_width: int) -> str:
        degrees = int(degrees_decimal)
        minutes = round((degrees_decimal - degrees) * 60.0, 2)
        if minutes >= 60.0:
            degrees += 1
            minutes = 0.0
        return f"{degrees:0{deg_width}d}{minutes:05.2f}{hemi}"
