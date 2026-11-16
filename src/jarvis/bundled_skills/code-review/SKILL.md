---
name: code-review
description: >
  Prüft Code im Arbeitsbereich auf belegbare Fehler, Sicherheitslücken und fehlende Tests.
  Verwenden, wenn der Nutzer ein Review, eine Fehlersuche oder eine Qualitätsprüfung wünscht.
---

# Code-Review

1. Umfang klären: Welche Dateien oder welches Modul? Ohne Angabe mit `list_files`
   (rekursiv, Muster `*.py` o. ä.) die relevanten Dateien bestimmen.
2. Jede Datei mit `read_file` vollständig lesen; Aufrufer mit `search_files` finden.
3. Nur **belegbare** Befunde melden, jeweils mit `pfad:zeile`, konkretem Fehlerszenario
   (Eingabe → falsches Verhalten) und einem Korrekturvorschlag.
4. Reihenfolge: Korrektheit → Sicherheit (Injection, Pfade, Geheimnisse) → Fehlerbehandlung
   → Tests → Lesbarkeit. Stilfragen nur, wenn sie Fehler begünstigen.
5. Wenn Tests existieren und `system.execute` aktiv ist: Testbefehl vorschlagen und nach
   Freigabe ausführen; Ergebnis wörtlich zusammenfassen.
6. Keine Dateien ändern, solange der Nutzer nicht ausdrücklich darum bittet.

Ausgabeformat: Tabelle „Schwere | Fundstelle | Problem | Vorschlag“, danach maximal drei
Sätze Gesamteinschätzung.
