---
name: research-brief
description: Erstellt eine belegte Kurzrecherche mit Quellen. Verwenden bei Fragen zu aktuellen Themen, Vergleichen, Preisen oder Fakten, die sich ändern können.
---

# Recherche-Briefing

1. Die Frage in 2–4 präzise Suchanfragen zerlegen und mit `search_web` suchen.
2. Die 3–5 aussagekräftigsten Treffer mit `fetch_url` lesen. Primärquellen (Hersteller,
   Behörden, Originalstudien) vor Sekundärquellen bevorzugen.
3. Bei umfangreichen Themen parallel `delegate_task` mit Rolle `researcher` für
   Teilfragen nutzen.
4. Widersprüche zwischen Quellen ausdrücklich nennen. Datum der Information angeben.
5. Nichts behaupten, was in keiner gelesenen Quelle steht.

Ausgabe:
- **Kurzantwort** (2–3 Sätze)
- **Details** als Stichpunkte, jeder mit Quellenverweis [1], [2] …
- **Quellen**: nummerierte Liste `Titel – URL (Stand)`
- **Unsicherheiten**
