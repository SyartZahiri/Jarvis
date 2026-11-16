---
name: safe-refactor
description: Führt Codeänderungen in kleinen, überprüften Schritten durch. Verwenden, wenn der Nutzer Code umbauen, Funktionen ergänzen oder Fehler beheben lassen möchte.
---

# Sicheres Ändern von Code

1. Vorher verstehen: betroffene Dateien lesen, Verwendungen mit `search_files` finden,
   bestehende Tests identifizieren.
2. Plan in höchstens fünf Schritten nennen und mit der Umsetzung beginnen.
3. Änderungen mit `edit_file` (kleine, eindeutige Ersetzungen) statt `write_file`
   vornehmen. Neue Dateien mit `write_file`.
4. Nach jeder logischen Änderung die Tests oder einen Syntaxcheck über `run_command`
   ausführen (z. B. `python -m compileall -q src`, `python -m pytest -q`).
5. Schlägt etwas fehl: Ursache aus der Ausgabe ableiten, korrigieren, erneut prüfen.
   Höchstens drei Versuche, danach Zwischenstand und Blocker berichten.
6. Abschluss: geänderte Dateien, Testergebnis (wörtliche Zahlen) und offene Punkte.

Keine Abhängigkeiten installieren und keine Dateien außerhalb des Auftrags ändern, ohne
vorher zu fragen.
