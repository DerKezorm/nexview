import { LoadingBar } from 'nexview-ui'

export const Standard = () => (
  <div className="bg-ink-950 p-6">
    <p className="mb-3 text-sm text-mist-500">Läuft, solange Daten nachgeladen werden:</p>
    <LoadingBar />
  </div>
)
