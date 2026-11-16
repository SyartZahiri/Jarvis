import asyncio
import json
import unittest

from helpers import WorkspaceTest, text

from jarvis import engine as engine_module
from jarvis.agents import run_agent
from jarvis.automation import AutomationRunner
from jarvis.models import Completion, ToolCall
from jarvis.tools import Tool, params


class EngineTest(WorkspaceTest):
    def test_tool_loop_streams_and_persists(self):
        self.permissions.set_enabled({"memory.read"})
        fake = self.use_fake_model(Completion("Moment.", [ToolCall("c1", "recall", {"query": ""})]),
                                   text("Fertig hier"))
        events = []
        conversation, answer = self.run_async(self.jarvis.ask("Hallo", on_event=events.append))
        self.assertEqual(answer, "Fertig hier")
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "conversation")
        self.assertIn("tool_start", kinds)
        self.assertEqual("".join(e["delta"] for e in events if e["type"] == "text").strip(),
                         "Moment. Fertig hier")
        roles = [m.role for m in self.database.get_messages(conversation)]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(fake.calls[1][0][-1].role, "tool")
        # Only enabled capabilities are exposed as tools.
        self.assertEqual({t["name"] for t in fake.calls[0][1]}, {"recall"})

    def test_relevant_memories_in_turn_context(self):
        self.database.remember("Der Nutzer heißt Ada und mag Tee")
        fake = self.use_fake_model(text("ok"))
        conversation, _ = self.run_async(self.jarvis.ask("Was mag ich trinken? Tee?"))
        sent = fake.calls[0][0]
        self.assertTrue(sent[-1].content.startswith("<kontext>\n"))
        self.assertTrue(sent[-1].content.endswith("</kontext>\n\nWas mag ich trinken? Tee?"))
        self.assertIn("Ada", sent[-1].content)
        self.assertIn("Zeit: ", sent[-1].content)
        # The stored message is exactly what the user typed.
        self.assertEqual(self.database.get_messages(conversation)[0].content,
                         "Was mag ich trinken? Tee?")
        self.permissions.set_enabled(set())
        fake = self.use_fake_model(text("ok"))
        self.run_async(self.jarvis.ask("Tee?"))
        self.assertNotIn("Ada", fake.calls[0][0][-1].content)

    def test_system_prompt_is_stable_for_prompt_caching(self):
        # Local runtimes reuse the processed prompt only while its prefix is identical,
        # so neither the clock nor recalled memories may appear in the system message.
        self.database.remember("Der Nutzer heißt Ada und mag Tee")
        fake = self.use_fake_model(text("ok"), text("ok"))
        conversation, _ = self.run_async(self.jarvis.ask("Tee?"))
        self.run_async(self.jarvis.ask("Kaffee?", conversation))
        first, second = fake.calls[0][0], fake.calls[1][0]
        self.assertEqual(first[0].content, second[0].content)
        self.assertNotIn("Ada", first[0].content)
        self.assertNotIn("Zeit:", first[0].content)
        # Earlier turns are resent without their old context block.
        self.assertEqual(second[1].content, "Tee?")
        self.assertIn("<kontext>", second[-1].content)

    def test_parallel_tools_run_concurrently_in_order(self):
        order = []

        async def slow(args):
            order.append(("start", args["n"]))
            await asyncio.sleep(0.05 if args["n"] == 1 else 0)
            order.append(("end", args["n"]))
            return args["n"]

        self.jarvis.tools.register(Tool("slow", "s", params({"n": {"type": "integer"}}, ["n"]),
                                        "compute.local", False, slow))
        self.use_fake_model(Completion("", [ToolCall("a", "slow", {"n": 1}),
                                            ToolCall("b", "slow", {"n": 2})]), text("ok"))
        conversation, _ = self.run_async(self.jarvis.ask("los"))
        self.assertEqual(order[:2], [("start", 1), ("start", 2)])
        tools = [m for m in self.database.get_messages(conversation) if m.role == "tool"]
        self.assertEqual([t.tool_call_id for t in tools], ["a", "b"])

    def test_approval_paths(self):
        self.permissions.set_enabled({"files.write"})
        write = ToolCall("1", "write_file", {"path": "a.txt", "content": "x"})
        denied = self.run_async(self.jarvis._execute(write, lambda tool, args: False))
        self.assertIn("abgelehnt", denied["error"])
        missing = self.run_async(self.jarvis._execute(write, None))
        self.assertIn("Freigabe", missing["error"])
        self.assertFalse((self.workspace / "a.txt").exists())

        async def approve(tool, args):
            return True

        allowed = self.run_async(self.jarvis._execute(write, approve))
        self.assertTrue(allowed["result"]["created"])

    def test_plugin_tools_cannot_skip_approval_for_side_effects(self):
        self.permissions.set_enabled({"files.write"})

        async def run(args):
            return "ran"

        tool = Tool("plugin_write", "w", params({}), "files.write", False, run,
                    source="plugin:evil")
        self.jarvis.tools.register(tool)
        result = self.run_async(self.jarvis._execute(ToolCall("1", "plugin_write", {}), None))
        self.assertIn("error", result)

    def test_argument_validation(self):
        self.permissions.set_enabled({"files.read", "memory.read"})
        cases = [
            ToolCall("1", "read_file", {"path": "a", "start_line": True}),   # bool is not int
            ToolCall("2", "read_file", {"path": "a", "start_line": 0}),      # below minimum
            ToolCall("3", "read_file", {"path": "a", "evil": 1}),            # unknown key
            ToolCall("4", "read_file", {}),                                   # missing key
            ToolCall("5", "recall", {"_invalid_json": "{x"}),                 # bad JSON
            ToolCall("6", "nope", {}),                                        # unknown tool
            ToolCall("7", "search_web", {"query": "x"}),                      # capability off
        ]
        for call in cases:
            with self.subTest(call=call.id):
                self.assertIn("error", self.run_async(self.jarvis._execute(call, None)))

    def test_round_limit_leaves_consistent_history(self):
        self.permissions.set_enabled({"memory.read"})
        loop = [Completion("", [ToolCall(f"c{i}", "recall", {"query": ""})])
                for i in range(engine_module.MAX_ROUNDS)]
        self.use_fake_model(*loop)
        conversation, answer = self.run_async(self.jarvis.ask("Endlos"))
        self.assertIn("Limit", answer)
        self.assertEqual(self.database.get_messages(conversation)[-1].role, "assistant")

    def test_cancellation_keeps_partial_text(self):
        started = asyncio.Event()

        async def hang(messages, tools):
            started.set()
            await asyncio.sleep(10)

        fake = self.use_fake_model(hang)

        async def complete(messages, tools=None, on_text=None):
            on_text("Teil")
            return await hang(messages, tools)

        fake.complete = complete

        async def scenario():
            task = asyncio.create_task(self.jarvis.ask("lang", on_event=lambda e: None))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.run_async(scenario())
        conversation = self.database.list_conversations()[0][0]
        last = self.database.get_messages(conversation)[-1]
        self.assertEqual(last.role, "assistant")
        self.assertIn("Teil", last.content)
        self.assertIn("abgebrochen", last.content)

    def test_empty_prompt_and_missing_profile(self):
        with self.assertRaises(ValueError):
            self.run_async(self.jarvis.ask("   "))
        with self.assertRaises(ValueError):
            self.run_async(self.jarvis.ask("Hallo"))


class AgentTest(WorkspaceTest):
    def test_agent_gets_only_read_tools(self):
        self.permissions.set_enabled({"files.read", "files.write", "agent.delegate"})
        (self.workspace / "notes.txt").write_text("Projektstand: grün", encoding="utf-8")
        fake = self.use_fake_model(
            Completion("", [ToolCall("r", "read_file", {"path": "notes.txt"}),
                            ToolCall("w", "write_file", {"path": "x", "content": "y"})]),
            text("Status ist grün."))
        result = self.run_async(run_agent(self.jarvis, "reviewer", "Prüfe notes.txt"))
        self.assertEqual(result["answer"], "Status ist grün.")
        offered = {t["name"] for t in fake.calls[0][1]}
        self.assertIn("read_file", offered)
        self.assertNotIn("write_file", offered)
        tool_results = [json.loads(m.content) for m in fake.calls[1][0] if m.role == "tool"]
        self.assertIn("grün", json.dumps(tool_results[0], ensure_ascii=False))
        self.assertIn("gesperrt", tool_results[1]["error"])
        self.assertFalse((self.workspace / "x").exists())
        with self.assertRaises(ValueError):
            self.run_async(run_agent(self.jarvis, "hacker", "x"))


class AutomationTest(WorkspaceTest):
    def test_due_job_runs_once_across_runners(self):
        automation = self.database.add_automation("Status?", 15, "2000-01-01T00:00:00+00:00")
        calls = []

        async def fake_ask(prompt, *args, **kwargs):
            calls.append(prompt)
            await asyncio.sleep(0.01)
            return "conversation", "OK: " + prompt

        self.jarvis.ask = fake_ask
        first, second = AutomationRunner(self.jarvis), AutomationRunner(self.jarvis)

        async def both():
            return await asyncio.gather(first.run_due(), second.run_due())

        results = self.run_async(both())
        self.assertEqual(calls, ["Status?"])
        self.assertEqual(sum(len(r) for r in results), 1)
        row = self.database.list_automations()[0]
        self.assertEqual(row[0], automation)
        self.assertEqual(row[5], "OK: Status?")
        self.assertIsNotNone(row[6])

    def test_failure_is_recorded(self):
        self.database.add_automation("x", 5, "2000-01-01T00:00:00+00:00")

        async def broken(*args, **kwargs):
            raise RuntimeError("kaputt")

        self.jarvis.ask = broken
        result = self.run_async(AutomationRunner(self.jarvis).run_due())
        self.assertIn("kaputt", result[0][1])


if __name__ == "__main__":
    unittest.main()
