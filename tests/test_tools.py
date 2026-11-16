import json
import os
import shutil
import sys
import unittest
from pathlib import Path

import httpx
from helpers import WorkspaceTest

from jarvis.approval import describe
from jarvis.markdown import plain, render
from jarvis.models import Completion, ToolCall
from jarvis.plugins import PluginManager
from jarvis.tools_web import html_to_text, register_web_tools


async def allow(tool, args):
    return True


class FileToolsTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.permissions.set_enabled({"files.read", "files.write", "system.execute"})

    def call(self, name, args, approve=allow):
        return self.run_async(self.jarvis._execute(ToolCall("t", name, args), approve))

    def test_read_with_line_window(self):
        (self.workspace / "big.txt").write_text("\n".join(f"zeile {i}" for i in range(1, 501)),
                                                encoding="utf-8")
        result = self.call("read_file", {"path": "big.txt", "start_line": 10, "max_lines": 3})
        data = result["result"]
        self.assertEqual((data["start_line"], data["end_line"], data["total_lines"]), (10, 12, 500))
        self.assertIn("zeile 12", data["content"])
        self.assertFalse(data["complete"])
        (self.workspace / "bin.dat").write_bytes(b"\x00\x01binary")
        self.assertIn("Binär", self.call("read_file", {"path": "bin.dat"})["error"])

    def test_edit_requires_unique_match(self):
        target = self.workspace / "app.py"
        target.write_text("x = 1\ny = 1\n", encoding="utf-8")
        ambiguous = self.call("edit_file", {"path": "app.py", "old_text": "= 1",
                                            "new_text": "= 2"})
        self.assertIn("2-mal", ambiguous["error"])
        ok = self.call("edit_file", {"path": "app.py", "old_text": "y = 1", "new_text": "y = 3"})
        self.assertEqual(ok["result"]["replacements"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\ny = 3\n")
        self.call("edit_file", {"path": "app.py", "old_text": "= ", "new_text": "== ",
                                "replace_all": True})
        self.assertEqual(target.read_text(encoding="utf-8"), "x == 1\ny == 3\n")

    def test_edit_ignores_copied_line_numbers(self):
        target = self.workspace / "einkauf.txt"
        target.write_text("Milch\nBrot\nEier\n", encoding="utf-8")
        args = {"path": "einkauf.txt", "old_text": "    3  Eier",
                "new_text": "    3  Eier\n    4  Käse"}
        view = describe("edit_file", "files.write", args, self.workspace)
        self.assertIn("+Käse", view.detail)
        self.assertNotIn("    4", view.detail)
        self.assertIn("result", self.call("edit_file", args))
        self.assertEqual(target.read_text(encoding="utf-8"), "Milch\nBrot\nEier\nKäse\n")

    def test_search_skips_vendor_dirs(self):
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "a.py").write_text("def Needle():\n    pass\n", encoding="utf-8")
        (self.workspace / "node_modules").mkdir()
        (self.workspace / "node_modules" / "b.py").write_text("Needle", encoding="utf-8")
        result = self.call("search_files", {"query": "needle", "pattern": "*.py"})["result"]
        self.assertEqual([(m["path"], m["line"]) for m in result["matches"]], [("src/a.py", 1)])
        regex = self.call("search_files", {"query": r"def \w+\(", "regex": True})["result"]
        self.assertEqual(len(regex["matches"]), 1)

    def test_delete_goes_to_trash_and_move(self):
        (self.workspace / "old.txt").write_text("weg", encoding="utf-8")
        moved = self.call("move_path", {"source": "old.txt", "destination": "archiv/alt.txt"})
        self.assertEqual(moved["result"]["to"], "archiv/alt.txt")
        trashed = self.call("delete_path", {"path": "archiv"})["result"]
        self.assertFalse((self.workspace / "archiv").exists())
        self.assertTrue((self.workspace / trashed["restore_from"] / "alt.txt").exists())
        self.assertIn("error", self.call("delete_path", {"path": "."}))

    def test_paths_cannot_escape_workspace(self):
        for name, args in (("read_file", {"path": "../secret.txt"}),
                           ("write_file", {"path": "../../x.txt", "content": "x"}),
                           ("list_files", {"path": str(Path(self.root).resolve())}),
                           ("move_path", {"source": "a", "destination": "../b"})):
            with self.subTest(name=name):
                self.assertIn("error", self.call(name, args))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_escape_blocked(self):
        outside = self.root / "outside.txt"
        outside.write_text("geheim", encoding="utf-8")
        try:
            (self.workspace / "link.txt").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink-Erstellung nicht erlaubt")
        self.assertIn("außerhalb", self.call("read_file", {"path": "link.txt"})["error"])

    def test_run_command_captures_output(self):
        result = self.call("run_command", {"argv": [sys.executable, "-c",
                                                    "print('hallo'); import sys; sys.exit(3)"]})
        self.assertEqual(result["result"]["exit_code"], 3)
        self.assertIn("hallo", result["result"]["stdout"])
        timeout = self.call("run_command", {"argv": [sys.executable, "-c",
                                                     "import time; time.sleep(5)"],
                                            "timeout_seconds": 1})
        self.assertIn("1 Sekunden", timeout["error"])

    def test_planted_program_in_workspace_is_not_preferred(self):
        from jarvis.tools_files import resolve_executable
        name = "python.exe" if sys.platform == "win32" else "python3"
        (self.workspace / name).write_bytes(b"fake")
        old = os.getcwd()
        os.chdir(self.workspace)
        try:
            try:
                resolved = resolve_executable(Path(name).stem, self.workspace)
            except ValueError:
                return  # No python on PATH in this environment; still not the planted one.
            self.assertNotEqual(Path(resolved).resolve().parent, self.workspace.resolve())
        finally:
            os.chdir(old)
        with self.assertRaises(ValueError):
            resolve_executable("../evil/tool", self.workspace)

    def test_approval_view_shows_diff(self):
        (self.workspace / "cfg.txt").write_text("a\nb\n", encoding="utf-8")
        view = describe("edit_file", "files.write", {"path": "cfg.txt", "old_text": "b",
                                                     "new_text": "c"}, self.workspace)
        self.assertIn("-b", view.detail)
        self.assertIn("+c", view.detail)
        command = describe("run_command", "system.execute", {"argv": ["git", "commit", "-m",
                                                                      "two words"]},
                           self.workspace)
        self.assertEqual(command.risk, "high")
        self.assertIn('"two words"', command.detail)


class WebToolsTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.permissions.set_enabled({"network.fetch", "network.search"})
        self.addresses = {"example.com": ["93.184.216.34"], "internal.test": ["10.0.0.5"],
                          "rebind.test": ["93.184.216.40", "127.0.0.1"]}
        self.requests = []
        self.routes = {}

        async def resolver(host, port):
            if host not in self.addresses:
                raise OSError("nx")
            return self.addresses[host]

        def handler(request):
            self.requests.append(request)
            return self.routes[request.headers["host"] + request.url.path](request)

        for name in ("fetch_url", "search_web"):
            self.jarvis.tools.unregister(name)
        register_web_tools(self.jarvis.tools, resolver, httpx.MockTransport(handler))

    def call(self, name, args):
        return self.run_async(self.jarvis._execute(ToolCall("w", name, args), None))

    def test_fetch_pins_ip_and_extracts_text(self):
        page = ("<html><head><title>Beispiel</title><style>x{}</style></head><body>"
                "<h1>Titel</h1><p>Erster &amp; Absatz</p><script>evil()</script>"
                "<a href='/mehr'>Mehr lesen</a></body></html>")
        self.routes["example.com/"] = lambda r: httpx.Response(
            200, text=page, headers={"content-type": "text/html; charset=utf-8"})
        result = self.call("fetch_url", {"url": "https://example.com/"})["result"]
        request = self.requests[0]
        self.assertEqual(request.url.host, "93.184.216.34")
        self.assertEqual(request.extensions["sni_hostname"], "example.com")
        self.assertEqual(result["title"], "Beispiel")
        self.assertIn("Erster & Absatz", result["text"])
        self.assertNotIn("evil", result["text"])
        self.assertEqual(result["links"][0]["url"], "https://example.com/mehr")

    def test_redirects_are_rechecked(self):
        self.routes["example.com/go"] = lambda r: httpx.Response(
            302, headers={"location": "https://internal.test/admin"})
        self.assertIn("Private", self.call("fetch_url", {"url": "https://example.com/go"})["error"])
        self.routes["example.com/http"] = lambda r: httpx.Response(
            301, headers={"location": "http://example.com/"})
        self.assertIn("HTTPS", self.call("fetch_url", {"url": "https://example.com/http"})["error"])

    def test_blocks_private_and_mixed_resolution(self):
        for url in ("https://internal.test/", "https://rebind.test/", "http://example.com/",
                    "https://user:pw@example.com/", "https://unknown.test/"):
            with self.subTest(url=url):
                self.assertIn("error", self.call("fetch_url", {"url": url}))
        self.assertEqual(self.requests, [])

    def test_rejects_binary_content(self):
        self.routes["example.com/file.zip"] = lambda r: httpx.Response(
            200, content=b"PK", headers={"content-type": "application/zip"})
        self.assertIn("Inhaltstyp", self.call("fetch_url",
                                              {"url": "https://example.com/file.zip"})["error"])

    def test_search_requires_configuration_then_uses_searxng(self):
        self.assertIn("nicht eingerichtet", self.call("search_web", {"query": "x"})["error"])
        self.settings.update(search_provider="searxng", searxng_url="http://localhost:8888")
        self.routes["localhost:8888/search"] = lambda r: httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://a.example", "content": "S"}]})
        result = self.call("search_web", {"query": "jarvis", "count": 3})["result"]
        self.assertEqual(result, [{"title": "T", "url": "https://a.example", "snippet": "S"}])
        self.assertEqual(self.requests[-1].url.params["format"], "json")

    def test_brave_search(self):
        self.secrets.set("brave-search", "brave-key")
        self.routes["api.search.brave.com/res/v1/web/search"] = lambda r: httpx.Response(
            200, json={"web": {"results": [{"title": "B", "url": "https://b", "description": "D"}]}})
        result = self.call("search_web", {"query": "x"})["result"]
        self.assertEqual(result[0]["snippet"], "D")
        self.assertEqual(self.requests[-1].headers["x-subscription-token"], "brave-key")

    def test_html_to_text_structure(self):
        title, body, links = html_to_text("<ul><li>Eins</li><li>Zwei</li></ul>",
                                          httpx.URL("https://x.test/"))
        self.assertEqual(body, "• Eins\n• Zwei")


class PluginTest(WorkspaceTest):
    def copy_calculator(self):
        source = Path(__file__).resolve().parents[1] / "plugins" / "calculator"
        target = self.workspace / "plugins" / "calculator"
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
        return target

    def test_calculator_requires_explicit_enable(self):
        self.copy_calculator()
        manager = PluginManager(self.workspace / "plugins", self.data / "plugins.json")
        self.assertEqual(manager.enabled(), set())
        manager.set_enabled({"calculator"})
        jarvis = self.make_jarvis()
        tool = jarvis.tools.get("calculate")
        self.assertEqual(tool.source, "plugin:calculator")
        self.assertEqual(self.run_async(tool.run({"expression": "2*(3+4)"})), 14)
        with self.assertRaises(ValueError):
            self.run_async(tool.run({"expression": "__import__('os')"}))

    def test_broken_plugin_is_isolated(self):
        folder = self.workspace / "plugins" / "broken"
        folder.mkdir(parents=True)
        (folder / "plugin.json").write_text(json.dumps({"name": "broken", "description": "x"}),
                                            encoding="utf-8")
        (folder / "plugin.py").write_text(
            "from jarvis.tools import Tool, params\n"
            "def register(registry):\n"
            "    async def run(args): return 1\n"
            "    registry.register(Tool('half', 'x', params({}), 'compute.local', False, run))\n"
            "    raise RuntimeError('boom')\n", encoding="utf-8")
        future = self.workspace / "plugins" / "future"
        future.mkdir()
        (future / "plugin.json").write_text(json.dumps({"name": "future", "description": "x",
                                                        "api": 99}), encoding="utf-8")
        (future / "plugin.py").write_text("def register(r): pass\n", encoding="utf-8")
        PluginManager(self.workspace / "plugins", self.data / "plugins.json").set_enabled(
            {"broken", "future"})
        jarvis = self.make_jarvis()
        self.assertIn("boom", jarvis.plugin_errors["broken"])
        self.assertIn("API", jarvis.plugin_errors["future"])
        self.assertIsNone(jarvis.tools.get("half"))
        self.assertIsNotNone(jarvis.tools.get("read_file"))


class SkillTest(WorkspaceTest):
    def write_skill(self, root, folder, frontmatter, body="# Body\n"):
        path = root / folder
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
        return path

    def test_frontmatter_variants_and_priority(self):
        from jarvis.skills import SkillRegistry
        primary, secondary = self.workspace / "skills", self.root / "user-skills"
        folder = self.write_skill(primary, "a", "name: alpha\ndescription: >\n  Erste Zeile\n"
                                  "  zweite Zeile\nlicense: MIT")
        (folder / "scripts").mkdir()
        (folder / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
        self.write_skill(secondary, "a2", 'name: alpha\ndescription: "überschattet"')
        self.write_skill(secondary, "b", "name: beta\ndescription: 'Beta: mit Doppelpunkt'")
        self.write_skill(secondary, "c", "name: kein-description")
        registry = SkillRegistry(primary, [(secondary, "jarvis")])
        skills = {s.name: s for s in registry.list()}
        self.assertEqual(skills["alpha"].description, "Erste Zeile zweite Zeile")
        self.assertEqual(skills["alpha"].source, "workspace")
        self.assertEqual(skills["beta"].description, "Beta: mit Doppelpunkt")
        self.assertNotIn("kein-description", skills)
        content = registry.read("alpha")
        self.assertIn("scripts/run.py", content)
        with self.assertRaises(ValueError):
            registry.read("fehlt")

    def test_skill_catalog_reaches_system_prompt(self):
        self.write_skill(self.workspace / "skills", "x", "name: notes\ndescription: Notizen")
        jarvis = self.make_jarvis()
        fake = self.use_fake_model(Completion("ok"))
        jarvis.client = self.jarvis.client
        self.run_async(jarvis.ask("hi"))
        self.assertIn("notes: Notizen", fake.calls[0][0][0].content)

    def test_bundled_skills_ship_with_the_package_and_can_be_overridden(self):
        skills = {s.name: s for s in self.jarvis.skills.list()}
        for name in ("code-review", "research-brief", "safe-refactor"):
            self.assertEqual(skills[name].source, "mitgeliefert")
        self.write_skill(self.workspace / "skills", "mine", "name: code-review\ndescription: Meins")
        skills = {s.name: s for s in self.make_jarvis().skills.list()}
        self.assertEqual((skills["code-review"].source, skills["code-review"].description),
                         ("workspace", "Meins"))


class MarkdownTest(unittest.TestCase):
    def test_render_blocks_and_inline(self):
        segments = render("# Titel\nText mit **fett** und `code`.\n- Punkt\n```py\nx = 1\n```")
        tags = {text: tags for text, tags in segments}
        self.assertIn("h1", tags["Titel"])
        self.assertIn("bold", tags["fett"])
        self.assertIn("code", tags["code"])
        self.assertIn("bullet", tags["Punkt"])
        self.assertIn("code_block", tags["x = 1\n"])

    def test_unknown_syntax_is_kept_and_plain_strips(self):
        self.assertEqual("".join(t for t, _ in render("2 * 3 * 4 = 24")), "2 * 3 * 4 = 24")
        self.assertEqual(plain("**Hallo** [Welt](https://w.de)"), "Hallo Welt (https://w.de)")


if __name__ == "__main__":
    unittest.main()
