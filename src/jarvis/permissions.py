from __future__ import annotations

import json
from pathlib import Path

from .config import atomic_json, data_dir


CAPABILITIES = {
    "files.read": "Dateien im Arbeitsbereich lesen",
    "files.write": "Dateien im Arbeitsbereich ändern",
    "system.execute": "Programme und Befehle ausführen",
    "memory.read": "Langzeitgedächtnis lesen",
    "memory.write": "Langzeitgedächtnis ändern",
    "network.search": "Im Web suchen",
    "network.fetch": "Webseiten abrufen",
    "skills.read": "Lokale Skills lesen",
    "agent.delegate": "Spezialisierte Agenten beauftragen",
    "computer.control": "Maus, Tastatur und Programme steuern",
    "compute.local": "Lokale Berechnungen ohne Seiteneffekte",
}
DEFAULT_ENABLED = frozenset({"memory.read", "skills.read", "compute.local"})
# Capabilities whose tools must ask for a one-time confirmation per call, regardless of
# what an individual tool or plugin declares.
APPROVAL_REQUIRED = frozenset({"files.write", "system.execute", "memory.write",
                               "computer.control"})


class PermissionPolicy:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "permissions.json"

    def enabled(self) -> set[str]:
        if not self.path.exists():
            return set(DEFAULT_ENABLED)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return set(raw.get("enabled", [])) & CAPABILITIES.keys()

    def set_enabled(self, capabilities: set[str]) -> None:
        if capabilities - CAPABILITIES.keys():
            raise ValueError("Unbekannte Berechtigung.")
        atomic_json(self.path, {"version": 1, "enabled": sorted(capabilities)})

    def allows(self, capability: str) -> bool:
        return capability in self.enabled()
