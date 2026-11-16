# Designsystem

Quelle: `src/jarvis/web/app.css` (Tokens in `:root`), `index.html` (Aufbau) und `app.js` (Kern-Animation). Neue Farben, Schriften oder Abstände kommen als Token in `:root`, nicht als Einzelwert in eine Regel.

## Haltung

Ein Instrumentenpult für einen Assistenten: tiefes Nachtblau, feine Linien, elektrisches Blau als einzige Signalfarbe. Im Zentrum steht der Kern, eine rotierende Punktkugel, die zeigt, was JARVIS gerade tut. Links stehen Systemwerte, rechts das Gespräch. Die Anmutung ist von Science-Fiction-Interfaces inspiriert, kopiert aber keine Vorlage.

Farbe trägt Bedeutung. Blau heißt aktiv oder wichtig, Grün erledigt, Bernstein wartet auf dich (Freigabe), Rot heißt Fehler oder hohes Risiko. Lila-Verläufe und Emoji-Icons gibt es bewusst nicht.

## Tokens

| Token | Wert | Verwendung |
|---|---|---|
| `--bg` | `#030913` | Grundfläche, darüber ein Raster und ein radialer Blauschimmer |
| `--panel` | `rgba(7,19,38,.72)` | Karten, mit `backdrop-filter: blur` |
| `--line`, `--line-2`, `--line-3` | Blau mit 12, 22, 42 % Deckkraft | Trennlinien, Kartenränder, Fokus |
| `--text`, `--text-2`, `--text-3` | `#DCEBFF`, `#98B3D6`, `#5F7A9E` | Text, Sekundärtext, Beschriftungen |
| `--blue`, `--blue-hi`, `--blue-deep` | `#3FA6FF`, `#9FD3FF`, `#1B6FE0` | Signalfarbe, Hervorhebung, Verlaufsende der Knöpfe |
| `--ok`, `--warn`, `--danger` | `#3FDDA0`, `#FFB547`, `#FF5F6D` | erledigt, Freigabe, Fehler |

## Schrift

Beschriftungen, Kopfzeile und Uhr stehen in Bahnschrift (bei Windows dabei, DIN-ähnlich), Fließtext in Segoe UI Variable, Zahlen und Pfade in Cascadia Mono. Ersatzschriften für macOS und Linux stehen in den Tokens `--font-hud`, `--font-body` und `--font-mono`. Kleine Beschriftungen sind Versalien mit weitem Abstand (`.label`), alles andere ist normal geschrieben. Zahlen in Tabellen und Uhr nutzen `tabular-nums`.

## Aufbau

```text
┌ Marke ─ Gruß · Uhr · Datum ──────────────── [Kern][Gedächtnis][Abläufe][System] ⌘ ┐
│ Systemstatus      │             Kern (Canvas)              │ Unterhaltung          │
│ Fähigkeiten       │     Punktkugel, Orbits, Messring        │  Verlauf (aufklappbar)│
│ Protokoll         │                       [Modus: Bereit]   │  Nachrichten          │
│                   │  [Neu][Recherche][Dateien][Code]…       ├───────────────────────┤
│                   │  [Modell ▾] [ Auftrag …        ] 🎤 ➤   │ Kontext-Ring · Werte  │
└───────────────────┴─────────────────────────────────────────┴───────────────────────┘
```

Unter 1160 px Breite fällt die linke Spalte weg, unter 900 px stehen Kern und Gespräch untereinander. Gedächtnis, Abläufe und System ersetzen die Mitte, die Seitenspalten bleiben.

## Der Kern

Ein 2D-Canvas zeichnet 1000 Punkte auf einer Fibonacci-Kugel, kurze Verbindungslinien, drei geneigte Orbits mit wandernden Lichtpunkten und einen Messring mit 120 Teilstrichen. Tiefe entsteht über Perspektive, Größe und Deckkraft der Punkte. Der Zustand steuert Drehgeschwindigkeit, Farbe und Puls:

| Zustand | Wirkung |
|---|---|
| Bereit | langsame Drehung, ruhiges Atmen des Kerns |
| Arbeitet | schnellere Drehung, Wellenbewegung durch die Kugel |
| Antwortet | Wellenform durch die Mitte, ihre Höhe folgt dem eintreffenden Text |
| Hört zu | dauerhafte Wellenform |
| Freigabe | fast Stillstand, Farbe Bernstein |
| Störung | Farbe Rot für gut zwei Sekunden |

Farbwechsel werden über einige hundert Millisekunden überblendet. Ist die Seite verborgen oder eine andere Ansicht offen, hält die Animation an. Mit „Bewegung reduzieren“ im Betriebssystem zeichnet der Kern nur ein Standbild.

## Bewegung

- Knöpfe reagieren sofort mit `scale(.94–.97)` beim Drücken, Hover ändert nur Farbe und Rand.
- Neue Nachrichten und Protokollzeilen erscheinen in 240 ms von unten (`translateY(6px)` und Deckkraft, `cubic-bezier(.23,1,.32,1)`).
- Dialoge erscheinen in 220 ms aus 97 % Größe. Die Befehlspalette (Strg+K) erscheint ohne Animation, weil man sie oft und per Tastatur öffnet.
- Animiert werden nur `transform` und `opacity`. Die Breite der Gesprächsspalte springt, statt zu gleiten.

## Interaktion

- Enter sendet, Umschalt+Enter macht eine neue Zeile, Esc stoppt eine laufende Antwort und schließt Dialoge.
- Im Freigabedialog liegt der Fokus auf „Ablehnen“, Tab wechselt nur zwischen den beiden Knöpfen. Hohes Risiko (Programme, Maus und Tastatur) färbt Rand und Überschrift rot.
- Das Gespräch scrollt nur dann automatisch mit, wenn man schon unten ist.
- Eine unbeantwortete letzte Nachricht zeigt „Erneut senden“. Lag es an Ollama, gibt es zusätzlich „Ollama starten“.
- Alle Bedienelemente sind per Tastatur erreichbar und haben einen sichtbaren Fokusrahmen. Icons ohne Text tragen `aria-label`.

## Icon

`icon.svg` ist das Vorbild. `scripts/make_icon.py` rendert daraus ohne Fremdpakete `icon.png` (256 px) und `icon.ico` (16 bis 256 px) für Startmenü, Desktop und Fenster.
