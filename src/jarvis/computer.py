"""Desktop control tools. Every action needs capability `computer.control` and approval.

* `open_target`  – open an https URL in the default browser or a workspace document with its
                   associated application (executables are refused; use run_command).
* `list_windows` / `focus_window` – Windows-native window management via user32 (no extra
                   dependency). Other platforms report that the feature is unavailable.
* `computer_action` – mouse, keyboard and screenshots through the optional pyautogui extra.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
import webbrowser
from typing import TYPE_CHECKING, Any

from .tools import Tool, params

if TYPE_CHECKING:
    from .engine import Jarvis

BLOCKED_OPEN_SUFFIXES = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
    ".wsh", ".msi", ".msp", ".scr", ".lnk", ".url", ".hta", ".cpl", ".jar", ".reg", ".sh",
    ".app", ".command", ".appref-ms", ".pif", ".inf", ".application", ".gadget", ".py",
    ".pyw"})


def _windows() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        raise RuntimeError("Fensterverwaltung ist derzeit nur unter Windows verfügbar.")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        found.append({"handle": int(hwnd), "title": buffer.value,
                      "minimized": bool(user32.IsIconic(hwnd))})
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def _focus(handle: int) -> None:
    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    if not user32.IsWindow(handle):
        raise ValueError("Fenster existiert nicht mehr.")
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(handle):
        raise RuntimeError("Windows hat den Fokuswechsel verweigert.")


def register_computer(jarvis: Jarvis) -> None:
    registry = jarvis.tools

    async def open_target(args: dict) -> Any:
        target = args["target"].strip()
        if target.lower().startswith(("https://", "http://")):
            if not target.lower().startswith("https://"):
                raise ValueError("Nur HTTPS-Adressen werden im Browser geöffnet.")
            opened = await asyncio.to_thread(webbrowser.open, target, 2)
            if not opened:
                raise RuntimeError("Kein Standardbrowser verfügbar.")
            return {"opened": target}
        path = registry.path(target)
        if not path.exists():
            raise ValueError("Datei oder Ordner existiert nicht.")
        if path.is_file() and path.suffix.lower() in BLOCKED_OPEN_SUFFIXES:
            raise ValueError("Ausführbare Dateien werden nicht geöffnet; run_command nutzen.")
        if sys.platform == "win32":
            await asyncio.to_thread(os.startfile, str(path))  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            await asyncio.to_thread(subprocess.Popen, [opener, str(path)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"opened": registry.relative(path)}

    async def list_windows(args: dict) -> Any:
        windows = await asyncio.to_thread(_windows)
        query = (args.get("filter") or "").lower()
        return [w for w in windows if query in w["title"].lower()][:100]

    async def focus_window(args: dict) -> Any:
        query = args["title"].lower()
        matches = [w for w in await asyncio.to_thread(_windows) if query in w["title"].lower()]
        if not matches:
            raise ValueError("Kein passendes Fenster gefunden.")
        await asyncio.to_thread(_focus, matches[0]["handle"])
        return {"focused": matches[0]["title"]}

    def input_sync(args: dict) -> Any:
        try:
            import pyautogui
        except ImportError:
            raise RuntimeError("Maus/Tastatur benötigt 'pip install jarvis-assistant[computer]'."
                               ) from None
        pyautogui.FAILSAFE = True  # Moving the mouse to a corner aborts automation.
        action = args["action"]

        def point() -> tuple[int, int]:
            if "x" not in args or "y" not in args:
                raise ValueError("x und y sind für diese Aktion erforderlich.")
            width, height = pyautogui.size()
            x, y = int(args["x"]), int(args["y"])
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError(f"Koordinate außerhalb des Bildschirms ({width}×{height}).")
            return x, y

        if action == "screen_info":
            width, height = pyautogui.size()
            x, y = pyautogui.position()
            return {"width": width, "height": height, "mouse": [x, y]}
        if action == "screenshot":
            directory = jarvis.workspace / ".jarvis" / "screenshots"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{uuid.uuid4()}.png"
            image = pyautogui.screenshot()
            image.save(path)
            return {"path": registry.relative(path), "width": image.width,
                    "height": image.height}
        if action in ("click", "double_click", "right_click", "move"):
            x, y = point()
            {"click": lambda: pyautogui.click(x, y),
             "double_click": lambda: pyautogui.doubleClick(x, y),
             "right_click": lambda: pyautogui.rightClick(x, y),
             "move": lambda: pyautogui.moveTo(x, y, duration=0.15)}[action]()
        elif action == "type":
            text = args.get("text", "")
            if not text or len(text) > 5000:
                raise ValueError("Text muss 1 bis 5000 Zeichen enthalten.")
            pyautogui.write(text, interval=0.02)
        elif action in ("hotkey", "press"):
            keys = args.get("keys") or []
            if not keys or len(keys) > 5 or any(k not in pyautogui.KEYBOARD_KEYS for k in keys):
                raise ValueError("Unbekannte oder zu viele Tasten.")
            pyautogui.hotkey(*keys) if action == "hotkey" else pyautogui.press(keys)
        elif action == "scroll":
            pyautogui.scroll(int(args.get("amount", 0)))
        else:
            raise ValueError("Unbekannte Computeraktion.")
        return {"done": action}

    async def computer_action(args: dict) -> Any:
        return await asyncio.to_thread(input_sync, args)

    registry.register(Tool("open_target",
        "Open an https URL in the default browser, or a workspace file/folder with its "
        "default application.",
        params({"target": {"type": "string"}}, ["target"]),
        "computer.control", True, open_target))
    registry.register(Tool("list_windows", "List visible desktop windows (Windows).",
        params({"filter": {"type": "string"}}), "computer.control", False, list_windows))
    registry.register(Tool("focus_window",
        "Bring the first visible window whose title contains the text to the foreground.",
        params({"title": {"type": "string"}}, ["title"]),
        "computer.control", True, focus_window))
    registry.register(Tool("computer_action",
        "Control mouse and keyboard or save a screenshot. Coordinates are screen pixels; "
        "call screen_info first.",
        params({"action": {"type": "string", "enum": [
                    "screen_info", "screenshot", "click", "double_click", "right_click",
                    "move", "type", "hotkey", "press", "scroll"]},
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "text": {"type": "string"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "amount": {"type": "integer"}}, ["action"]),
        "computer.control", True, computer_action))
