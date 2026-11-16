"""Claude-compatible skills (`<root>/<name>/SKILL.md`), read as untrusted text.

Only the frontmatter (`name`, `description`) is put into the system prompt; the model
loads the full body on demand with `read_skill`. Bundled scripts are never executed.
Several roots are searched in priority order; the first skill with a given name wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_BYTES = 100_000


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    path: Path
    source: str = "workspace"


def parse_frontmatter(raw: str) -> dict[str, str]:
    """Parse the flat YAML subset used by SKILL.md, including folded/literal blocks."""
    match = re.match(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", raw, re.S)
    if not match:
        return {}
    fields: dict[str, str] = {}
    key: str | None = None
    block: list[str] = []
    style = ""

    def flush() -> None:
        if key is not None and style:
            joiner = "\n" if style == "|" else " "
            fields[key] = joiner.join(line.strip() for line in block if line.strip())

    for line in match.group(1).splitlines():
        if key is not None and style and (line.startswith((" ", "\t")) or not line.strip()):
            block.append(line)
            continue
        flush()
        key, style, block = None, "", []
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        name, value = line.split(":", 1)
        name, value = name.strip(), value.strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            key, style = name, value[0]
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[name] = value
    flush()
    return fields


class SkillRegistry:
    def __init__(self, root: Path, extra_roots: list[tuple[Path, str]] | None = None):
        self.root = root
        self.roots: list[tuple[Path, str]] = [(root, "workspace")] + list(extra_roots or [])

    def list(self) -> list[Skill]:
        found: dict[str, Skill] = {}
        for root, source in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/SKILL.md")):
                try:
                    if path.stat().st_size > MAX_SKILL_BYTES:
                        continue
                    fields = parse_frontmatter(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
                name, description = fields.get("name", ""), fields.get("description", "")
                if name and description and name not in found:
                    found[name] = Skill(name, " ".join(description.split())[:1024], path,
                                        source)
        return sorted(found.values(), key=lambda s: s.name)

    def read(self, name: str) -> str:
        for skill in self.list():
            if skill.name == name:
                content = skill.path.read_text(encoding="utf-8")
                resources = sorted(p.relative_to(skill.path.parent).as_posix()
                                   for p in skill.path.parent.rglob("*")
                                   if p.is_file() and p.name != "SKILL.md")[:50]
                if resources:
                    content += ("\n\n<!-- JARVIS: Weitere Dateien dieses Skills (nicht "
                                "automatisch geladen oder ausgeführt): "
                                + ", ".join(resources) + " -->")
                return content
        raise ValueError(f"Skill nicht gefunden: {name}")
