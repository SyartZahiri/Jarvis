# Arbeitsstand

Stand: 16. November 2026, Version 0.3.0. Ein Haken heißt: gebaut und durch die genannten Tests geprüft. Was nur von Hand oder gar nicht geprüft wurde, steht darunter getrennt.

## Fertig

- [x] Oberfläche als HUD in einem App-Fenster (Edge, Chrome oder Brave): Punktkugel als Zustandsanzeige, Systemstatus, Fähigkeiten, Protokoll, Gespräch mit Live-Text und Werkzeugschritten, Kontext-Anzeige, Befehlspalette (Strg+K), Verlauf mit Suche, Umbenennen und Löschen
- [x] Lokaler Server mit Sitzungscookie, Host-Prüfung, Origin und `X-Jarvis` bei POST, fester Dateiliste und strenger CSP
- [x] Einrichtungsassistent: Ollama erkennen und starten, Modell nach Arbeitsspeicher vorschlagen und mit Fortschritt laden, Cloud-Anbieter mit Key, Test und Modellliste, Voreinstellungen für Berechtigungen
- [x] „Erneut senden“ bei unbeantworteter Nachricht, „Ollama starten“, wenn Ollama nicht läuft
- [x] Installer für Windows (`install.cmd`/`install.ps1`, ohne Adminrechte, mit Deinstallation) und für macOS/Linux (`install.sh`); App-Icon als SVG, PNG und ICO
- [x] Anbieter OpenAI, OpenRouter, OpenAI-kompatibel, Anthropic, Gemini und Ollama, jeweils mit und ohne Streaming; Retry bei 408/425/429/5xx/529, sofortiger Fehler bei abgelehnter lokaler Verbindung
- [x] Profile mit Kontextbudget, Ausgabelimit, Temperatur und Nachdenken (Ollama `think`)
- [x] Stabiler Systemprompt für Prompt-Caching, Uhrzeit und Erinnerungen im Block vor der neuesten Nachricht
- [x] Kontextverwaltung: Reparatur nach Abbrüchen, Budget nach vollständigen Turns, Kürzung alter Werkzeugergebnisse
- [x] SQLite mit Migrationen und Backup, FTS5-Gedächtnissuche, JSON-Export, Import von Erinnerungen
- [x] Werkzeuge für Dateien, Code, Web (Brave, SearXNG, `fetch_url` mit IP-Pinning), Computer (Öffnen, Fenster, optional Maus/Tastatur), Teilagenten, Abläufe, Sprache
- [x] Skills im Claude-Format, drei davon im Paket mitgeliefert und überschreibbar; Plugins mit Fehlerisolation
- [x] CLI mit `chat`, `ask`, `profiles` (inkl. `pull`, `think`), `gui --no-window`, `automations serve`, `export`
- [x] CI für Windows, Linux und macOS mit Python 3.11 bis 3.13

## Prüfungen am 16.11.2026 (Windows 11, Python 3.14.5, Ollama 0.32.14)

- `python -m unittest discover -s tests`: 81 Tests, 1 übersprungen (Symlinks brauchen unter Windows den Entwicklermodus).
- `node --check src/jarvis/web/app.js` und `python -m compileall -q src`: ohne Fehler.
- Oberfläche bei 1480×920 im eingebauten Chromium-Browser: Startbild, Anfrage mit Werkzeugaufruf (`read_file`, Antwort „Milch, Brot“ mit `qwen3:0.6b`), Fehlerweg mit gestopptem Ollama, „Ollama starten“, „Erneut senden“, Verlauf, Systemseite, kompletter Einrichtungsassistent mit leerem Datenordner.
- Dabei gefunden und behoben: Der Kontextblock am Ende der Nachricht brachte `qwen3:0.6b` dazu, ihn endlos zu wiederholen. Ein losgelöst gestartetes `ollama serve` lud keine Modelle. Abgelehnte lokale Verbindungen wurden zweimal wiederholt (12 s bis zur Fehlermeldung). Die Modellvorauswahl im Assistenten stand fest, bevor der Ollama-Status da war. Mitgelieferte Skills fehlten nach einer Installation.
- `install.ps1` real ausgeführt (`-NoDesktop -NoStart`): Python 3.14 gefunden, Umgebung angelegt, Paket mit Web-Dateien und Skills installiert, Startmenü-Eintrag mit Icon. Dabei gefunden und behoben: Windows PowerShell 5.1 entfernt doppelte Anführungszeichen aus Argumenten, die Python-Erkennung schlug deshalb fehl.

## Prüfung am PC (16.11.2026, Windows 11, RTX 3080, Ollama 0.40.16)

- Kompletter Einrichtungsassistent mit drei installierten Ollama-Modellen, Anfrage mit `read_file` und Bearbeitung mit Freigabedialog (Erlauben, Ablehnen, Esc) mit einem 9B-Modell auf der Grafikkarte, Antwort in 2,3 s.
- Die Start-URL gilt im echten Browser nur einmal (zweiter Aufruf: 403).
- Dabei gefunden und behoben: Mit mehreren installierten Modellen war der Knopf im Assistenten abgeschnitten. Ein langer Modellname lief oben im Kern über die Anzeige rechts. Das Modell kopierte die Zeilennummern aus `read_file` in `edit_file`, die Bearbeitung schlug fehl.

## Nicht geprüft

- Das echte App-Fenster per Bildschirmfoto (auf diesem Rechner Chrome, Edge ist nicht installiert): Der Rechner war während der Prüfung gesperrt, die Darstellung wurde im eingebauten Browser geprüft, der dieselbe Engine nutzt.
- `install.sh` unter macOS und Linux, `install.ps1` auf einem Rechner ohne Python (winget-Weg) und `-Uninstall`.
- Cloud-Anbieter mit echtem Key, Ollama-Modelle über 9B.
- Mikrofon mit Vosk, Vorlesen, pyautogui, Fensterfokus auf einem echten Desktop.
- Skalierung 125 und 150 %, Screenreader.

## Als Nächstes

- [ ] Die offenen Prüfungen oben nachholen.
- [ ] Nachdenken auch für Anthropic, Gemini und die Reasoning-Modelle von OpenAI einstellbar machen und den Denkfortschritt optional zeigen.
- [ ] Bilder und Dateien als Anhang im Gespräch; Bildschirminhalt für Modelle mit Bildeingabe.
- [ ] Abläufe nach Uhrzeit und Wochentag (Cron), Benachrichtigungen, Ausführung als Hintergrunddienst.
- [ ] Semantische Gedächtnissuche über lokale Embeddings, Einträge bearbeiten.
- [ ] MCP-Client für externe Werkzeug-Server.
- [ ] Signierte Builds, damit Windows SmartScreen nicht warnt.
- [ ] Externes Sicherheitsreview, Fuzzing der Parser.
