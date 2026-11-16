"""Conversation history preparation: protocol repair and context-window budgeting.

Providers reject histories in which an assistant tool call has no matching tool result
(or a result has no call). Interrupted runs can leave such gaps, so history is repaired
before every request. Afterwards the oldest complete turns are dropped until the
character budget fits; a turn always starts at a user message.
"""
from __future__ import annotations

import json

from .models import Message

OLD_TOOL_RESULT_LIMIT = 2_000


def size(message: Message) -> int:
    calls = sum(len(c.name) + len(json.dumps(c.arguments, ensure_ascii=False))
                for c in message.tool_calls)
    return len(message.content) + calls + 16


def repair(history: list[Message]) -> list[Message]:
    """Insert error results for unanswered tool calls and drop orphaned tool results."""
    repaired: list[Message] = []
    pending: dict[str, str] = {}

    def close_pending() -> None:
        for call_id, name in pending.items():
            repaired.append(Message("tool", json.dumps({"error": "Abgebrochen; kein Ergebnis."},
                                                        ensure_ascii=False),
                                    tool_call_id=call_id, name=name))
        pending.clear()

    for message in history:
        if message.role == "tool":
            if message.tool_call_id in pending:
                pending.pop(message.tool_call_id)
                repaired.append(message)
            continue
        close_pending()
        repaired.append(message)
        if message.role == "assistant":
            pending.update({c.id: c.name for c in message.tool_calls})
    close_pending()
    return repaired


def _turns(history: list[Message]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    for message in history:
        if message.role == "user" or not turns:
            turns.append([])
        turns[-1].append(message)
    return turns


def fit(history: list[Message], budget_chars: int) -> list[Message]:
    """Return the newest complete turns that fit the budget; the last turn is always kept."""
    turns = _turns(repair(history))
    if not turns:
        return []
    kept: list[list[Message]] = []
    used = 0
    for index, turn in enumerate(reversed(turns)):
        if index > 0:
            turn = [_compact(m) for m in turn]
        cost = sum(size(m) for m in turn)
        if index > 0 and used + cost > budget_chars:
            break
        kept.append(turn)
        used += cost
    result = [message for turn in reversed(kept) for message in turn]
    while result and result[0].role != "user":
        result.pop(0)
    return result


def _compact(message: Message) -> Message:
    """Shorten large tool results from earlier turns; the model can re-run the tool."""
    if message.role != "tool" or len(message.content) <= OLD_TOOL_RESULT_LIMIT:
        return message
    head = message.content[:OLD_TOOL_RESULT_LIMIT]
    return Message("tool", head + f" … [gekürzt, {len(message.content)} Zeichen]",
                   tool_call_id=message.tool_call_id, name=message.name)
