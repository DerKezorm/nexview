import { StarRating } from 'nexview-ui'

export const Bewertungen = () => (
  <div className="bg-ink-950 p-6 flex flex-col gap-3">
    <StarRating value={5} />
    <StarRating value={3} />
    <StarRating value={1} />
  </div>
)

export const Klein = () => (
  <div className="bg-ink-950 p-6">
    <StarRating value={4} size="sm" />
  </div>
)
