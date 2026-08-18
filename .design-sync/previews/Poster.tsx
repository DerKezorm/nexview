import { Poster } from 'nexview-ui'

/** Ohne Bild bleibt eine ruhige Fläche mit dem Titel - nie ein kaputtes Bild. */
export const OhneBild = () => (
  <div className="bg-ink-950 p-6 flex gap-4">
    <div className="aspect-2/3 w-32 overflow-hidden rounded-xl border border-ink-700">
      <Poster url={null} title="Tage des Donners" />
    </div>
    <div className="aspect-2/3 w-32 overflow-hidden rounded-xl border border-ink-700">
      <Poster url={null} title="Alles steht Kopf 2" />
    </div>
  </div>
)
