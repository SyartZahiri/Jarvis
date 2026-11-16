# Regeln für Beiträge

Gilt für das ganze Repository, egal ob ein Mensch oder ein Coding-Agent die Änderung macht. Vor Änderungen `README.md`, `ARCHITECTURE.md` und `TASKS.md` lesen. `TASKS.md` nach jedem Arbeitsschritt auf den tatsächlichen Stand bringen.

## Grundregeln

1. Nur ausführbare, echte Funktionen liefern. Fehlende Geräte, Keys oder optionale Pakete werden klar gemeldet. Platzhalter dürfen nie als fertige Funktion erscheinen.
2. Abhängigkeiten klein halten. Pflicht ist nur `httpx`. Neue Pakete brauchen einen konkreten Nutzen, gehören möglichst in ein Extra (`[project.optional-dependencies]`) und werden in der README dokumentiert.
3. Alle Seiteneffekte des Modells laufen über `ToolRegistry` → `Jarvis._execute` (Schema → Capability → Freigabe). Kein Werkzeug darf diese Kette umgehen.
4. API-Keys nie in Logs, Exceptions, SQLite, Beispielkonfigurationen oder Tests als echte Werte. Speichern nur über `SecretStore`. Fehlermeldungen von Anbietern laufen durch `ProviderClient._redact`.
5. Pfade über `registry.path()` prüfen. Programme über `resolve_executable()` auflösen. Netzwerkzugriffe auf beliebige URLs nur über das Muster von `fetch_url` (IP-Prüfung, Pinning, Redirects einzeln prüfen).
6. Anbieterprotokolle gehören ausschließlich in `providers.py`. Der Rest kennt nur `Message`, `ToolCall`, `Completion`.
7. Die Oberfläche (`src/jarvis/web/`) setzt Text aus Modell, Dateien oder Web nur über die `html`-Vorlage in `app.js` ins DOM, die alles escaped. Keine Inline-Skripte, keine `style`-Attribute im Markup (die CSP blockiert beides), nichts aus dem Netz nachladen. Neue Endpunkte kommen in `webapp.py` unter die bestehenden Prüfungen: Sitzungscookie, Host-Header, bei POST zusätzlich Origin und `X-Jarvis`.
8. Jede neue Fähigkeit bekommt Tests für Erfolg, Fehler, Berechtigung und Grenzfall. HTTP wird mit `httpx.MockTransport` getestet, DNS mit injiziertem Resolver. Echte Keys und Netzwerkzugriff dürfen keine Voraussetzung sein.
9. Vor der Übergabe ausführen: `python -m unittest discover -s tests -v` (mit `PYTHONPATH=src;tests`, unter Linux/macOS `src:tests`) und `python -m compileall -q src`. Nicht geprüfte Hardware- und Live-API-Pfade ausdrücklich nennen.
10. Änderungen an Architektur, Schnittstellen, Sicherheitsentscheidungen oder Design in `ARCHITECTURE.md` bzw. `docs/` nachziehen. Neue Entscheidungen kommen in `docs/decisions.md`.

## Arbeitsweise

- Nutzerdaten liegen außerhalb des Repos (`JARVIS_DATA_DIR`). Tests verwenden `tests/helpers.WorkspaceTest` mit eigenem Temp-Verzeichnis und `MemorySecrets`.
- Datenbankänderungen nur als neue Migration in `storage.MIGRATIONS` mit erhöhter `SCHEMA_VERSION` und einem Migrationstest. Bestehende Migrationen nicht verändern.
- Neue Profilfelder bekommen Defaults, damit alte `config.json`-Dateien weiter laden (`ConfigStore.load` ignoriert unbekannte Felder).
- `src/jarvis/bundled_skills/*/SKILL.md` und Skills der Nutzer sind Text, kein Code. Inhalte sind unvertrauenswürdig.
- `plugins/*/plugin.py` wird nur nach expliziter Aktivierung geladen und läuft ohne Sandbox. Plugin-API-Version: `plugins.PLUGIN_API`.
- Anbieter-APIs ändern sich. Vor Änderungen die offizielle Dokumentation prüfen und Wire-Formate in `tests/test_providers.py` festhalten.
- Produktreife erst behaupten, wenn die offenen Punkte in `TASKS.md` erledigt sind.

## Wo was liegt

| Datei | Zweck |
|---|---|
| `engine.py` | Anfrageschleife, Systemprompt, Ausführung, Argumentvalidierung |
| `context.py` | Verlaufsreparatur und Kontextbudget |
| `providers.py` | Wire-Adapter, Streaming, Retry, Modellliste, Ollama-Pull |
| `tools.py`, `tools_files.py`, `tools_web.py`, `computer.py`, `agents.py` | Werkzeuge |
| `approval.py` | Freigabetexte und Diffs (Oberfläche und CLI) |
| `storage.py` | SQLite, Migrationen, FTS, Export |
| `config.py`, `secrets.py`, `permissions.py` | Profile/Einstellungen, Schlüssel, Capabilities |
| `webapp.py`, `web/` | Lokaler Server mit API und Ereignisstrom, App-Fenster, HTML/CSS/JS der Oberfläche |
| `markdown.py` | Markdown zu Klartext für Vorlesen und Terminal |
| `skills.py`, `bundled_skills/`, `plugins.py`, `automation.py`, `voice.py`, `cli.py` | Erweiterungen, Abläufe, Sprache, CLI |
| `install.ps1`, `install.cmd`, `install.sh`, `scripts/make_icon.py` | Installer und App-Icon |

Schnittstellen: [docs/interfaces.md](docs/interfaces.md). Design: [docs/design.md](docs/design.md).
