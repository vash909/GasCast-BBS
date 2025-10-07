#!/usr/bin/env python3
"""Simple APRS Bulletin Board System (BBS).

This script implements a minimal bulletin board and chat system that
connects to the Automatic Packet Reporting System internet service
(APRS‑IS) using only Python's standard library.  It logs in to an
APRS‑IS server with your callsign, listens for APRS message frames
addressed to the BBS callsign and executes simple text commands.  The
code maintains mailboxes for delayed delivery of private messages and
supports creating/joining chat groups.  When a user logs in, any
queued messages for that callsign are delivered automatically.

Key features
------------

* **APRS‑IS connectivity** – Creates a TCP connection to an APRS‑IS
  server (defaults to rotate.aprs2.net on the user defined feed port
  14580).  After connecting, a login string is sent containing your
  callsign, passcode and software name.  According to the APRS‑IS
  documentation, clients must establish a TCP connection to an
  appropriate server port and log in with the format
  `user mycall pass passcode vers software version`【888069444694824†L17-L34】.

* **Passcode generation** – A helper function computes the APRS‑IS
  passcode for a callsign using the standard algorithm described in
  various open source implementations.  The algorithm starts with an
  initial 16‑bit hash of `0x73e2` and XORs pairs of characters from
  the callsign into the hash before taking the absolute value【522874913848782†L51-L73】.

* **Message parsing** – APRS text messages start with a colon, a
  nine‑character addressee field padded with spaces, another colon and
  then the message text【290685697518821†L18-L22】.  The BBS listens for
  packets whose payload follows this format and extracts the
  addressee and message.  It ignores all other packets.

* **Mailboxes and chat groups** – Private messages are stored in
  in‑memory mailboxes keyed by callsign.  When a user connects (by
  sending a `login` command), the BBS delivers all queued
  messages.  Chat groups can be created with `group create name`,
  joined with `group join name` and left with `group leave name`.
  Messages sent with `group msg name text` are relayed to all group
  members.

The BBS understands the following commands when addressed via an APRS
message:

* `login` – Register with the BBS and receive any pending mail.
* `help` – Display a short help summary.
* `msg CALLSIGN MESSAGE` – Store a private message for CALLSIGN.
* `group create NAME` – Create a chat group called NAME and join it.
* `group join NAME` – Join an existing chat group.
* `group leave NAME` – Leave a chat group.
* `group msg NAME MESSAGE` – Send MESSAGE to all members of group NAME.

To run the script you need a valid amateur radio callsign.  The
passcode is derived automatically from the base callsign (without
SSID).  Run `python3 aprs_bbs.py --help` for command line options.
"""

import argparse
import os
import logging
import socket
import threading
import time
from typing import Dict, List, Tuple, Set, Union


def generate_passcode(callsign: str) -> int:
    """Generate an APRS‑IS passcode for a given base callsign.

    The passcode algorithm uses a 16‑bit hash initialised to 0x73e2 and
    XORs every pair of characters from the upper‑case callsign into
    the high and low byte of the hash.  The resulting hash is
    returned as a positive integer【522874913848782†L51-L73】.

    Parameters
    ----------
    callsign : str
        The amateur radio callsign (without SSID) to generate a passcode for.

    Returns
    -------
    int
        A non‑negative integer passcode to use in APRS‑IS login.
    """
    base_call = callsign.split('-')[0].upper()
    hash_val = 0x73E2
    for i in range(0, len(base_call), 2):
        c1 = ord(base_call[i])
        hash_val ^= (c1 << 8)
        if i + 1 < len(base_call):
            c2 = ord(base_call[i + 1])
            hash_val ^= c2
    # Ensure non‑negative passcode
    return abs(hash_val)


class APRSBBS:
    """A minimal bulletin board and chat server for APRS‑IS.

    Instances of this class maintain state about logged‑in users,
    pending private messages and chat group membership.  They connect
    to an APRS‑IS server via TCP, send a login line and then spawn a
    background thread to receive packets.  When a message addressed
    to the BBS callsign arrives, it is parsed into a command and
    processed accordingly.
    """

    def __init__(
        self,
        callsign: str,
        server_host: str = "rotate.aprs2.net",
        port: int = 14580,
        software_name: str = "PYBBS",
        software_version: str = "0.1",
        filter_string: Union[str, None] = None,
        passcode:  Union[str, None] = None,
    ) -> None:
        """Initialise the BBS with connection parameters.

        Parameters
        ----------
        callsign : str
            Your amateur radio callsign.  This will be used as the
            source callsign when sending messages.
        server_host : str, optional
            Hostname of the APRS‑IS server to connect to (default
            ``rotate.aprs2.net``).  ``rotate.aprs2.net`` resolves to
            one of the tier 2 servers that support filter port 14580.
        port : int, optional
            TCP port on the APRS‑IS server (default 14580).  Port
            14580 is the user‑defined filter port recommended by the
            APRS‑IS specification【888069444694824†L30-L33】.
        software_name : str, optional
            A single word identifying this software in the login line.
        software_version : str, optional
            The version string for this software.
        filter_string : str, optional
            Server side filter expression to restrict inbound traffic.
            If not provided, a default filter requesting message
            packets addressed to your callsign is used.
        """
        self.callsign = callsign.upper()
        # Determine the APRS‑IS passcode.  If a passcode is supplied
        # explicitly use it, otherwise compute one from the base callsign.
        if passcode is not None:
            # Use the provided passcode verbatim.  A negative
            # passcode signals receive‑only mode; APRS‑IS servers will
            # ignore outbound packets in that case.
            self.passcode = int(passcode)
        else:
            # No passcode provided – compute one from the base callsign
            # using the standard algorithm.
            self.passcode = generate_passcode(self.callsign)
        self.server_host = server_host
        self.port = port
        self.software_name = software_name
        self.software_version = software_version
        # Build login line.  Use the filter string if provided, otherwise
        # request only message packets destined for our callsign.  The
        # filter command has the form "filter m/<callsign>" which
        # restricts inbound packets to messages destined for the
        # specified callsign.
        if filter_string:
            self.filter_string = filter_string
        else:
            self.filter_string = f"filter m/{self.callsign}"
        self.login_line = (
            f"user {self.callsign} pass {self.passcode} "
            f"vers {self.software_name} {self.software_version} {self.filter_string}"
        )
        # Data structures for messages and groups
        self.mailboxes: Dict[str, List[Tuple[str, str]]] = {}
        self.chat_groups: Dict[str, Set[str]] = {}
        # Socket and thread control
        self._sock: socket.socket | None = None
        self._sock_file = None
        self._recv_thread: threading.Thread | None = None
        self._running = False
        # Set up logging
        self.logger = logging.getLogger(__name__)

        # ------------------------------------------------------------------
        # Acknowledgement tracking
        # ------------------------------------------------------------------
        # next_seq holds the next outgoing sequence number per recipient.  Sequence
        # numbers are two digits (00-99) and wrap around.  Using a dict keyed
        # by callsign lets us maintain separate counters for each station.
        self.next_seq: Dict[str, int] = {}
        # pending_acks maps (callsign, seq_str) -> original message text for
        # messages awaiting acknowledgement.  When an ack is received, the
        # entry is removed.
        self.pending_acks: Dict[Tuple[str, str], str] = {}

    def connect(self) -> None:
        """Connect to the APRS‑IS server and start the receive thread.

        A TCP connection is established to the configured server and
        port.  After the server banner line (which starts with `#`),
        the login line is sent followed by CR/LF.  A background thread
        then continuously reads packets and dispatches them to
        ``_handle_packet``.
        """
        self.logger.info(
            "Connecting to APRS‑IS server %s:%d as %s",
            self.server_host,
            self.port,
            self.callsign,
        )
        self._sock = socket.create_connection((self.server_host, self.port))
        # Use a file wrapper for convenient line‑oriented reads/writes
        self._sock_file = self._sock.makefile("rwb", buffering=0)
        # Read the initial banner line (begins with '#')
        banner = self._sock_file.readline().decode("utf-8", errors="ignore").strip()
        self.logger.debug("Server banner: %s", banner)
        # Send login line
        login_str = self.login_line + "\r\n"
        self._sock_file.write(login_str.encode("utf-8"))
        self._sock_file.flush()
        self.logger.debug("Sent login line: %s", self.login_line)
        # Start the receiver thread
        self._running = True
        self._recv_thread = threading.Thread(target=self._receive_loop, name="aprs_recv", daemon=True)
        self._recv_thread.start()

    def close(self) -> None:
        """Close the APRS‑IS connection and stop the receive thread."""
        self._running = False
        if self._sock_file:
            try:
                self._sock_file.close()
            except Exception:
                pass
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Receiving and parsing
    # ------------------------------------------------------------------
    def _receive_loop(self) -> None:
        """Background thread: read packets line by line and dispatch them."""
        assert self._sock_file is not None
        while self._running:
            try:
                line_bytes = self._sock_file.readline()
                if not line_bytes:
                    self.logger.warning("Connection closed by server")
                    break
                line = line_bytes.decode("utf-8", errors="ignore").strip()
                # Ignore comment lines beginning with '#'
                if not line or line.startswith('#'):
                    continue
                self.logger.debug("Received: %s", line)
                self._handle_packet(line)
            except Exception as exc:
                self.logger.error("Error reading from APRS‑IS: %s", exc)
                break
        self.close()

    def _handle_packet(self, packet: str) -> None:
        """Parse a received TNC2 formatted line and handle messages.

        An APRS packet in TNC2 format looks like:

            SRC>DEST,PATH1,PATH2:DATA

        For messages the DATA portion begins with a colon and the
        addressee field is contained within the first nine bytes of
        DATA【290685697518821†L18-L22】.  Only messages addressed to the
        BBS callsign are processed; everything else is ignored.
        """
        try:
            # Separate header from payload
            if ':' not in packet:
                return
            header, data = packet.split(':', 1)
            # Extract source and destination callsigns
            # Header is of the form SRC>DEST[,PATH...]
            if '>' not in header:
                return
            src, rest = header.split('>', 1)
            # dest_and_path = rest (destination plus optional path)
            # We could parse dest separately but it's not needed – we
            # identify messages by the addressee field in the payload.
            # Only process text messages starting with colon.
            if not data.startswith(':'):
                return
            # Extract the nine‑character addressee field and message
            # The data field has the form :ADDRESSEE:MESSAGE
            if len(data) < 11:  # need at least ":<dest9>:"
                return
            dest_field = data[1:10].strip().upper()
            # Skip over the dest field and the following colon
            remainder = data[10:]
            if not remainder.startswith(':'):
                return
            message_text = remainder[1:]
            full_message_text = message_text
            seq_num: str | None = None
            # Remove optional sequence number appended with '{'.  Capture it if present
            if '{' in message_text:
                body, seq_part = message_text.split('{', 1)
                # seq_part may include digits and possibly more; take leading digits up to 5
                digits = ''
                for ch in seq_part:
                    if ch.isdigit():
                        digits += ch
                        if len(digits) >= 5:
                            break
                    else:
                        break
                if digits:
                    seq_num = digits
                message_text = body
            # Only handle messages addressed to us
            if dest_field != self.callsign:
                # Check for ack/rej directed to us for pending outgoing messages
                # Only process ack/rej messages where dest_field is our callsign
                return
            # If the message is an acknowledgement for one of our sent messages
            lower_msg = message_text.strip().lower()
            if lower_msg.startswith('ack'):
                seq = lower_msg[3:]
                # Extract digits from seq
                seq_digits = ''
                for ch in seq:
                    if ch.isdigit():
                        seq_digits += ch
                        if len(seq_digits) >= 5:
                            break
                    else:
                        break
                if seq_digits:
                    removed = self.pending_acks.pop((src.upper(), seq_digits), None)
                    if removed:
                        self.logger.info("Received ack% s from %s for message '%s'", seq_digits, src, removed)
                    else:
                        self.logger.debug("Received ack% s from %s but no pending entry", seq_digits, src)
                return
            elif lower_msg.startswith('rej'):
                seq = lower_msg[3:]
                # Extract digits for rejection
                seq_digits = ''
                for ch in seq:
                    if ch.isdigit():
                        seq_digits += ch
                        if len(seq_digits) >= 5:
                            break
                    else:
                        break
                if seq_digits:
                    removed = self.pending_acks.pop((src.upper(), seq_digits), None)
                    if removed:
                        self.logger.warning("Received rejection rej% s from %s for message '%s'", seq_digits, src, removed)
                    else:
                        self.logger.debug("Received rej% s from %s but no pending entry", seq_digits, src)
                return
            self.logger.info("Message from %s: %s", src, message_text)
            # Process the command (if any)
            self._process_command(src.upper(), message_text.strip())
            # If there was a sequence number in the incoming message, send an ack
            if seq_num:
                # Acknowledge back to sender with ackNN format
                self._send_message(src, f"ack{seq_num}")
        except Exception as exc:
            self.logger.error("Packet parse error: %s", exc)

    # ------------------------------------------------------------------
    # Command processing
    # ------------------------------------------------------------------
    def _process_command(self, from_call: str, text: str) -> None:
        """Interpret an incoming message as a command and execute it."""
        if not text:
            return
        parts = text.strip().split()
        if not parts:
            return
        cmd = parts[0].lower()
        # LOGIN command: deliver any pending mail
        if cmd == 'login':
            self._handle_login(from_call)
        # HELP command: provide command summary
        elif cmd == 'help':
            self._handle_help(from_call)
        # MSG command: store a private message
        elif cmd in ('msg', 'send'):
            if len(parts) < 3:
                self._send_message(from_call, f"Usage: msg CALLSIGN message")
                return
            to_call = parts[1].upper()
            message_body = ' '.join(parts[2:])
            self._store_private_message(from_call, to_call, message_body)
        # GROUP commands
        elif cmd == 'group' and len(parts) >= 2:
            sub = parts[1].lower()
            if sub == 'create' and len(parts) >= 3:
                group_name = parts[2].lower()
                self._create_group(from_call, group_name)
            elif sub == 'join' and len(parts) >= 3:
                group_name = parts[2].lower()
                self._join_group(from_call, group_name)
            elif sub == 'leave' and len(parts) >= 3:
                group_name = parts[2].lower()
                self._leave_group(from_call, group_name)
            elif sub == 'msg' and len(parts) >= 4:
                group_name = parts[2].lower()
                msg_body = ' '.join(parts[3:])
                self._group_message(from_call, group_name, msg_body)
            else:
                self._send_message(from_call, "Usage: group [create|join|leave|msg] <name> [message]")
        else:
            # Unknown command
            self._send_message(from_call, "Unknown command. Send 'help' for a list of commands.")

    # ------------------------------------------------------------------
    # Private messaging
    # ------------------------------------------------------------------
    def _store_private_message(self, from_call: str, to_call: str, message: str) -> None:
        """Store a private message in the recipient's mailbox."""
        self.mailboxes.setdefault(to_call, []).append((from_call, message))
        self._send_message(from_call, f"Stored message for {to_call}.")

    def _handle_login(self, callsign: str) -> None:
        """Handle the 'login' command by greeting the user and delivering mail."""
        # Add the user to our mailboxes if not present so they can receive mail
        self.mailboxes.setdefault(callsign, [])
        # Deliver any queued messages
        inbox = self.mailboxes.get(callsign, [])
        if inbox:
            for from_call, message in inbox:
                # Deliver each stored message with ack request
                self._send_message(
                    callsign,
                    f"From {from_call}: {message}",
                    expect_ack=True,
                )
            self.mailboxes[callsign] = []  # Clear delivered messages
        else:
            self._send_message(callsign, "No new messages.")
        # Send help hint - not needed actually
        #self._send_message(callsign, "Send 'help' for a list of commands.")

    def _handle_help(self, callsign: str) -> None:
        """Send a brief help listing to the caller."""
        help_lines = [
            "Available commands:",
            "login                        — Register and check for new mail",
            "msg CALLSIGN MESSAGE         — Send a private message",
            "group create NAME            — Create a new chat group and join it",
            "group join NAME              — Join an existing chat group",
            "group leave NAME             — Leave a chat group",
            "group msg NAME MESSAGE       — Send a message to all group members",
            "help                         — Show this help message",
        ]
        for line in help_lines:
            self._send_message(callsign, line)

    # ------------------------------------------------------------------
    # Chat group handling
    # ------------------------------------------------------------------
    def _create_group(self, caller: str, group_name: str) -> None:
        """Create a new chat group and add the caller as the first member."""
        if group_name in self.chat_groups:
            self._send_message(caller, f"Group '{group_name}' already exists.")
            return
        self.chat_groups[group_name] = {caller}
        self._send_message(caller, f"Group '{group_name}' created and you have joined.")

    def _join_group(self, caller: str, group_name: str) -> None:
        """Add the caller to an existing chat group."""
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
        """Remove the caller from a chat group."""
        members = self.chat_groups.get(group_name)
        if not members or caller not in members:
            self._send_message(caller, f"You are not a member of '{group_name}'.")
            return
        members.remove(caller)
        self._send_message(caller, f"Left group '{group_name}'.")
        # Clean up empty groups
        if not members:
            del self.chat_groups[group_name]

    def _group_message(self, caller: str, group_name: str, message: str) -> None:
        """Send a message to all members of a chat group."""
        members = self.chat_groups.get(group_name)
        if not members or caller not in members:
            self._send_message(caller, f"You are not a member of '{group_name}'.")
            return
        for member in members:
            if member != caller:
                self._send_message(
                    member,
                    f"[{group_name}] {caller}: {message}",
                    expect_ack=True,
                )
        self._send_message(caller, f"Sent to group '{group_name}'.")

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def _send_message(self, to_call: str, message: str, *, expect_ack: bool = True) -> None:
        """Format and transmit an APRS text message via APRS‑IS.

        According to the APRS message format, the payload begins with a
        colon, then a nine‑character addressee padded with spaces,
        another colon and the message text【290685697518821†L18-L22】.  The
        header uses the BBS callsign as the source and the
        destination 'APRS' to indicate an APRS message.  The
        `TCPIP*` path is used because packets originate from an
        internet client.  Each outgoing message is terminated with
        CR/LF as required by APRS‑IS【888069444694824†L126-L128】.
        """
        if not self._sock_file:
            return
        # If acknowledgement is requested, append a sequence number to the
        # message in the form "{NN" where NN is a two-digit sequence.  Store
        # the message in pending_acks for later correlation when an ack is
        # received.  Sequence numbers wrap around from 99 back to 00.
        orig_message = message
        if expect_ack:
            seq_num = self.next_seq.get(to_call, 1)
            seq_str = f"{seq_num:02d}"
            # Prepare next sequence number for this recipient
            self.next_seq[to_call] = (seq_num % 99) + 1
            message = f"{message}{{{seq_str}"
            # Record the pending ack with callsign and sequence
            self.pending_acks[(to_call.upper(), seq_str)] = orig_message
            self.logger.debug(
                "Queued message for ack: %s -> %s (seq %s)",
                to_call,
                orig_message,
                seq_str,
            )
        # Build the message payload (addressee padded to 9 chars)
        addressee = to_call.upper().ljust(9)[:9]
        payload = f":{addressee}:{message}"
        # Compose the full TNC2 packet
        packet = f"{self.callsign}>APRS,TCPIP*:{payload}\r\n"
        try:
            self._sock_file.write(packet.encode("utf-8"))
            self._sock_file.flush()
            self.logger.debug("Sent: %s", packet.strip())
        except Exception as exc:
            self.logger.error("Failed to send message to %s: %s", to_call, exc)

    # ------------------------------------------------------------------
    # APRS object beacon
    # ------------------------------------------------------------------
    def send_object(self,
                    name: str,
                    lat: str,
                    lon: str,
                    comment: str,
                    symbol_table: str = "/",
                    symbol_code: str = "-",
                    ) -> None:
        """Transmit an APRS object packet announcing this BBS on the map.

        Parameters
        ----------
        name : str
            The object name (max 9 characters).  It will be padded or truncated
            to exactly nine characters as required by the protocol.
        lat : str
            Latitude in APRS DDMM.mmN/S format (e.g. "4540.00N").
        lon : str
            Longitude in APRS DDDMM.mmE/W format (e.g. "00911.00E").
        comment : str
            A free‑form comment to include after the coordinates.  It should
            typically begin with a hyphen ("-") as per APRS conventions.

        Notes
        -----
        An APRS object packet starts with a semicolon followed by the object
        name, an asterisk, a timestamp and the position.  A trailing comment
        describes the object.  See aprs.org for details.  The timestamp
        included here uses UTC and the hhmmssz format.
        """
        if not self._sock_file:
            return
        # Pad or truncate the object name to nine characters
        objname = name.ljust(9)[:9]
        # Construct UTC timestamp HHMMSSz
        ts = time.strftime("%H%M%Sz", time.gmtime())
        # Build the APRS object payload
        # Build the APRS object payload with symbol table and code.
        # The symbol_table should be either '/' (primary) or '\\' (secondary).
        # The symbol_code is a single character (letter, digit or punctuation)
        # representing the icon shown on the map.
        payload = f";{objname}*{ts}{lat}{symbol_table}{lon}{symbol_code}{comment}"
        # Compose TNC2 packet: callsign>APRS,TCPIP*:<payload>
        packet = f"{self.callsign}>APRS,TCPIP*:{payload}\r\n"
        try:
            self._sock_file.write(packet.encode("utf-8"))
            self._sock_file.flush()
            self.logger.debug("Sent object: %s", packet.strip())
        except Exception as exc:
            self.logger.error("Failed to send object %s: %s", name, exc)

    def start_object_beacon(
        self,
        name: str,
        lat: str,
        lon: str,
        comment: str,
        interval: int = 900,
        symbol_table: str = "/",
        symbol_code: str = "-",
    ) -> None:
        """Start a background thread that periodically transmits the object.

        Parameters
        ----------
        name : str
            Name of the object (max 9 characters).
        lat : str
            Latitude in APRS DDMM.mmN/S format.
        lon : str
            Longitude in APRS DDDMM.mmE/W format.
        comment : str
            Comment string to append after the position, including a leading
            hyphen if desired.
        interval : int, optional
            Beacon interval in seconds (default 900, i.e. 15 minutes).

        This spawns a daemon thread that calls :meth:`send_object` every
        ``interval`` seconds while the BBS is running.  The thread exits
        automatically when the BBS is closed.
        """
        def beacon_loop() -> None:
            while self._running:
                self.send_object(
                    name,
                    lat,
                    lon,
                    comment,
                    symbol_table=symbol_table,
                    symbol_code=symbol_code,
                )
                time.sleep(interval)

        # Start the beacon thread as a daemon so it won't block exit
        t = threading.Thread(target=beacon_loop, name="aprs_object_beacon", daemon=True)
        t.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="APRS Bulletin Board System")
    parser.add_argument(
        "callsign",
        help="Your amateur radio callsign (e.g. N0CALL). Do not include SSID.",
    )
    parser.add_argument(
        "--server",
        default="rotate.aprs2.net",
        help="Hostname of APRS‑IS server to connect to (default: rotate.aprs2.net)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=14580,
        help="TCP port on the APRS‑IS server (default: 14580)",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Custom javAPRFilters expression. Defaults to messages addressed to your callsign.",
    )
    parser.add_argument(
        "--passcode",
        type=int,
        default=None,
        help=(
            "APRS‑IS passcode to use for login. If omitted the code will look for "
            "the APRS_PASSCODE environment variable and, failing that, compute "
            "the standard passcode from your base callsign."
        ),
    )

    # Arguments for APRS object beaconing
    parser.add_argument(
        "--object-name",
        default=None,
        help=(
            "Name of APRS object to beacon (max 9 characters). If provided, the BBS "
            "will periodically send an APRS object position on behalf of your callsign."
        ),
    )
    parser.add_argument(
        "--lat",
        default=None,
        help=(
            "Latitude for the APRS object in DDMM.mmN/S format (e.g. 4540.00N)."
        ),
    )
    parser.add_argument(
        "--lon",
        default=None,
        help=(
            "Longitude for the APRS object in DDDMM.mmE/W format (e.g. 00911.00E)."
        ),
    )
    parser.add_argument(
        "--comment",
        default="-APRS BBS disponibile",
        help=(
            "Comment to append to the APRS object (default: '-APRS BBS disponibile')."
        ),
    )
    parser.add_argument(
        "--object-interval",
        type=int,
        default=900,
        help=(
            "Interval in seconds between APRS object beacons (default: 900)."
        ),
    )
    parser.add_argument(
        "--symbol-table",
        default="/",
        help=(
            "Symbol table for the APRS object ('/' for primary, '\\' for secondary). "
            "Default is '/'."
        ),
    )
    parser.add_argument(
        "--symbol-code",
        default="-",
        help=(
            "Symbol code (single character) for the APRS object icon. Default '-' (house)."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Determine the passcode.  If the command line did not supply a value,
    # fall back to the APRS_PASSCODE environment variable.  A negative
    # number indicates an unverified or receive‑only mode and will be
    # ignored by the constructor, which will compute a passcode from
    # the base callsign.
    passcode = args.passcode
    if passcode is None:
        env_pass = os.getenv("APRS_PASSCODE")
        if env_pass is not None:
            try:
                passcode = int(env_pass)
            except ValueError:
                passcode = None
    # Create and start the BBS, passing through the chosen passcode.
    bbs = APRSBBS(
        callsign=args.callsign,
        server_host=args.server,
        port=args.port,
        filter_string=args.filter,
        passcode=passcode,
    )
    try:
        bbs.connect()
        # If object beacon parameters are provided, start the beacon thread
        if (
            args.object_name
            and args.lat
            and args.lon
        ):
            bbs.start_object_beacon(
                name=args.object_name,
                lat=args.lat,
                lon=args.lon,
                comment=args.comment,
                interval=args.object_interval,
                symbol_table=args.symbol_table,
                symbol_code=args.symbol_code,
            )
        # Keep the main thread alive while the receiver thread runs.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down BBS...")
    finally:
        bbs.close()


if __name__ == "__main__":
    main()