# Sicherheitsmodell und bekannte Grenzen

## Bedrohungsmodell

JARVIS führt Anweisungen eines Sprachmodells aus, das Inhalte aus unvertrauenswürdigen Quellen liest (Webseiten, Dateien, Skills, Werkzeugergebnisse). Die zentrale Annahme lautet: **Das Modell kann manipuliert werden (Prompt Injection).** Die Schutzmaßnahmen sind deshalb technisch umgesetzt und verlassen sich nicht auf den Prompt.

## Geheimnisse

- API-Keys stehen nie in `config.json`, SQLite, Logs oder Fehlermeldungen. Windows verschlüsselt sie per DPAPI (`CryptProtectData`) im Benutzerkontext, andere Systeme nutzen `keyring`. Ohne passendes Backend schlägt das Speichern fehl, statt im Klartext zu schreiben.
- Fehlertexte der Anbieter werden gekürzt und geschwärzt (der eigene Key und tokenähnliche Zeichenfolgen). Fehler-Bodies, die kein JSON sind, werden nie übernommen.
- DPAPI schützt gegen Diebstahl der Datei, nicht gegen Schadsoftware, die als derselbe Benutzer läuft.

## Fähigkeiten und Freigaben

- Ab Werk aktiv: `memory.read`, `skills.read`, `compute.local`. Alles andere wird bewusst eingeschaltet. Werkzeuge inaktiver Fähigkeiten sieht das Modell nicht.
- `files.write`, `system.execute`, `memory.write` und `computer.control` brauchen eine **Freigabe je Aufruf**. Der Dialog zeigt einen Diff bzw. den exakten Befehl samt aufgelöstem Programmpfad. Der Fokus liegt auf „Ablehnen“, und Enter löst keine Freigabe aus.
- Plugins können die Freigabepflicht für diese Fähigkeiten nicht abschalten. Sie sind trotzdem vertrauenswürdiger Code ohne Sandbox: Ein Plugin kann die Registry verändern. Deshalb nur geprüfte Plugins aktivieren.
- Teilagenten bekommen nur Lesewerkzeuge ohne Freigabepflicht. Automationen haben keinen Freigabekanal und können daher weder schreiben noch ausführen.

## Dateisystem und Prozesse

- Jeder Pfad wird nach dem Auflösen (inklusive Symlinks) gegen den Arbeitsbereich geprüft. Löschen verschiebt nach `.jarvis/trash/<Zeitstempel>/`, das ist wiederherstellbar. Schreiben erfolgt atomar über eine temporäre Datei.
- `run_command` startet ohne Shell mit Argumentliste, ohne Konsolenfenster, mit Timeout (Standard 60 s, höchstens 900 s) und gekürzter Ausgabe. Programmnamen werden **nur in absoluten PATH-Einträgen** gesucht, nie im aktuellen Verzeichnis. So kann eine eingeschleuste `git.exe` im Arbeitsbereich nicht das echte Programm ersetzen. Relative Pfade (`./build.bat`) müssen im Arbeitsbereich liegen.
- Ein gestartetes Programm hat die vollen Benutzerrechte und ist **nicht** auf den Arbeitsbereich beschränkt. Das muss bei der Freigabe beurteilt werden.
- `open_target` öffnet keine ausführbaren oder Skriptdateien (`.exe`, `.bat`, `.ps1`, `.lnk`, `.py` …); dafür gibt es `run_command` mit eigener Freigabe.

## Netzwerk

- Provider-Endpunkte: HTTPS, HTTP nur für Loopback; keine Zugangsdaten oder Query-Parameter in der URL; keine Weiterleitungen; `trust_env=False` (keine Proxy-Variablen).
- `fetch_url`: nur HTTPS, keine Zugangsdaten. Alle aufgelösten Adressen müssen global sein; schon eine private Adresse führt zur Ablehnung. Die Verbindung geht an genau die geprüfte IP, während SNI, Zertifikatsprüfung und `Host`-Header den ursprünglichen Namen verwenden (**Schutz gegen DNS-Rebinding**). Jede Weiterleitung (höchstens 5) wird erneut geprüft. Erlaubt sind nur Textinhalte, der Download ist auf 3 MB begrenzt.
- Bekannte Restrisiken: Ein Modell, das Daten lesen und Webseiten abrufen darf, kann Daten über URL-Parameter an fremde Server schicken (Exfiltration), wenn eine Webseite es dazu anstiftet. Wer `files.read` oder ein Gedächtnis mit sensiblen Daten nutzt, sollte `network.fetch` nur bei Bedarf aktivieren.

## Die Oberfläche

Die Oberfläche ist eine Webseite, die ein lokaler Server an `127.0.0.1` auf einem zufälligen Port ausliefert. Weil jede Webseite im normalen Browser Anfragen an `127.0.0.1` schicken kann, gelten diese Regeln:

- Das App-Fenster bekommt beim Start eine einmalige URL mit einem 256-Bit-Token. Der Server tauscht sie gegen ein Cookie (`HttpOnly`, `SameSite=Strict`). Ohne dieses Cookie liefert er nichts aus, auch keine statischen Dateien. Fremde Seiten kennen das Token nicht und bekommen das Cookie wegen `SameSite=Strict` nicht mitgeschickt.
- Der `Host`-Header muss `127.0.0.1:<port>` oder `localhost:<port>` lauten. Damit scheitert DNS-Rebinding, bei dem eine fremde Domain plötzlich auf `127.0.0.1` zeigt.
- Zustandsändernde Anfragen sind POSTs mit `Content-Type: application/json` und dem Header `X-Jarvis: 1`. Ein fremder `Origin` oder `Sec-Fetch-Site: cross-site` führt zur Ablehnung. Der Server beantwortet keine CORS-Vorabanfragen.
- Ausgeliefert werden nur die Dateien einer festen Liste. Pfade werden nicht aufgelöst.
- Jede Antwort trägt `Content-Security-Policy: default-src 'none'; script-src 'self'; …`. Inline-Skripte und Inline-Styles laufen nicht. Text von Modell, Dateien und Webseiten setzt `app.js` ausschließlich escaped ins DOM, Links öffnen über den Server im Standardbrowser und nur mit `http` oder `https`.
- Der Server protokolliert keine URLs, weil die Start-URL das Token enthält.
- API-Keys gehen von der Seite zum Server, nie zurück. `GET /api/state` meldet nur `has_key`.

Restrisiko: Schadsoftware, die als derselbe Benutzer läuft, kann die Start-URL aus der Prozessliste lesen. Gegen solche Software schützt aber auch DPAPI nicht.

## Daten an Anbieter

Mit Cloud-Profilen gehen Nachrichten, bis zu fünf passende Erinnerungen (im Block `<kontext>` vor der neuesten Nachricht), der Skill-Katalog und Werkzeugergebnisse (z. B. Dateiinhalte) an den Anbieter. Claude-Skills aus `~/.claude/skills` werden nur nach Opt-in einbezogen, weil ihre Beschreibungen persönliche Angaben enthalten können. Für vollständige Lokalität: Ollama oder ein lokaler Endpunkt, Websuche aus.

## Inhalte aus Erweiterungen

`SKILL.md`, Dateien und Webseiten sind Daten. Der Systemprompt kennzeichnet sie so, die technischen Prüfungen oben gelten aber unabhängig davon. Skripte eines Skills werden nie automatisch ausgeführt.
