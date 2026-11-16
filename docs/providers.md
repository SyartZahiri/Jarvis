# Anbieterprotokolle

Alle Adapter liegen in `src/jarvis/providers.py`. Tests mit festgehaltenen Wire-Formaten: `tests/test_providers.py`.

| Anbieter | Endpunkt | Auth | Streaming | Besonderheiten |
|---|---|---|---|---|
| OpenAI | `POST {base}/chat/completions` | `Authorization: Bearer` | SSE, `data: [DONE]` | `max_completion_tokens`; Tool-Argumente als JSON-String in Deltas, nach `index` zusammengesetzt |
| OpenRouter / kompatibel | wie OpenAI | Bearer (lokal optional) | SSE | `max_tokens`; OpenRouter erhält `X-Title: JARVIS` |
| Anthropic | `POST {base}/v1/messages` | `x-api-key`, `anthropic-version: 2023-06-01` | SSE mit `event:`-Typen | System getrennt; parallele `tool_result` in **einer** User-Nachricht; `is_error`; leere Assistententexte ausgelassen; Temperatur ≤ 1 |
| Gemini | `POST {base}/models/{model}:generateContent` bzw. `:streamGenerateContent?alt=sse` | `x-goog-api-key` | SSE | Schema-Typen in Großbuchstaben; `thoughtSignature` an `functionCall`-Parts wird in `ToolCall.extra` gespeichert und zurückgesendet; mehrere `functionResponse` in einem Content; Blockierungen werden als Fehler gemeldet |
| Ollama | `POST {base}/api/chat` | – | NDJSON | `options.num_ctx` aus dem Kontextbudget (8k–128k), `num_predict`; Tool-Nachrichten mit `tool_name`; `think` aus dem Profil (nur wenn gesetzt), `message.thinking` wird nie in die Antwort übernommen |

## Gemeinsame Regeln

- **Retry:** 408, 425, 429, 500, 502, 503, 504 und 529 sowie Verbindungsfehler werden bis zu zweimal wiederholt. Eine abgelehnte Verbindung zu einem lokalen Endpunkt (`localhost`, `127.0.0.1`) wird sofort gemeldet, weil dann schlicht der Server nicht läuft. `Retry-After` wird beachtet (höchstens 30 s), sonst gilt ein exponentielles Backoff (1,5 s, 3 s). Nach dem ersten gestreamten Byte gibt es keinen Retry mehr.
- **Timeouts:** Verbindungsaufbau 15 s, Lesen 180 s (Ollama-Pull unbegrenzt).
- **Ungültige Tool-Argumente** (kaputtes JSON) werden als `{"_invalid_json": …}` weitergegeben. Die Engine meldet das dem Modell als Fehler, statt den Aufruf stillschweigend zu verwerfen.
- **Modellliste:** OpenAI-Familie `GET /models`, Anthropic `GET /v1/models`, Gemini `GET /models` (gefiltert auf `generateContent`), Ollama `GET /api/tags`.

## Latenz bei lokalen Modellen

Live gemessen am 12.11.2026 mit `qwen3:0.6b` auf der CPU (Ollama 0.32.14):

- **Prompt-Cache:** Ollama verarbeitet einen Prompt-Anfang, der mit der vorigen Anfrage übereinstimmt, praktisch ohne Kosten (0,0 s). Ändert sich etwas weit vorne, kostete das hier 5,5 s. Deshalb enthält der Systemprompt keine Uhrzeit und keine Erinnerungen (siehe ARCHITECTURE.md). Eine einfache Antwort mit warmem Modell sank dadurch von 13 s auf 2,4 s.
- **Nachdenken:** qwen3 denkt in Ollama standardmäßig nach. Für eine Ein-Satz-Antwort waren das 79 statt 9 Ausgabetokens (8,0 s statt 1,3 s). Mit `think=false` im Profil (`jarvis profiles think <Profil> aus` oder im Profilformular) antwortet es direkt. Für schwierige Aufgaben kann Nachdenken die Qualität verbessern.
- Ein anderes `num_ctx` als beim vorigen Aufruf lädt das Modell neu. JARVIS leitet es stabil aus dem Profil ab.

## Neuen Anbieter ergänzen

1. `config.DEFAULT_ENDPOINTS` und `KIND_LABELS` erweitern.
2. In `ProviderClient.complete` Request-Builder, Parser und Stream-Parser verdrahten.
3. `list_models` ergänzen.
4. Tests schreiben: normale Antwort, Stream, Tool-Roundtrip und Fehlerfall mit `httpx.MockTransport`.
