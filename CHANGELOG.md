# Änderungen

## 0.3.0 (16.11.2026)

- Neue Oberfläche im HUD-Stil in einem eigenen App-Fenster (Edge, Chrome oder Brave). Die alte Tk-Oberfläche ist entfernt.
- Einrichtungsassistent beim ersten Start: erkennt und startet Ollama, schlägt ein Modell passend zum Arbeitsspeicher vor und lädt es, verbindet Cloud-Anbieter mit Key und Test.
- Installer für Windows (`install.cmd`) sowie macOS und Linux (`install.sh`), App-Icon. Unter Windows deinstalliert der Startmenü-Eintrag „JARVIS deinstallieren“, auch ohne den heruntergeladenen Ordner.
- Unbeantwortete Nachrichten lassen sich erneut senden. Läuft Ollama nicht, bietet JARVIS an, es zu starten.
- Uhrzeit und Erinnerungen stehen jetzt vor der Nachricht des Nutzers. Kleine lokale Modelle hatten den Block am Ende endlos wiederholt.
- Abgelehnte Verbindungen zu lokalen Endpunkten werden sofort gemeldet statt zweimal wiederholt.
- Die mitgelieferten Skills sind Teil des Pakets und nach jeder Installation vorhanden.
- Kopiert ein Modell die Zeilennummern aus `read_file` in `edit_file`, werden sie ignoriert, statt dass die Bearbeitung fehlschlägt.
- Im Einrichtungsassistenten ist der Knopf auch mit vielen installierten Modellen erreichbar. Lange Modellnamen laufen nicht mehr über die Anzeige im Kern.
- Die Start-URL des Fensters gilt nur noch einmal. Vorher ließ sie sich wiederverwenden, solange JARVIS lief, und andere Programme können die Befehlszeile des Browsers auslesen.

## 0.2.1 (12.11.2026)

- Profiloption „Nachdenken“ für Ollama. Mit „aus“ antwortet qwen3 mehrfach schneller.
- Der Systemprompt bleibt zwischen Anfragen gleich, damit Ollama den verarbeiteten Anfang wiederverwendet.

## 0.2.0 (12.11.2026)

- Streaming für alle Anbieter, Abbrechen, parallele Werkzeugaufrufe.
- Freigabedialog mit Diff und aufgelöstem Programmpfad.
- Programme werden nur noch in absoluten PATH-Einträgen gesucht, `fetch_url` ist gegen DNS-Rebinding geschützt.
- Datenbankmigrationen mit Backup, Gedächtnissuche über FTS5, JSON-Export.
- Skills mit mehrzeiligem Frontmatter, Teilagenten, Abläufe mit Schutz gegen Doppelausführung.

## 0.1.0

- Erste Fassung mit Chat, Werkzeugen, Gedächtnis und Anbieterprofilen.
