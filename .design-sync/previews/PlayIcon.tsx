import { PlayIcon } from 'nexview-ui'

export const AufKnopf = () => (
  <div className="bg-ink-950 p-8 flex items-center gap-4 text-mist-100">
    <span className="flex items-center gap-2 rounded-full border border-ink-700 px-4 py-2 text-sm">
      <PlayIcon />
      Trailer
    </span>
    <span className="text-accent-500"><PlayIcon /></span>
  </div>
)
