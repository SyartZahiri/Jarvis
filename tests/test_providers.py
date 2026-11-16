import asyncio
import json
import unittest

import httpx

from jarvis.config import ProviderProfile
from jarvis.models import Message, ToolCall
from jarvis.providers import ProviderClient, ProviderError

TOOLS = [{"name": "recall", "description": "Recall", "parameters": {
    "type": "object", "properties": {"query": {"type": "string"}},
    "required": ["query"], "additionalProperties": False}}]


async def no_sleep(_seconds):
    return None


def client_for(kind, handler, key="test-key", **options):
    endpoint = "https://api.example.com/v1" if kind == "compatible" else ""
    profile = ProviderProfile.create("Test", kind, "test-model", endpoint, **options)
    return ProviderClient(profile, None if kind == "ollama" else key,
                          httpx.MockTransport(handler), sleep=no_sleep)


def sse(*events) -> bytes:
    out = []
    for event in events:
        if isinstance(event, tuple):
            out.append(f"event: {event[0]}\ndata: {json.dumps(event[1])}\n\n")
        else:
            out.append(f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n")
    return "".join(out).encode()


class NonStreamingTest(unittest.TestCase):
    def roundtrip(self, kind, response, path, header):
        seen = {}

        def handler(request):
            seen["request"] = request
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=response)

        result = asyncio.run(client_for(kind, handler).complete([Message("system", "S"),
                                                                  Message("user", "hello")]))
        self.assertEqual(result.text, "OK")
        self.assertTrue(seen["request"].url.path.endswith(path))
        if header:
            self.assertTrue(seen["request"].headers.get(header))
        return seen["body"]

    def test_openai_family(self):
        for kind in ("openai", "openrouter", "compatible"):
            with self.subTest(kind=kind):
                body = self.roundtrip(kind, {"choices": [{"message": {"content": "OK"}}]},
                                      "/chat/completions", "authorization")
                self.assertFalse(body["stream"])
                self.assertIn("max_completion_tokens" if kind == "openai" else "max_tokens",
                              body)

    def test_ollama_sets_context_window(self):
        body = self.roundtrip("ollama", {"message": {"content": "OK"}}, "/api/chat", None)
        self.assertGreaterEqual(body["options"]["num_ctx"], 8192)
        self.assertNotIn("think", body)   # model default unless the profile says otherwise

    def test_ollama_think_switch(self):
        for think in (False, True):
            seen = {}

            def handler(request):
                seen["body"] = json.loads(request.content)
                return httpx.Response(200, json={"message": {"content": "OK",
                                                             "thinking": "hidden"}})

            client = client_for("ollama", handler, think=think)
            result = asyncio.run(client.complete([Message("user", "hi")]))
            self.assertIs(seen["body"]["think"], think)
            self.assertEqual(result.text, "OK")   # reasoning is never mixed into the answer
        with self.assertRaises(ValueError):
            ProviderProfile.create("T", "ollama", "m", think="ja")

    def test_anthropic(self):
        body = self.roundtrip("anthropic", {"content": [{"type": "text", "text": "OK"}]},
                              "/v1/messages", "anthropic-version")
        self.assertEqual(body["system"], "S")
        self.assertEqual(body["max_tokens"], 4096)

    def test_gemini(self):
        body = self.roundtrip("gemini", {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]},
                              "/models/test-model:generateContent", "x-goog-api-key")
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "S")

    def test_openai_tool_roundtrip_and_invalid_json_arguments(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": None,
                "tool_calls": [{"id": "call-1", "function": {"name": "recall",
                                                             "arguments": "{\"query\":\"A\"}"}},
                               {"id": "call-2", "function": {"name": "recall",
                                                             "arguments": "{broken"}}]}}]})

        client = client_for("openai", handler)
        result = asyncio.run(client.complete([Message("user", "x")], TOOLS))
        self.assertEqual(result.tool_calls[0].arguments, {"query": "A"})
        self.assertIn("_invalid_json", result.tool_calls[1].arguments)
        asyncio.run(client.complete([Message("user", "x"), Message("assistant", "",
                                     result.tool_calls[:1]),
                                     Message("tool", "{}", tool_call_id="call-1", name="recall")]))
        self.assertEqual(seen["body"]["messages"][-1]["tool_call_id"], "call-1")

    def test_anthropic_merges_parallel_tool_results(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})

        calls = [ToolCall("a", "recall", {"query": "1"}), ToolCall("b", "recall", {"query": "2"})]
        asyncio.run(client_for("anthropic", handler).complete([
            Message("user", "x"), Message("assistant", "", calls),
            Message("tool", '{"result": 1}', tool_call_id="a", name="recall"),
            Message("tool", '{"error": "no"}', tool_call_id="b", name="recall")], TOOLS))
        last = seen["body"]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual([b["tool_use_id"] for b in last["content"]], ["a", "b"])
        self.assertTrue(last["content"][1]["is_error"])

    def test_gemini_schema_and_thought_signature_roundtrip(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                {"functionCall": {"name": "recall", "args": {"query": "A"}},
                 "thoughtSignature": "SIG"}]}}]})

        client = client_for("gemini", handler)
        result = asyncio.run(client.complete([Message("user", "x")], TOOLS))
        call = result.tool_calls[0]
        self.assertEqual(call.extra, {"thoughtSignature": "SIG"})
        schema = seen[0]["tools"][0]["functionDeclarations"][0]["parameters"]
        self.assertEqual(schema["properties"]["query"]["type"], "STRING")
        asyncio.run(client.complete([Message("user", "x"), Message("assistant", "", [call]),
                                     Message("tool", '{"result": []}', tool_call_id=call.id,
                                             name="recall")], TOOLS))
        model_turn = seen[1]["contents"][1]
        self.assertEqual(model_turn["parts"][0]["thoughtSignature"], "SIG")
        self.assertNotIn("id", model_turn["parts"][0]["functionCall"])
        self.assertEqual(seen[1]["contents"][2]["parts"][0]["functionResponse"]["name"], "recall")


class StreamingTest(unittest.TestCase):
    def stream(self, kind, body, content_type="text/event-stream"):
        seen = {}

        def handler(request):
            seen["request"] = request
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=body, headers={"content-type": content_type})

        deltas = []
        result = asyncio.run(client_for(kind, handler).complete([Message("user", "x")], TOOLS,
                                                                on_text=deltas.append))
        return result, deltas, seen

    def test_openai_stream_text_and_tool_deltas(self):
        body = sse({"choices": [{"delta": {"content": "Hal"}}]},
                   {"choices": [{"delta": {"content": "lo"}}]},
                   {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                       "function": {"name": "recall", "arguments": "{\"qu"}}]}}]},
                   {"choices": [{"delta": {"tool_calls": [{"index": 0,
                       "function": {"arguments": "ery\":\"B\"}"}}]}}]},
                   "[DONE]")
        result, deltas, seen = self.stream("openai", body)
        self.assertTrue(seen["body"]["stream"])
        self.assertEqual(deltas, ["Hal", "lo"])
        self.assertEqual(result.text, "Hallo")
        self.assertEqual((result.tool_calls[0].id, result.tool_calls[0].arguments),
                         ("c1", {"query": "B"}))

    def test_anthropic_stream(self):
        body = sse(("message_start", {"type": "message_start", "message": {}}),
                   ("content_block_start", {"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "text", "text": ""}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": "Hi"}}),
                   ("content_block_start", {"type": "content_block_start", "index": 1,
                                            "content_block": {"type": "tool_use", "id": "t1",
                                                              "name": "recall", "input": {}}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                            "delta": {"type": "input_json_delta",
                                                      "partial_json": "{\"query\": \"C\"}"}}),
                   ("message_stop", {"type": "message_stop"}))
        result, deltas, _ = self.stream("anthropic", body)
        self.assertEqual(deltas, ["Hi"])
        self.assertEqual(result.tool_calls[0].arguments, {"query": "C"})

    def test_anthropic_stream_error_event(self):
        body = sse(("error", {"type": "error", "error": {"message": "overloaded"}}))
        with self.assertRaises(ProviderError) as raised:
            self.stream("anthropic", body)
        self.assertIn("overloaded", str(raised.exception))

    def test_gemini_stream(self):
        body = sse({"candidates": [{"content": {"parts": [{"text": "Gu"}]}}]},
                   {"candidates": [{"content": {"parts": [{"text": "t", "thought": False},
                       {"functionCall": {"name": "recall", "args": {"query": "D"}}}]}}]})
        result, deltas, seen = self.stream("gemini", body)
        self.assertTrue(str(seen["request"].url).endswith(":streamGenerateContent?alt=sse"))
        self.assertEqual(result.text, "Gut")
        self.assertEqual(result.tool_calls[0].arguments, {"query": "D"})

    def test_ollama_ndjson_stream(self):
        lines = [{"message": {"content": "A"}}, {"message": {"content": "B", "tool_calls": [
            {"function": {"name": "recall", "arguments": {"query": "E"}}}]}}, {"done": True}]
        body = "\n".join(json.dumps(line) for line in lines).encode()
        result, deltas, _ = self.stream("ollama", body, "application/x-ndjson")
        self.assertEqual(deltas, ["A", "B"])
        self.assertEqual(result.tool_calls[0].arguments, {"query": "E"})


class ResilienceTest(unittest.TestCase):
    def test_retries_rate_limit_then_succeeds(self):
        attempts = []
        delays = []

        async def record(seconds):
            delays.append(seconds)

        def handler(request):
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(429, headers={"retry-after": "2"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

        profile = ProviderProfile.create("T", "openai", "m")
        client = ProviderClient(profile, "k", httpx.MockTransport(handler), sleep=record)
        self.assertEqual(asyncio.run(client.complete([Message("user", "x")])).text, "OK")
        self.assertEqual(delays, [2.0, 2.0])

    def test_gives_up_after_retries_with_hint(self):
        client = client_for("openai", lambda request: httpx.Response(503))
        with self.assertRaises(ProviderError) as raised:
            asyncio.run(client.complete([Message("user", "x")]))
        self.assertIn("HTTP 503", str(raised.exception))

    def test_error_detail_is_redacted(self):
        def handler(request):
            return httpx.Response(401, json={"error": {"message":
                "Incorrect API key provided: my-secret-key-value-123456789012345678"}})

        client = client_for("openai", handler, key="my-secret-key-value-123456789012345678")
        with self.assertRaises(ProviderError) as raised:
            asyncio.run(client.test_connection())
        message = str(raised.exception)
        self.assertIn("API-Key prüfen", message)
        self.assertNotIn("my-secret-key-value", message)

    def test_plain_text_error_body_never_echoed(self):
        client = client_for("openai", lambda r: httpx.Response(500, text="secret-value inside"))
        client.max_retries = 0
        with self.assertRaises(ProviderError) as raised:
            asyncio.run(client.complete([Message("user", "x")]))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_connection_error_is_wrapped(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        client = client_for("ollama", handler)
        with self.assertRaises(ProviderError) as raised:
            asyncio.run(client.complete([Message("user", "x")]))
        self.assertIn("Keine Verbindung", str(raised.exception))

    def test_missing_key_rejected_early(self):
        with self.assertRaises(ProviderError):
            ProviderClient(ProviderProfile.create("T", "anthropic", "m"), None)


class ModelListTest(unittest.TestCase):
    def test_lists_models_per_provider(self):
        cases = {
            "openai": ({"data": [{"id": "gpt-b"}, {"id": "gpt-a"}]}, "/models", ["gpt-a", "gpt-b"]),
            "anthropic": ({"data": [{"id": "claude-x"}]}, "/v1/models", ["claude-x"]),
            "gemini": ({"models": [
                {"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]},
                "/models", ["gemini-x"]),
            "ollama": ({"models": [{"name": "llama3.2:latest"}]}, "/api/tags",
                       ["llama3.2:latest"]),
        }
        for kind, (payload, path, expected) in cases.items():
            with self.subTest(kind=kind):
                seen = {}

                def handler(request, seen=seen, payload=payload):
                    seen["path"] = request.url.path
                    return httpx.Response(200, json=payload)

                self.assertEqual(asyncio.run(client_for(kind, handler).list_models()), expected)
                self.assertTrue(seen["path"].endswith(path))

    def test_ollama_pull_reports_progress(self):
        lines = [{"status": "pulling manifest"},
                 {"status": "downloading", "total": 100, "completed": 50},
                 {"status": "success"}]
        body = "\n".join(json.dumps(line) for line in lines).encode()
        updates = []
        client = client_for("ollama", lambda r: httpx.Response(200, content=body))
        asyncio.run(client.pull_ollama_model("llama3.2", lambda s, f: updates.append((s, f))))
        self.assertEqual(updates[1], ("downloading", 0.5))
        with self.assertRaises(ValueError):
            asyncio.run(client.pull_ollama_model("bad name; rm"))


if __name__ == "__main__":
    unittest.main()
