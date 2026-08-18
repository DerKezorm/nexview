import { Button } from 'nexview-ui'

/** Alle Vorschauen stehen auf ink-950 - der Seitenfarbe von Nexview. */
const buehne = 'bg-ink-950 p-6 flex flex-wrap items-center gap-3'

export const Varianten = () => (
  <div className={buehne}>
    <Button>Anfragen</Button>
    <Button variant="ghost">Abbrechen</Button>
  </div>
)

export const Zustaende = () => (
  <div className={buehne}>
    <Button loading>Wird gesendet</Button>
    <Button disabled>Nicht verfügbar</Button>
    <Button variant="ghost" disabled>Gesperrt</Button>
  </div>
)
