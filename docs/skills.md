# SKILL.md in JARVIS

JARVIS übernimmt das Grundmuster der Claude-Skills: ein Ordner pro Skill mit einer `SKILL.md`, die mit YAML-Frontmatter (`name`, `description`) beginnt und darunter Markdown-Anweisungen enthält.

```markdown
---
name: code-review
description: >
  Prüft Code auf belegbare Fehler. Verwenden bei Reviews und Fehlersuche.
---

# Code-Review
1. …
```

## Fundorte (in dieser Reihenfolge, der erste Name gewinnt)

1. `<arbeitsbereich>/skills/` (Quelle `workspace`)
2. `<JARVIS-Datenordner>/skills/` (Quelle `jarvis`), gilt für alle Arbeitsbereiche
3. `~/.claude/skills/` (Quelle `claude`), nur wenn unter System → Skills eingeschaltet
4. `src/jarvis/bundled_skills/` (Quelle `mitgeliefert`), wird mit JARVIS installiert

## Ablauf

Im Systemprompt stehen Name und Beschreibung aller Skills. Passt ein Skill zur Aufgabe, lädt das Modell ihn mit `read_skill`. JARVIS hängt dann eine Liste der übrigen Dateien des Skills an (Skripte, Referenzen), lädt oder startet sie aber nicht. Soll ein Skript laufen, braucht es dafür `run_command` mit Freigabe.

Unterstützt werden einzeilige, gequotete und mehrzeilige Werte (`>`, `|`). Weitere Felder wie `license` oder `allowed-tools` werden ignoriert, Berechtigungen kann ein Skill nicht vergeben. Pro Datei gilt eine Obergrenze von 100 KB.

## Mitgelieferte Skills

- `code-review`: belegbare Befunde mit Fundstelle als Tabelle
- `research-brief`: Recherche mit Primärquellen, Quellenliste und Unsicherheiten
- `safe-refactor`: kleine Schritte mit `edit_file` und einem Testlauf nach jeder Änderung

Eigene Skills mit demselben Namen haben Vorrang vor den mitgelieferten.
