import { Symbol, type SymbolName } from 'nexview-ui'

/**
 * Der ganze Satz. Alle auf demselben Raster: 24x24, Strich statt Flaeche,
 * `currentColor` - deshalb nimmt jedes Symbol die Farbe seines Knopfes an.
 *
 * ⚠️ Maße hier bewusst als `style`: Vorschauen werden nicht von Tailwind
 * durchsucht, eine Klasse wie `grid-cols-5` gäbe es im Stylesheet also nicht.
 */
const ALLE: SymbolName[] = [
  'system', 'dienste', 'glocke', 'benutzer', 'merkliste',
  'kontingent', 'sperre', 'adresse', 'mail', 'protokoll',
  'sicherung', 'allgemein', 'film', 'fernseher', 'radarr',
  'sonarr', 'serie', 'medienserver', 'herunterladen', 'loeschen',
]

export const AlleSymbole = () => (
  <div
    className="bg-ink-950 p-6"
    style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '1.25rem', width: '30rem' }}
  >
    {ALLE.map((name) => (
      <div key={name} className="flex flex-col items-center gap-2 text-mist-500">
        <Symbol name={name} className="" />
        <span style={{ fontSize: '0.6875rem' }}>{name}</span>
      </div>
    ))}
  </div>
)

/** Die Farbe kommt immer von aussen - das Symbol hat keine eigene. */
export const Faerbung = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-4" style={{ width: '22rem' }}>
    <Symbol name="film" className="h-5 w-5 text-mist-600" />
    <Symbol name="film" className="h-5 w-5 text-mist-300" />
    <Symbol name="film" className="h-5 w-5 text-mist-100" />
    <Symbol name="film" className="h-5 w-5 text-accent-400" />
    <Symbol name="film" className="h-5 w-5 text-ok-500" />
    <Symbol name="film" className="h-5 w-5 text-bad-500" />
  </div>
)
