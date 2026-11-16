"""Wire adapters for every supported model API.

The rest of JARVIS speaks only `Message`, `ToolCall` and `Completion`. Each adapter maps
those to one provider protocol, in both directions, with and without streaming:

* OpenAI Chat Completions (OpenAI, OpenRouter, any OpenAI-compatible endpoint) - SSE
* Anthropic Messages - SSE with typed events
* Google Gemini generateContent / streamGenerateContent - SSE
* Ollama /api/chat - NDJSON
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import quote

import httpx

from .config import ProviderProfile
from .models import Completion, Message, ToolCall

TextSink = Callable[[str], None]
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
STATUS_HINTS = {
    400: "Anfrage abgelehnt",
    401: "Zugang abgelehnt – API-Key prüfen",
    403: "Zugriff verweigert – Berechtigung oder Region prüfen",
    404: "Modell oder Endpunkt nicht gefunden",
    413: "Anfrage zu groß – Kontextbudget im Profil verringern",
    429: "Rate-Limit oder Kontingent erreicht",
    529: "Anbieter überlastet",
}
_TOKEN_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|"
                            r"[A-Za-z0-9_\-]{32,})")


class ProviderError(RuntimeError):
    pass


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        # Surfaced to the model as an argument error instead of silently dropping the call.
        return {"_invalid_json": str(raw)[:2000]}
    return value if isinstance(value, dict) else {"_invalid_json": str(raw)[:2000]}


async def _sse(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """Minimal Server-Sent-Events reader yielding (event, data)."""
    event, data = "message", []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield event, "\n".join(data)


class ProviderClient:
    def __init__(self, profile: ProviderProfile, api_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, *,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 max_retries: int = 2):
        profile.validate()
        if profile.needs_key() and not api_key:
            raise ProviderError("Für dieses Profil fehlt ein API-Key.")
        self.profile = profile
        self.api_key = api_key
        self.transport = transport
        self._sleep = sleep
        self.max_retries = max_retries

    # Public API ----------------------------------------------------------------------------

    async def complete(self, messages: list[Message], tools: list[dict[str, Any]] | None = None,
                       on_text: TextSink | None = None) -> Completion:
        """Run one model turn. With `on_text`, text is streamed as it arrives."""
        stream = on_text is not None
        kind = self.profile.kind
        tools = tools or []
        if kind in ("openai", "openrouter", "compatible"):
            url, headers, payload, params = self._openai_request(messages, tools, stream)
            handler = self._read_openai_stream if stream else self._read_openai
        elif kind == "ollama":
            url, headers, payload, params = self._ollama_request(messages, tools, stream)
            handler = self._read_ollama_stream if stream else self._read_ollama
        elif kind == "anthropic":
            url, headers, payload, params = self._anthropic_request(messages, tools, stream)
            handler = self._read_anthropic_stream if stream else self._read_anthropic
        else:
            url, headers, payload, params = self._gemini_request(messages, tools, stream)
            handler = self._read_gemini_stream if stream else self._read_gemini

        async def read(response: httpx.Response) -> Completion:
            return await handler(response, on_text) if stream else handler(
                json.loads(await response.aread()))

        return await self._send("POST", url, headers, read, payload=payload, params=params)

    async def test_connection(self) -> str:
        result = await self.complete([Message("user", "Antworte nur mit OK.")])
        if not result.text.strip():
            raise ProviderError("Das Modell hat keine Textantwort geliefert.")
        return result.text.strip()

    async def list_models(self) -> list[str]:
        """Model identifiers the endpoint offers for chat, sorted."""
        kind, endpoint = self.profile.kind, self.profile.endpoint
        if kind == "ollama":
            url, headers, params = f"{endpoint}/api/tags", {}, None
        elif kind == "anthropic":
            url, headers, params = f"{endpoint}/v1/models", self._anthropic_headers(), {
                "limit": 1000}
        elif kind == "gemini":
            url, headers, params = f"{endpoint}/models", {"x-goog-api-key": self.api_key or ""}, {
                "pageSize": 1000}
        else:
            url, headers, params = f"{endpoint}/models", self._bearer(), None

        async def read(response: httpx.Response) -> list[str]:
            data = json.loads(await response.aread())
            if kind == "ollama":
                names = [m["name"] for m in data.get("models", [])]
            elif kind == "gemini":
                names = [m["name"].removeprefix("models/") for m in data.get("models", [])
                         if "generateContent" in m.get("supportedGenerationMethods", [])]
            else:
                names = [m["id"] for m in data.get("data", [])]
            return sorted({str(name) for name in names if name})

        return await self._send("GET", url, headers, read, params=params)

    async def list_ollama_models(self) -> list[str]:
        if self.profile.kind != "ollama":
            raise ValueError("Nur für Ollama verfügbar.")
        return await self.list_models()

    async def ollama_version(self) -> str:
        async def read(response: httpx.Response) -> str:
            return str(json.loads(await response.aread()).get("version") or "")

        return await self._send("GET", f"{self.profile.endpoint}/api/version", {}, read,
                                read_timeout=5)

    async def ollama_models_detail(self) -> list[dict[str, str]]:
        """Installed models with a readable size, newest first."""
        async def read(response: httpx.Response) -> list[dict[str, str]]:
            models = json.loads(await response.aread()).get("models", [])
            models.sort(key=lambda m: m.get("modified_at", ""), reverse=True)
            return [{"name": str(m.get("name")),
                     "size": f"{m.get('size', 0) / 1e9:.1f} GB".replace(".", ",")}
                    for m in models if m.get("name")]

        return await self._send("GET", f"{self.profile.endpoint}/api/tags", {}, read,
                                read_timeout=8)

    async def pull_ollama_model(self, name: str,
                                on_progress: Callable[[str, float | None], None] | None = None
                                ) -> None:
        """Download a model into the local Ollama installation, reporting progress."""
        if self.profile.kind != "ollama":
            raise ValueError("Nur für Ollama verfügbar.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-/:]{0,199}", name):
            raise ValueError("Ungültiger Modellname.")

        async def read(response: httpx.Response) -> None:
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if "error" in item:
                    raise ProviderError(f"Ollama: {str(item['error'])[:300]}")
                total, done = item.get("total"), item.get("completed")
                fraction = done / total if total and done is not None else None
                if on_progress:
                    on_progress(str(item.get("status", "")), fraction)
                if item.get("status") == "success":
                    return
            raise ProviderError("Ollama hat den Download nicht abgeschlossen.")

        await self._send("POST", f"{self.profile.endpoint}/api/pull", {}, read,
                         payload={"model": name, "stream": True}, read_timeout=None)

    # Transport -----------------------------------------------------------------------------

    async def _send(self, method: str, url: str, headers: dict[str, str], read,
                    payload: dict | None = None, params: dict | None = None,
                    read_timeout: float | None = 180) -> Any:
        kind = self.profile.kind
        timeout = httpx.Timeout(connect=15, read=read_timeout, write=60, pool=15)
        attempt = 0
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport,
                                     follow_redirects=False, trust_env=False) as client:
            while True:
                try:
                    request = client.build_request(method, url, headers=headers, json=payload,
                                                   params=params)
                    response = await client.send(request, stream=True)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                    # A refused connection on this machine means the server is not running;
                    # waiting will not change that, so report it right away.
                    if attempt < self.max_retries and not self.profile.is_local:
                        await self._sleep(1.5 * 2 ** attempt)
                        attempt += 1
                        continue
                    raise ProviderError(f"{kind}: Keine Verbindung zu {self._host()}.") from None
                except httpx.TimeoutException:
                    raise ProviderError(f"{kind}: Zeitüberschreitung.") from None
                except httpx.RequestError as exc:
                    raise ProviderError(f"{kind}: Verbindung fehlgeschlagen "
                                        f"({type(exc).__name__}).") from None
                try:
                    if response.status_code >= 400:
                        await response.aread()
                        if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                            await self._sleep(self._retry_delay(response, attempt))
                            attempt += 1
                            continue
                        raise ProviderError(self._error_message(response))
                    if 300 <= response.status_code < 400:
                        raise ProviderError(f"{kind}: Unerwartete Weiterleitung "
                                            f"(HTTP {response.status_code}); Endpunkt prüfen.")
                    return await read(response)
                except ProviderError:
                    raise
                except httpx.TimeoutException:
                    raise ProviderError(f"{kind}: Zeitüberschreitung beim Lesen.") from None
                except httpx.RequestError as exc:
                    raise ProviderError(f"{kind}: Verbindung unterbrochen "
                                        f"({type(exc).__name__}).") from None
                except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                    raise ProviderError(f"{kind}: Unerwartetes Antwortformat "
                                        f"({type(exc).__name__}).") from None
                finally:
                    await response.aclose()

    def _host(self) -> str:
        return httpx.URL(self.profile.endpoint).host

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after", "")
        try:
            return max(0.0, min(float(header), 30.0))
        except ValueError:
            return min(1.5 * 2 ** attempt, 30.0)

    def _redact(self, text: str) -> str:
        if self.api_key:
            text = text.replace(self.api_key, "[KEY]")
        return _TOKEN_PATTERN.sub("[…]", text)

    def _error_message(self, response: httpx.Response) -> str:
        status = response.status_code
        base = f"{self.profile.kind}: HTTP {status}"
        if status in STATUS_HINTS:
            base += f" · {STATUS_HINTS[status]}"
        detail = ""
        try:
            data = response.json()
            error = data.get("error", data) if isinstance(data, dict) else data
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("status") or "")
            elif isinstance(error, str):
                detail = error
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                detail = str(data[0].get("error", {}).get("message", ""))
        except (ValueError, AttributeError, TypeError):
            detail = ""  # Non-JSON bodies are never echoed: they may contain anything.
        detail = " ".join(self._redact(detail).split())[:300]
        return f"{base} – {detail}" if detail else base

    def _bearer(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        if self.profile.kind == "openrouter":
            headers["X-Title"] = "JARVIS"
        return headers

    def _anthropic_headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"}

    # OpenAI-compatible ---------------------------------------------------------------------

    def _openai_request(self, messages: list[Message], tools: list[dict[str, Any]],
                        stream: bool):
        out = []
        for message in messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_calls:
                item["tool_calls"] = [{"id": c.id, "type": "function",
                                       "function": {"name": c.name,
                                                    "arguments": json.dumps(c.arguments)}}
                                      for c in message.tool_calls]
            if message.role == "tool":
                item["tool_call_id"] = message.tool_call_id
            out.append(item)
        payload: dict[str, Any] = {"model": self.profile.model, "messages": out,
                                   "stream": stream}
        limit_key = "max_completion_tokens" if self.profile.kind == "openai" else "max_tokens"
        payload[limit_key] = self.profile.max_output_tokens
        if self.profile.temperature is not None:
            payload["temperature"] = self.profile.temperature
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
        return f"{self.profile.endpoint}/chat/completions", self._bearer(), payload, None

    @staticmethod
    def _read_openai(data: dict) -> Completion:
        if "error" in data and "choices" not in data:
            raise ProviderError(f"Anbieterfehler: {str(data['error'])[:300]}")
        message = data["choices"][0]["message"]
        calls = [ToolCall(item.get("id") or str(uuid.uuid4()), item["function"]["name"],
                          _parse_arguments(item["function"].get("arguments")))
                 for item in message.get("tool_calls") or []]
        return Completion(message.get("content") or "", calls)

    async def _read_openai_stream(self, response: httpx.Response, on_text: TextSink
                                  ) -> Completion:
        text: list[str] = []
        slots: dict[int, dict[str, str]] = {}
        async for _event, data in _sse(response):
            if data.strip() == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                message = chunk["error"].get("message") if isinstance(chunk["error"], dict) \
                    else chunk["error"]
                raise ProviderError(f"{self.profile.kind}: {self._redact(str(message))[:300]}")
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                    on_text(delta["content"])
                for item in delta.get("tool_calls") or []:
                    slot = slots.setdefault(int(item.get("index", 0)),
                                            {"id": "", "name": "", "arguments": ""})
                    function = item.get("function") or {}
                    if item.get("id"):
                        slot["id"] = item["id"]
                    if function.get("name") and not slot["name"]:
                        slot["name"] = function["name"]
                    slot["arguments"] += function.get("arguments") or ""
        calls = [ToolCall(slot["id"] or str(uuid.uuid4()), slot["name"],
                          _parse_arguments(slot["arguments"]))
                 for _, slot in sorted(slots.items()) if slot["name"]]
        return Completion("".join(text), calls)

    # Ollama --------------------------------------------------------------------------------

    def _ollama_request(self, messages: list[Message], tools: list[dict[str, Any]],
                        stream: bool):
        out = []
        for message in messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_calls:
                item["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}}
                                      for c in message.tool_calls]
            if message.role == "tool":
                item["tool_name"] = message.name
            out.append(item)
        # Ollama silently truncates prompts beyond num_ctx, which would drop the system
        # prompt first; size the window from the profile budget (≈3 chars per token).
        num_ctx = max(8192, min(131072, self.profile.max_context_chars // 3
                                + self.profile.max_output_tokens))
        options: dict[str, Any] = {"num_ctx": num_ctx,
                                   "num_predict": self.profile.max_output_tokens}
        if self.profile.temperature is not None:
            options["temperature"] = self.profile.temperature
        payload: dict[str, Any] = {"model": self.profile.model, "messages": out,
                                   "stream": stream, "options": options}
        if self.profile.think is not None:
            payload["think"] = self.profile.think
        if tools:
            payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
        return f"{self.profile.endpoint}/api/chat", {}, payload, None

    @staticmethod
    def _read_ollama(data: dict) -> Completion:
        if "error" in data:
            raise ProviderError(f"ollama: {str(data['error'])[:300]}")
        message = data["message"]
        calls = [ToolCall(str(uuid.uuid4()), item["function"]["name"],
                          _parse_arguments(item["function"].get("arguments")))
                 for item in message.get("tool_calls") or []]
        return Completion(message.get("content") or "", calls)

    async def _read_ollama_stream(self, response: httpx.Response, on_text: TextSink
                                  ) -> Completion:
        text: list[str] = []
        calls: list[ToolCall] = []
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise ProviderError(f"ollama: {str(chunk['error'])[:300]}")
            message = chunk.get("message") or {}
            if message.get("content"):
                text.append(message["content"])
                on_text(message["content"])
            calls.extend(ToolCall(str(uuid.uuid4()), item["function"]["name"],
                                  _parse_arguments(item["function"].get("arguments")))
                         for item in message.get("tool_calls") or [])
            if chunk.get("done"):
                break
        return Completion("".join(text), calls)

    # Anthropic -----------------------------------------------------------------------------

    def _anthropic_request(self, messages: list[Message], tools: list[dict[str, Any]],
                           stream: bool):
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        out: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                block = {"type": "tool_result", "tool_use_id": message.tool_call_id,
                         "content": message.content}
                if message.content.startswith('{"error"'):
                    block["is_error"] = True
                # All results answering one assistant turn belong in a single user message.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"][-1].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif message.role == "assistant" and message.tool_calls:
                blocks: list[dict] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend({"type": "tool_use", "id": c.id, "name": c.name,
                               "input": c.arguments} for c in message.tool_calls)
                out.append({"role": "assistant", "content": blocks})
            elif message.role == "assistant" and not message.content:
                continue  # Empty assistant text is rejected by the Messages API.
            else:
                out.append({"role": message.role, "content": message.content})
        payload: dict[str, Any] = {"model": self.profile.model,
                                   "max_tokens": self.profile.max_output_tokens,
                                   "messages": out}
        if stream:
            payload["stream"] = True
        if self.profile.temperature is not None:
            payload["temperature"] = min(self.profile.temperature, 1.0)
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t["description"],
                                 "input_schema": t["parameters"]} for t in tools]
        return f"{self.profile.endpoint}/v1/messages", self._anthropic_headers(), payload, None

    @staticmethod
    def _read_anthropic(data: dict) -> Completion:
        if data.get("type") == "error":
            raise ProviderError(f"anthropic: {str(data.get('error', {}).get('message'))[:300]}")
        blocks = data["content"]
        text = "".join(b["text"] for b in blocks if b.get("type") == "text")
        calls = [ToolCall(b["id"], b["name"], _parse_arguments(b.get("input")))
                 for b in blocks if b.get("type") == "tool_use"]
        return Completion(text, calls)

    async def _read_anthropic_stream(self, response: httpx.Response, on_text: TextSink
                                     ) -> Completion:
        text: list[str] = []
        blocks: dict[int, dict[str, Any]] = {}
        async for event, data in _sse(response):
            item = json.loads(data)
            kind = item.get("type", event)
            if kind == "error":
                message = (item.get("error") or {}).get("message", "Streamfehler")
                raise ProviderError(f"anthropic: {self._redact(str(message))[:300]}")
            if kind == "content_block_start":
                block = item["content_block"]
                blocks[item["index"]] = {"type": block.get("type"), "id": block.get("id"),
                                         "name": block.get("name"), "json": ""}
                if block.get("type") == "text" and block.get("text"):
                    text.append(block["text"])
                    on_text(block["text"])
            elif kind == "content_block_delta":
                delta = item["delta"]
                if delta.get("type") == "text_delta":
                    text.append(delta["text"])
                    on_text(delta["text"])
                elif delta.get("type") == "input_json_delta":
                    blocks[item["index"]]["json"] += delta.get("partial_json", "")
            elif kind == "message_stop":
                break
        calls = [ToolCall(b["id"], b["name"], _parse_arguments(b["json"]))
                 for _, b in sorted(blocks.items()) if b["type"] == "tool_use"]
        return Completion("".join(text), calls)

    # Gemini --------------------------------------------------------------------------------

    def _gemini_request(self, messages: list[Message], tools: list[dict[str, Any]],
                        stream: bool):
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                try:
                    result: Any = json.loads(message.content)
                except ValueError:
                    result = {"text": message.content}
                response_part: dict[str, Any] = {"name": message.name, "response":
                    result if isinstance(result, dict) else {"result": result}}
                if message.tool_call_id and not message.tool_call_id.startswith("jarvis-"):
                    response_part["id"] = message.tool_call_id
                part = {"functionResponse": response_part}
                if contents and contents[-1]["role"] == "user" and \
                        "functionResponse" in contents[-1]["parts"][-1]:
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif message.role == "assistant" and message.tool_calls:
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    function: dict[str, Any] = {"name": call.name, "args": call.arguments}
                    if not call.id.startswith("jarvis-"):
                        function["id"] = call.id
                    part = {"functionCall": function}
                    if call.extra.get("thoughtSignature"):
                        part["thoughtSignature"] = call.extra["thoughtSignature"]
                    parts.append(part)
                contents.append({"role": "model", "parts": parts})
            elif message.content:
                contents.append({"role": "model" if message.role == "assistant" else "user",
                                 "parts": [{"text": message.content}]})
        config: dict[str, Any] = {"maxOutputTokens": self.profile.max_output_tokens}
        if self.profile.temperature is not None:
            config["temperature"] = self.profile.temperature
        payload: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [{"name": t["name"],
                "description": t["description"],
                "parameters": self._gemini_schema(t["parameters"])} for t in tools]}]
        model = quote(self.profile.model.removeprefix("models/"), safe="-_./")
        method = "streamGenerateContent" if stream else "generateContent"
        return (f"{self.profile.endpoint}/models/{model}:{method}",
                {"x-goog-api-key": self.api_key or ""}, payload,
                {"alt": "sse"} if stream else None)

    @staticmethod
    def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Convert the supported JSON-Schema subset to Gemini's Schema wire format."""
        output: dict[str, Any] = {}
        for key in ("type", "description", "enum", "required", "minimum", "maximum"):
            if key in schema:
                output[key] = schema[key].upper() if key == "type" else schema[key]
        if "properties" in schema:
            output["properties"] = {name: ProviderClient._gemini_schema(value)
                                    for name, value in schema["properties"].items()}
        if "items" in schema:
            output["items"] = ProviderClient._gemini_schema(schema["items"])
        return output

    @staticmethod
    def _gemini_parts(data: dict, text: list[str], calls: list[ToolCall],
                      on_text: TextSink | None) -> None:
        if not data.get("candidates"):
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            if reason:
                raise ProviderError(f"gemini: Anfrage blockiert ({reason}).")
            return
        candidate = data["candidates"][0]
        for part in (candidate.get("content") or {}).get("parts", []):
            if "text" in part and not part.get("thought"):
                text.append(part["text"])
                if on_text:
                    on_text(part["text"])
            elif "functionCall" in part:
                function = part["functionCall"]
                extra = {"thoughtSignature": part["thoughtSignature"]} \
                    if part.get("thoughtSignature") else {}
                calls.append(ToolCall(function.get("id") or f"jarvis-{uuid.uuid4()}",
                                      function["name"],
                                      _parse_arguments(function.get("args")), extra))
        reason = candidate.get("finishReason")
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT") \
                and not text and not calls:
            raise ProviderError(f"gemini: Antwort blockiert ({reason}).")

    @classmethod
    def _read_gemini(cls, data: dict) -> Completion:
        text: list[str] = []
        calls: list[ToolCall] = []
        cls._gemini_parts(data, text, calls, None)
        return Completion("".join(text), calls)

    async def _read_gemini_stream(self, response: httpx.Response, on_text: TextSink
                                  ) -> Completion:
        text: list[str] = []
        calls: list[ToolCall] = []
        async for _event, data in _sse(response):
            item = json.loads(data)
            if "error" in item:
                raise ProviderError(f"gemini: {self._redact(str(item['error'].get('message')))[:300]}")
            self._gemini_parts(item, text, calls, on_text)
        return Completion("".join(text), calls)
