from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Provider-specific metadata that must be echoed back unchanged on the next request,
    # for example Gemini thought signatures. Never contains credentials.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


# Events emitted while a request runs. Keys: "type" plus type-specific fields:
#   text        -> delta: str
#   tool_start  -> id, name, arguments
#   tool_end    -> id, name, ok: bool
#   round       -> index: int (a new model round begins)
Event = dict[str, Any]
EventSink = Callable[[Event], None]
