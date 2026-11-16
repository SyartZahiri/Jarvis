# Teststrategie

## Ausführen

```powershell
$env:PYTHONPATH="src;tests"      # Linux/macOS: export PYTHONPATH=src:tests
python -m unittest discover -s tests -v
python -m compileall -q src
```

CI (`.github/workflows/tests.yml`) führt dasselbe auf Windows, Linux und macOS mit Python 3.11–3.13 aus. Der DPAPI-Test läuft nur unter Windows.

## Aufbau

| Datei | Schwerpunkt |
|---|---|
| `helpers.py` | `WorkspaceTest` (isolierte Daten, `MemorySecrets`), `FakeClient` (skriptgesteuertes Modell mit Streaming) |
| `test_storage.py` | Migration einer Alt-Datenbank inkl. Backup, FTS-Suche, Export/Import, Automations-Claim, Kontextreparatur und -budget, Profil- und Einstellungsvalidierung, DPAPI |
| `test_providers.py` | Wire-Formate aller Anbieter, Streaming (SSE/NDJSON), Tool-Roundtrips, Thought-Signatures, Retry/Backoff, Fehlerschwärzung, Modelllisten, Ollama-Pull |
| `test_engine.py` | Tool-Schleife mit Ereignissen, Erinnerungen im Kontext, parallele Ausführung, Freigaben, Plugin-Freigabepflicht, Argumentvalidierung, Rundenlimit, Abbruch, Agenten, Automationen |
| `test_tools.py` | Dateiwerkzeuge, Pfad- und Symlink-Grenzen, Programmauflösung, `run_command`, Freigabe-Diffs, `fetch_url` (Pinning, Redirects, private Ziele), Suche, Plugins (Isolation, API-Version), Skills, Markdown |
| `test_web.py` | Der Server über echtes HTTP: Sitzungscookie, Host-Header, Origin und `X-Jarvis`, Liste der statischen Dateien, Keys bleiben auf dem Server, Ereignisstrom einer Anfrage, Freigabe erteilen und ablehnen, zweite Anfrage wird abgewiesen, „Erneut senden“, Ollama-Status, Einrichtung |

Regeln: Netzwerk nur über `httpx.MockTransport`, DNS über einen injizierten Resolver (`register_web_tools(registry, resolver, transport)`), Zeit/Sleep injizierbar (`ProviderClient(sleep=...)`). Tests dürfen weder echte Keys noch Internet oder das Nutzerdatenverzeichnis benötigen.

## End-to-End ohne Cloud

Einen kleinen OpenAI-kompatiblen Server auf `127.0.0.1` starten, der SSE liefert, und dann:

```powershell
$env:JARVIS_DATA_DIR="$env:TEMP\jarvis-e2e"
jarvis profiles add Mock compatible mock-model --endpoint http://127.0.0.1:18765/v1
jarvis permissions enable files.read
jarvis ask "Was liegt im Arbeitsbereich?"
```

## Oberfläche prüfen

`jarvis gui --no-window` startet nur den Server und gibt die Start-URL aus. Die lässt sich in jedem Chromium-Browser öffnen. Mit einem leeren `JARVIS_DATA_DIR` erscheint der Einrichtungsassistent. JavaScript prüft `node --check src/jarvis/web/app.js` auf Syntaxfehler.

## Noch manuell zu prüfen

Je Anbieter mit echtem Konto: Verbindungstest, Streaming, mehrere parallele Tool-Calls, 401/429. Ollama mit einem tool-fähigen Modell. Vosk mit Mikrofon, pyttsx3, pyautogui und Fensterfokus auf einem echten Desktop. Die Oberfläche bei 100, 125 und 150 % Skalierung, mit Tastatur und mit einem Screenreader. Den Installer auf einem frischen Windows-Konto, unter macOS und unter Linux. Ergebnisse mit Datum in `TASKS.md` eintragen.
