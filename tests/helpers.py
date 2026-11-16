"""Shared fixtures: isolated data directories and an in-memory secret store."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from jarvis.config import ConfigStore, ProviderProfile, SettingsStore
from jarvis.engine import Jarvis
from jarvis.models import Completion
from jarvis.permissions import PermissionPolicy
from jarvis.secrets import SecretStore
from jarvis.storage import Database


class MemorySecrets(SecretStore):
    """Platform independent stand-in; the DPAPI path has its own Windows-only test."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        if not key or not value:
            raise ValueError("leer")
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class FakeClient:
    """Scripted model: each entry is a Completion or a callable(messages, tools)."""

    def __init__(self, script, profile: ProviderProfile):
        self.script = list(script)
        self.profile = profile
        self.calls: list[tuple[list, list]] = []

    async def complete(self, messages, tools=None, on_text=None):
        self.calls.append((list(messages), list(tools or [])))
        step = self.script.pop(0)
        result = step(messages, tools) if callable(step) else step
        if asyncio.iscoroutine(result):
            result = await result
        if on_text and result.text:
            for word in result.text.split(" "):
                on_text(word + " ")
        return result


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.data = self.root / "data"
        self.database = Database(self.data / "jarvis.sqlite3")
        self.config = ConfigStore(self.data / "config.json")
        self.secrets = MemorySecrets()
        self.permissions = PermissionPolicy(self.data / "permissions.json")
        self.settings = SettingsStore(self.data / "settings.json")
        self.jarvis = self.make_jarvis()

    def make_jarvis(self) -> Jarvis:
        return Jarvis(self.workspace, database=self.database, config=self.config,
                      secrets=self.secrets, permissions=self.permissions,
                      settings=self.settings, plugin_state=self.data / "plugins.json")

    def use_fake_model(self, *script) -> FakeClient:
        profile = ProviderProfile.create("Local", "ollama", "test-model")
        self.config.save([profile], profile.id)
        fake = FakeClient(script, profile)
        self.jarvis.client = lambda profile=None: fake
        return fake

    @staticmethod
    def run_async(coroutine):
        return asyncio.run(coroutine)


def text(value: str) -> Completion:
    return Completion(value)
