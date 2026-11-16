# Architektur

## Ziel und Grenzen

JARVIS ist eine Desktopanwendung in Python, die vollständig auf dem eigenen Rechner läuft. Die einzige Pflichtabhängigkeit ist `httpx`. Die Oberfläche ist HTML, CSS und JavaScript ohne Framework und ohne Build-Schritt. Ein lokaler Server in `webapp.py` liefert sie aus, ein Chromium-Browser im App-Modus (Edge, Chrome oder Brave, mit eigenem Profil) zeigt sie an. Modellanbieter sind austauschbar, ein Cloudkonto braucht man nicht. Datei- und Codewerkzeuge arbeiten nur im Arbeitsbereich (`--workspace`, beim Start über das Startmenü `~/JARVIS`).

## Schichten

```text
App-Fenster (--app)   ── HTTP + Server-Sent Events ──┐        cli.py
                                                        ▼          │ asyncio.run
                         webapp.py  JarvisWeb (127.0.0.1)          │
                           Sitzung, Host-, Origin-Prüfung          │
                           AsyncWorker-Loop · Hub (Ereignisse)     │
                                                        ▼          ▼
                     Jarvis.ask(prompt, conversation, approve, on_event)   engine.py
                        │
                        ├─ context.fit()       Verlauf reparieren, Kontextbudget    context.py
                        ├─ _system_message()   stabiler Teil: Rechte, Skills
                        ├─ _turn_context()     Uhrzeit, Erinnerungen, vor der Nachricht
                        ├─ ProviderClient.complete(messages, tools, on_text)        providers.py
                        │     OpenAI-kompatibel · Anthropic · Gemini · Ollama
                        └─ asyncio.gather(_execute(call) …)   parallel je Runde
                              │  Schema → Capability → Freigabe (Lock) → run()
                              ▼
                         ToolRegistry                                               tools.py
                           ├─ tools_files.py  Dateien, Suche, Edit, Papierkorb, run_command
                           ├─ tools_web.py    search_web, fetch_url (IP-Pinning)
                           ├─ computer.py     open_target, Fenster, Maus/Tastatur
                           ├─ agents.py       delegate_task → run_agent (nur Lesewerkzeuge)
                           ├─ Gedächtnis, Skills  remember, recall, forget, list/read_skill
                           └─ plugins.py      geprüfte Python-Erweiterungen

Persistenz: storage.py (SQLite, Migrationen, FTS5) · config.py (Profile, Einstellungen)
            secrets.py (DPAPI bzw. Keyring) · permissions.py (Fähigkeiten)
```

## Ablauf einer Anfrage

1. Die Nachricht wird gespeichert, das Ereignis `conversation` meldet die ID.
2. `context.fit` repariert den Verlauf (fehlende Werkzeugergebnisse nach Abbrüchen werden ergänzt, verwaiste entfernt) und behält die neuesten vollständigen Turns innerhalb von `max_context_chars`. Große Werkzeugergebnisse älterer Turns werden auf 2000 Zeichen gekürzt.
3. Der Systemprompt enthält Arbeitsbereich, aktive und inaktive Fähigkeiten und den Skill-Katalog. Er bleibt zwischen Anfragen unverändert, damit Ollama und die Caches der Cloud-Anbieter den schon verarbeiteten Anfang wiederverwenden können. Gemessen kostete ein veränderter Anfang mit Ollama 5,5 s pro Anfrage. Was sich ändert (Uhrzeit, bis zu fünf passende Erinnerungen, nur mit `memory.read`), steht in einem Block `<kontext>…</kontext>` vor der neuesten Nutzernachricht. Der Block steht vorne, weil kleine lokale Modelle einen angehängten Block fortgeschrieben haben, statt zu antworten. Er existiert nur in der Kopie für den Anbieter, gespeichert wird der Text des Nutzers.
4. Bis zu 12 Modellrunden. Mit `on_event` wird Text gestreamt. Die Werkzeugaufrufe einer Runde laufen per `asyncio.gather` parallel und behalten ihre Reihenfolge. Freigabepflichtige Aufrufe werden über einen Lock nacheinander bestätigt.
5. Jede Antwort und jedes Werkzeugergebnis wird sofort gespeichert. Bei einem Abbruch bleibt der Teiltext mit dem Vermerk „[abgebrochen]“ erhalten. Ist das Rundenlimit erreicht, endet der Verlauf mit einem Hinweis, damit die Historie gültig bleibt.

## Oberfläche und Server

`webapp.run_web` startet `JarvisWeb` auf einem freien Port an `127.0.0.1` und öffnet das Fenster mit einer einmaligen Start-URL (`/?k=<token>`). Der Server tauscht das Token gegen ein `HttpOnly`-Cookie mit `SameSite=Strict`. Ab dann gilt für jede Anfrage:

- Ohne gültiges Sitzungscookie gibt es nichts, auch keine statischen Dateien.
- Der `Host`-Header muss `127.0.0.1:<port>` oder `localhost:<port>` sein. Das blockiert DNS-Rebinding.
- POST-Anfragen brauchen zusätzlich den Header `X-Jarvis: 1`, und `Origin` sowie `Sec-Fetch-Site` dürfen nicht auf eine fremde Seite zeigen.
- Jede Antwort trägt eine strenge Content-Security-Policy (`script-src 'self'`, kein Inline-Code, keine fremden Quellen).

Die Seite holt den Zustand über `GET /api/state`, Aktionen laufen über kleine JSON-Endpunkte (`/api/ask`, `/api/approval`, `/api/profiles/*` …). Alles, was der Kern von sich aus meldet, kommt über einen einzigen Server-Sent-Events-Strom `/api/events`: gestreamter Text, Werkzeugschritte, Freigabeanfragen, Download-Fortschritt, Ergebnisse von Abläufen. Schließt der Nutzer das Fenster, reißt der Strom ab. Nach 6 s ohne Verbindung beendet sich der Server.

Freigaben sind `asyncio.Future`s im Loop des Kerns. Die Seite zeigt einen Dialog, die Entscheidung kommt per `POST /api/approval` zurück. Wird der Lauf abgebrochen, werden offene Freigaben abgelehnt.

## Einrichtung beim ersten Start

Hat JARVIS kein Modellprofil, öffnet die Seite einen Einrichtungsassistenten. `GET /api/setup/ollama` meldet, ob Ollama installiert ist und läuft, welche Modelle vorhanden sind und wie viel Arbeitsspeicher der Rechner hat. Daraus folgt die Empfehlung (`qwen3:1.7b` bis `qwen3:14b`). Fehlende Modelle lädt `POST /api/profiles/pull` mit Fortschritt über den Ereignisstrom. `POST /api/setup/ollama/start` startet Ollama, unter Windows über die Tray-App, weil ein losgelöst gestartetes `ollama serve` zwar Anfragen annahm, aber kein Modell lud.

## Internes Protokoll

`Message(role, content, tool_calls, tool_call_id, name)`, `ToolCall(id, name, arguments, extra)` und `Completion(text, tool_calls)`. In `extra` stehen anbieterspezifische Daten, die unverändert zurückgehen müssen, etwa Gemini-`thoughtSignature`. Die Adapter in `providers.py` übersetzen in beide Richtungen, mit und ohne Streaming. Einzelheiten: [docs/providers.md](docs/providers.md).

## Datenhaltung

| Datei | Inhalt |
|---|---|
| `config.json` | Modellprofile (Kontextbudget, Ausgabelimit, Temperatur, Nachdenken) und aktives Profil, keine Keys |
| `secrets.json` | unter Windows DPAPI-verschlüsselte Werte, sonst Verweise auf den System-Schlüsselbund |
| `settings.json` | Suchanbieter, SearXNG-Adresse, Sprachmodell, Vorlesen, Claude-Skills |
| `permissions.json` | aktivierte Fähigkeiten |
| `plugins.json` | aktivierte Plugins |
| `jarvis.sqlite3` | Gespräche, Nachrichten, Erinnerungen mit FTS5-Index, Abläufe |
| `window/` | Profilordner des App-Fensters (getrennt vom normalen Browser) |
| `app/` | die vom Installer angelegte Python-Umgebung |

Das Schema wird über `PRAGMA user_version` versioniert (aktuell 2). Vor jeder Migration entsteht ein Backup `jarvis.v<alt>.bak`. Eine Datenbank aus einer neueren Version wird nicht angefasst. Neue Migrationen kommen als Funktion in `storage.MIGRATIONS`.

## Nebenläufigkeit

- Ein dauerhafter asyncio-Loop läuft in einem eigenen Thread (`AsyncWorker`). HTTP-Anfragen kommen über `ThreadingHTTPServer` in eigenen Threads an und reichen Coroutinen an diesen Loop weiter. Ereignisse gehen über `Hub` an alle verbundenen Seiten. Eine hängende Seite bremst den Kern nicht, ihre Warteschlange verwirft bei 5000 Einträgen.
- Pro Prozess läuft höchstens eine Anfrage gleichzeitig. Eine zweite bekommt `409`.
- SQLite: eine kurze Verbindung pro Operation im WAL-Modus. Abläufe beanspruchen einen fälligen Lauf atomar (`claim_automation`), sodass Fenster und `automations serve` nicht doppelt ausführen.
- Blockierende Bibliotheken (Vosk, pyttsx3, pyautogui, Dateisuche, DNS) laufen über `asyncio.to_thread`.

## Sicherheitsmodell in Kürze

Das Modell sieht nur Werkzeuge aktiver Fähigkeiten. `files.write`, `system.execute`, `memory.write` und `computer.control` verlangen eine Freigabe pro Aufruf, und Plugins können das nicht abschalten. Pfade werden nach dem Auflösen von Symlinks gegen den Arbeitsbereich geprüft. Programme werden nur über absolute PATH-Einträge gefunden, nie im aktuellen Verzeichnis. `fetch_url` verbindet sich mit der geprüften IP und kontrolliert jede Weiterleitung neu. Ausführlich: [docs/security.md](docs/security.md).

## Erweiterungspunkte

- Neuer Anbieter: Request-Builder und Parser (normal und Stream) in `providers.py`, Eintrag in `config.DEFAULT_ENDPOINTS` und `KIND_LABELS`, Tests mit `httpx.MockTransport`.
- Neues Werkzeug: `Tool(...)` im passenden Modul registrieren, Capability aus `permissions.CAPABILITIES`, bei Freigabepflicht einen Text in `approval.describe`.
- Skills: `skills/<name>/SKILL.md` im Arbeitsbereich oder im Datenordner. Mitgelieferte Skills liegen in `src/jarvis/bundled_skills/` und lassen sich durch einen gleichnamigen eigenen Skill ersetzen.
- Plugins: `plugins/<name>/plugin.json` und `plugin.py` mit `register(registry)`, siehe [docs/interfaces.md](docs/interfaces.md).
- Neue Seite in der Oberfläche: ein `<section class="view" data-view="…">` in `index.html`, eine Render-Funktion in `app.js`, Endpunkte in `webapp.py`.
