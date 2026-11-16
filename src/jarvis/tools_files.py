"""File management and programming tools, confined to the workspace."""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .tools import SKIP_DIRS, Tool, params

if TYPE_CHECKING:
    from .tools import ToolRegistry

READ_LIMIT_BYTES = 5_000_000
READ_RETURN_CHARS = 60_000
WRITE_LIMIT_BYTES = 2_000_000
_NUMBERED = re.compile(r"^ *\d+  ")   # prefix that read_file puts in front of each line


def unnumber(old: str, new: str, text: str) -> tuple[str, str]:
    """Small models copy read_file's line numbers into edit_file. Drop them when that is
    the only reason old_text does not match."""
    def numbered(s: str) -> bool:
        return all(_NUMBERED.match(line) for line in s.split("\n"))
    if old in text or not numbered(old):
        return old, new
    def strip(s: str) -> str:
        return "\n".join(_NUMBERED.sub("", line, count=1) for line in s.split("\n"))
    return strip(old), strip(new) if numbered(new) else new


def _is_binary(path: Path) -> bool:
    with path.open("rb") as stream:
        return b"\x00" in stream.read(8192)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    newline = ""  # Keep the line endings the model provided.
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as stream:
            stream.write(content)
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_executable(name: str, workspace: Path) -> str:
    """Find a program like a shell would, but never in the current directory.

    Windows' default search (and shutil.which) checks the current directory first, so a
    planted `git.exe` in the workspace could shadow the real one. Bare names are looked up
    only in absolute PATH entries; explicit relative paths resolve inside the workspace.
    """
    if os.sep in name or (os.altsep and os.altsep in name):
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = (workspace / candidate).resolve()
            if not candidate.is_relative_to(workspace):
                raise ValueError("Relativer Programmpfad liegt außerhalb des Arbeitsbereichs.")
        if not candidate.is_file():
            raise ValueError(f"Programm nicht gefunden: {name}")
        return str(candidate)
    extensions = [""]
    if sys.platform == "win32":
        pathext = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").lower().split(";")
                   if e]
        extensions = [""] if Path(name).suffix.lower() in pathext else pathext
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        base = Path(directory.strip().strip('"'))
        if not directory.strip() or not base.is_absolute():
            continue  # Skips "." and relative entries.
        for extension in extensions:
            candidate = base / (name + extension)
            if candidate.is_file() and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
                return str(candidate)
    raise ValueError(f"Programm nicht im PATH gefunden: {name}")


def register_file_tools(registry: ToolRegistry) -> None:
    ws = registry.workspace

    def walk(root: Path, pattern: str | None, max_files: int):
        count = 0
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                             or d in (".github",))
            for name in sorted(files):
                path = Path(current) / name
                rel = path.relative_to(ws).as_posix()
                if pattern and not (fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern)):
                    continue
                count += 1
                if count > max_files:
                    return
                yield path

    async def list_files(args: dict) -> Any:
        path = registry.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("Kein Verzeichnis.")
        if args.get("recursive"):
            def collect():
                return [{"path": registry.relative(p), "bytes": p.stat().st_size}
                        for p in walk(path, args.get("pattern"), 1000)]
            entries = await asyncio.to_thread(collect)
            return {"root": registry.relative(path), "files": entries,
                    "truncated": len(entries) >= 1000}
        entries = []
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]:
            entry: dict[str, Any] = {"name": item.name, "directory": item.is_dir()}
            if item.is_file():
                entry["bytes"] = item.stat().st_size
            entries.append(entry)
        return {"root": registry.relative(path), "entries": entries}

    async def read_file(args: dict) -> Any:
        path = registry.path(args["path"])
        if not path.is_file():
            raise ValueError("Datei nicht gefunden.")
        if path.stat().st_size > READ_LIMIT_BYTES:
            raise ValueError("Datei ist größer als 5 MB.")
        if _is_binary(path):
            raise ValueError("Binärdatei; nur Textdateien können gelesen werden.")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, args.get("start_line", 1))
        selected, used, end = [], 0, start - 1
        for number in range(start - 1, min(len(lines),
                                            start - 1 + args.get("max_lines", 2000))):
            line = f"{number + 1:>5}  {lines[number]}"
            if used + len(line) > READ_RETURN_CHARS:
                break
            selected.append(line)
            used += len(line) + 1
            end = number + 1
        return {"path": registry.relative(path), "total_lines": len(lines),
                "start_line": start, "end_line": end, "content": "\n".join(selected),
                "complete": start == 1 and end >= len(lines)}

    async def write_file(args: dict) -> Any:
        content = args["content"]
        if len(content.encode("utf-8")) > WRITE_LIMIT_BYTES:
            raise ValueError("Datei darf höchstens 2 MB groß sein.")
        path = registry.path(args["path"])
        if path.is_dir():
            raise ValueError("Pfad ist ein Verzeichnis.")
        existed = path.exists()
        _atomic_write(path, content)
        return {"written": registry.relative(path), "created": not existed,
                "bytes": len(content.encode("utf-8"))}

    async def edit_file(args: dict) -> Any:
        path = registry.path(args["path"])
        if not path.is_file():
            raise ValueError("Datei nicht gefunden.")
        if not args["old_text"]:
            raise ValueError("old_text darf nicht leer sein.")
        text = path.read_text(encoding="utf-8")
        old, new = unnumber(args["old_text"], args["new_text"], text)
        count = text.count(old)
        if count == 0:
            raise ValueError("old_text kommt in der Datei nicht vor; zuerst read_file nutzen.")
        if count > 1 and not args.get("replace_all"):
            raise ValueError(f"old_text kommt {count}-mal vor; mehr Kontext angeben "
                             "oder replace_all setzen.")
        _atomic_write(path, text.replace(old, new) if args.get("replace_all")
                      else text.replace(old, new, 1))
        return {"edited": registry.relative(path),
                "replacements": count if args.get("replace_all") else 1}

    async def search_files(args: dict) -> Any:
        query = args["query"]
        if not query or len(query) > 300:
            raise ValueError("Suchmuster muss 1 bis 300 Zeichen enthalten.")
        flags = 0 if args.get("case_sensitive") else re.IGNORECASE
        expression = re.compile(query if args.get("regex") else re.escape(query), flags)
        root = registry.path(args.get("path", "."))
        if not root.is_dir():
            raise ValueError("Kein Verzeichnis.")

        def scan():
            matches, scanned = [], 0
            for path in walk(root, args.get("pattern"), 5000):
                try:
                    if path.stat().st_size > 1_000_000 or _is_binary(path):
                        continue
                    scanned += 1
                    with path.open(encoding="utf-8", errors="replace") as stream:
                        for number, line in enumerate(stream, 1):
                            if expression.search(line):
                                matches.append({"path": registry.relative(path), "line": number,
                                                "text": line.rstrip()[:300]})
                                if len(matches) >= 200:
                                    return matches, scanned, True
                except OSError:
                    continue
            return matches, scanned, False

        matches, scanned, truncated = await asyncio.to_thread(scan)
        return {"matches": matches, "files_scanned": scanned, "truncated": truncated}

    async def make_directory(args: dict) -> Any:
        path = registry.path(args["path"])
        path.mkdir(parents=True, exist_ok=True)
        return {"directory": registry.relative(path)}

    async def move_path(args: dict) -> Any:
        source, target = registry.path(args["source"]), registry.path(args["destination"])
        if not source.exists():
            raise ValueError("Quelle existiert nicht.")
        if source == ws:
            raise ValueError("Der Arbeitsbereich selbst kann nicht verschoben werden.")
        if target.exists():
            raise ValueError("Ziel existiert bereits.")
        if target.is_relative_to(source):
            raise ValueError("Ein Ordner kann nicht in sich selbst verschoben werden.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {"moved": registry.relative(source), "to": registry.relative(target)}

    async def delete_path(args: dict) -> Any:
        """Moves into .jarvis/trash instead of deleting, so every deletion is reversible."""
        path = registry.path(args["path"])
        if not path.exists():
            raise ValueError("Pfad existiert nicht.")
        trash_root = ws / ".jarvis" / "trash"
        if path == ws or trash_root.is_relative_to(path):
            raise ValueError("Dieser Pfad kann nicht gelöscht werden.")
        target = trash_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f") / registry.relative(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        return {"trashed": registry.relative(path), "restore_from": registry.relative(target)}

    async def run_command(args: dict) -> Any:
        argv = args["argv"]
        if not argv or len(argv) > 64 or not all(isinstance(i, str) and i for i in argv):
            raise ValueError("argv muss eine Liste von 1 bis 64 Argumenten sein.")
        cwd = registry.path(args.get("cwd", "."))
        if not cwd.is_dir():
            raise ValueError("Arbeitsverzeichnis existiert nicht.")
        timeout = args.get("timeout_seconds", 60)
        executable = resolve_executable(argv[0], ws)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flash
        process = await asyncio.create_subprocess_exec(
            executable, *argv[1:], cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError) as exc:
            process.kill()
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ValueError(f"Befehl nach {timeout} Sekunden beendet.") from None

        def tail(data: bytes, limit: int) -> str:
            text = data.decode("utf-8", errors="replace")
            return text if len(text) <= limit else "… " + text[-limit:]

        return {"exit_code": process.returncode, "stdout": tail(stdout, 12_000),
                "stderr": tail(stderr, 6_000)}

    path_prop = {"type": "string", "description": "Path relative to the workspace"}
    registry.register(Tool("list_files",
        "List a workspace directory, or recursively list files (optionally filtered by a "
        "glob pattern such as '*.py').",
        params({"path": path_prop, "recursive": {"type": "boolean"},
                "pattern": {"type": "string"}}),
        "files.read", False, list_files))
    registry.register(Tool("read_file",
        "Read a UTF-8 text file with line numbers. Use start_line/max_lines for large files.",
        params({"path": path_prop, "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 5000}}, ["path"]),
        "files.read", False, read_file))
    registry.register(Tool("search_files",
        "Search text in workspace files (like grep). Returns path, line number and line.",
        params({"query": {"type": "string"}, "path": path_prop,
                "pattern": {"type": "string", "description": "Glob filter, e.g. '*.py'"},
                "regex": {"type": "boolean"}, "case_sensitive": {"type": "boolean"}},
               ["query"]),
        "files.read", False, search_files))
    registry.register(Tool("write_file",
        "Create or fully replace a UTF-8 text file. Prefer edit_file for small changes.",
        params({"path": path_prop, "content": {"type": "string"}}, ["path", "content"]),
        "files.write", True, write_file))
    registry.register(Tool("edit_file",
        "Replace an exact text snippet in a file. old_text must match exactly once unless "
        "replace_all is true. Leave out the line-number prefix that read_file shows.",
        params({"path": path_prop, "old_text": {"type": "string"},
                "new_text": {"type": "string"}, "replace_all": {"type": "boolean"}},
               ["path", "old_text", "new_text"]),
        "files.write", True, edit_file))
    registry.register(Tool("make_directory", "Create a directory (with parents).",
        params({"path": path_prop}, ["path"]), "files.write", True, make_directory))
    registry.register(Tool("move_path", "Move or rename a file or directory.",
        params({"source": path_prop, "destination": path_prop}, ["source", "destination"]),
        "files.write", True, move_path))
    registry.register(Tool("delete_path",
        "Delete a file or directory by moving it to the workspace trash (.jarvis/trash).",
        params({"path": path_prop}, ["path"]), "files.write", True, delete_path))
    registry.register(Tool("run_command",
        "Run a program without a shell, e.g. ['python', '-m', 'pytest']. Returns exit code "
        "and output.",
        params({"argv": {"type": "array", "items": {"type": "string"}},
                "cwd": path_prop,
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 900}},
               ["argv"]),
        "system.execute", True, run_command))
