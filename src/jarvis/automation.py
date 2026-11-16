from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

from .engine import Jarvis

LEASE_MINUTES = 30


class AutomationRunner:
    """Runs interval prompts while a JARVIS process is active.

    Each due job is claimed atomically in SQLite (next_run moves to a lease time), so a
    desktop window and `jarvis automations serve` never execute the same run twice. A run
    that crashes is retried after the lease expires. Automations have no confirmation
    channel: tools that need approval are refused.
    """

    def __init__(self, jarvis: Jarvis):
        self.jarvis = jarvis

    def add(self, prompt: str, interval_minutes: int, start_now: bool = False) -> str:
        delay = 0 if start_now else interval_minutes
        next_run = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()
        return self.jarvis.database.add_automation(prompt, interval_minutes, next_run)

    async def run_due(self) -> list[tuple[str, str]]:
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(minutes=LEASE_MINUTES)).isoformat()
        results = []
        for automation_id, prompt, interval, next_run in \
                self.jarvis.database.due_automations(now.isoformat()):
            if not self.jarvis.database.claim_automation(automation_id, next_run, lease):
                continue  # Another process took it.
            try:
                _, result = await self.jarvis.ask(prompt)
            except Exception as exc:
                result = f"Fehler: {type(exc).__name__}: {exc}"
            following = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat()
            self.jarvis.database.complete_automation(automation_id, following, result)
            results.append((automation_id, result))
        return results

    async def serve(self, on_result: Callable[[str, str], None] | None = None,
                    interval_seconds: float = 30) -> None:
        while True:
            for item in await self.run_due():
                if on_result:
                    on_result(*item)
            await asyncio.sleep(interval_seconds)
