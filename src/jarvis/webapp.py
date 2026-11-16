"""Desktop interface: a local HTTP server that serves the HUD (web/) to an app window.

The page talks to JARVIS through a small JSON API plus one Server-Sent-Events stream.
Nothing listens beyond 127.0.0.1. Every request must carry the session cookie that the
launcher hands to its own window (one-time `?k=` exchange), the Host header must match
the bound address (DNS rebinding) and state-changing requests need same-origin plus a
custom header (CSRF). Model output is rendered by the page under a strict CSP.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import json
import mimetypes
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import __version__
from .approval import describe
from .automation import AutomationRunner
from .config import DEFAULT_ENDPOINTS, KIND_LABELS, KINDS, ProviderProfile, data_dir
from .engine import Jarvis
from .permissions import APPROVAL_REQUIRED, CAPABILITIES

STATIC_DIR = Path(__file__).with_name("web")
STATIC_FILES = {"index.html", "app.css", "app.js", "icon.svg", "icon.png"}
COOKIE = "jarvis_session"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
MAX_BODY = 2_000_000
AUTOMATION_INTERVAL = 30.0


class AsyncWorker:
    """One persistent event loop in a background thread for all model and tool work."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="jarvis-async", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    def submit(self, coroutine) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def stop(self) -> None:
        def shutdown() -> None:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
            self.loop.stop()

        if self.loop.is_running():
            self.loop.call_soon_threadsafe(shutdown)
        self.thread.join(timeout=3)


class Hub:
    """Fan-out of events to every connected page (normally exactly one window)."""

    def __init__(self):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.last_seen = time.monotonic()
        self.ever_connected = False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self._lock:
            self._clients.append(q)
            self.ever_connected = True
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)
            self.last_seen = time.monotonic()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, event: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:  # a stalled page must not block the engine
                pass

    def close(self) -> None:
        with self._lock:
            clients, self._clients = self._clients, []
        for q in clients:
            q.put(None)


class _Handled(Exception):
    """Raised after a handler already wrote its own response."""


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _profile_json(profile: ProviderProfile, active: str | None, has_key: bool) -> dict:
    data = asdict(profile)
    data.update(active=profile.id == active, needs_key=profile.needs_key(), has_key=has_key,
                is_local=profile.is_local, label=KIND_LABELS[profile.kind])
    return data


def _tool_result(content: str) -> dict:
    """Small summary of a stored tool message for the transcript (full data stays local)."""
    try:
        data = json.loads(content)
    except ValueError:
        return {"ok": True, "preview": content[:400]}
    if isinstance(data, dict) and "error" in data:
        return {"ok": False, "error": str(data["error"])[:300]}
    return {"ok": True, "preview": json.dumps(data.get("result", data) if isinstance(data, dict)
                                               else data, ensure_ascii=False)[:400]}


class JarvisWeb:
    """Bridges the HTTP API to one Jarvis instance."""

    def __init__(self, jarvis: Jarvis):
        self.jarvis = jarvis
        self.worker = AsyncWorker()
        self.hub = Hub()
        self.runner = AutomationRunner(jarvis)
        self.token = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)
        self.run: dict[str, Any] | None = None
        self.approvals: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}
        self.listen_stop: threading.Event | None = None
        self.lock = threading.Lock()
        self.automation_busy = False
        self.server: ThreadingHTTPServer | None = None
        self.closed = threading.Event()

    # Lifecycle -----------------------------------------------------------------------------

    def start(self, port: int = 0) -> str:
        app = self

        class Handler(RequestHandler):
            web = app

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="jarvis-http",
                         daemon=True).start()
        threading.Thread(target=self._automation_loop, name="jarvis-automation",
                         daemon=True).start()
        return self.origin

    @property
    def origin(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def launch_url(self) -> str:
        return f"{self.origin}/?k={self.token}"

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        with self.lock:
            run = self.run
        if run:
            run["future"].cancel()
        if self.listen_stop:
            self.listen_stop.set()
        self.hub.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.worker.stop()

    def status(self, text: str, error: bool = False) -> None:
        self.hub.publish({"type": "status", "text": text, "error": error})

    def _wait(self, coroutine, timeout: float = 240):
        try:
            return self.worker.submit(coroutine).result(timeout)
        except concurrent.futures.TimeoutError:
            raise ApiError("Zeitüberschreitung.", 504) from None

    # State ---------------------------------------------------------------------------------

    def state(self) -> dict:
        j = self.jarvis
        profiles, active = j.config.load()
        enabled = j.permissions.enabled()
        plugins_enabled = j.plugins.enabled()
        with self.lock:
            run = dict(self.run) if self.run else None
        return {
            "version": __version__,
            "workspace": str(j.workspace),
            "data_dir": str(data_dir()),
            "profiles": [_profile_json(p, active, bool(j.secrets.get(f"provider:{p.id}")))
                         for p in profiles],
            "active": active,
            "kinds": [{"kind": k, "label": KIND_LABELS[k], "endpoint": DEFAULT_ENDPOINTS[k]}
                      for k in sorted(KINDS)],
            "permissions": [{"name": name, "description": text, "enabled": name in enabled,
                             "approval": name in APPROVAL_REQUIRED}
                            for name, text in CAPABILITIES.items()],
            "tools": [{"name": t.name, "capability": t.capability, "source": t.source,
                       "enabled": t.capability in enabled,
                       "approval": j.needs_approval(t)} for t in j.tools.all()],
            "settings": j.settings.load(),
            "has_brave_key": bool(j.secrets.get("brave-search")),
            "skills": [{"name": s.name, "description": s.description, "source": s.source}
                       for s in j.skills.list()],
            "plugins": [{"name": p.name, "description": p.description, "version": p.version,
                         "enabled": p.name in plugins_enabled,
                         "error": j.plugin_errors.get(p.name)} for p in j.plugins.list()],
            "conversations": self.conversations(),
            "memory_count": len(j.database.recall("", 10_000)),
            "run": {"id": run["id"], "conversation": run["conversation"]} if run else None,
            "platform": sys.platform,
        }

    def conversations(self) -> list[dict]:
        return [{"id": cid, "title": title}
                for cid, title in self.jarvis.database.list_conversations(300)]

    def conversation(self, conversation_id: str) -> dict:
        messages = self.jarvis.database.get_messages(conversation_id)
        results = {m.tool_call_id: _tool_result(m.content) for m in messages if m.role == "tool"}
        out = []
        for message in messages:
            if message.role == "user":
                out.append({"role": "user", "content": message.content})
            elif message.role == "assistant":
                out.append({"role": "assistant", "content": message.content, "tools": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments,
                     **(results.get(c.id) or {"ok": None})} for c in message.tool_calls]})
        title = next((t for i, t in self.jarvis.database.list_conversations(1000)
                      if i == conversation_id), None)
        if title is None:
            raise ApiError("Gespräch nicht gefunden.", 404)
        return {"id": conversation_id, "title": title, "messages": out}

    # Chat ----------------------------------------------------------------------------------

    def ask(self, prompt: str, conversation_id: str | None) -> dict:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ApiError("Die Nachricht ist leer.")
        self.jarvis.active_profile()  # raises a readable ValueError without a model
        run_id = str(uuid.uuid4())
        with self.lock:
            if self.run:
                raise ApiError("JARVIS arbeitet noch. Mit Esc abbrechen.", 409)
            self.run = {"id": run_id, "conversation": conversation_id, "started": time.time()}

        def on_event(event: dict) -> None:
            if event["type"] == "conversation":
                with self.lock:
                    if self.run and self.run["id"] == run_id:
                        self.run["conversation"] = event["id"]
            self.hub.publish({**event, "run": run_id,
                              "type": event["type"] if event["type"] != "conversation"
                              else "run_conversation"})

        future = self.worker.submit(self.jarvis.ask(prompt, conversation_id, self._approve,
                                                    on_event))
        with self.lock:
            self.run["future"] = future
        future.add_done_callback(lambda fut: self._finished(run_id, fut))
        self.hub.publish({"type": "run_start", "run": run_id, "conversation": conversation_id,
                          "prompt": prompt})
        return {"run": run_id}

    def _finished(self, run_id: str, future: concurrent.futures.Future) -> None:
        with self.lock:
            run = self.run if self.run and self.run["id"] == run_id else None
            self.run = None
        conversation = run["conversation"] if run else None
        elapsed = round(time.time() - run["started"], 2) if run else None
        if future.cancelled():
            self.hub.publish({"type": "run_end", "run": run_id, "state": "cancelled",
                              "conversation": conversation, "elapsed": elapsed})
            return
        error = future.exception()
        if error is not None:
            self.hub.publish({"type": "run_end", "run": run_id, "state": "error",
                              "message": str(error), "conversation": conversation,
                              "elapsed": elapsed})
            return
        conversation, reply = future.result()
        self.hub.publish({"type": "run_end", "run": run_id, "state": "done",
                          "conversation": conversation, "elapsed": elapsed})
        if self.jarvis.settings.get("speak_replies") and not self.closed.is_set():
            from .voice import OfflineVoice
            spoken = self.worker.submit(OfflineVoice.speak(reply))
            spoken.add_done_callback(lambda fut: fut.cancelled() or fut.exception() is None or
                                     self.status(f"Vorlesen: {fut.exception()}", True))

    def cancel(self) -> None:
        with self.lock:
            run = self.run
        if run and "future" in run:
            run["future"].cancel()
        for loop, future in list(self.approvals.values()):
            loop.call_soon_threadsafe(lambda f=future: f.done() or f.set_result(False))

    async def _approve(self, tool, arguments: dict) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        approval_id = str(uuid.uuid4())
        self.approvals[approval_id] = (loop, future)
        view = describe(tool.name, tool.capability, arguments, self.jarvis.workspace)
        self.hub.publish({"type": "approval", "id": approval_id, "tool": tool.name,
                          "capability": tool.capability, "label": view.capability,
                          "title": view.title, "detail": view.detail, "risk": view.risk})
        try:
            return await future
        finally:
            self.approvals.pop(approval_id, None)
            self.hub.publish({"type": "approval_closed", "id": approval_id})

    def decide(self, approval_id: str, approve: bool) -> None:
        entry = self.approvals.get(approval_id)
        if not entry:
            raise ApiError("Diese Freigabe ist nicht mehr offen.", 404)
        loop, future = entry
        loop.call_soon_threadsafe(lambda: future.done() or future.set_result(bool(approve)))

    # Profiles ------------------------------------------------------------------------------

    def _form_profile(self, data: dict) -> ProviderProfile:
        def number(key: str, default: int) -> int:
            value = data.get(key)
            try:
                return int(value) if value not in (None, "") else default
            except (TypeError, ValueError):
                raise ApiError("Kontext und Antwortlänge müssen ganze Zahlen sein.") from None

        temperature = data.get("temperature")
        try:
            temperature = float(temperature) if temperature not in (None, "") else None
        except (TypeError, ValueError):
            raise ApiError("Temperatur muss eine Zahl sein.") from None
        think = data.get("think")
        if think not in (None, True, False):
            raise ApiError("Ungültige Einstellung für Nachdenken.")
        kind = str(data.get("kind") or "")
        if kind not in KINDS:
            raise ApiError("Unbekannter Anbieter.")
        model = str(data.get("model") or "").strip()
        profile = ProviderProfile(
            str(data.get("id") or uuid.uuid4()),
            str(data.get("name") or "").strip() or model, kind, model,
            (str(data.get("endpoint") or "").strip() or DEFAULT_ENDPOINTS[kind]).rstrip("/"),
            number("max_context_chars", 60_000), number("max_output_tokens", 4_096),
            temperature, think)
        return profile

    def save_profile(self, data: dict) -> dict:
        profile = self._form_profile(data)
        profile.validate()
        key = str(data.get("key") or "").strip()
        secret_name = f"provider:{profile.id}"
        if profile.needs_key() and not (key or self.jarvis.secrets.get(secret_name)):
            raise ApiError("Für diesen Anbieter ist ein API-Key nötig.")
        if key:
            self.jarvis.secrets.set(secret_name, key)
        profiles, active = self.jarvis.config.load()
        if any(p.id == profile.id for p in profiles):
            profiles = [profile if p.id == profile.id else p for p in profiles]
        else:
            profiles.append(profile)
        if data.get("activate") is True or not active:
            active = profile.id
        self.jarvis.config.save(profiles, active)
        return {"id": profile.id}

    # First-run setup -----------------------------------------------------------------------

    def ollama_status(self) -> dict:
        """Is Ollama installed and running, which models are there, what fits this PC?"""
        from .providers import ProviderClient
        probe = ProviderProfile("setup", "setup", "ollama", "setup", DEFAULT_ENDPOINTS["ollama"])
        client = ProviderClient(probe, None, max_retries=0)
        result: dict[str, Any] = {"found": find_ollama() is not None, "running": False,
                                  "version": None, "models": [], "ram_gb": total_ram_gb()}
        try:
            result["version"] = self._wait(client.ollama_version(), 8)
            result["running"] = result["found"] = True
            result["models"] = self._wait(client.ollama_models_detail(), 8)
        except Exception:  # noqa: BLE001 - "not running" is a normal answer here
            pass
        # Machines report a little less than the nominal size (32 GB shows up as ~31).
        ram = result["ram_gb"] or 0
        result["recommended"] = ("qwen3:14b" if ram >= 28 else "qwen3:8b" if ram >= 14 else
                                 "qwen3:4b" if ram >= 7 else "qwen3:1.7b")
        return result

    def start_ollama(self) -> None:
        exe = find_ollama()
        if exe is None:
            raise ApiError("Ollama wurde nicht gefunden. Bitte zuerst von ollama.com installieren.")
        tray = Path(exe).with_name("ollama app.exe")
        if sys.platform == "win32" and tray.is_file():
            # The tray app is how Ollama expects to run on Windows. A detached
            # `ollama serve` started from here accepted requests but never loaded a model.
            os.startfile(tray)  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin" and Path("/Applications/Ollama.app").is_dir():
            subprocess.Popen(["open", "-a", "Ollama"])
            return
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags,
                         start_new_session=sys.platform != "win32")

    def probe_profile(self, data: dict) -> tuple[ProviderProfile, str | None]:
        data = dict(data)
        data["model"] = str(data.get("model") or "").strip() or "probe"
        profile = self._form_profile(data)
        profile.validate()
        return profile, (str(data.get("key") or "").strip() or None)

    def pull(self, data: dict) -> None:
        profile, _ = self.probe_profile(data)
        if profile.kind != "ollama":
            raise ApiError("Herunterladen ist nur für Ollama-Profile möglich.")
        name = str(data.get("model") or "").strip()
        if not name:
            raise ApiError("Modellnamen eintragen, z. B. qwen3:8b.")
        from .providers import ProviderClient
        client = ProviderClient(profile)

        def progress(status: str, fraction: float | None) -> None:
            self.hub.publish({"type": "pull", "model": name, "status": status,
                              "fraction": fraction})

        future = self.worker.submit(client.pull_ollama_model(name, progress))

        def done(fut: concurrent.futures.Future) -> None:
            if fut.cancelled():
                return
            error = fut.exception()
            self.hub.publish({"type": "pull_end", "model": name, "ok": error is None,
                              "message": str(error) if error else f"Modell {name} ist bereit."})

        future.add_done_callback(done)

    # Voice ---------------------------------------------------------------------------------

    def listen(self) -> None:
        from .voice import OfflineVoice
        path = str(self.jarvis.settings.get("voice_model_path") or "")
        if not path or not Path(path).is_dir():
            raise ApiError("Kein Vosk-Sprachmodell eingerichtet. Ordner unter System → "
                           "Sprache eintragen.")
        if self.listen_stop is not None:
            raise ApiError("Ich höre bereits zu.", 409)
        voice = OfflineVoice(Path(path))
        self.listen_stop = threading.Event()
        seconds = int(self.jarvis.settings.get("voice_seconds"))

        def partial(text: str) -> None:
            self.hub.publish({"type": "listen_partial", "text": text})

        future = self.worker.submit(voice.listen(seconds, self.listen_stop, partial))

        def done(fut: concurrent.futures.Future) -> None:
            self.listen_stop = None
            if fut.cancelled():
                return
            error = fut.exception()
            self.hub.publish({"type": "listen_end", "text": "" if error else (fut.result() or ""),
                              "error": str(error) if error else None})

        future.add_done_callback(done)

    # Automations ---------------------------------------------------------------------------

    def automations(self) -> list[dict]:
        return [{"id": a[0], "prompt": a[1], "interval": a[2], "next_run": a[3],
                 "enabled": bool(a[4]), "last_result": a[5], "last_run": a[6]}
                for a in self.jarvis.database.list_automations()]

    def automation_tick(self) -> None:
        if self.automation_busy or self.closed.is_set():
            return
        try:
            self.jarvis.active_profile()
        except ValueError:
            return
        self.automation_busy = True
        self.hub.publish({"type": "automation_busy", "busy": True})
        future = self.worker.submit(self.runner.run_due())

        def done(fut: concurrent.futures.Future) -> None:
            self.automation_busy = False
            self.hub.publish({"type": "automation_busy", "busy": False})
            if fut.cancelled():
                return
            error = fut.exception()
            if error:
                self.status(f"Automation: {error}", True)
            elif fut.result():
                self.hub.publish({"type": "automations_ran", "count": len(fut.result())})

        future.add_done_callback(done)

    def _automation_loop(self) -> None:
        if self.closed.wait(4):
            return
        while not self.closed.is_set():
            self.automation_tick()
            if self.closed.wait(AUTOMATION_INTERVAL):
                return

    # Misc ----------------------------------------------------------------------------------

    def open(self, target: str) -> None:
        if target == "data":
            path = data_dir()
            path.mkdir(parents=True, exist_ok=True)
            _open_path(path)
        elif target == "workspace":
            _open_path(self.jarvis.workspace)
        else:
            parsed = urlparse(target)
            if parsed.scheme not in ("https", "http") or not parsed.hostname:
                raise ApiError("Nur http- und https-Links werden geöffnet.")
            webbrowser.open(target)


def find_ollama() -> str | None:
    candidates = []
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            candidates.append(Path(base) / "Programs/Ollama/ollama.exe")
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                candidates.append(Path(os.environ[env]) / "Ollama/ollama.exe")
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Ollama.app/Contents/Resources/ollama"),
                       Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama")]
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    found = next((str(c) for c in candidates if c.is_file()), None)
    if found:
        return found
    which = shutil.which("ollama")
    return which if which and os.path.isabs(which) else None


def total_ram_gb() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                            ("page_total", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                            ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_free", ctypes.c_ulonglong),
                            ("extended", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return round(status.total / 1024 ** 3)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3)
    except (AttributeError, OSError, ValueError):
        return None


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


# HTTP ------------------------------------------------------------------------------------------


class RequestHandler(BaseHTTPRequestHandler):
    web: JarvisWeb
    server_version = "JARVIS"
    sys_version = ""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        return  # no access log: URLs may contain the one-time launch token

    # Security ------------------------------------------------------------------------------

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _session_ok(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001 - malformed cookies are simply unauthenticated
            return False
        value = cookie.get(COOKIE)
        return bool(value) and hmac.compare_digest(value.value, self.web.session)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.web.origin:
            return False
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "none"):
            return False
        return self.headers.get("X-Jarvis") == "1"

    def _headers(self, status: int, content_type: str, length: int | None = None,
                 extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _deny(self, status: int = 403, message: str = "Nicht erlaubt.") -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("Anfrage zu groß.", 413)
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ApiError("JSON erwartet.", 415)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError("Ungültiges JSON.") from None
        if not isinstance(data, dict):
            raise ApiError("JSON-Objekt erwartet.")
        return data

    # Routing -------------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self._host_ok():
            return self._deny(421, "Falscher Host.")
        url = urlparse(self.path)
        if url.path == "/" and "k" in parse_qs(url.query):
            token = parse_qs(url.query)["k"][0].encode()
            # Single use: the token sits in the browser's argv, readable by other processes.
            with self.web.lock:
                valid = self.web.token is not None and \
                    hmac.compare_digest(token, self.web.token.encode())
                if valid:
                    self.web.token = None
            if not valid:
                return self._deny()
            self._headers(303, "text/plain", 0, {
                "Location": "/",
                "Set-Cookie": f"{COOKIE}={self.web.session}; HttpOnly; SameSite=Strict; Path=/"})
            return None
        if not self._session_ok():
            return self._deny(403, "Bitte JARVIS über den Starter öffnen.")
        try:
            if url.path == "/api/events":
                return self._events()
            if url.path.startswith("/api/"):
                return self._json(self._api_get(url.path, parse_qs(url.query)))
            return self._static(url.path)
        except _Handled:
            return None
        except ApiError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (ValueError, RuntimeError, OSError) as exc:
            return self._json({"error": str(exc)}, 400)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self._host_ok():
            return self._deny(421, "Falscher Host.")
        if not self._session_ok() or not self._same_origin():
            return self._deny()
        path = urlparse(self.path).path
        try:
            result = self._api_post(path, self._body())
            return self._json(result if result is not None else {"ok": True})
        except ApiError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (ValueError, RuntimeError, OSError) as exc:
            return self._json({"error": str(exc)}, 400)

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        if name not in STATIC_FILES:
            raise ApiError("Nicht gefunden.", 404)
        body = (STATIC_DIR / name).read_bytes()
        content_type = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                        "svg": "image/svg+xml", "html": "text/html; charset=utf-8",
                        "png": "image/png"}.get(name.rsplit(".", 1)[-1]) or \
            mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._headers(200, content_type, len(body))
        self.wfile.write(body)

    def _events(self) -> None:
        q = self.web.hub.subscribe()
        try:
            self._headers(200, "text/event-stream; charset=utf-8", None,
                          {"Connection": "close", "X-Accel-Buffering": "no"})
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if event is None:
                    return
                data = json.dumps(event, ensure_ascii=False, default=str)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            return
        finally:
            self.web.hub.unsubscribe(q)

    def _api_get(self, path: str, query: dict[str, list[str]]) -> Any:
        web, j = self.web, self.web.jarvis
        if path == "/api/state":
            return web.state()
        if path == "/api/conversations":
            needle = (query.get("q") or [""])[0].lower()
            return [c for c in web.conversations() if needle in c["title"].lower()]
        if path.startswith("/api/conversations/"):
            return web.conversation(path.rsplit("/", 1)[-1])
        if path == "/api/memories":
            return [{"id": i, "content": c}
                    for i, c in j.database.recall((query.get("q") or [""])[0], 500)]
        if path == "/api/automations":
            return web.automations()
        if path == "/api/setup/ollama":
            return web.ollama_status()
        if path == "/api/export":
            body = json.dumps(j.database.export(), ensure_ascii=False, indent=2).encode()
            name = f"jarvis-export-{datetime.now():%Y%m%d-%H%M}.json"
            self._headers(200, "application/json; charset=utf-8", len(body),
                          {"Content-Disposition": f'attachment; filename="{name}"'})
            self.wfile.write(body)
            raise _Handled
        raise ApiError("Nicht gefunden.", 404)

    def _api_post(self, path: str, data: dict) -> Any:
        web, j = self.web, self.web.jarvis
        routes: dict[str, Callable[[], Any]] = {
            "/api/ask": lambda: web.ask(data.get("prompt", ""), data.get("conversation") or None),
            "/api/cancel": web.cancel,
            "/api/approval": lambda: web.decide(str(data.get("id")), data.get("approve") is True),
            "/api/conversations/rename": lambda: j.database.rename_conversation(
                str(data.get("id")), str(data.get("title") or "")),
            "/api/conversations/delete": lambda: self._delete_conversation(str(data.get("id"))),
            "/api/conversations/drop-last": lambda: self._drop_last(str(data.get("id"))),
            "/api/memories/add": lambda: {"id": j.database.remember(str(data.get("content")
                                                                       or ""))},
            "/api/memories/delete": lambda: j.database.forget(str(data.get("id"))),
            "/api/automations/add": lambda: {"id": web.runner.add(
                str(data.get("prompt") or ""), int(data.get("minutes") or 0),
                bool(data.get("now")))},
            "/api/automations/toggle": lambda: j.database.set_automation_enabled(
                str(data.get("id")), bool(data.get("enabled"))),
            "/api/automations/run": lambda: (j.database.schedule_automation_now(
                str(data.get("id"))), web.automation_tick()) and None,
            "/api/automations/delete": lambda: j.database.delete_automation(str(data.get("id"))),
            "/api/profiles/save": lambda: web.save_profile(data),
            "/api/profiles/activate": lambda: self._activate(str(data.get("id"))),
            "/api/profiles/delete": lambda: self._delete_profile(str(data.get("id"))),
            "/api/profiles/test": lambda: {"reply": web._wait(j.test_profile(
                *web.probe_profile(data)))},
            "/api/profiles/models": lambda: {"models": web._wait(j.list_models(
                *web.probe_profile(data)))},
            "/api/profiles/pull": lambda: web.pull(data),
            "/api/settings": lambda: j.settings.update(**{
                k: v for k, v in data.items() if k in j.settings.load()}),
            "/api/web-key": lambda: self._web_key(str(data.get("key") or "")),
            "/api/permissions": lambda: j.permissions.set_enabled(
                set(map(str, data.get("enabled") or []))),
            "/api/plugins": lambda: j.plugins.set_enabled(set(map(str, data.get("enabled")
                                                                  or []))),
            "/api/setup/ollama/start": web.start_ollama,
            "/api/listen": web.listen,
            "/api/listen/stop": lambda: web.listen_stop and web.listen_stop.set(),
            "/api/open": lambda: web.open(str(data.get("target") or "")),
        }
        handler = routes.get(path)
        if handler is None:
            raise ApiError("Nicht gefunden.", 404)
        result = handler()
        return result if isinstance(result, dict) else None

    def _delete_conversation(self, conversation_id: str) -> None:
        with self.web.lock:
            if self.web.run and self.web.run.get("conversation") == conversation_id:
                raise ApiError("Laufendes Gespräch kann nicht gelöscht werden.", 409)
        self.web.jarvis.database.delete_conversation(conversation_id)

    def _drop_last(self, conversation_id: str) -> None:
        with self.web.lock:
            if self.web.run and self.web.run.get("conversation") == conversation_id:
                raise ApiError("Das Gespräch läuft noch.", 409)
        self.web.jarvis.database.drop_unanswered(conversation_id)

    def _activate(self, profile_id: str) -> None:
        profiles, _ = self.web.jarvis.config.load()
        if all(p.id != profile_id for p in profiles):
            raise ApiError("Profil nicht gefunden.", 404)
        self.web.jarvis.config.save(profiles, profile_id)

    def _delete_profile(self, profile_id: str) -> None:
        j = self.web.jarvis
        profiles, active = j.config.load()
        remaining = [p for p in profiles if p.id != profile_id]
        if len(remaining) == len(profiles):
            raise ApiError("Profil nicht gefunden.", 404)
        if active == profile_id:
            active = remaining[0].id if remaining else None
        j.config.save(remaining, active)
        j.secrets.delete(f"provider:{profile_id}")

    def _web_key(self, key: str) -> None:
        if key:
            self.web.jarvis.secrets.set("brave-search", key.strip())
        else:
            self.web.jarvis.secrets.delete("brave-search")


# Window ----------------------------------------------------------------------------------------


def find_app_browser() -> str | None:
    """A Chromium browser that supports --app windows. Edge is usually there on Windows,
    but it can be uninstalled in the EU, so Chrome and Brave are tried as well."""
    candidates: list[str] = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [str(Path(base) / "Microsoft/Edge/Application/msedge.exe"),
                               str(Path(base) / "Google/Chrome/Application/chrome.exe"),
                               str(Path(base) / "BraveSoftware/Brave-Browser/Application/brave.exe")]
    elif sys.platform == "darwin":
        candidates += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                       "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                       "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                       "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser",
                     "brave-browser"):
            found = shutil.which(name)
            if found and os.path.isabs(found):
                candidates.append(found)
    return next((c for c in candidates if Path(c).is_file()), None)


def open_window(url: str) -> subprocess.Popen | None:
    browser = find_app_browser()
    if browser is None:
        webbrowser.open(url)
        return None
    profile = data_dir() / "window"
    profile.mkdir(parents=True, exist_ok=True)
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return subprocess.Popen(
        [browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", "--window-size=1480,940",
         "--disable-features=Translate,msEdgeSidebarV2,msHubApps,EdgeCollections",
         "--disable-sync"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def run_web(jarvis: Jarvis, *, open_app: bool = True, port: int = 0,
            on_ready: Callable[[str], None] | None = None) -> None:
    """Serve the HUD until its window has been closed (no page connected for a while)."""
    web = JarvisWeb(jarvis)
    web.start(port)
    if on_ready:
        on_ready(web.launch_url)
    if open_app:
        open_window(web.launch_url)
    started = time.monotonic()
    try:
        while not web.closed.is_set():
            time.sleep(0.5)
            hub = web.hub
            if hub.count:
                continue
            idle = time.monotonic() - hub.last_seen
            # Wait up to 60 s for the first window, then 6 s after the last one closed
            # (a reload reconnects within ~1.5 s).
            if (hub.ever_connected and idle > 6) or \
                    (not hub.ever_connected and time.monotonic() - started > 60):
                break
    except KeyboardInterrupt:
        pass
    finally:
        web.close()
