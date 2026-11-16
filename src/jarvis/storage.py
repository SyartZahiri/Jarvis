from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import data_dir
from .models import Message, ToolCall


SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _baseline(db: sqlite3.Connection) -> None:
    """Schema version 1. Idempotent so pre-migration databases are adopted safely."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            role TEXT NOT NULL, content TEXT NOT NULL,
            tool_calls TEXT NOT NULL DEFAULT '[]',
            tool_call_id TEXT, name TEXT, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, id);
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS automations (
            id TEXT PRIMARY KEY, prompt TEXT NOT NULL, interval_minutes INTEGER NOT NULL,
            next_run TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            last_result TEXT
        );
    """)


def _v2(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE conversations ADD COLUMN updated_at TEXT")
    db.execute("UPDATE conversations SET updated_at = COALESCE((SELECT MAX(created_at) "
               "FROM messages WHERE conversation_id = conversations.id), created_at)")
    db.execute("ALTER TABLE automations ADD COLUMN last_run TEXT")


MIGRATIONS = {1: _baseline, 2: _v2}


class Database:
    """SQLite store. Short-lived connections per operation keep worker threads independent."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "jarvis.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fts = False
        self._migrate()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Die Datenbank stammt aus einer neueren JARVIS-Version.")
            has_data = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='conversations'").fetchone()
            if version < SCHEMA_VERSION and has_data:
                backup = self.path.with_name(f"{self.path.stem}.v{version}.bak")
                if not backup.exists():
                    target_db = sqlite3.connect(backup)
                    try:
                        db.backup(target_db)
                    finally:
                        target_db.close()
            for target in range(version + 1, SCHEMA_VERSION + 1):
                MIGRATIONS[target](db)
                db.execute(f"PRAGMA user_version={target}")
            db.commit()
            db.execute("PRAGMA journal_mode=WAL")
            self.fts = self._ensure_fts(db)

    @staticmethod
    def _ensure_fts(db: sqlite3.Connection) -> bool:
        """Full-text index for memories; falls back to LIKE when SQLite lacks FTS5."""
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='memories_fts'").fetchone()
        try:
            db.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content, content='memories', content_rowid='rowid',
                    tokenize='unicode61 remove_diacritics 2');
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content)
                    VALUES ('delete', old.rowid, old.content);
                    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
                END;
            """)
        except sqlite3.OperationalError:
            return False
        if not exists:
            db.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
        return True

    # Conversations -------------------------------------------------------------------------

    def new_conversation(self, title: str = "Neues Gespräch") -> str:
        conversation_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as db:
            db.execute("INSERT INTO conversations (id, title, created_at, updated_at) "
                       "VALUES (?, ?, ?, ?)", (conversation_id, title[:120], now, now))
        return conversation_id

    def list_conversations(self, limit: int = 500) -> list[tuple[str, str]]:
        with self._connect() as db:
            return db.execute("SELECT id, title FROM conversations "
                              "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        title = " ".join(title.split())
        if not title:
            raise ValueError("Der Titel darf nicht leer sein.")
        with self._connect() as db:
            db.execute("UPDATE conversations SET title=? WHERE id=?", (title[:120], conversation_id))

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as db:
            db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            return db.execute("DELETE FROM conversations WHERE id=?",
                              (conversation_id,)).rowcount > 0

    def add_message(self, conversation_id: str, message: Message) -> None:
        calls = [dict(id=c.id, name=c.name, arguments=c.arguments, extra=c.extra)
                 for c in message.tool_calls]
        now = _now()
        with self._connect() as db:
            db.execute("""INSERT INTO messages
                (conversation_id, role, content, tool_calls, tool_call_id, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (conversation_id, message.role, message.content, json.dumps(calls),
                 message.tool_call_id, message.name, now))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
            if message.role == "user":
                count = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=? "
                                   "AND role='user'", (conversation_id,)).fetchone()[0]
                if count == 1:
                    title = " ".join(message.content.split())[:70]
                    db.execute("UPDATE conversations SET title=? WHERE id=?",
                               (title, conversation_id))

    def drop_unanswered(self, conversation_id: str) -> bool:
        """Remove the newest message if it is a user prompt nobody answered (retry)."""
        with self._connect() as db:
            row = db.execute("SELECT id, role FROM messages WHERE conversation_id=? "
                             "ORDER BY id DESC LIMIT 1", (conversation_id,)).fetchone()
            if not row or row[1] != "user":
                return False
            db.execute("DELETE FROM messages WHERE id=?", (row[0],))
            return True

    def get_messages(self, conversation_id: str) -> list[Message]:
        with self._connect() as db:
            rows = db.execute("""SELECT role, content, tool_calls, tool_call_id, name
                FROM messages WHERE conversation_id=? ORDER BY id""",
                (conversation_id,)).fetchall()
        return [Message(row[0], row[1], [ToolCall(**call) for call in json.loads(row[2])],
                        row[3], row[4]) for row in rows]

    # Memory --------------------------------------------------------------------------------

    def remember(self, content: str) -> str:
        value = content.strip()
        if not value or len(value) > 4000:
            raise ValueError("Die Erinnerung muss 1 bis 4000 Zeichen enthalten.")
        memory_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO memories (id, content, created_at) VALUES (?, ?, ?)",
                       (memory_id, value, _now()))
        return memory_id

    def recall(self, query: str, limit: int = 8) -> list[tuple[str, str]]:
        """Relevance-ranked search (FTS5/BM25, prefix match on every word); empty = newest."""
        limit = max(1, min(int(limit), 200))
        words = re.findall(r"\w+", query.lower())[:16]
        with self._connect() as db:
            if not words:
                return db.execute("SELECT id, content FROM memories "
                                  "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            if self.fts:
                expression = " OR ".join(f'"{word}"*' for word in words)
                return db.execute("""SELECT m.id, m.content FROM memories_fts
                    JOIN memories m ON m.rowid = memories_fts.rowid
                    WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts), m.created_at DESC
                    LIMIT ?""", (expression, limit)).fetchall()
            clauses = " OR ".join("content LIKE ? ESCAPE '!'" for _ in words)
            patterns = ["%" + w.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
                        for w in words]
            return db.execute(f"SELECT id, content FROM memories WHERE {clauses} "
                              "ORDER BY created_at DESC LIMIT ?", (*patterns, limit)).fetchall()

    def forget(self, memory_id: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    # Automations ---------------------------------------------------------------------------

    def add_automation(self, prompt: str, interval_minutes: int, next_run: str) -> str:
        if not prompt.strip() or not 1 <= interval_minutes <= 525600:
            raise ValueError("Prompt und Intervall (1 bis 525600 Minuten) sind erforderlich.")
        automation_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO automations (id, prompt, interval_minutes, next_run) "
                       "VALUES (?, ?, ?, ?)", (automation_id, prompt.strip(), interval_minutes,
                                               next_run))
        return automation_id

    def list_automations(self) -> list[tuple]:
        with self._connect() as db:
            return db.execute("SELECT id, prompt, interval_minutes, next_run, enabled, "
                              "last_result, last_run FROM automations ORDER BY next_run").fetchall()

    def due_automations(self, now: str) -> list[tuple]:
        with self._connect() as db:
            return db.execute("""SELECT id, prompt, interval_minutes, next_run FROM automations
                WHERE enabled=1 AND next_run<=? ORDER BY next_run""", (now,)).fetchall()

    def claim_automation(self, automation_id: str, expected_next_run: str,
                         lease_until: str) -> bool:
        """Atomically move next_run forward; only one process wins a due run."""
        with self._connect() as db:
            return db.execute("UPDATE automations SET next_run=? WHERE id=? AND next_run=? "
                              "AND enabled=1", (lease_until, automation_id,
                                                expected_next_run)).rowcount == 1

    def complete_automation(self, automation_id: str, next_run: str, result: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE automations SET next_run=?, last_result=?, last_run=? WHERE id=?",
                       (next_run, result[:4000], _now(), automation_id))

    def set_automation_enabled(self, automation_id: str, enabled: bool) -> None:
        with self._connect() as db:
            db.execute("UPDATE automations SET enabled=? WHERE id=?", (int(enabled), automation_id))

    def schedule_automation_now(self, automation_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE automations SET next_run=?, enabled=1 WHERE id=?",
                       (_now(), automation_id))

    def delete_automation(self, automation_id: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM automations WHERE id=?",
                              (automation_id,)).rowcount > 0

    # Export --------------------------------------------------------------------------------

    def export(self) -> dict:
        """Plain JSON snapshot of user data (no secrets are stored in this database)."""
        conversations = []
        for conversation_id, title in self.list_conversations(limit=1_000_000):
            conversations.append({"id": conversation_id, "title": title, "messages": [
                {"role": m.role, "content": m.content, "tool_call_id": m.tool_call_id,
                 "name": m.name, "tool_calls": [{"id": c.id, "name": c.name,
                                                 "arguments": c.arguments}
                                                for c in m.tool_calls]}
                for m in self.get_messages(conversation_id)]})
        with self._connect() as db:
            memories = [{"id": r[0], "content": r[1], "created_at": r[2]} for r in db.execute(
                "SELECT id, content, created_at FROM memories ORDER BY created_at")]
        automations = [{"id": a[0], "prompt": a[1], "interval_minutes": a[2], "enabled": bool(a[4])}
                       for a in self.list_automations()]
        return {"format": "jarvis-export", "version": 1, "exported_at": _now(),
                "conversations": conversations, "memories": memories,
                "automations": automations}

    def import_memories(self, items: list[dict]) -> int:
        """Import memories from an export; existing IDs are skipped."""
        added = 0
        with self._connect() as db:
            for item in items:
                content = str(item.get("content", "")).strip()
                if not content or len(content) > 4000:
                    continue
                memory_id = str(item.get("id") or uuid.uuid4())
                added += db.execute("INSERT OR IGNORE INTO memories (id, content, created_at) "
                                    "VALUES (?, ?, ?)", (memory_id, content,
                                                         str(item.get("created_at") or _now()))
                                    ).rowcount
        return added
