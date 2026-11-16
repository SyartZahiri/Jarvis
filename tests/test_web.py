"""The desktop server: access control, the event stream, approvals and first-run setup.

Everything runs over real HTTP on 127.0.0.1 with a scripted model, so these tests cover the
same path the app window uses.
"""
import json
import queue
import threading
import unittest

import httpx

from helpers import WorkspaceTest, text

from jarvis.models import Completion, ToolCall
from jarvis.webapp import JarvisWeb


class WebTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.web = JarvisWeb(self.jarvis)
        self.origin = self.web.start(0)
        self.addCleanup(self.web.close)
        self.client = httpx.Client(base_url=self.origin, timeout=10)
        self.addCleanup(self.client.close)
        self.launch_token = self.web.token
        response = self.client.get(f"/?k={self.launch_token}")   # one-time launch exchange
        self.assertEqual(response.status_code, 303)
        self.client.cookies.set("jarvis_session", response.cookies["jarvis_session"])

    def post(self, path, body=None, **headers):
        merged = {"X-Jarvis": "1", "Origin": self.origin, **headers}
        return self.client.post(path, json=body or {}, headers=merged)

    def events(self):
        """Collect stream events on a background thread; returns the queue."""
        out: queue.Queue = queue.Queue()
        ready = threading.Event()

        def read():
            with httpx.Client(base_url=self.origin, timeout=20,
                              cookies=self.client.cookies) as stream_client:
                with stream_client.stream("GET", "/api/events") as response:
                    for line in response.iter_lines():
                        if line.startswith("retry"):
                            ready.set()
                        if line.startswith("data: "):
                            out.put(json.loads(line[6:]))

        threading.Thread(target=read, daemon=True).start()
        self.assertTrue(ready.wait(5))
        return out

    @staticmethod
    def until(events, kind, timeout=10):
        seen = []
        while True:
            event = events.get(timeout=timeout)
            seen.append(event)
            if event["type"] == kind:
                return event, seen

    # Access control --------------------------------------------------------------------------

    def test_session_cookie_is_required(self):
        anonymous = httpx.Client(base_url=self.origin)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get("/").status_code, 403)
        self.assertEqual(anonymous.get("/api/state").status_code, 403)
        self.assertEqual(anonymous.get("/?k=wrong").status_code, 403)
        self.assertEqual(anonymous.get("/?k=%C3%A4").status_code, 403)
        self.assertEqual(anonymous.get(f"/?k={self.launch_token}").status_code, 403)  # used
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["x-frame-options"], "DENY")

    def test_foreign_host_header_is_rejected(self):
        # DNS rebinding: a page on evil.example resolving to 127.0.0.1 sends its own Host.
        response = self.client.get("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 421)

    def test_state_changes_need_same_origin_and_custom_header(self):
        self.assertEqual(self.client.post("/api/cancel", json={}).status_code, 403)
        self.assertEqual(self.post("/api/cancel", Origin="https://evil.example").status_code, 403)
        self.assertEqual(self.post("/api/cancel", **{"Sec-Fetch-Site": "cross-site"}).status_code,
                         403)
        self.assertEqual(self.post("/api/cancel").status_code, 200)

    def test_only_known_static_files_are_served(self):
        for path in ("/webapp.py", "/../config.py", "/web/app.js", "/%2e%2e/engine.py"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertEqual(self.client.get("/app.js").status_code, 200)
        self.assertEqual(self.client.get("/icon.png").headers["content-type"], "image/png")

    def test_api_keys_never_leave_the_server(self):
        saved = self.post("/api/profiles/save", {"name": "Cloud", "kind": "openai",
                                                 "model": "gpt-test", "key": "sk-geheim-123"})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.secrets.get(f"provider:{saved.json()['id']}"), "sk-geheim-123")
        state = self.client.get("/api/state").text
        self.assertNotIn("sk-geheim-123", state)
        self.assertTrue(json.loads(state)["profiles"][0]["has_key"])

    def test_profile_without_required_key_is_refused(self):
        response = self.post("/api/profiles/save", {"kind": "anthropic", "model": "claude-x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("API-Key", response.json()["error"])

    # Conversation ----------------------------------------------------------------------------

    def test_ask_streams_events_and_persists(self):
        self.use_fake_model(text("Hallo zurück"))
        events = self.events()
        started = self.post("/api/ask", {"prompt": "Hallo"})
        self.assertEqual(started.status_code, 200, started.text)
        end, seen = self.until(events, "run_end")
        self.assertEqual(end["state"], "done")
        kinds = [e["type"] for e in seen]
        self.assertIn("run_conversation", kinds)
        streamed = "".join(e["delta"] for e in seen if e["type"] == "text")
        self.assertEqual(streamed.strip(), "Hallo zurück")
        conversation = self.client.get(f"/api/conversations/{end['conversation']}").json()
        self.assertEqual([m["role"] for m in conversation["messages"]], ["user", "assistant"])

    def test_second_request_while_running_is_refused(self):
        gate = threading.Event()

        async def slow(messages, tools):
            import asyncio
            while not gate.is_set():
                await asyncio.sleep(0.02)
            return text("fertig")

        self.use_fake_model(slow)
        events = self.events()
        self.assertEqual(self.post("/api/ask", {"prompt": "eins"}).status_code, 200)
        self.assertEqual(self.post("/api/ask", {"prompt": "zwei"}).status_code, 409)
        gate.set()
        self.assertEqual(self.until(events, "run_end")[0]["state"], "done")

    def test_approval_round_trip_writes_file(self):
        self.permissions.set_enabled({"files.write"})
        self.use_fake_model(Completion("", [ToolCall("w1", "write_file",
                                                     {"path": "notiz.txt", "content": "hallo"})]),
                            text("erledigt"))
        events = self.events()
        self.post("/api/ask", {"prompt": "Schreib eine Notiz"})
        approval, _ = self.until(events, "approval")
        self.assertEqual(approval["tool"], "write_file")
        self.assertIn("+hallo", approval["detail"])
        self.assertEqual(self.post("/api/approval", {"id": approval["id"], "approve": True})
                         .status_code, 200)
        end, _ = self.until(events, "run_end")
        self.assertEqual(end["state"], "done")
        self.assertEqual((self.workspace / "notiz.txt").read_text(encoding="utf-8"), "hallo")

    def test_denied_approval_leaves_files_alone(self):
        self.permissions.set_enabled({"files.write"})
        self.use_fake_model(Completion("", [ToolCall("w1", "write_file",
                                                     {"path": "x.txt", "content": "nein"})]),
                            text("verstanden"))
        events = self.events()
        self.post("/api/ask", {"prompt": "Schreib"})
        approval, _ = self.until(events, "approval")
        self.post("/api/approval", {"id": approval["id"], "approve": False})
        self.until(events, "run_end")
        self.assertFalse((self.workspace / "x.txt").exists())

    def test_retry_drops_only_an_unanswered_prompt(self):
        conversation = self.database.new_conversation()
        from jarvis.models import Message
        self.database.add_message(conversation, Message("user", "Frage"))
        self.database.add_message(conversation, Message("assistant", "Antwort"))
        self.post("/api/conversations/drop-last", {"id": conversation})
        self.assertEqual(len(self.database.get_messages(conversation)), 2)
        self.database.add_message(conversation, Message("user", "Nachfrage"))
        self.post("/api/conversations/drop-last", {"id": conversation})
        self.assertEqual([m.content for m in self.database.get_messages(conversation)],
                         ["Frage", "Antwort"])

    # Setup -----------------------------------------------------------------------------------

    def test_ollama_status_is_reported_either_way(self):
        status = self.client.get("/api/setup/ollama").json()
        for key in ("found", "running", "models", "recommended", "ram_gb"):
            self.assertIn(key, status)
        self.assertIn(status["recommended"], {"qwen3:1.7b", "qwen3:4b", "qwen3:8b", "qwen3:14b"})

    def test_setup_profile_becomes_active(self):
        self.post("/api/profiles/save", {"name": "Erst", "kind": "ollama", "model": "a"})
        self.post("/api/profiles/save", {"name": "Lokal", "kind": "ollama", "model": "qwen3:4b",
                                         "think": False, "activate": True})
        profiles, active = self.config.load()
        current = next(p for p in profiles if p.id == active)
        self.assertEqual((current.name, current.think), ("Lokal", False))

    def test_permission_preset_is_validated(self):
        self.assertEqual(self.post("/api/permissions", {"enabled": ["files.read"]}).status_code,
                         200)
        self.assertEqual(self.permissions.enabled(), {"files.read"})
        self.assertEqual(self.post("/api/permissions", {"enabled": ["root.everything"]})
                         .status_code, 400)


if __name__ == "__main__":
    unittest.main()
