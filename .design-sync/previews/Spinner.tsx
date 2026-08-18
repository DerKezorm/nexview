import { Spinner } from 'nexview-ui'

export const Groessen = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-6 text-mist-300">
    <span className="flex items-center gap-2 text-sm"><Spinner /> Wird geladen …</span>
    <Spinner className="h-6 w-6" />
    <Spinner className="h-10 w-10" />
  </div>
)
