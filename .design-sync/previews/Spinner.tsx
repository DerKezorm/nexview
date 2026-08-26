import { Spinner } from 'nexview-ui'

/**
 * ⚠️ Nur Maße verwenden, die es im Stylesheet auch gibt (`h-4`, `h-8`, `h-12`).
 * Vorschauen werden von Tailwind nicht durchsucht — eine erfundene Klasse wie
 * `h-10` entsteht nicht, und ohne Maß wächst das SVG auf die volle Breite.
 */
export const Groessen = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-6 text-mist-300" style={{ width: '26rem' }}>
    <span className="flex items-center gap-2 text-sm"><Spinner /> Wird geladen …</span>
    <Spinner className="h-8 w-8" />
    <Spinner className="h-12 w-12" />
  </div>
)
