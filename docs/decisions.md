# Architekturentscheidungen

| Datum | Entscheidung | Grund / Folge |
|---|---|---|
| 2026-11-12 | Tkinter für die erste Desktopversion | Gehört zur Python-Standardbibliothek, lässt sich ohne verpflichtendes GUI-Paket starten. Am 16.11.2026 durch die HTML-Oberfläche abgelöst. |
| 2026-11-12 | `httpx` als einzige Kernabhängigkeit | Asynchrone Requests, MockTransport für Tests, einheitliches Timeout-Verhalten. |
| 2026-11-12 | Vier direkte Wire-Adapter | OpenAI-kompatible Chat Completions decken OpenAI, OpenRouter und eigene Endpunkte ab; Anthropic, Gemini und Ollama benötigen eigene Nachrichtenformate. |
| 2026-11-12 | SQLite und JSON statt Server | Lokal nutzbar, geringe Betriebsanforderungen. Migrationen und Backups vor Produktfreigabe nötig. |
| 2026-11-12 | DPAPI bzw. System-Keyring | Kein selbstgebauter Master-Key und keine Klartext-Keys in Projektdateien. |
| 2026-11-12 | Skills sind Text, Plugins Code | Claude-Skills können gelesen werden, ohne Skripte automatisch auszuführen; Python-Plugins werden nur nach expliziter Aktivierung geladen. |
| 2026-11-12 | Seiteneffekte brauchen Capability und ggf. Einzelbestätigung | Modellantworten allein sollen keine dauerhafte Ausführungsberechtigung erhalten. |

| 2026-11-12 | Streaming über `on_text`/`on_event`-Callbacks statt Async-Generatoren | Provider bleiben einfach testbar; GUI und CLI hängen sich ohne Kopplung an. Ohne Callback läuft der nicht-streamende Pfad. |
| 2026-11-12 | Ein persistenter asyncio-Loop pro GUI-Prozess | Abbrechbar (`Future.cancel`), Freigaben als `asyncio.Future` ohne Blockieren, keine Loop-Erzeugung pro Anfrage. |
| 2026-11-12 | Parallele Werkzeugausführung pro Runde, Freigaben seriell per Lock | Schnellere Recherche/Agenten; der Nutzer sieht nie zwei Freigabedialoge gleichzeitig. |
| 2026-11-12 | Kontextbudget in Zeichen statt Tokens | Ohne Tokenizer-Abhängigkeit für alle Anbieter gleich; Profilwert anpassbar. Ganze Turns bleiben erhalten, damit Tool-Paare nie getrennt werden. |
| 2026-11-12 | `PRAGMA user_version`-Migrationen mit Backup-API | Keine ORM-Abhängigkeit; konsistente Sicherung auch im WAL-Modus. |
| 2026-11-12 | FTS5 für das Gedächtnis, Embeddings vertagt | Sofort nutzbar und lokal; semantische Suche erfordert ein Embedding-Modell und folgt optional (TASKS.md). |
| 2026-11-12 | Erinnerungen automatisch in den Kontext (max. 5, nur mit `memory.read`) | Das Gedächtnis wirkt, ohne dass das Modell erst `recall` aufrufen muss. Abschaltbar über die Berechtigung. |
| 2026-11-12 | `fetch_url` mit IP-Pinning und SNI | Schließt das DNS-Rebinding-Fenster ohne zusätzliche Abhängigkeit (httpcore-Extension `sni_hostname`). |
| 2026-11-12 | Eigene PATH-Suche statt `shutil.which` | Windows durchsucht sonst zuerst das aktuelle Verzeichnis; eingeschleuste Programme wären möglich. |
| 2026-11-12 | Löschen = Papierkorb im Arbeitsbereich | Jede Löschung durch das Modell bleibt umkehrbar. |
| 2026-11-12 | Designsystem Graphit/Bernstein mit Mono-Legenden | Eine Signalfarbe für Zustände. Am 16.11.2026 durch den HUD-Stil in Blau abgelöst. |
| 2026-11-12 | Claude-Skills aus `~/.claude/skills` nur per Opt-in | Beschreibungen können persönliche Angaben enthalten und würden an Cloud-Anbieter gehen. |
| 2026-11-12 | Stabiler Systemprompt, Uhrzeit und Erinnerungen in einem Kontextblock der neuesten Nachricht | Live gemessen: Ein geänderter Prompt-Anfang kostete mit Ollama 5,5 s je Anfrage. Ein stabiler Anfang wird gecacht, auch von Cloud-Anbietern. |
| 2026-11-12 | Profiloption „Nachdenken“ (`think`), Standard = Modellverhalten | qwen3 denkt in Ollama standardmäßig nach und war dadurch rund sechsmal langsamer. Nutzer wählen zwischen Tempo und Gründlichkeit. |
| 2026-11-16 | Oberfläche als HTML/CSS/JS in einem Chromium-App-Fenster statt Tkinter | Tk kann weder Kantenglättung noch Transparenz oder flüssige Animation. Der lokale Server braucht keine neue Abhängigkeit. Edge ist meist vorhanden, kann in der EU aber fehlen, deshalb auch Chrome und Brave, notfalls der Standardbrowser. |
| 2026-11-16 | Sitzungscookie aus Einmal-Token, Host-Prüfung, Origin plus `X-Jarvis` bei POST, strenge CSP | Eine Webseite im normalen Browser darf JARVIS weder lesen noch steuern, auch nicht per DNS-Rebinding oder CSRF. Modellausgaben dürfen kein Skript ausführen. |
| 2026-11-16 | Server-Sent Events statt WebSocket | Ein Kanal nur vom Kern zur Seite reicht, Aktionen laufen über normale POSTs. Die Standardbibliothek kann SSE ohne Zusatzpaket. |
| 2026-11-16 | Kontextblock `<kontext>` vor statt nach der Nutzernachricht | Live mit qwen3:0.6b: Stand der Block am Ende, schrieb das Modell ihn endlos fort. Davor gestellt rief es das richtige Werkzeug auf und antwortete korrekt. |
| 2026-11-16 | Keine Wiederholung bei abgelehnter Verbindung zu lokalen Endpunkten | Läuft Ollama nicht, ändert Warten nichts. Der Fehler kommt sofort, die Oberfläche bietet „Ollama starten“ an. |
| 2026-11-16 | Ollama unter Windows über die Tray-App starten | Ein losgelöst gestartetes `ollama serve` nahm Anfragen an, lud aber nie ein Modell. |
| 2026-11-16 | Einrichtungsassistent mit Modellvorschlag nach Arbeitsspeicher | Neue Nutzer sollen ohne Vorwissen zu einem passenden lokalen Modell kommen. |
| 2026-11-16 | Installer pro Benutzer mit eigener venv unter `%LOCALAPPDATA%\JARVIS\app` | Keine Adminrechte, keine Konflikte mit anderen Python-Projekten, Deinstallation ohne Datenverlust. |
| 2026-11-16 | Mitgelieferte Skills im Paket (`bundled_skills`) | Sonst fehlen sie nach einer normalen Installation. Eigene Skills gleichen Namens haben Vorrang. |
