"""SQLite persistence for users, messages, mail and boards."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@dataclass
class User:
    username: str
    callsign: str | None


class BBSDatabase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                callsign TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );

            CREATE TABLE IF NOT EXISTS mails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );

            CREATE TABLE IF NOT EXISTS board_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board TEXT NOT NULL,
                sender TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rf_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_call TEXT NOT NULL,
                to_call TEXT NOT NULL,
                message TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rf_group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                from_call TEXT NOT NULL,
                message TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def user_exists(self, username: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)
        )
        return cur.fetchone() is not None

    def create_user(self, username: str, password: str, callsign: str | None = None) -> bool:
        if self.user_exists(username):
            return False
        self.conn.execute(
            "INSERT INTO users(username, password_hash, callsign, created_at) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), callsign, utc_now()),
        )
        self.conn.commit()
        return True

    def get_user(self, username: str) -> User | None:
        cur = self.conn.execute(
            "SELECT username, callsign FROM users WHERE lower(username)=lower(?)",
            (username,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return User(username=row["username"], callsign=row["callsign"])

    def get_or_create_callsign_user(self, callsign: str) -> User:
        normalized = callsign.upper()
        user = self.get_user(normalized)
        if user:
            return user
        # Password is never used in callsign-only telnet mode.
        self.create_user(normalized, f"autologin::{normalized}", normalized)
        created = self.get_user(normalized)
        if created:
            return created
        return User(username=normalized, callsign=normalized)

    def authenticate(self, username: str, password: str) -> User | None:
        cur = self.conn.execute(
            "SELECT username, callsign, password_hash FROM users WHERE lower(username)=lower(?)",
            (username,),
        )
        row = cur.fetchone()
        if not row:
            return None
        if row["password_hash"] != hash_password(password):
            return None
        return User(username=row["username"], callsign=row["callsign"])

    def list_users(self) -> list[User]:
        cur = self.conn.execute("SELECT username, callsign FROM users ORDER BY username")
        return [User(username=r["username"], callsign=r["callsign"]) for r in cur.fetchall()]

    def save_note(self, sender: str, recipient: str, subject: str, body: str) -> None:
        self.conn.execute(
            "INSERT INTO notes(sender, recipient, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (sender, recipient, subject, body, utc_now()),
        )
        self.conn.commit()

    def list_notes(self, username: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT id, sender, subject, created_at, read_at FROM notes WHERE lower(recipient)=lower(?) ORDER BY id DESC",
            (username,),
        )
        return cur.fetchall()

    def read_note(self, username: str, note_id: int) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM notes WHERE id=? AND lower(recipient)=lower(?)", (note_id, username)
        )
        row = cur.fetchone()
        if row and row["read_at"] is None:
            self.conn.execute("UPDATE notes SET read_at=? WHERE id=?", (utc_now(), note_id))
            self.conn.commit()
            cur2 = self.conn.execute(
                "SELECT * FROM notes WHERE id=? AND lower(recipient)=lower(?)",
                (note_id, username),
            )
            return cur2.fetchone()
        return row

    def save_mail(self, sender: str, recipient: str, subject: str, body: str) -> None:
        self.conn.execute(
            "INSERT INTO mails(sender, recipient, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (sender, recipient, subject, body, utc_now()),
        )
        self.conn.commit()

    def list_mail(self, username: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT id, sender, subject, created_at, read_at FROM mails WHERE lower(recipient)=lower(?) ORDER BY id DESC",
            (username,),
        )
        return cur.fetchall()

    def read_mail(self, username: str, mail_id: int) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM mails WHERE id=? AND lower(recipient)=lower(?)", (mail_id, username)
        )
        row = cur.fetchone()
        if row and row["read_at"] is None:
            self.conn.execute("UPDATE mails SET read_at=? WHERE id=?", (utc_now(), mail_id))
            self.conn.commit()
            cur2 = self.conn.execute(
                "SELECT * FROM mails WHERE id=? AND lower(recipient)=lower(?)",
                (mail_id, username),
            )
            return cur2.fetchone()
        return row

    def post_board(self, board: str, sender: str, title: str, body: str) -> None:
        self.conn.execute(
            "INSERT INTO board_posts(board, sender, title, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (board, sender, title, body, utc_now()),
        )
        self.conn.commit()

    def list_board_posts(self, board: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT id, sender, title, created_at FROM board_posts WHERE lower(board)=lower(?) ORDER BY id DESC",
            (board,),
        )
        return cur.fetchall()

    def read_board_post(self, post_id: int) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM board_posts WHERE id=?", (post_id,))
        return cur.fetchone()

    def list_boards(self) -> list[str]:
        cur = self.conn.execute("SELECT DISTINCT board FROM board_posts ORDER BY board")
        return [row[0] for row in cur.fetchall()]

    def queue_rf_message(self, from_call: str, to_call: str, message: str) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO rf_messages(from_call, to_call, message, delivered, created_at) VALUES (?, ?, ?, 0, ?)",
                (from_call.upper(), to_call.upper(), message, utc_now()),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def list_pending_rf_messages(self, to_call: str) -> list[sqlite3.Row]:
        with self.lock:
            cur = self.conn.execute(
                "SELECT id, from_call, to_call, message, created_at FROM rf_messages WHERE delivered=0 AND upper(to_call)=upper(?) ORDER BY id",
                (to_call,),
            )
            return cur.fetchall()

    def mark_rf_message_delivered(self, msg_id: int) -> None:
        with self.lock:
            self.conn.execute("UPDATE rf_messages SET delivered=1 WHERE id=?", (msg_id,))
            self.conn.commit()

    def mark_rf_messages_delivered_by_match(self, from_call: str, to_call: str, message: str) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE rf_messages SET delivered=1 WHERE delivered=0 AND upper(from_call)=upper(?) AND upper(to_call)=upper(?) AND message=?",
                (from_call, to_call, message),
            )
            self.conn.commit()

    def save_rf_group_message(self, group: str, from_call: str, message: str, source: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO rf_group_messages(group_name, from_call, message, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (group.lower(), from_call.upper(), message, source.lower(), utc_now()),
            )
            self.conn.commit()

    def list_rf_group_messages(self, group: str, limit: int = 50) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 200))
        with self.lock:
            cur = self.conn.execute(
                "SELECT id, group_name, from_call, message, source, created_at FROM rf_group_messages WHERE lower(group_name)=lower(?) ORDER BY id DESC LIMIT ?",
                (group, safe_limit),
            )
            return cur.fetchall()

    def seed_defaults(self) -> None:
        if not self.user_exists("sysop"):
            self.create_user("sysop", "sysop", "IU0SYS")

        if not self.list_board_posts("announcements"):
            self.post_board(
                "announcements",
                "sysop",
                "Welcome to GasCast",
                "GasCast supports real-time chat, offline notes, local mail, and ham radio tools.",
            )
        if not self.list_board_posts("radio"):
            self.post_board(
                "radio",
                "sysop",
                "Propagation and bands",
                "Use 'ham prop 20m' and 'ham bands' to get started.",
            )

    def close(self) -> None:
        self.conn.close()
