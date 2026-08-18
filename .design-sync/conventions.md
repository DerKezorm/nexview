## Nexview UI — wie man damit baut

Ein **durchgehend dunkles** System. Es gibt keinen hellen Modus: Flächen sind
dunkel, Text hell, und ein einziges Rot setzt die Akzente.

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
graue Standardtöne wie `bg-gray-900` fallen sofort aus dem Bild:

| Rolle | Klassen |
|---|---|
| Flächen, dunkel nach hell | `bg-ink-950` (Seite) · `bg-ink-900` · `bg-ink-850` (Karten) · `bg-ink-800` (Hover) |
| Rahmen | `border-ink-700` · `border-ink-600` |
| Text, hell nach gedimmt | `text-mist-100` · `text-mist-300` · `text-mist-500` · `text-mist-600` |
| Akzent | `bg-accent-500` / `text-accent-400` / `border-accent-600` · dunkler: `accent-700` |
| Signalfarben | `text-ok-500` (gut) · `text-warn-500` (Achtung) · `text-bad-500` (Fehler) |

Formen: Karten und Felder `rounded-xl` bis `rounded-2xl`, Abzeichen und
Bedienelemente `rounded-full`. Abstände in Vielfachen von `gap-2`/`gap-3`.
Schrift: `--font-sans` (Inter, sonst Systemschrift) — **es wird keine Schrift
mitgeliefert**, das ist Absicht.

### Woran man sich hält

- `styles.css` und die davon eingebundenen Dateien sind die Wahrheit über
  Farben und Grundstile — dort nachsehen, bevor man eigene Werte erfindet.
- Jede Komponente hat eine `.d.ts` mit ihren Eigenschaften und eine
  `.prompt.md` mit Verwendungsbeispielen.
- **Aufbauen statt nachbauen**: Wo eine Komponente existiert (`Button`, `Card`,
  `Field`, `StatusBadge`), diese verwenden. Eigenes Layout darum herum mit den
  Klassen oben.

### Was hier fehlt

Kacheln, Benutzermenü, Glocke und Detailfenster gehören zur Anwendung, nicht
zur Bibliothek: Sie laden ihre Daten selbst und wären hier funktionslos.
`StatusBadge`, `Poster` und `RatingBadge` sind die Bausteine, aus denen sich
eine Filmkachel zusammensetzen lässt.
