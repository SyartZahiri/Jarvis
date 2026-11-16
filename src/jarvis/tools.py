"""Tool registry: the single gate through which the model reaches side effects."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .secrets import SecretStore
from .skills import SkillRegistry
from .storage import Database

ToolFunction = Callable[[dict[str, Any]], Awaitable[Any]]
SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
                       ".jarvis", ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist",
                       "build", ".idea", ".tox"})


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    capability: str
    requires_approval: bool
    run: ToolFunction
    source: str = "builtin"

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


def params(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


class ToolRegistry:
    def __init__(self, workspace: Path, database: Database, secrets: SecretStore,
                 skills: SkillRegistry, settings=None):
        self.workspace = workspace.resolve()
        self.database = database
        self.secrets = secrets
        self.skills = skills
        self.settings = settings
        self._tools: dict[str, Tool] = {}
        from .tools_files import register_file_tools
        from .tools_web import register_web_tools
        register_file_tools(self)
        register_web_tools(self)
        self._register_memory_and_skills()

    # Registry ------------------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Werkzeug bereits registriert: {tool.name}")
        if not tool.name.replace("_", "").isalnum() or len(tool.name) > 64:
            raise ValueError(f"Ungültiger Werkzeugname: {tool.name}")
        if tool.parameters.get("type") != "object" or "properties" not in tool.parameters:
            raise ValueError(f"{tool.name}: Parameter müssen ein JSON-Schema-Objekt sein.")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> set[str]:
        return set(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def path(self, value: str) -> Path:
        """Resolve a user/model supplied path; symlinks may not escape the workspace."""
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("Ungültiger Pfad.")
        candidate = (self.workspace / value).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError("Pfad liegt außerhalb des Arbeitsbereichs.")
        return candidate

    _path = path  # Backwards compatible alias for plugins written against 0.1.

    def relative(self, path: Path) -> str:
        return path.relative_to(self.workspace).as_posix() or "."

    # Memory and skills ---------------------------------------------------------------------

    def _register_memory_and_skills(self) -> None:
        async def remember(args: dict) -> Any:
            return {"id": self.database.remember(args["content"])}

        async def recall(args: dict) -> Any:
            return [{"id": memory_id, "content": content} for memory_id, content in
                    self.database.recall(args["query"], args.get("limit", 8))]

        async def forget(args: dict) -> Any:
            return {"deleted": self.database.forget(args["id"])}

        async def list_skills(_args: dict) -> Any:
            return [{"name": s.name, "description": s.description} for s in self.skills.list()]

        async def read_skill(args: dict) -> Any:
            return self.skills.read(args["name"])

        self.register(Tool("remember",
            "Save a durable personal note. Use only when the user asks to remember something.",
            params({"content": {"type": "string"}}, ["content"]),
            "memory.write", True, remember))
        self.register(Tool("forget", "Delete a saved memory by its id.",
            params({"id": {"type": "string"}}, ["id"]), "memory.write", True, forget))
        self.register(Tool("recall",
            "Search saved memories by relevance (any word matches, prefix aware). "
            "An empty query lists the newest memories.",
            params({"query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["query"]),
            "memory.read", False, recall))
        self.register(Tool("list_skills", "List installed SKILL.md skills.",
            params({}), "skills.read", False, list_skills))
        self.register(Tool("read_skill",
            "Read an installed Claude-compatible SKILL.md by name before following it.",
            params({"name": {"type": "string"}}, ["name"]),
            "skills.read", False, read_skill))
