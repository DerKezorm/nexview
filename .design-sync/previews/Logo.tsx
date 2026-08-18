import { Logo } from 'nexview-ui'

export const Groessen = () => (
  <div className="bg-ink-950 p-8 flex items-center gap-6">
    <Logo className="h-8 w-8" />
    <Logo className="h-12 w-12" />
    <span className="flex items-center gap-2">
      <Logo className="h-9 w-9" />
      <span className="text-xl font-bold tracking-tight">
        NEX<span className="text-accent-500">VIEW</span>
      </span>
    </span>
  </div>
)
