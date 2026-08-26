# Die Wahrheit steht in der Anwendung

Diese Dateien **erfinden keine Werte**. Die Farbrampen von Nexview stehen in
`frontend/src/styles/index.css` unter `@theme` und landen über den Bau in
`_ds_bundle.css` — als `--color-ink-950`, `--color-mist-100`, `--color-accent-500`
und so weiter, hell wie dunkel.

Was hier liegt, ist die Schicht darüber:

| Datei | Inhalt |
|---|---|
| `colors.css` | **Rollen** statt Rampenstufen: `--surface-card` statt `--color-ink-850`. Zeigt zugleich, welche Stufe wofür gedacht ist. |
| `typography.css` | Schriftrollen, Zeilenhöhen, Laufweiten — in der Anwendung stecken sie in Tailwind-Klassen, hier stehen sie benennbar. |
| `spacing.css` | Das 4-px-Raster und die Seitenmaße. |
| `radius.css` | Radien und Rahmenbreiten. |
| `elevation.css` | Schatten, der Rotschimmer, der Fokusrahmen. |
| `motion.css` | Dauern, Easing und die Bewegungen mit Namen. |
| `base.css` | Minimaler Reset. **Wird von `styles.css` bewusst nicht eingebunden** — die Anwendung bringt ihren eigenen mit (Tailwind Preflight). Gedacht für rahmenwerkfreie Seiten wie die Projektseite. |

## Warum es diese Schicht überhaupt gibt

Zwei Dinge tragen dieselbe Gestaltung, aber nicht denselben Werkzeugkasten:
die **Anwendung** (React + Tailwind 4) und die **Projektseite** unter
`nexview.nexapps.dev` (reines CSS, kein Rahmenwerk). Die Seite kann keine
Tailwind-Klasse benutzen; sie braucht die Werte als Eigenschaften. Ohne eine
gemeinsame Schicht laufen beide auseinander — und genau das ist passiert: Die
Projektseite kennt bis heute keinen hellen Modus, die Anwendung seit 0.5.0
schon.

## Regel

Ändert sich eine **Farbe**, ändert sie sich in `frontend/src/styles/index.css`
und nirgends sonst. Ändert sich eine **Rolle** — welche Stufe wofür steht —,
ändert sie sich hier.
