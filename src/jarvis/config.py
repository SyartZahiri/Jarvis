from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://127.0.0.1:11434",
    "compatible": "",
}
KINDS = frozenset(DEFAULT_ENDPOINTS)
KIND_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "gemini": "Google Gemini",
    "openrouter": "OpenRouter",
    "ollama": "Ollama (lokal)",
    "compatible": "OpenAI-kompatibel (eigener Endpunkt)",
}
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def data_dir() -> Path:
    override = os.environ.get("JARVIS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        return base / "JARVIS"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "jarvis"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(slots=True)
class ProviderProfile:
    id: str
    name: str
    kind: str
    model: str
    endpoint: str
    max_context_chars: int = 60_000
    max_output_tokens: int = 4_096
    temperature: float | None = None
    # Reasoning ("thinking") for models that support it; None keeps the model default.
    # Currently sent to Ollama only, where qwen3/deepseek-r1 think by default and are
    # several times slower for short answers.
    think: bool | None = None

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Unbekannter Anbieter: {self.kind}")
        if not self.name.strip() or not self.model.strip():
            raise ValueError("Name und Modell sind erforderlich.")
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Ungültiger API-Endpunkt.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Der API-Endpunkt darf keine Zugangsdaten oder Parameter enthalten.")
        if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
            raise ValueError("Unverschlüsseltes HTTP ist nur für lokale Endpunkte erlaubt.")
        if not 4_000 <= int(self.max_context_chars) <= 4_000_000:
            raise ValueError("Kontextbudget muss zwischen 4000 und 4000000 Zeichen liegen.")
        if not 64 <= int(self.max_output_tokens) <= 200_000:
            raise ValueError("Ausgabelimit muss zwischen 64 und 200000 Tokens liegen.")
        if self.temperature is not None and not 0 <= float(self.temperature) <= 2:
            raise ValueError("Temperatur muss zwischen 0 und 2 liegen.")
        if self.think is not None and not isinstance(self.think, bool):
            raise ValueError("Nachdenken muss an, aus oder Standard sein.")

    @property
    def is_local(self) -> bool:
        return urlparse(self.endpoint).hostname in LOCAL_HOSTS

    def needs_key(self) -> bool:
        return self.kind != "ollama" and not (self.kind == "compatible" and self.is_local)

    @classmethod
    def create(cls, name: str, kind: str, model: str, endpoint: str = "",
               **options) -> ProviderProfile:
        profile = cls(str(uuid.uuid4()), name.strip(), kind, model.strip(),
                      (endpoint or DEFAULT_ENDPOINTS.get(kind, "")).rstrip("/"), **options)
        profile.validate()
        return profile


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "config.json"

    def load(self) -> tuple[list[ProviderProfile], str | None]:
        if not self.path.exists():
            return [], None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        fields = set(ProviderProfile.__dataclass_fields__)
        profiles = [ProviderProfile(**{k: v for k, v in item.items() if k in fields})
                    for item in raw.get("profiles", [])]
        for profile in profiles:
            profile.validate()
        active = raw.get("active_profile")
        return profiles, active if any(p.id == active for p in profiles) else None

    def save(self, profiles: list[ProviderProfile], active: str | None) -> None:
        if active is not None and all(p.id != active for p in profiles):
            raise ValueError("Aktives Profil existiert nicht.")
        for profile in profiles:
            profile.validate()
        atomic_json(self.path, {"version": 1, "active_profile": active,
                                "profiles": [asdict(p) for p in profiles]})



SETTINGS_DEFAULTS: dict[str, object] = {
    "search_provider": "brave",      # brave | searxng
    "searxng_url": "",
    "voice_model_path": "",
    "voice_seconds": 8,
    "speak_replies": False,
    "include_claude_skills": False,  # also read ~/.claude/skills (opt-in: sent to the model)
}


class SettingsStore:
    """Small validated key/value settings without secrets (settings.json)."""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"

    def load(self) -> dict[str, object]:
        values = dict(SETTINGS_DEFAULTS)
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            values.update({k: v for k, v in raw.get("values", {}).items()
                           if k in SETTINGS_DEFAULTS})
        # Pre-0.2 installations kept the voice model path in voice.json.
        legacy = self.path.with_name("voice.json")
        if not values["voice_model_path"] and legacy.exists():
            try:
                values["voice_model_path"] = json.loads(
                    legacy.read_text(encoding="utf-8")).get("model_path", "")
            except ValueError:
                pass
        return values

    def get(self, key: str):
        return self.load()[key]

    def update(self, **changes) -> dict[str, object]:
        unknown = set(changes) - set(SETTINGS_DEFAULTS)
        if unknown:
            raise ValueError(f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")
        values = self.load()
        values.update(changes)
        if values["search_provider"] not in ("brave", "searxng"):
            raise ValueError("Suchanbieter muss 'brave' oder 'searxng' sein.")
        url = str(values["searxng_url"] or "").rstrip("/")
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or (
                    parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS):
                raise ValueError("SearXNG-URL muss HTTPS oder ein lokaler HTTP-Endpunkt sein.")
        values["searxng_url"] = url
        if not 1 <= int(values["voice_seconds"]) <= 30:
            raise ValueError("Aufnahmedauer muss 1 bis 30 Sekunden betragen.")
        atomic_json(self.path, {"version": 1, "values": values})
        return values
