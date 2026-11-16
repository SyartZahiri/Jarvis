"""Human-readable descriptions of actions awaiting confirmation (shared by GUI and CLI)."""
from __future__ import annotations

import difflib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .permissions import CAPABILITIES

MAX_DETAIL_LINES = 400


@dataclass(frozen=True, slots=True)
class ApprovalView:
    title: str          # One line: what will happen
    capability: str     # Human label of the capability
    detail: str         # Monospaced detail (diff, command, JSON)
    risk: str           # "normal" | "high"


def _diff(before: str, after: str, path: str) -> str:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      f"a/{path}", f"b/{path}", lineterm="", n=2))
    if len(lines) > MAX_DETAIL_LINES:
        lines = lines[:MAX_DETAIL_LINES] + [f"… {len(lines) - MAX_DETAIL_LINES} weitere Zeilen"]
    return "\n".join(lines) or "(keine inhaltliche Änderung)"


def _read(workspace: Path, relative: str) -> str | None:
    try:
        path = (workspace / relative).resolve()
        if path.is_relative_to(workspace) and path.is_file() and path.stat().st_size < 2_000_000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


def describe(tool_name: str, capability: str, args: dict[str, Any],
             workspace: Path) -> ApprovalView:
    label = CAPABILITIES.get(capability, capability)
    workspace = workspace.resolve()
    path = str(args.get("path", ""))
    if tool_name == "write_file":
        before = _read(workspace, path)
        content = str(args.get("content", ""))
        if before is None:
            preview = content.splitlines()[:MAX_DETAIL_LINES]
            return ApprovalView(f"Neue Datei anlegen: {path}", label,
                                "\n".join("+" + line for line in preview), "normal")
        return ApprovalView(f"Datei überschreiben: {path}", label,
                            _diff(before, content, path), "normal")
    if tool_name == "edit_file":
        before = _read(workspace, path)
        old, new = str(args.get("old_text", "")), str(args.get("new_text", ""))
        if before is not None:
            from .tools_files import unnumber
            old, new = unnumber(old, new, before)
        if before is not None and old in before:
            after = before.replace(old, new) if args.get("replace_all") else \
                before.replace(old, new, 1)
            detail = _diff(before, after, path)
        else:
            detail = _diff(old, new, path)
        return ApprovalView(f"Datei bearbeiten: {path}", label, detail, "normal")
    if tool_name == "delete_path":
        return ApprovalView(f"In den Papierkorb des Arbeitsbereichs verschieben: {path}",
                            label, "Wiederherstellbar aus .jarvis/trash/", "normal")
    if tool_name == "move_path":
        return ApprovalView(f"Verschieben: {args.get('source')} → {args.get('destination')}",
                            label, "", "normal")
    if tool_name == "make_directory":
        return ApprovalView(f"Ordner anlegen: {path}", label, "", "normal")
    if tool_name == "run_command":
        argv = [str(item) for item in args.get("argv", [])]
        command = subprocess.list2cmdline(argv)
        from .tools_files import resolve_executable
        try:
            program = resolve_executable(argv[0], workspace) if argv else "?"
        except ValueError as exc:
            program = f"nicht auflösbar – {exc}"
        detail = f"> {command}\n\nProgramm: {program}\nOrdner:   {args.get('cwd', '.')}\nTimeout: " \
                 f"{args.get('timeout_seconds', 60)} s\n\nDas Programm läuft mit deinen " \
                 "Benutzerrechten und ist nicht auf den Arbeitsbereich beschränkt."
        return ApprovalView("Programm ausführen", label, detail, "high")
    if tool_name in ("remember", "forget"):
        text = args.get("content") or args.get("id", "")
        return ApprovalView("Im Gedächtnis speichern" if tool_name == "remember"
                            else "Erinnerung löschen", label, str(text), "normal")
    if tool_name == "open_target":
        return ApprovalView(f"Öffnen: {args.get('target')}", label, "", "normal")
    if tool_name == "focus_window":
        return ApprovalView(f"Fenster in den Vordergrund holen: {args.get('title')}",
                            label, "", "normal")
    if tool_name == "computer_action":
        action = args.get("action")
        detail = json.dumps({k: v for k, v in args.items() if k != "action"},
                            ensure_ascii=False, indent=2)
        return ApprovalView(f"Computersteuerung: {action}", label,
                            detail if detail != "{}" else "", "high")
    detail = json.dumps(args, ensure_ascii=False, indent=2)
    return ApprovalView(f"Werkzeug {tool_name} ausführen", label, detail[:20_000], "normal")
