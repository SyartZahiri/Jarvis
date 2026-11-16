"""Trusted Python plugins, loaded only after explicit activation.

Manifest `plugins/<name>/plugin.json`: {"name", "description", "api": 1, "version"?}
Code `plugins/<name>/plugin.py`: def register(registry: ToolRegistry) -> None

A failing plugin is isolated: its error is reported and every tool it managed to
register is removed again, so the rest of JARVIS keeps working.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import atomic_json, data_dir
from .permissions import CAPABILITIES
from .tools import ToolRegistry

PLUGIN_API = 1


@dataclass(frozen=True, slots=True)
class PluginInfo:
    name: str
    description: str
    path: Path
    version: str = ""
    api: int = 1


class PluginManager:
    def __init__(self, root: Path, settings_path: Path | None = None):
        self.root = root.resolve()
        self.settings_path = settings_path or data_dir() / "plugins.json"

    def list(self) -> list[PluginInfo]:
        if not self.root.exists():
            return []
        found = []
        for manifest in sorted(self.root.glob("*/plugin.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                name = data["name"]
                if name != manifest.parent.name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                    continue
                script = manifest.parent / "plugin.py"
                if script.is_file() and script.resolve().is_relative_to(self.root):
                    found.append(PluginInfo(name, str(data["description"]), script,
                                            str(data.get("version", "")),
                                            int(data.get("api", 1))))
            except (KeyError, ValueError, TypeError, OSError):
                continue
        return found

    def enabled(self) -> set[str]:
        if not self.settings_path.exists():
            return set()
        raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        return set(raw.get("enabled", []))

    def set_enabled(self, enabled: set[str]) -> None:
        known = {plugin.name for plugin in self.list()}
        if enabled - known:
            raise ValueError("Unbekanntes Plugin.")
        atomic_json(self.settings_path, {"version": 1, "enabled": sorted(enabled)})

    def load_enabled(self, registry: ToolRegistry) -> dict[str, str]:
        """Load enabled plugins; returns {plugin name: error message} for failures."""
        errors: dict[str, str] = {}
        enabled = self.enabled()
        for plugin in self.list():
            if plugin.name not in enabled:
                continue
            before = registry.names()
            try:
                self._load(plugin, registry, before)
            except Exception as exc:  # Isolation boundary for third-party code.
                for name in registry.names() - before:
                    registry.unregister(name)
                errors[plugin.name] = f"{type(exc).__name__}: {exc}"
        return errors

    @staticmethod
    def _load(plugin: PluginInfo, registry: ToolRegistry, before: set[str]) -> None:
        if plugin.api > PLUGIN_API:
            raise RuntimeError(f"benötigt Plugin-API {plugin.api}, unterstützt wird "
                               f"{PLUGIN_API}.")
        module_name = f"jarvis_plugin_{plugin.name}"
        spec = importlib.util.spec_from_file_location(module_name, plugin.path)
        if spec is None or spec.loader is None:
            raise RuntimeError("konnte nicht geladen werden.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise RuntimeError("hat keine register-Funktion.")
            register(registry)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        for name in registry.names() - before:
            tool = registry.get(name)
            tool.source = f"plugin:{plugin.name}"
            if tool.capability not in CAPABILITIES:
                raise RuntimeError(f"Werkzeug {name} nutzt unbekannte Berechtigung "
                                   f"'{tool.capability}'.")
