type LogoProps = {
  className?: string
  withWordmark?: boolean
}

/*
 * Das Zeichen: ein Popcorn-Becher, der Heimkino-Abend. Dieselbe Form steht in
 * public/logo.svg (Favicon) und in tools/symbole.mjs (Bilder fuer den
 * Home-Bildschirm). ⚠️ Wer sie hier aendert, aendert sie dort mit.
 *
 * Die Farben kommen aus styles/index.css (``--nv-logo-…``) und folgen damit
 * dem Modus der App: weisse Kachel im hellen, fast schwarze im dunklen. Ein
 * Element fuer beide, damit das Zeichen beim Umschalten nicht flackert.
 */
const KOERNER: [number, number, number][] = [
  [20.5, 27, 5],
  [27.5, 21.5, 5.5],
  [36.5, 21.5, 5.5],
  [43.5, 27, 5],
  [32, 26.5, 5.5],
]

const GRUND = { fill: 'var(--nv-logo-grund)' }
const VERLAUF = { stroke: 'url(#nexview-mark)' }

/** Nexview-Zeichen: Popcorn-Becher im Rotverlauf. */
export function Logo({ className = 'h-8 w-8', withWordmark = false }: LogoProps) {
  const mark = (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient
          id="nexview-mark"
          gradientUnits="userSpaceOnUse"
          x1="10"
          y1="10"
          x2="54"
          y2="54"
        >
          <stop offset="0" style={{ stopColor: 'var(--nv-logo-verlauf-1)' }} />
          <stop offset=".5" style={{ stopColor: 'var(--nv-logo-verlauf-2)' }} />
          <stop offset="1" style={{ stopColor: 'var(--nv-logo-verlauf-3)' }} />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" style={GRUND} />
      <rect
        x="2.75"
        y="2.75"
        width="58.5"
        height="58.5"
        rx="15.3"
        fill="none"
        strokeWidth="1.5"
        style={{ stroke: 'var(--nv-logo-rand)', strokeOpacity: 'var(--nv-logo-rand-deckung)' }}
      />
      {KOERNER.map(([cx, cy, r]) => (
        <circle
          key={`${cx}-${cy}`}
          cx={cx}
          cy={cy}
          r={r}
          strokeWidth="3.4"
          style={{ ...GRUND, ...VERLAUF }}
        />
      ))}
      <path
        d="M17.5 31H46.5L42.8 52H21.2Z"
        strokeWidth="4.2"
        strokeLinejoin="round"
        style={{ ...GRUND, ...VERLAUF }}
      />
      <path
        d="M27 31.5 27.8 51.5M37 31.5 36.2 51.5"
        strokeWidth="3.6"
        style={{ stroke: 'var(--nv-logo-streifen)' }}
      />
      <path d="M15.5 31H48.5" strokeWidth="4.2" strokeLinecap="round" style={VERLAUF} />
    </svg>
  )

  if (!withWordmark) return mark

  return (
    <span className="flex items-center gap-2.5">
      {mark}
      {/* Auf dem Telefon nur das Zeichen - der Schriftzug wuerde die
          Kopfzeile mit Glocke, beiden Schaltern und Menue ueberfuellen. */}
      <span className="hidden text-lg font-bold tracking-tight sm:inline">
        NEX<span className="text-accent-500">VIEW</span>
      </span>
    </span>
  )
}
