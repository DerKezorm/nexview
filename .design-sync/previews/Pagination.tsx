import { Pagination } from 'nexview-ui'

/** Zwanzig pro Seite - die Groesse, bei der man noch scrollt statt zu suchen. */
export const Mittendrin = () => (
  <div className="bg-ink-950 p-6">
    <Pagination seite={3} seiten={7} onSeite={() => {}} />
  </div>
)

export const ErsteSeite = () => (
  <div className="bg-ink-950 p-6">
    <Pagination seite={1} seiten={4} onSeite={() => {}} />
  </div>
)
