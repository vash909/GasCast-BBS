#!/usr/bin/env python3
"""
Simple APRS Bulletin Board System (BBS).

- Connects to APRS-IS (default rotate.aprs2.net:14580)
- Sends login immediately (no blocking on server banner)
- Stores/delivers private messages on user login
- Simple chat groups (create/join/leave/msg)
"""

import argparse
import logging
import os
import socket
import threading
import time
from typing import Dict, List, Tuple, Set


def generate_passcode(callsign: str) -> int:
    """Generate APRS-IS passcode from BASE callsign (no SSID)."""
    base = callsign.split("-")[0].upper()
    h = 0x73E2
    for i in range(0, len(base), 2):
        h ^= (ord(base[i]) << 8)
        if i + 1 < len(base):
            h ^= ord(base[i + 1])
    return abs(h)


class APRSBBS:
    def __init__(
        self,
        callsign: str,
        server_host: str = "rotate.aprs2.net",
        port: int = 14580,
        software_name: str = "PYBBS",
        software_version: str = "0.2",
        filter_string: str | None = None,
        passcode: int | None = None,
        ipv4_only: bool = False,
    ) -> None:
        self.callsign = callsign.upper()
        self.passcode = int(passcode) if passcode is not None else generate_passcode(self.callsign)
        self.server_host = server_host
        self.port = port
        self.software_name = software_name
        self.software_version = software_version
        self.filter_string = filter_string or f"filter m/{self.callsign}"
        self.login_line = (
            f"user {self.callsign} pass {self.passcode} "
            f"vers {self.software_name} {self.software_version} {self.filter_string}"
        )
        self.ipv4_only = ipv4_only

        self.mailboxes: Dict[str, List[Tuple[str, str]]] = {}
        self.chat_groups: Dict[str, Set[str]] = {}

        self._sock: socket.socket | None = None
        self._sock_file = None
        self._recv_thread: threading.Thread | None = None
        self._running = False

        self.logger = logging.getLogger(__name__)

    # -------------------- connection --------------------

    def _connect_once(self, sockaddr, family, socktype, proto) -> bool:
        s = socket.socket(family, socktype, proto)
        try:
            s.settimeout(10)  # connect timeout
            s.connect(sockaddr)
            self.logger.info("Connected to %s:%s", sockaddr[0], sockaddr[1])
            s.settimeout(5)  # short read timeout for banner/first lines

            # line-oriented wrapper
            f = s.makefile("rwb", buffering=0)

            # SEND LOGIN IMMEDIATELY (don't block waiting for banner)
            login = (self.login_line + "\r\n").encode("ascii", "ignore")
            f.write(login)
            f.flush()
            self.logger.debug("Sent login line: %s", self.login_line)

            # Try to read one line non-fatal (banner or logresp); ignore timeout
            try:
                line = f.readline().decode("utf-8", "ignore").strip()
                if line:
                    self.logger.debug("Server: %s", line)
            except (socket.timeout, OSError):
                pass

            # Clear timeout for normal ops
            s.settimeout(None)

            self._sock = s
            self._sock_file = f
            return True
        except OSError as e:
            self.logger.warning("Connect attempt to %s failed: %s", sockaddr, e)
            try:
                s.close()
            except Exception:
                pass
            return False

    def connect(self) -> None:
        """Resolve and connect (tries all addresses, IPv4 first if requested)."""
        self.logger.info(
            "Connecting to APRS-IS server %s:%d as %s",
            self.server_host, self.port, self.callsign
        )

        # Resolve
        family_hint = socket.AF_INET if self.ipv4_only else 0
        addrs = socket.getaddrinfo(self.server_host, self.port, family_hint, socket.SOCK_STREAM)
        # Prefer IPv4 addresses if ipv4_only or mixed results
        addrs_sorted = sorted(addrs, key=lambda a: 0 if a[0] == socket.AF_INET else 1)

        last_error = None
        for family, socktype, proto, _canon, sockaddr in addrs_sorted:
            if self._connect_once(sockaddr, family, socktype, proto):
                break
        else:
            raise ConnectionError(f"Could not connect to {self.server_host}:{self.port}")

        # Start receiver
        self._running = True
        self._recv_thread = threading.Thread(target=self._receive_loop, name="aprs_recv", daemon=True)
        self._recv_thread.start()

    def close(self) -> None:
        self._running = False
        if self._sock_file:
            try: self._sock_file.close()
            except Exception: pass
        if self._sock:
            try: self._sock.close()
            except Exception: pass

    # -------------------- RX & parsing --------------------

    def _receive_loop(self) -> None:
        assert self._sock_file is not None
        while self._running:
            try:
                line_b = self._sock_file.readline()
                if not line_b:
                    self.logger.warning("Connection closed by server")
                    break
                line = line_b.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith("#"):
                    continue
                self.logger.debug("RX: %s", line)
                self._handle_packet(line)
            except Exception as exc:
                self.logger.error("Error reading from APRS-IS: %s", exc)
                break
        self.close()

    def _handle_packet(self, packet: str) -> None:
        try:
            if ":" not in packet:
                return
            header, data = packet.split(":", 1)
            if ">" not in header:
                return
            src, _rest = header.split(">", 1)
            if not data.startswith(":"):
                return
            if len(data) < 11:
                return
            dest_field = data[1:10].strip().upper()
            remainder = data[10:]
            if not remainder.startswith(":"):
                return
            message_text = remainder[1:]
            if "{" in message_text:
                message_text = message_text.split("{", 1)[0]
            if dest_field != self.callsign:
                return
            self.logger.info("Message from %s: %s", src, message_text)
            self._process_command(src.upper(), message_text.strip())
        except Exception as exc:
            self.logger.error("Packet parse error: %s", exc)

    # -------------------- commands --------------------

    def _process_command(self, from_call: str, text: str) -> None:
        if not text:
            return
        parts = text.strip().split()
        if not parts:
            return
        cmd = parts[0].lower()

        if cmd == "login":
            self._handle_login(from_call)
        elif cmd == "help":
            self._handle_help(from_call)
        elif cmd in ("msg", "send"):
            if len(parts) < 3:
                self._send_message(from_call, "Usage: msg CALLSIGN message")
                return
            to_call = parts[1].upper()
            message_body = " ".join(parts[2:])
            self._store_private_message(from_call, to_call, message_body)
        elif cmd == "group" and len(parts) >= 2:
            sub = parts[1].lower()
            if sub == "create" and len(parts) >= 3:
                self._create_group(from_call, parts[2].lower())
            elif sub == "join" and len(parts) >= 3:
                self._join_group(from_call, parts[2].lower())
            elif sub == "leave" and len(parts) >= 3:
                self._leave_group(from_call, parts[2].lower())
            elif sub == "msg" and len(parts) >= 4:
                self._group_message(from_call, parts[2].lower(), " ".join(parts[3:]))
            else:
                self._send_message(from_call, "Usage: group [create|join|leave|msg] <name> [message]")
        else:
            self._send_message(from_call, "Unknown command. Send 'help' for a list of commands.")

    # -------------------- mailbox --------------------

    def _store_private_message(self, from_call: str, to_call: str, message: str) -> None:
        self.mailboxes.setdefault(to_call, []).append((from_call, message))
        self._send_message(from_call, f"Stored message for {to_call}.")

    def _handle_login(self, callsign: str) -> None:
        self.mailboxes.setdefault(callsign, [])
        inbox = self.mailboxes.get(callsign, [])
        if inbox:
            for from_call, message in inbox:
                self._send_message(callsign, f"From {from_call}: {message}")
            self.mailboxes[callsign] = []
        else:
            self._send_message(callsign, "No new messages.")
        self._send_message(callsign, "Send 'help' for a list of commands.")

    def _handle_help(self, callsign: str) -> None:
        help_lines = [
            "Available commands:",
            "login                        — Register and check for new mail",
            "msg CALLSIGN MESSAGE         — Send a private message",
            "group create NAME            — Create a new chat group and join it",
            "group join NAME              — Join an existing chat group",
            "group leave NAME             — Leave a chat group",
            "group msg NAME MESSAGE       — Message all members of a group",
            "help                         — Show this help",
        ]
        for line in help_lines:
            self._send_message(callsign, line)

    # -------------------- groups --------------------

    def _create_group(self, caller: str, group_name: str) -> None:
        if group_name in self.chat_groups:
            self._send_message(caller, f"Group '{group_name}' already exists.")
            return
        self.chat_groups[group_name] = {caller}
        self._send_message(caller, f"Group '{group_name}' created and you have joined.")

    def _join_group(self, caller: str, group_name: str) -> None:
        members = self.chat_groups.get(group_name)
        if not members:
            self._send_message(caller, f"Group '{group_name}' does not exist.")
            return
        if caller in members:
            self._send_message(caller, f"You are already a member of '{group_name}'.")
            return
        members.add(caller)
        self._send_message(caller, f"Joined group '{group_name}'.")

    def _leave_group(self, caller: str, group_name: str) -> None:
        members = self.chat_groups.get(group_name)
        if not members or caller not in members:
            self._send_message(caller, f"You are not a member of '{group_name}'.")
            return
        members.remove(caller)
        self._send_message(caller, f"Left group '{group_name}'.")
        if not members:
            del self.chat_groups[group_name]

    def _group_message(self, caller: str, group_name: str, message: str) -> None:
        members = self.chat_groups.get(group_name)
        if not members or caller not in members:
            self._send_message(caller, f"You are not a member of '{group_name}'.")
            return
        for member in members:
            if member != caller:
                self._send_message(member, f"[{group_name}] {caller}: {message}")
        self._send_message(caller, f"Sent to group '{group_name}'.")

    # -------------------- TX --------------------

    def _send_message(self, to_call: str, message: str) -> None:
        if not self._sock_file:
            return
        addressee = to_call.upper().ljust(9)[:9]
        payload = f":{addressee}:{message}"
        packet = f"{self.callsign}>APRS,TCPIP*:{payload}\r\n"
        try:
            self._sock_file.write(packet.encode("utf-8"))
            self._sock_file.flush()
            self.logger.debug("TX: %s", packet.strip())
        except Exception as exc:
            self.logger.error("Failed to send to %s: %s", to_call, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="APRS Bulletin Board System")
    parser.add_argument("callsign", help="Your amateur radio callsign (base or with SSID).")
    parser.add_argument("--server", default="rotate.aprs2.net", help="APRS-IS server (default: rotate.aprs2.net)")
    parser.add_argument("--port", type=int, default=14580, help="APRS-IS port (default: 14580)")
    parser.add_argument("--filter", default=None, help="javAPRS filter string; defaults to messages for your callsign.")
    parser.add_argument("--passcode",
                        type=int,
                        default=int(os.getenv("APRS_PASSCODE", "0")) if os.getenv("APRS_PASSCODE") else None,
                        help="APRS-IS passcode; if omitted, compute from base callsign or read APRS_PASSCODE env var.")
    parser.add_argument("--ipv4", action="store_true", help="Force IPv4 when resolving the server.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bbs = APRSBBS(
        callsign=args.callsign,
        server_host=args.server,
        port=args.port,
        filter_string=args.filter,
        passcode=args.passcode,
        ipv4_only=args.ipv4,
    )

    try:
        bbs.connect()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down BBS...")
    finally:
        bbs.close()


if __name__ == "__main__":
    main()
