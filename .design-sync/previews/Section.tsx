import { Section, Field, Button } from 'nexview-ui'

/**
 * Ein abgegrenzter Einstellungsbereich - Ueberschrift plus Inhalt in einer
 * Karte. Damit sieht jede Einstellungsseite gleich aus; vorher baute sich
 * jede ihre Bereiche selbst.
 */
export const MitFeldern = () => (
  <div className="bg-ink-950 p-6 max-w-2xl">
    <Section title="Radarr">
      <Field label="Adresse" value="http://radarr:7878" onChange={() => {}} />
      <Field label="Schlüssel" value="••••••••••••" onChange={() => {}} hint="Steht in Radarr unter Einstellungen › Allgemein." />
      <div><Button>Verbindung prüfen</Button></div>
    </Section>
  </div>
)

/** `breit` fuer Inhalte, die die volle Breite brauchen - Tabellen etwa. */
export const Breit = () => (
  <div className="bg-ink-950 p-6 max-w-2xl">
    <Section title="Belegung" breit>
      <p className="text-sm text-mist-500">Ohne <code className="text-accent-400">breit</code> bliebe der Inhalt bei max-w-3xl.</p>
    </Section>
  </div>
)
