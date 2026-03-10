"""Async Telnet BBS server with realtime and offline messaging."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .aprs_bridge import APRSBridge
from .ansi import BOLD, FG, banner, box, c, prompt
from .db import BBSDatabase, User
from .ham import HamService


class TelnetDecoder:
    """Strip telnet negotiation bytes from the stream."""

    NORMAL = 0
    IAC = 1
    IAC_OPT = 2
    SB = 3
    SB_IAC = 4

    def __init__(self) -> None:
        self.state = self.NORMAL

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            if self.state == self.NORMAL:
                if byte == 255:  # IAC
                    self.state = self.IAC
                else:
                    out.append(byte)
            elif self.state == self.IAC:
                if byte in (251, 252, 253, 254):
                    self.state = self.IAC_OPT
                elif byte == 250:  # SB
                    self.state = self.SB
                elif byte == 255:
                    out.append(255)
                    self.state = self.NORMAL
                else:
                    self.state = self.NORMAL
            elif self.state == self.IAC_OPT:
                self.state = self.NORMAL
            elif self.state == self.SB:
                if byte == 255:
                    self.state = self.SB_IAC
            elif self.state == self.SB_IAC:
                if byte == 240:  # SE
                    self.state = self.NORMAL
                elif byte != 255:
                    self.state = self.SB
        return bytes(out)


@dataclass
class ClientSession:
    username: str
    callsign: str | None
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    channel: str = "Lobby"
    decoder: TelnetDecoder = field(default_factory=TelnetDecoder)
    text_buffer: str = ""
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ui_enabled: bool = False
    ui_title: str = "Dashboard"
    ui_log: list[str] = field(default_factory=list)
    render_callback: Callable[["ClientSession"], Awaitable[None]] | None = None

    async def raw_write(self, text: str, newline: bool = True) -> None:
        payload = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        if newline:
            payload += "\r\n"
        async with self.write_lock:
            self.writer.write(payload.encode("utf-8", errors="ignore"))
            await self.writer.drain()

    async def write(self, text: str, newline: bool = True) -> None:
        if self.ui_enabled and newline and self.render_callback is not None:
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            self.ui_log.extend(normalized.split("\n"))
            self.ui_log = [ln for ln in self.ui_log if ln is not None]
            if len(self.ui_log) > 500:
                self.ui_log = self.ui_log[-500:]
            await self.render_callback(self)
            return
        await self.raw_write(text, newline=newline)

    async def read_line(self) -> str:
        while True:
            if "\n" in self.text_buffer:
                line, self.text_buffer = self.text_buffer.split("\n", 1)
                return line.strip()

            chunk = await self.reader.read(1024)
            if not chunk:
                raise EOFError
            filtered = self.decoder.feed(chunk)
            text = filtered.decode("utf-8", errors="ignore").replace("\r", "")
            self.text_buffer += text


class BBSServer:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 2323,
        data_dir: str = "./data",
        aprs_config: dict | None = None,
    ):
        self.host = host
        self.port = port
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = BBSDatabase(self.data_dir / "bbs.sqlite3")
        self.db.seed_defaults()
        self.ham = HamService()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.logger = logging.getLogger("gascast.server")

        self.online: dict[str, ClientSession] = {}
        self.channels: dict[str, set[str]] = {"Lobby": set()}
        self.aprs: APRSBridge | None = None
        self.aprs_config = aprs_config or {}
        self._configure_aprs()

    def find_online(self, username: str) -> ClientSession | None:
        uname = username.lower()
        for key, sess in self.online.items():
            if key.lower() == uname:
                return sess
        return None

    def _configure_aprs(self) -> None:
        enabled = bool(self.aprs_config.get("enabled"))
        callsign = str(self.aprs_config.get("callsign", "")).strip().upper()
        if not enabled or not callsign:
            return
        self.aprs = APRSBridge(
            db=self.db,
            callsign=callsign,
            server_host=str(self.aprs_config.get("server", "rotate.aprs2.net")),
            port=int(self.aprs_config.get("port", 14580)),
            filter_string=self.aprs_config.get("filter"),
            passcode=self.aprs_config.get("passcode"),
            object_name=self.aprs_config.get("object_name"),
            object_lat=self.aprs_config.get("object_lat"),
            object_lon=self.aprs_config.get("object_lon"),
            object_comment=str(self.aprs_config.get("object_comment", "-GasCast RF")),
            object_interval=int(self.aprs_config.get("position_interval", self.aprs_config.get("object_interval", 900))),
            object_symbol_table=str(self.aprs_config.get("object_symbol_table", "/")),
            object_symbol_code=str(self.aprs_config.get("object_symbol_code", "-")),
            on_private=self._on_aprs_private,
            on_group=self._on_aprs_group,
        )

    def _on_aprs_private(self, from_call: str, to_call: str, message: str, msg_id: int) -> None:
        if not self.loop:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._handle_aprs_private(from_call, to_call, message, msg_id),
            self.loop,
        )
        fut.add_done_callback(self._consume_future)

    def _on_aprs_group(self, group: str, from_call: str, message: str) -> None:
        if not self.loop:
            return
        fut = asyncio.run_coroutine_threadsafe(
            self._handle_aprs_group(group, from_call, message),
            self.loop,
        )
        fut.add_done_callback(self._consume_future)

    def _consume_future(self, future: asyncio.Future) -> None:
        try:
            _ = future.exception()
        except Exception:
            return

    async def _handle_aprs_private(self, from_call: str, to_call: str, message: str, msg_id: int) -> None:
        target = self.find_online(to_call)
        if not target:
            return
        await target.write(c(f"[APRS-IS] {from_call}: ", FG["bright_magenta"], BOLD) + message)
        self.db.mark_rf_message_delivered(msg_id)

    async def _handle_aprs_group(self, group: str, from_call: str, message: str) -> None:
        self.db.save_rf_group_message(group, from_call, message, "aprs-is")
        channel = group
        formatted = (
            c("[", FG["bright_black"])
            + c(channel, FG["bright_yellow"], BOLD)
            + c("] ", FG["bright_black"])
            + c(f"APRS-IS:{from_call}", FG["bright_magenta"], BOLD)
            + c(": ", FG["bright_black"])
            + message
        )
        await self.broadcast_channel(channel, formatted)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self.aprs:
            try:
                self.aprs.start()
                print(f"GasCast APRS-IS bridge active on {self.aprs.callsign}")
            except Exception as exc:
                self.aprs = None
                print(f"GasCast APRS-IS bridge disabled: {exc}")

        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        addr = ", ".join(str(sock.getsockname()) for sock in (server.sockets or []))
        print(f"GasCast listening on {addr}")
        try:
            async with server:
                await server.serve_forever()
        finally:
            if self.aprs:
                self.aprs.stop()

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        temp = ClientSession(username="guest", callsign=None, reader=reader, writer=writer)
        active_session: ClientSession | None = temp
        try:
            user = await self.auth_flow(temp)
            if user is None:
                await temp.write(c("Connection closed.", FG["bright_black"]))
                writer.close()
                await writer.wait_closed()
                return

            if self.find_online(user.username):
                await temp.write(c("User already online. Try again later.", FG["bright_red"]))
                writer.close()
                await writer.wait_closed()
                return

            session = ClientSession(
                username=user.username,
                callsign=user.callsign,
                reader=reader,
                writer=writer,
                decoder=temp.decoder,
                text_buffer=temp.text_buffer,
            )
            active_session = session
            self.online[user.username] = session
            self.channels.setdefault("Lobby", set()).add(user.username)
            session.ui_enabled = True
            session.render_callback = self.render_ui
            await self.show_main_screen(session)
            await self.deliver_pending_rf_to_telnet(session)
            await self.broadcast_channel(
                "Lobby",
                c(f"[+] {session.username} connected from {peer}", FG["bright_green"]),
                exclude=session.username,
            )

            while True:
                if session.ui_enabled:
                    await self.render_ui(session)
                else:
                    await session.write(prompt(session.username, session.channel), newline=False)
                line = await session.read_line()
                if not line:
                    continue
                done = await self.execute_command(session, line)
                if done:
                    break

        except EOFError:
            pass
        except ConnectionResetError:
            pass
        finally:
            await self.disconnect(active_session)
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def disconnect(self, session: ClientSession | None) -> None:
        if not session:
            return
        if session.username in self.online:
            del self.online[session.username]
        for users in self.channels.values():
            users.discard(session.username)
        empty_channels = [name for name, users in self.channels.items() if not users and name != "Lobby"]
        for name in empty_channels:
            del self.channels[name]
        if session.username != "guest":
            await self.broadcast_channel(
                session.channel,
                c(f"[-] {session.username} disconnected", FG["bright_red"]),
                exclude=session.username,
            )

    async def auth_flow(self, session: ClientSession) -> User | None:
        await session.write("\x1b[2J\x1b[H", newline=False)
        await session.write(banner())
        await session.write(
            box(
                "GasCast Telnet Node",
                [
                    "Enter callsign",
                    "Q to quit",
                ],
            )
        )
        for _ in range(5):
            await session.write(c("Callsign: ", FG["bright_yellow"], BOLD), newline=False)
            raw = (await session.read_line()).strip()
            if raw.lower() in {"q", "quit", "exit"}:
                return None
            callsign = self.normalize_callsign(raw)
            if not callsign:
                await session.write(c("Bad callsign.", FG["bright_red"]))
                continue
            return self.db.get_or_create_callsign_user(callsign)
        return None

    def normalize_callsign(self, value: str) -> str | None:
        callsign = value.strip().upper()
        if not callsign:
            return None
        if not re.fullmatch(r"[A-Z0-9]{3,10}(?:-[0-9]{1,2})?", callsign):
            return None
        return callsign

    async def deliver_pending_rf_to_telnet(self, session: ClientSession) -> None:
        pending = self.db.list_pending_rf_messages(session.username)
        for row in pending:
            await session.write(
                c(f"[APRS-IS] {row['from_call']}: ", FG["bright_magenta"], BOLD) + row["message"]
            )
            self.db.mark_rf_message_delivered(int(row["id"]))

    async def show_main_screen(self, session: ClientSession) -> None:
        unread_notes = sum(1 for n in self.db.list_notes(session.username) if n["read_at"] is None)
        unread_mail = sum(1 for m in self.db.list_mail(session.username) if m["read_at"] is None)
        session.ui_title = "Main Menu"
        session.ui_log.extend(
            [
                f"User: {session.username}  Callsign: {session.callsign or '-'}",
                f"Online now: {len(self.online)} users",
                f"Current channel: {session.channel}",
                f"Unread notes: {unread_notes}  |  Unread mail: {unread_mail}",
                "Quick command: help",
            ]
        )
        await self.render_ui(session)

    async def execute_command(self, session: ClientSession, line: str) -> bool:
        if line.startswith("/"):
            line = line[1:]

        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            return False

        cmd = parts[0].lower()
        args = parts[1:]

        shortcuts: dict[str, tuple[str, list[str]]] = {
            "0": ("help", []),
            "1": ("channels", []),
            "2": ("note", ["list"]),
            "3": ("mail", ["inbox"]),
            "4": ("board", ["list"]),
            "5": ("aprs-is", ["status"]),
            "6": ("cls", []),
        }
        if cmd in shortcuts:
            cmd, forced_args = shortcuts[cmd]
            args = forced_args

        if cmd in {"quit", "exit", "bye", "logout"}:
            await session.write(c("73 de GasCast. Disconnecting...", FG["bright_yellow"]))
            return True
        if cmd == "help":
            await self.cmd_help(session)
            return False
        if cmd == "cls":
            await self.show_main_screen(session)
            return False
        if cmd == "who":
            await self.cmd_who(session)
            return False
        if cmd == "users":
            await self.cmd_users(session)
            return False
        if cmd == "channels":
            await self.cmd_channels(session)
            return False
        if cmd == "join":
            await self.cmd_join(session, args)
            return False
        if cmd in {"say", "chat"}:
            await self.cmd_say(session, args)
            return False
        if cmd == "dm":
            await self.cmd_dm(session, args)
            return False
        if cmd == "note":
            await self.cmd_note(session, args)
            return False
        if cmd == "mail":
            await self.cmd_mail(session, args)
            return False
        if cmd == "board":
            await self.cmd_board(session, args)
            return False
        if cmd == "ham":
            await self.cmd_ham(session, args)
            return False
        if cmd in {"rf", "aprs", "aprs-is"}:
            await self.cmd_rf(session, args)
            return False

        await session.write(c("Unknown command. Use 'help'.", FG["bright_red"]))
        return False

    async def cmd_help(self, session: ClientSession) -> None:
        session.ui_title = "Help"
        lines = [
            "who | users | channels",
            "join <channel>                  -> switch realtime chat channel",
            "say <message>                   -> send message to channel",
            "dm <user> <message>             -> realtime direct message (offline -> note)",
            "note send <user>                -> asynchronous note (multiline)",
            "note list | note read <id>",
            "mail compose <user>             -> local mail (multiline)",
            "mail inbox | mail read <id>",
            "board list | board ls <board>",
            "board post <board> | board read <id>",
            "aprs-is status | aprs-is groups | aprs-is msg <call> <text>",
            "aprs-is gmsg <group> <text> | aprs-is gread <group> [limit]",
            "cls | quit",
        ]
        await session.write(box("Commands", lines))

    async def cmd_who(self, session: ClientSession) -> None:
        session.ui_title = "Online Users"
        rows = []
        for user, sess in sorted(self.online.items()):
            call = sess.callsign or "-"
            rows.append(f"{user:<16} [{call:<10}]  channel={sess.channel}")
        if not rows:
            rows = ["No users online."]
        await session.write(box("Online Users", rows))

    async def cmd_users(self, session: ClientSession) -> None:
        session.ui_title = "Users"
        users = self.db.list_users()
        lines = [f"{u.username:<16} callsign={u.callsign or '-'}" for u in users]
        await session.write(box("Registered Users", lines or ["No registered users."]))

    async def cmd_channels(self, session: ClientSession) -> None:
        session.ui_title = "Channels"
        lines = [f"{name:<16} users={len(users)}" for name, users in sorted(self.channels.items())]
        await session.write(box("Channels", lines))

    async def cmd_join(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Channels"
        if not args:
            await session.write(c("Usage: join <channel>", FG["bright_yellow"]))
            return
        new_channel = args[0]
        old = session.channel
        if old == new_channel:
            await session.write(c("You are already in this channel.", FG["bright_black"]))
            return

        self.channels.setdefault(new_channel, set()).add(session.username)
        self.channels.setdefault(old, set()).discard(session.username)
        session.channel = new_channel

        await self.broadcast_channel(old, c(f"[-] {session.username} left the channel", FG["bright_black"]), exclude=session.username)
        await self.broadcast_channel(new_channel, c(f"[+] {session.username} joined the channel", FG["bright_green"]), exclude=session.username)
        await session.write(c(f"Active channel: {new_channel}", FG["bright_green"]))

    async def cmd_say(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Chat"
        if not args:
            await session.write(c("Usage: say <message>", FG["bright_yellow"]))
            return
        message = " ".join(args)
        formatted = (
            c("[", FG["bright_black"]) +
            c(session.channel, FG["bright_yellow"], BOLD) +
            c("] ", FG["bright_black"]) +
            c(session.username, FG["bright_cyan"], BOLD) +
            c(": ", FG["bright_black"]) +
            message
        )
        await self.broadcast_channel(session.channel, formatted)
        if self.aprs and self.aprs.is_connected:
            sent = self.aprs.relay_group_from_bbs(session.channel.lower(), session.username, message)
            if sent > 0:
                self.db.save_rf_group_message(session.channel, session.username, message, "telnet")

    async def cmd_dm(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Direct Messages"
        if len(args) < 2:
            await session.write(c("Usage: dm <user> <message>", FG["bright_yellow"]))
            return
        target = args[0].upper()
        message = " ".join(args[1:])

        target_session = self.find_online(target)
        if target_session:
            await target_session.write(
                c(f"[DM] {session.username}: ", FG["bright_magenta"], BOLD) + message
            )
            await session.write(c("DM sent.", FG["bright_green"]))
        else:
            note_saved = False
            rf_saved = False
            if self.db.user_exists(target):
                self.db.save_note(session.username, target, "DM offline", message)
                note_saved = True

            if self.aprs and self.aprs.is_connected and self.normalize_callsign(target):
                self.db.queue_rf_message(session.username, target, message)
                self.aprs.send_direct_from_bbs(session.username, target, message)
                rf_saved = True

            if note_saved:
                await session.write(c("User offline: DM saved as note.", FG["bright_yellow"]))
            if rf_saved:
                await session.write(c("APRS-IS queue.", FG["bright_yellow"]))
            if note_saved or rf_saved:
                return
            else:
                await session.write(c("Recipient user does not exist.", FG["bright_red"]))

    async def cmd_note(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Notes"
        if not args:
            await session.write(c("Usage: note send|list|read", FG["bright_yellow"]))
            return
        sub = args[0].lower()
        if sub == "list":
            notes = self.db.list_notes(session.username)
            lines = []
            for note in notes[:20]:
                unread = "*" if note["read_at"] is None else " "
                lines.append(
                    f"{unread} #{note['id']:<4} from {note['sender']:<10} {note['subject'][:28]:<28} {note['created_at']}"
                )
            await session.write(box("Notes", lines or ["No notes."]))
            return

        if sub == "read":
            if len(args) < 2 or not args[1].isdigit():
                await session.write(c("Usage: note read <id>", FG["bright_yellow"]))
                return
            row = self.db.read_note(session.username, int(args[1]))
            if not row:
                await session.write(c("Note not found.", FG["bright_red"]))
                return
            await session.write(
                box(
                    f"Note #{row['id']}",
                    [
                        f"From: {row['sender']}",
                        f"Subject: {row['subject']}",
                        f"Date: {row['created_at']}",
                        "",
                        *row["body"].splitlines(),
                    ],
                )
            )
            return

        if sub == "send":
            if len(args) < 2:
                await session.write(c("Usage: note send <user>", FG["bright_yellow"]))
                return
            target = args[1]
            if not self.db.user_exists(target):
                await session.write(c("Recipient not found.", FG["bright_red"]))
                return
            await session.write(c("Subject: ", FG["bright_cyan"]), newline=False)
            subject = (await session.read_line()).strip() or "(no subject)"
            body = await self.collect_multiline(session, "Note body")
            self.db.save_note(session.username, target, subject, body)
            await session.write(c("Note saved.", FG["bright_green"]))
            target_online = self.find_online(target)
            if target_online:
                await target_online.write(
                    c(f"[NOTE] New note from {session.username}", FG["bright_magenta"])
                )
            return

        await session.write(c("Usage: note send|list|read", FG["bright_yellow"]))

    async def cmd_mail(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Mail"
        if not args:
            await session.write(c("Usage: mail compose|inbox|read", FG["bright_yellow"]))
            return

        sub = args[0].lower()
        if sub == "inbox":
            mails = self.db.list_mail(session.username)
            lines = []
            for mail in mails[:20]:
                unread = "*" if mail["read_at"] is None else " "
                lines.append(
                    f"{unread} #{mail['id']:<4} from {mail['sender']:<10} {mail['subject'][:28]:<28} {mail['created_at']}"
                )
            await session.write(box("Inbox", lines or ["No mail."]))
            return

        if sub == "read":
            if len(args) < 2 or not args[1].isdigit():
                await session.write(c("Usage: mail read <id>", FG["bright_yellow"]))
                return
            row = self.db.read_mail(session.username, int(args[1]))
            if not row:
                await session.write(c("Mail not found.", FG["bright_red"]))
                return
            await session.write(
                box(
                    f"Mail #{row['id']}",
                    [
                        f"From: {row['sender']}",
                        f"Subject: {row['subject']}",
                        f"Date: {row['created_at']}",
                        "",
                        *row["body"].splitlines(),
                    ],
                )
            )
            return

        if sub == "compose":
            if len(args) < 2:
                await session.write(c("Usage: mail compose <user>", FG["bright_yellow"]))
                return
            target = args[1]
            if not self.db.user_exists(target):
                await session.write(c("Recipient not found.", FG["bright_red"]))
                return
            await session.write(c("Mail subject: ", FG["bright_cyan"]), newline=False)
            subject = (await session.read_line()).strip() or "(no subject)"
            body = await self.collect_multiline(session, "Mail body")
            self.db.save_mail(session.username, target, subject, body)
            await session.write(c("Mail sent.", FG["bright_green"]))
            target_online = self.find_online(target)
            if target_online:
                await target_online.write(
                    c(f"[MAIL] New mail from {session.username}", FG["bright_magenta"])
                )
            return

        await session.write(c("Usage: mail compose|inbox|read", FG["bright_yellow"]))

    async def cmd_board(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Boards"
        if not args or args[0] in {"list", "boards"}:
            boards = self.db.list_boards()
            await session.write(box("Boards", boards or ["No boards available."]))
            return

        sub = args[0].lower()
        if sub == "ls":
            if len(args) < 2:
                await session.write(c("Usage: board ls <name>", FG["bright_yellow"]))
                return
            board = args[1]
            posts = self.db.list_board_posts(board)
            lines = [
                f"#{p['id']:<4} {p['title'][:34]:<34} by {p['sender']:<12} {p['created_at']}"
                for p in posts[:30]
            ]
            await session.write(box(f"Board {board}", lines or ["No posts."]))
            return

        if sub == "read":
            if len(args) < 2 or not args[1].isdigit():
                await session.write(c("Usage: board read <id>", FG["bright_yellow"]))
                return
            post = self.db.read_board_post(int(args[1]))
            if not post:
                await session.write(c("Post not found.", FG["bright_red"]))
                return
            await session.write(
                box(
                    f"Post #{post['id']} [{post['board']}]",
                    [
                        f"Title: {post['title']}",
                        f"Author: {post['sender']}",
                        f"Date: {post['created_at']}",
                        "",
                        *post["body"].splitlines(),
                    ],
                )
            )
            return

        if sub == "post":
            if len(args) < 2:
                await session.write(c("Usage: board post <board_name>", FG["bright_yellow"]))
                return
            board = args[1]
            await session.write(c("Post title: ", FG["bright_cyan"]), newline=False)
            title = (await session.read_line()).strip() or "(untitled)"
            body = await self.collect_multiline(session, "Post body")
            self.db.post_board(board, session.username, title, body)
            await session.write(c(f"Post published in board '{board}'.", FG["bright_green"]))
            return

        await session.write(c("Usage: board list|ls|post|read", FG["bright_yellow"]))

    async def cmd_ham(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "Ham Tools"
        if not args:
            await session.write(c("Usage: ham bands|prop|qcode|grayline|sun", FG["bright_yellow"]))
            return

        sub = args[0].lower()
        if sub == "bands":
            await session.write(box("HF Bands", self.ham.bands_table()))
            return

        if sub == "prop":
            if len(args) < 2:
                await session.write(c("Usage: ham prop <band>  ex: ham prop 20m", FG["bright_yellow"]))
                return
            try:
                f = self.ham.propagation(args[1])
            except ValueError:
                await session.write(c("Unsupported band.", FG["bright_red"]))
                return
            lines = [
                f"Band: {f.band}",
                f"Estimated index: {f.score}/100",
                f"Condition: {f.condition}",
                f"Suggestion: {f.recommendation}",
            ]
            await session.write(box("Propagation Forecast", lines))
            return

        if sub == "qcode":
            if len(args) < 2:
                await session.write(c("Usage: ham qcode <QRM|QRN|QRP|...>", FG["bright_yellow"]))
                return
            desc = self.ham.qcode(args[1])
            if not desc:
                await session.write(c("Q-code not found in the local dictionary.", FG["bright_red"]))
                return
            await session.write(box("Q-Code", [f"{args[1].upper()}: {desc}"]))
            return

        if sub == "grayline":
            await session.write(box("Grayline Tip", [self.ham.grayline_tip()]))
            return

        if sub == "sun":
            await session.write(box("Solar Snapshot", self.ham.solar_snapshot()))
            return

        await session.write(c("Usage: ham bands|prop|qcode|grayline|sun", FG["bright_yellow"]))

    async def cmd_rf(self, session: ClientSession, args: list[str]) -> None:
        session.ui_title = "APRS-IS"
        if not self.aprs:
            await session.write(c("APRS-IS off.", FG["bright_red"]))
            return
        if not args or args[0] == "status":
            state = "up" if self.aprs.is_connected else "down"
            lines = [
                f"Call: {self.aprs.callsign}",
                f"Link: {state}",
                f"Server: {self.aprs.server_host}:{self.aprs.port}",
            ]
            await session.write(box("APRS-IS", lines))
            return

        sub = args[0].lower()
        if sub == "groups":
            groups = sorted(self.aprs.chat_groups.keys())
            await session.write(box("APRS-IS Groups", groups or ["none"]))
            return

        if sub == "msg":
            if len(args) < 3:
                await session.write(c("Usage: aprs-is msg <call> <text>", FG["bright_yellow"]))
                return
            to_call = self.normalize_callsign(args[1])
            if not to_call:
                await session.write(c("Bad callsign.", FG["bright_red"]))
                return
            text = " ".join(args[2:])
            self.db.queue_rf_message(session.username, to_call, text)
            if self.aprs.is_connected:
                self.aprs.send_direct_from_bbs(session.username, to_call, text)
            await session.write(c("APRS-IS queued.", FG["bright_green"]))
            return

        if sub in {"gmsg", "groupmsg"}:
            if len(args) < 3:
                await session.write(c("Usage: aprs-is gmsg <group> <text>", FG["bright_yellow"]))
                return
            group = args[1].lower()
            text = " ".join(args[2:])
            if not self.aprs.is_connected:
                await session.write(c("APRS-IS link down.", FG["bright_red"]))
                return
            sent = self.aprs.relay_group_from_bbs(group, session.username, text)
            if sent > 0:
                self.db.save_rf_group_message(group, session.username, text, "telnet")
                await session.write(c(f"APRS-IS group sent ({sent}).", FG["bright_green"]))
            else:
                await session.write(c("APRS-IS group empty.", FG["bright_yellow"]))
            return

        if sub in {"gread", "ghistory", "history"}:
            if len(args) < 2:
                await session.write(c("Usage: aprs-is gread <group> [limit]", FG["bright_yellow"]))
                return
            group = args[1].lower()
            limit = 40
            if len(args) >= 3:
                if not args[2].isdigit():
                    await session.write(c("Limit must be numeric.", FG["bright_red"]))
                    return
                limit = int(args[2])
            rows = self.db.list_rf_group_messages(group, limit=limit)
            if not rows:
                await session.write(box(f"APRS-IS Group {group}", ["No stored messages."]))
                return
            lines: list[str] = []
            for row in reversed(rows):
                lines.append(
                    f"#{row['id']} {row['created_at']} [{row['source']}] {row['from_call']}: {row['message']}"
                )
            await session.write(box(f"APRS-IS Group {group}", lines))
            return

        if sub == "group":
            if len(args) >= 4 and args[1].lower() == "msg":
                group = args[2].lower()
                text = " ".join(args[3:])
                if not self.aprs.is_connected:
                    await session.write(c("APRS-IS link down.", FG["bright_red"]))
                    return
                sent = self.aprs.relay_group_from_bbs(group, session.username, text)
                if sent > 0:
                    self.db.save_rf_group_message(group, session.username, text, "telnet")
                    await session.write(c(f"APRS-IS group sent ({sent}).", FG["bright_green"]))
                else:
                    await session.write(c("APRS-IS group empty.", FG["bright_yellow"]))
                return
            await session.write(c("Usage: aprs-is group msg <group> <text>", FG["bright_yellow"]))
            return

        await session.write(c("Usage: aprs-is status|groups|msg|gmsg|gread", FG["bright_yellow"]))

    def _clean_ui_line(self, text: str) -> str:
        # Strip ANSI escapes before drawing inside fixed-width boxes.
        return re.sub(r"\x1b\[[0-9;]*m", "", text).strip()

    async def render_ui(self, session: ClientSession) -> None:
        width = 96
        link_state = "down"
        link_call = "-"
        if self.aprs:
            link_state = "up" if self.aprs.is_connected else "down"
            link_call = self.aprs.callsign

        status_lines = [
            f"Callsign: {session.username} | Channel: {session.channel} | Online: {len(self.online)}",
            f"APRS-IS: {link_state} ({link_call})",
        ]

        menu_lines = [
            "0) Help  1) Channels  2) Notes  3) Mail  4) Boards  5) APRS-IS  6) Main",
            "You can still type full commands (e.g. say, dm, board, note, mail, aprs-is).",
        ]

        content = [self._clean_ui_line(ln) for ln in session.ui_log if ln and ln.strip()]
        if not content:
            content = ["Welcome to GasCast."]
        content = content[-14:]

        frame = (
            "\x1b[2J\x1b[H"
            + banner()
            + "\n"
            + box("Status", status_lines, width=width)
            + "\n"
            + box(session.ui_title, content, width=width)
            + "\n"
            + box("Menu", menu_lines, width=width)
            + "\n"
            + prompt(session.username, session.channel)
        )
        await session.raw_write(frame, newline=False)

    async def collect_multiline(self, session: ClientSession, title: str) -> str:
        await session.write(c(f"{title}: end with a line containing only '.'", FG["bright_black"]))
        lines: list[str] = []
        while True:
            await session.write(c("| ", FG["bright_blue"]), newline=False)
            line = await session.read_line()
            if line == ".":
                break
            lines.append(line)
        return "\n".join(lines).strip() or "(empty)"

    async def broadcast_channel(self, channel: str, message: str, exclude: str | None = None) -> None:
        users = self.channels.get(channel, set()).copy()
        coros = []
        for username in users:
            if exclude and username == exclude:
                continue
            sess = self.online.get(username)
            if sess:
                coros.append(sess.write(message))
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
