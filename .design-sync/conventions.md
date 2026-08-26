## Nexview UI — wie man damit baut

Ein **dunkles System mit hellem Zwilling**. Standard ist dunkel; `data-theme="light"`
am `<html>` tauscht die Werte hinter denselben Namen aus. Kein Baustein wird dafür
angefasst — die Oberfläche benutzt durchgehend nur `ink` (Flächen), `mist` (Text)
und `accent` (das Rot), und im hellen Modus wechseln nur die Farbwerte die Rollen:
aus dem dunkelsten Seitengrund wird der hellste, aus dem hellsten Text der dunkelste.

### Alles in `NexviewProvider` einschließen

```jsx
import { NexviewProvider, Button, StatusBadge } from 'nexview-ui'

<NexviewProvider language="de">
  <div className="min-h-screen bg-ink-950 p-8 text-mist-100">
    <StatusBadge status="downloaded" />
    <Button>Anfragen</Button>
  </div>
</NexviewProvider>
```

Ohne diesen Rahmen erscheinen statt der Beschriftungen die Übersetzungs-
schlüssel (`status.downloaded`), `Poster` und `CastStrip` werfen beim Rendern
(sie sind Verweise), und `LoadingBar` bricht ab. `language` ist `"de"` oder
`"en"` — mehr gibt es nicht.

### Die Klassenwelt

Tailwind mit einer eigenen, kleinen Farbpalette. **Nur diese Namen verwenden** —
graue Standardtöne wie `bg-gray-900` fallen sofort aus dem Bild und kennen den
hellen Modus nicht:

| Rolle | Klassen |
|---|---|
| Flächen, dunkel nach hell | `bg-ink-950` (Seite) · `bg-ink-900` · `bg-ink-850` (Karten) · `bg-ink-800` (Hover) |
| Rahmen | `border-ink-700` · `border-ink-600` |
| Text, hell nach gedimmt | `text-mist-100` · `text-mist-300` · `text-mist-500` · `text-mist-600` |
| Akzent | `bg-accent-500` / `text-accent-400` / `border-accent-600` · dunkler: `accent-700` |
| Signalfarben | `text-ok-500` (gut) · `text-warn-500` (Achtung) · `text-bad-500` (Fehler) |
| Diagramme | `--color-viz-1` · `--color-viz-2` · `--color-viz-3` — **nur dort** |
| Schleier | `bg-scrim` — hinter Dialogen und auf Postern, in **jedem** Modus dunkel |

Formen: Karten und Felder `rounded-xl` bis `rounded-2xl`, Abzeichen und
Bedienelemente `rounded-full`. Abstände in Vielfachen von `gap-2`/`gap-3`.
Schrift: `--font-sans` (Inter, sonst Systemschrift) — **es wird keine Schrift
mitgeliefert**, das ist Absicht: kein fremder Server, kein Ladeverzug, kein
Textsprung.

### Drei Regeln, die man leicht verletzt

1. **Genau drei Diagrammfarben.** Bei vier fällt jede Kombination durch die
   Prüfung auf Farbfehlsichtigkeit, sobald *alle* Paare verglichen werden und
   nicht nur benachbarte. Alles darüber gehört in „Andere".
2. **Abzeichen auf Bildern sitzen auf einer deckenden dunklen Platte**
   (`bg-ink-950/85`), nie auf einer eingefärbten Fläche mit wenig Deckkraft —
   auf einem hellen Poster bliebe davon ein blasser Fleck.
3. **Ein Fenster hat genau einen sichtbaren Ausgang.** Wer unten „Abbrechen"
   anbietet, bekommt oben keinen Schließen-Knopf mehr. Escape und ein Klick
   daneben schließen ohnehin.

### Woran man sich hält

- `tokens/` und das davon eingebundene `styles.css` sind die Wahrheit über
  Farben, Schrift, Abstände und Bewegung — dort nachsehen, bevor man eigene
  Werte erfindet.
- Jede Komponente hat eine `.d.ts` mit ihren Eigenschaften und eine
  `.prompt.md` mit Verwendungsbeispielen.
- **Aufbauen statt nachbauen**: Wo eine Komponente existiert (`Button`, `Card`,
  `Field`, `Section`, `StatusBadge`, `Reiterreihe`, `Umschalter`), diese
  verwenden. Eigenes Layout darum herum mit den Klassen oben.

### Was hier fehlt

Kacheln, Benutzermenü, Glocke und Detailfenster gehören zur Anwendung, nicht
zur Bibliothek: Sie laden ihre Daten selbst und wären hier funktionslos.
`StatusBadge`, `UhdBadge`, `WatchedBadge`, `Poster` und `RatingBadge` sind die
Bausteine, aus denen sich eine Filmkachel zusammensetzen lässt.
