from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

from .approval import describe
from .automation import AutomationRunner
from .config import KIND_LABELS, KINDS, ProviderProfile
from .engine import Jarvis
from .permissions import APPROVAL_REQUIRED, CAPABILITIES


def _approve(tool, args: dict) -> bool:
    view = describe(tool.name, tool.capability, args, Path.cwd())
    print(f"\n── Freigabe · {tool.capability} " + "─" * 30)
    print(view.title)
    if view.detail:
        print(view.detail[:6000])
    return input("Einmal ausführen? [j/N] ").strip().lower() in ("j", "ja", "y", "yes")


THINK_CHOICES: dict[str, bool | None] = {"an": True, "aus": False, "standard": None}


def _stream_printer():
    state = {"line_open": False}

    def on_event(event: dict) -> None:
        kind = event["type"]
        if kind == "text":
            print(event["delta"], end="", flush=True)
            state["line_open"] = True
        elif kind == "tool_start":
            if state["line_open"]:
                print()
                state["line_open"] = False
            hint = next((str(event["arguments"][k]) for k in ("path", "query", "url", "target")
                         if event["arguments"].get(k)), "")
            print(f"  ↳ {event['name']} {hint[:70]}", flush=True)
        elif kind == "tool_end" and not event["ok"]:
            print(f"  ✕ {event['name']} fehlgeschlagen", flush=True)

    return on_event


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS – lokaler KI-Assistent")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(),
                        help="Arbeitsbereich für Datei- und Codewerkzeuge")
    sub = parser.add_subparsers(dest="command", required=True)
    gui = sub.add_parser("gui", help="Desktop-Oberfläche starten")
    gui.add_argument("--no-window", action="store_true",
                     help="Kein App-Fenster öffnen, nur die Start-URL ausgeben")
    gui.add_argument("--port", type=int, default=0, help="Fester Port (Standard: frei)")
    chat = sub.add_parser("chat", help="Interaktiver Chat im Terminal")
    chat.add_argument("--continue", dest="resume", action="store_true",
                      help="Letztes Gespräch fortsetzen")
    ask = sub.add_parser("ask", help="Eine einzelne Frage stellen und beenden")
    ask.add_argument("prompt", nargs="+")

    profiles = sub.add_parser("profiles", help="Modellprofile verwalten")
    profile_sub = profiles.add_subparsers(dest="action", required=True)
    profile_sub.add_parser("list")
    add = profile_sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("kind", choices=sorted(KINDS))
    add.add_argument("model")
    add.add_argument("--endpoint", default="")
    add.add_argument("--context-chars", type=int, default=60_000)
    add.add_argument("--max-output-tokens", type=int, default=4_096)
    add.add_argument("--temperature", type=float, default=None)
    add.add_argument("--think", choices=sorted(THINK_CHOICES), default="standard",
                     help="Nachdenken des Modells (derzeit für Ollama)")
    think = profile_sub.add_parser("think", help="Nachdenken an/aus/standard setzen")
    think.add_argument("id")
    think.add_argument("mode", choices=sorted(THINK_CHOICES))
    profile_sub.add_parser("test").add_argument("id")
    profile_sub.add_parser("use").add_argument("id")
    profile_sub.add_parser("delete").add_argument("id")
    profile_sub.add_parser("models", help="Verfügbare Modelle abfragen").add_argument("id")
    pull = profile_sub.add_parser("pull", help="Ollama-Modell herunterladen")
    pull.add_argument("id")
    pull.add_argument("model")

    permissions = sub.add_parser("permissions", help="Werkzeugberechtigungen")
    permission_sub = permissions.add_subparsers(dest="action", required=True)
    permission_sub.add_parser("list")
    permission_sub.add_parser("enable").add_argument("capability", choices=sorted(CAPABILITIES))
    permission_sub.add_parser("disable").add_argument("capability", choices=sorted(CAPABILITIES))

    memory = sub.add_parser("memory", help="Langzeitgedächtnis")
    memory_sub = memory.add_subparsers(dest="action", required=True)
    memory_sub.add_parser("list").add_argument("query", nargs="?", default="")
    memory_sub.add_parser("add").add_argument("content")
    memory_sub.add_parser("forget").add_argument("id")

    automations = sub.add_parser("automations", help="Wiederkehrende Aufgaben")
    automation_sub = automations.add_subparsers(dest="action", required=True)
    automation_sub.add_parser("list")
    add_auto = automation_sub.add_parser("add")
    add_auto.add_argument("minutes", type=int)
    add_auto.add_argument("prompt")
    add_auto.add_argument("--now", action="store_true", help="Erste Ausführung sofort")
    automation_sub.add_parser("delete").add_argument("id")
    automation_sub.add_parser("pause").add_argument("id")
    automation_sub.add_parser("resume").add_argument("id")
    automation_sub.add_parser("serve")

    skills = sub.add_parser("skills", help="SKILL.md-Skills anzeigen")
    skills.add_argument("--read", metavar="NAME")
    plugins = sub.add_parser("plugins", help="Python-Plugins")
    plugin_sub = plugins.add_subparsers(dest="action", required=True)
    plugin_sub.add_parser("list")
    plugin_sub.add_parser("enable").add_argument("name")
    plugin_sub.add_parser("disable").add_argument("name")
    sub.add_parser("tools", help="Registrierte Werkzeuge und ihren Status zeigen")

    voice = sub.add_parser("voice", help="Offline-Sprachdialog mit lokalem Vosk-Modell")
    voice.add_argument("model_path", type=Path, nargs="?")
    voice.add_argument("--seconds", type=float, default=None)
    voice.add_argument("--speak", action="store_true", help="Antworten vorlesen")
    voice.add_argument("--loop", action="store_true",
                       help="Dialog fortsetzen, bis „Ende“ gesagt oder Strg+C gedrückt wird")

    web = sub.add_parser("web-key", help="Brave Search API-Key sicher speichern")
    web.add_argument("--delete", action="store_true")
    search = sub.add_parser("search-provider", help="Websuche: brave oder searxng")
    search.add_argument("provider", choices=["brave", "searxng"])
    search.add_argument("--url", default="", help="SearXNG-Basis-URL")
    export = sub.add_parser("export", help="Gespräche, Gedächtnis und Automationen als JSON")
    export.add_argument("file", type=Path)
    imp = sub.add_parser("import-memories", help="Erinnerungen aus einem Export übernehmen")
    imp.add_argument("file", type=Path)
    return parser


def _find_profile(jarvis: Jarvis, profile_id: str) -> ProviderProfile:
    profiles, _ = jarvis.config.load()
    matches = [p for p in profiles if p.id == profile_id or p.id.startswith(profile_id)
               or p.name == profile_id]
    if len(matches) != 1:
        raise ValueError("Profil nicht gefunden oder nicht eindeutig.")
    return matches[0]


def _chat(jarvis: Jarvis, resume: bool) -> int:
    print("JARVIS · /exit beendet · /neu startet ein neues Gespräch")
    conversation_id = None
    if resume:
        conversations = jarvis.database.list_conversations(1)
        conversation_id = conversations[0][0] if conversations else None
    while True:
        try:
            prompt = input("\nDu › ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if prompt in ("/exit", "/quit"):
            return 0
        if prompt == "/neu":
            conversation_id = None
            print("Neues Gespräch.")
            continue
        if not prompt:
            continue
        print("\nJARVIS › ", end="", flush=True)
        try:
            conversation_id, _ = asyncio.run(jarvis.ask(prompt, conversation_id, _approve,
                                                        _stream_printer()))
            print()
        except KeyboardInterrupt:
            print("\n[abgebrochen]")
        except Exception as exc:
            print(f"\nFehler: {exc}")


def _voice(jarvis: Jarvis, args) -> int:
    from .voice import OfflineVoice
    settings = jarvis.settings.load()
    path = args.model_path or Path(str(settings["voice_model_path"] or ""))
    if not str(path) or not path.is_dir():
        raise ValueError("Pfad zum Vosk-Modell angeben (einmalig, wird gespeichert).")
    if args.model_path:
        jarvis.settings.update(voice_model_path=str(args.model_path.resolve()))
    voice = OfflineVoice(path)
    seconds = args.seconds or float(settings["voice_seconds"])
    conversation_id = None

    async def session() -> int:
        nonlocal conversation_id
        while True:
            print(f"\n🎙  Ich höre zu (max. {seconds:g} s) …", flush=True)
            prompt = await voice.listen(seconds)
            if not prompt:
                print("Keine Sprache erkannt.")
                if not args.loop:
                    return 1
                continue
            print(f"Du › {prompt}")
            if args.loop and prompt.strip().lower() in ("ende", "stopp", "beenden", "stop"):
                return 0
            print("JARVIS › ", end="", flush=True)
            conversation_id, reply = await jarvis.ask(prompt, conversation_id, _approve,
                                                      _stream_printer())
            print()
            if args.speak:
                await voice.speak(reply)
            if not args.loop:
                return 0

    try:
        return asyncio.run(session())
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    jarvis = Jarvis(args.workspace)
    for name, error in jarvis.plugin_errors.items():
        print(f"Warnung: Plugin {name} nicht geladen – {error}", file=sys.stderr)
    try:
        return _dispatch(jarvis, args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


def gui_main() -> int:
    """Entry point of the windowless `jarvis-gui` launcher.

    Started from a shortcut, the current directory is arbitrary (often System32), so the
    workspace defaults to ~/JARVIS unless --workspace is given. Without a console, startup
    errors are shown in a dialog instead of being lost.
    """
    argv = sys.argv[1:]
    if not any(a == "--workspace" or a.startswith("--workspace=") for a in argv):
        workspace = Path.home() / "JARVIS"
        workspace.mkdir(exist_ok=True)
        argv = ["--workspace", str(workspace), *argv]
    try:
        args = build_parser().parse_args([*argv, "gui"])
        from .webapp import run_web
        run_web(Jarvis(args.workspace))
        return 0
    except Exception as exc:  # noqa: BLE001 - last resort for a windowless process
        message = f"JARVIS konnte nicht starten:\n{exc}"
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "JARVIS", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1


def _dispatch(jarvis: Jarvis, args) -> int:
    command = args.command
    if command == "gui":
        from .webapp import run_web

        def ready(url: str) -> None:
            if args.no_window:
                print(f"JARVIS läuft: {url}", "Strg+C beendet.", sep="\n", flush=True)

        run_web(jarvis, open_app=not args.no_window, port=args.port, on_ready=ready)
        return 0
    if command == "chat":
        return _chat(jarvis, args.resume)
    if command == "ask":
        _, reply = asyncio.run(jarvis.ask(" ".join(args.prompt), approve=_approve,
                                          on_event=_stream_printer()))
        print()
        return 0
    if command == "profiles":
        return _profiles(jarvis, args)
    if command == "permissions":
        enabled = jarvis.permissions.enabled()
        if args.action == "list":
            for capability, label in CAPABILITIES.items():
                note = "  (Freigabe je Aktion)" if capability in APPROVAL_REQUIRED else ""
                print(f"{'✓' if capability in enabled else '·'} {capability:<17} {label}{note}")
        else:
            enabled.add(args.capability) if args.action == "enable" \
                else enabled.discard(args.capability)
            jarvis.permissions.set_enabled(enabled)
            print("Gespeichert.")
        return 0
    if command == "memory":
        if args.action == "list":
            for memory_id, content in jarvis.database.recall(args.query, 200):
                print(f"{memory_id[:8]}  {content}")
        elif args.action == "add":
            print(jarvis.database.remember(args.content))
        else:
            ids = [m[0] for m in jarvis.database.recall("", 10_000) if m[0].startswith(args.id)]
            if len(ids) != 1:
                print("Nicht gefunden oder nicht eindeutig.")
                return 1
            jarvis.database.forget(ids[0])
            print("Gelöscht.")
        return 0
    if command == "automations":
        return _automations(jarvis, args)
    if command == "skills":
        if args.read:
            print(jarvis.skills.read(args.read))
        else:
            for skill in jarvis.skills.list():
                print(f"{skill.name}: {skill.description}")
        return 0
    if command == "plugins":
        enabled = jarvis.plugins.enabled()
        if args.action == "list":
            for plugin in jarvis.plugins.list():
                error = jarvis.plugin_errors.get(plugin.name)
                print(f"{'●' if plugin.name in enabled else '○'} {plugin.name} {plugin.version}: "
                      f"{plugin.description}" + (f"  [Fehler: {error}]" if error else ""))
        elif args.action == "enable":
            if args.name not in {p.name for p in jarvis.plugins.list()}:
                raise ValueError("Plugin nicht gefunden.")
            if input("Python-Plugins haben vollen Zugriff auf diesen Prozess. "
                     f"{args.name} vertrauen und beim nächsten Start laden? [j/N] "
                     ).strip().lower() in ("j", "ja", "y", "yes"):
                enabled.add(args.name)
                jarvis.plugins.set_enabled(enabled)
                print("Plugin aktiviert. JARVIS neu starten.")
        else:
            enabled.discard(args.name)
            jarvis.plugins.set_enabled(enabled)
            print("Plugin deaktiviert. JARVIS neu starten.")
        return 0
    if command == "tools":
        enabled = jarvis.permissions.enabled()
        for tool in sorted(jarvis.tools.all(), key=lambda t: (t.capability, t.name)):
            state = "✓" if tool.capability in enabled else "·"
            approval = " Freigabe" if jarvis.needs_approval(tool) else ""
            print(f"{state} {tool.name:<16} {tool.capability:<17}{approval:<9} {tool.source}")
        return 0
    if command == "voice":
        return _voice(jarvis, args)
    if command == "web-key":
        if args.delete:
            jarvis.secrets.delete("brave-search")
            print("Web-Key gelöscht.")
        else:
            jarvis.secrets.set("brave-search", getpass.getpass("Brave Search API-Key: ").strip())
            print("Web-Key gespeichert.")
        return 0
    if command == "search-provider":
        jarvis.settings.update(search_provider=args.provider, searxng_url=args.url)
        print("Gespeichert.")
        return 0
    if command == "export":
        args.file.write_text(json.dumps(jarvis.database.export(), ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"Exportiert: {args.file}")
        return 0
    if command == "import-memories":
        data = json.loads(args.file.read_text(encoding="utf-8"))
        if data.get("format") != "jarvis-export":
            raise ValueError("Keine JARVIS-Exportdatei.")
        print(f"{jarvis.database.import_memories(data.get('memories', []))} Erinnerungen "
              "importiert.")
        return 0
    return 1


def _profiles(jarvis: Jarvis, args) -> int:
    profiles, active = jarvis.config.load()
    if args.action == "list":
        if not profiles:
            print("Keine Profile. Beispiel: jarvis profiles add Lokal ollama llama3.2")
        for profile in profiles:
            print(f"{'*' if profile.id == active else ' '} {profile.id[:8]}  {profile.name} · "
                  f"{KIND_LABELS[profile.kind]} · {profile.model} · {profile.endpoint}")
        return 0
    if args.action == "add":
        profile = ProviderProfile.create(args.name, args.kind, args.model, args.endpoint,
                                         max_context_chars=args.context_chars,
                                         max_output_tokens=args.max_output_tokens,
                                         temperature=args.temperature,
                                         think=THINK_CHOICES[args.think])
        key = getpass.getpass("API-Key: ").strip() if profile.needs_key() else ""
        if profile.needs_key() and not key:
            raise ValueError("API-Key fehlt.")
        if key:
            jarvis.secrets.set(f"provider:{profile.id}", key)
        profiles.append(profile)
        jarvis.config.save(profiles, active or profile.id)
        print(f"Gespeichert: {profile.id[:8]}")
        return 0
    profile = _find_profile(jarvis, args.id)
    if args.action == "use":
        jarvis.config.save(profiles, profile.id)
        print(f"Aktiv: {profile.name}")
    elif args.action == "think":
        profile.think = THINK_CHOICES[args.mode]
        jarvis.config.save([profile if p.id == profile.id else p for p in profiles], active)
        print(f"Nachdenken für {profile.name}: {args.mode}")
    elif args.action == "delete":
        remaining = [p for p in profiles if p.id != profile.id]
        jarvis.config.save(remaining, None if profile.id == active else active)
        jarvis.secrets.delete(f"provider:{profile.id}")
        print("Profil gelöscht.")
    elif args.action == "models":
        for name in asyncio.run(jarvis.list_models(profile)):
            print(name)
    elif args.action == "pull":
        def progress(status: str, fraction: float | None) -> None:
            suffix = f" {fraction:6.1%}" if fraction is not None else ""
            print(f"\r{status[:50]:<50}{suffix}", end="", flush=True)
        asyncio.run(jarvis.client(profile).pull_ollama_model(args.model, progress))
        print("\nFertig.")
    else:
        print(asyncio.run(jarvis.test_profile(profile)))
    return 0


def _automations(jarvis: Jarvis, args) -> int:
    runner = AutomationRunner(jarvis)
    if args.action == "add":
        print(runner.add(args.prompt, args.minutes, args.now))
        return 0
    if args.action == "list":
        for item in jarvis.database.list_automations():
            print(f"{item[0][:8]}  alle {item[2]} min · nächste Ausführung {item[3][:16]} · "
                  f"{'aktiv' if item[4] else 'pausiert'}\n          {item[1]}")
        return 0
    if args.action == "serve":
        print("Automationen laufen. Strg+C beendet den Prozess.")
        try:
            asyncio.run(runner.serve(lambda id, result: print(f"{id[:8]}: {result}")))
        except KeyboardInterrupt:
            pass
        return 0
    ids = [a[0] for a in jarvis.database.list_automations() if a[0].startswith(args.id)]
    if len(ids) != 1:
        raise ValueError("Automation nicht gefunden oder nicht eindeutig.")
    if args.action == "delete":
        jarvis.database.delete_automation(ids[0])
    else:
        jarvis.database.set_automation_enabled(ids[0], args.action == "resume")
    print("Gespeichert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
