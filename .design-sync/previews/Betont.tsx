import { Betont } from 'nexview-ui'

/**
 * Genau zwei Auszeichnungen - `**fett**` und `` `code` `` - und bewusst kein
 * Markdown-Paket: aus einem Text kann hier nie Markup werden.
 */
export const InEinemAbsatz = () => (
  <div className="bg-ink-950 p-6 text-mist-300" style={{ width: '32rem' }}>
    <p>
      <Betont text="Nexview zeigt dir jetzt, **was ungenutzt herumliegt**. Der Wert steht in `NEXVIEW_UNUSED_DAYS` und gilt ab dem nächsten Start." />
    </p>
  </div>
)

/** Alles Unpaarige bleibt stehen: ein Sternchen in „3 * 4" ist ein Sternchen. */
export const UnpaarigBleibtStehen = () => (
  <div className="bg-ink-950 p-6 text-mist-300" style={{ width: '32rem' }}>
    <p><Betont text="3 * 4 ist 12, und **das** bleibt so." /></p>
  </div>
)
