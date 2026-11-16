# Öffentliche Schnittstellen

## Engine

```python
jarvis = Jarvis(workspace, database=..., config=..., secrets=..., permissions=...,
                settings=..., plugin_state=...)        # alle Stores optional
conversation_id, text = await jarvis.ask(prompt, conversation_id=None,
                                         approve=None, on_event=None)
```

- `approve(tool, args) -> bool | Awaitable[bool]` wird nur für freigabepflichtige Werkzeuge aufgerufen. Fehlt der Callback, wird die Aktion abgelehnt.
- `on_event(event: dict)` erhält `{"type": "conversation", "id"}`, `{"type": "round", "index"}`, `{"type": "text", "delta"}`, `{"type": "tool_start", "id", "name", "arguments"}` und `{"type": "tool_end", "id", "name", "ok"}`. Der Callback läuft im Thread des Event-Loops.
- Wird die Aufgabe abgebrochen (`task.cancel()`), bleibt Teiltext gespeichert.
- `jarvis.list_models(profile, key=None)`, `jarvis.test_profile(profile, key=None)`, `jarvis.available_tools()`, `jarvis.needs_approval(tool)`, `jarvis.plugin_errors`.

## Desktop-Server

`webapp.run_web(jarvis, open_app=True, port=0, on_ready=None)` startet `JarvisWeb`, öffnet das Fenster und läuft, bis 6 s lang keine Seite mehr verbunden ist. `JarvisWeb(jarvis).start(port) -> origin`, `.launch_url`, `.close()`.

Alle Endpunkte verlangen das Sitzungscookie, POSTs zusätzlich `X-Jarvis: 1` und JSON. Fehler kommen als `{"error": "…"}` mit passendem Status.

| Methode und Pfad | Zweck |
|---|---|
| `GET /api/state` | Profile (ohne Keys), Fähigkeiten, Werkzeuge, Einstellungen, Skills, Plugins, Gespräche, laufende Anfrage |
| `GET /api/events` | Server-Sent Events, siehe unten |
| `GET /api/conversations[?q=]`, `GET /api/conversations/<id>` | Liste, einzelnes Gespräch mit zusammengefassten Werkzeugergebnissen |
| `POST /api/ask {prompt, conversation}` | Anfrage starten, `409` wenn schon eine läuft |
| `POST /api/cancel`, `POST /api/approval {id, approve}` | abbrechen, Freigabe entscheiden |
| `POST /api/conversations/rename`, `/delete`, `/drop-last` | Gespräch verwalten; `drop-last` entfernt nur eine unbeantwortete letzte Nachricht |
| `GET /api/memories[?q=]`, `POST /api/memories/add`, `/delete` | Gedächtnis |
| `GET /api/automations`, `POST /api/automations/add`, `/toggle`, `/run`, `/delete` | Abläufe |
| `POST /api/profiles/save` (mit `key`, `activate`), `/activate`, `/delete`, `/test`, `/models`, `/pull` | Modellprofile |
| `GET /api/setup/ollama`, `POST /api/setup/ollama/start` | Einrichtung |
| `POST /api/settings`, `/api/web-key`, `/api/permissions`, `/api/plugins` | Einstellungen |
| `POST /api/listen`, `/api/listen/stop`, `POST /api/open {target}` | Sprache, Ordner oder Link öffnen |
| `GET /api/export` | JSON-Export als Download |

Ereignisse im Strom: `run_start`, `run_conversation`, `round`, `text`, `tool_start`, `tool_end`, `run_end {state: done|cancelled|error}`, `approval`, `approval_closed`, `pull`, `pull_end`, `listen_partial`, `listen_end`, `automation_busy`, `automations_ran`, `status`.

## Provider

`ProviderProfile(id, name, kind, model, endpoint, max_context_chars=60000, max_output_tokens=4096, temperature=None, think=None)`; `think` (an/aus, `None` = Modellstandard) geht derzeit nur an Ollama; `kind` ∈ `openai | openrouter | compatible | anthropic | gemini | ollama`. `needs_key()` ist für Ollama und lokale kompatible Endpunkte falsch.

`ProviderClient(profile, api_key, transport=None, sleep=asyncio.sleep, max_retries=2)`, zusätzlich `ollama_version()` und `ollama_models_detail()` für die Einrichtung:
- `await complete(messages, tools=None, on_text=None) -> Completion`; mit `on_text` wird gestreamt.
- `await list_models() -> list[str]`, `await test_connection() -> str`
- `await pull_ollama_model(name, on_progress(status, fraction|None))`
- Fehler kommen als `ProviderError` mit Status, Hinweis und geschwärztem Anbietertext.

`tools` ist eine Liste von `{"name", "description", "parameters": JSON-Schema}`.

## Werkzeuge

```python
Tool(name, description, parameters, capability, requires_approval, run, source="builtin")
```

`run` ist `async def run(args: dict) -> JSON-serialisierbar`. `parameters` muss ein Objekt-Schema mit `properties` sein. Die Engine prüft `type` (Integer ohne bool), `enum`, `minimum`/`maximum`, Array-`items`, unbekannte und fehlende Felder.

`ToolRegistry`: `register`, `unregister`, `get`, `all`, `names`, `schemas`, `path(relativ) -> Path` (nach dem Auflösen im Arbeitsbereich), `relative(Path) -> str`. Zusätzlich verfügbar sind `registry.database`, `registry.secrets`, `registry.settings` und `registry.skills`.

| Werkzeug | Capability | Freigabe |
|---|---|---|
| `list_files`, `read_file`, `search_files` | files.read | – |
| `write_file`, `edit_file`, `make_directory`, `move_path`, `delete_path` | files.write | ja |
| `run_command` | system.execute | ja |
| `recall` / `remember`, `forget` | memory.read / memory.write | – / ja |
| `search_web` / `fetch_url` | network.search / network.fetch | – |
| `list_skills`, `read_skill` | skills.read | – |
| `delegate_task` | agent.delegate | – (Agenten nur mit Lesewerkzeugen) |
| `list_windows` / `open_target`, `focus_window`, `computer_action` | computer.control | – / ja |

## Plugins

`plugins/<name>/plugin.json`:

```json
{"name": "calculator", "description": "…", "version": "1.1.0", "api": 1}
```

`plugins/<name>/plugin.py` definiert `register(registry)`. Der Name muss dem Ordnernamen entsprechen (`[a-z][a-z0-9_]*`). Erlaubt sind nur Capabilities aus `permissions.CAPABILITIES`, für reine Berechnungen `compute.local`. Plugin-Werkzeuge bekommen `source="plugin:<name>"`. Bei Capabilities aus `APPROVAL_REQUIRED` gilt immer die Freigabepflicht. Schlägt `register` fehl, werden die bis dahin registrierten Werkzeuge entfernt und der Fehler landet in `jarvis.plugin_errors`. Ein `api` größer als `PLUGIN_API` wird abgelehnt.

## Skills

`skills/<name>/SKILL.md` mit Frontmatter `name` und `description` (einzeilig, gequotet oder `>`/`|`). `SkillRegistry(root, extra_roots=[(Path, source)])`, `.list() -> [Skill(name, description, path, source)]`, `.read(name) -> str`. Details: [skills.md](skills.md).

## Automation

`AutomationRunner(jarvis).add(prompt, interval_minutes, start_now=False)`, `await run_due() -> [(id, result)]`, `await serve(on_result, interval_seconds=30)`. Datenbank: `claim_automation`, `complete_automation`, `schedule_automation_now`, `set_automation_enabled`, `delete_automation`.

## Einstellungen

`SettingsStore.load()`, `.get(key)`, `.update(**changes)` mit Validierung. Die Schlüssel stehen in `config.SETTINGS_DEFAULTS`.
