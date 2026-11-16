import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jarvis import context
from jarvis.config import ConfigStore, ProviderProfile, SettingsStore
from jarvis.models import Message, ToolCall
from jarvis.secrets import SecretStore
from jarvis.storage import SCHEMA_VERSION, Database


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "j.sqlite3")

    def test_migrates_legacy_database_and_keeps_backup(self):
        legacy = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        connection.executescript("""
            CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                                        created_at TEXT NOT NULL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                tool_calls TEXT NOT NULL DEFAULT '[]', tool_call_id TEXT, name TEXT,
                created_at TEXT NOT NULL);
            CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT NOT NULL,
                                   created_at TEXT NOT NULL);
            CREATE TABLE automations (id TEXT PRIMARY KEY, prompt TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL, next_run TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, last_result TEXT);
            INSERT INTO conversations VALUES ('c1', 'Alt', '2025-01-01T00:00:00+00:00');
            INSERT INTO messages (conversation_id, role, content, tool_calls, created_at)
                VALUES ('c1', 'user', 'Hallo', '[{"id":"x","name":"recall","arguments":{}}]',
                        '2025-01-01T00:00:01+00:00');
            INSERT INTO memories VALUES ('m1', 'Lieblingsfarbe ist Grün', '2025-01-01');
        """)
        connection.commit()
        connection.close()
        db = Database(legacy)
        with db._connect() as raw:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertTrue((self.root / "legacy.v0.bak").exists())
        self.assertEqual(db.list_conversations(), [("c1", "Alt")])
        self.assertEqual(db.get_messages("c1")[0].tool_calls[0].extra, {})
        self.assertEqual(db.recall("farbe grün")[0][0], "m1")

    def test_relevance_search_and_forget(self):
        first = self.db.remember("Mein Zahnarzttermin ist am Dienstag")
        self.db.remember("Ich trinke Kaffee ohne Zucker")
        self.assertEqual(self.db.recall("Zahnarzt")[0][0], first)
        self.assertEqual(len(self.db.recall("Kaffee Dienstag")), 2)
        self.assertEqual(self.db.recall("%'\"*"), self.db.recall(""))  # nothing searchable
        self.assertEqual(self.db.recall('_ OR " NEAR('), [])  # FTS syntax is neutralised
        self.assertTrue(self.db.forget(first))
        self.assertEqual(self.db.recall("Zahnarzt"), [])
        with self.assertRaises(ValueError):
            self.db.remember("   ")

    def test_conversation_lifecycle_and_export(self):
        conversation = self.db.new_conversation()
        self.db.add_message(conversation, Message("user", "Plane\n meine Woche"))
        call = ToolCall("1", "recall", {"query": ""}, {"thoughtSignature": "sig"})
        self.db.add_message(conversation, Message("assistant", "", [call]))
        self.assertEqual(self.db.list_conversations()[0][1], "Plane meine Woche")
        self.assertEqual(self.db.get_messages(conversation)[1].tool_calls[0].extra["thoughtSignature"],
                         "sig")
        self.db.rename_conversation(conversation, "  Woche  ")
        self.assertEqual(self.db.list_conversations()[0][1], "Woche")
        self.db.remember("Notiz")
        exported = self.db.export()
        self.assertEqual(exported["format"], "jarvis-export")
        self.assertEqual(len(exported["conversations"][0]["messages"]), 2)
        other = Database(self.root / "other.sqlite3")
        self.assertEqual(other.import_memories(exported["memories"]), 1)
        self.assertEqual(other.import_memories(exported["memories"]), 0)
        self.assertTrue(self.db.delete_conversation(conversation))
        self.assertEqual(self.db.list_conversations(), [])

    def test_automation_claim_is_exclusive(self):
        automation = self.db.add_automation("Bericht", 10, "2000-01-01T00:00:00+00:00")
        due = self.db.due_automations("2001-01-01T00:00:00+00:00")
        self.assertEqual(len(due), 1)
        self.assertTrue(self.db.claim_automation(automation, due[0][3], "2999-01-01"))
        self.assertFalse(self.db.claim_automation(automation, due[0][3], "2999-01-01"))


class ContextTest(unittest.TestCase):
    def test_repair_closes_missing_results_and_drops_orphans(self):
        history = [Message("user", "a"),
                   Message("assistant", "", [ToolCall("1", "recall", {}),
                                             ToolCall("2", "recall", {})]),
                   Message("tool", "{}", tool_call_id="1", name="recall"),
                   Message("tool", "{}", tool_call_id="zzz", name="recall"),
                   Message("user", "b")]
        repaired = context.repair(history)
        self.assertEqual([m.role for m in repaired],
                         ["user", "assistant", "tool", "tool", "user"])
        self.assertEqual(repaired[3].tool_call_id, "2")
        self.assertIn("Abgebrochen", repaired[3].content)

    def test_fit_keeps_whole_recent_turns(self):
        history = []
        for index in range(30):
            history += [Message("user", f"frage {index} " + "x" * 400),
                        Message("assistant", "", [ToolCall(str(index), "read_file", {})]),
                        Message("tool", "y" * 5000, tool_call_id=str(index), name="read_file"),
                        Message("assistant", f"antwort {index}")]
        fitted = context.fit(history, 12_000)
        self.assertEqual(fitted[0].role, "user")
        self.assertTrue(fitted[-1].content.startswith("antwort 29"))
        self.assertLess(sum(context.size(m) for m in fitted), 12_000 + 6000)
        self.assertIn("gekürzt", next(m for m in fitted if m.role == "tool").content)
        # The newest turn is never dropped, even if it alone exceeds the budget.
        huge = [Message("user", "z" * 50_000)]
        self.assertEqual(len(context.fit(huge, 4000)), 1)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_profile_validation(self):
        for endpoint in ("http://example.org/v1", "https://user:pw@example.org",
                         "https://example.org/v1?key=1", "ftp://example.org"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ProviderProfile.create("Bad", "compatible", "m", endpoint)
        local = ProviderProfile.create("Lokal", "compatible", "m", "http://127.0.0.1:1234/v1")
        self.assertFalse(local.needs_key())
        self.assertTrue(ProviderProfile.create("C", "anthropic", "m").needs_key())
        with self.assertRaises(ValueError):
            ProviderProfile.create("T", "openai", "m", temperature=3)

    def test_old_config_without_new_fields_loads(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"version": 1, "active_profile": "a", "profiles": [
            {"id": "a", "name": "Alt", "kind": "ollama", "model": "llama3",
             "endpoint": "http://127.0.0.1:11434", "future_field": 1}]}), encoding="utf-8")
        profiles, active = ConfigStore(path).load()
        self.assertEqual(active, "a")
        self.assertEqual(profiles[0].max_context_chars, 60_000)

    def test_settings_validation(self):
        store = SettingsStore(self.root / "settings.json")
        self.assertEqual(store.get("search_provider"), "brave")
        store.update(search_provider="searxng", searxng_url="http://localhost:8888/")
        self.assertEqual(store.get("searxng_url"), "http://localhost:8888")
        for bad in ({"search_provider": "google"}, {"searxng_url": "http://example.org"},
                    {"voice_seconds": 99}, {"unknown": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.update(**bad)

    @unittest.skipUnless(os.name == "nt", "DPAPI ist nur unter Windows verfügbar")
    def test_dpapi_secret_roundtrip_is_not_plaintext(self):
        store = SecretStore(self.root / "secrets.json")
        store.set("provider:x", "secret-value-123")
        self.assertNotIn("secret-value-123", store.path.read_text(encoding="utf-8"))
        self.assertEqual(store.get("provider:x"), "secret-value-123")
        store.delete("provider:x")
        self.assertIsNone(store.get("provider:x"))


if __name__ == "__main__":
    unittest.main()
