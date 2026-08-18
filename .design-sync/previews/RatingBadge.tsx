import { RatingBadge } from 'nexview-ui'

/** Die Farbe folgt der Wertung: gut, mittel, schwach. */
export const NachWertung = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-3">
    <RatingBadge vote={8.4} count={12045} />
    <RatingBadge vote={6.1} count={880} />
    <RatingBadge vote={3.2} count={140} />
    <RatingBadge vote={0} count={0} />
  </div>
)
