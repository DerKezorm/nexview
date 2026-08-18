type LogoProps = {
  className?: string
  withWordmark?: boolean
}

/** Nexview-Zeichen: Kamerablende mit Play-Dreieck im Rotverlauf. */
export function Logo({ className = 'h-8 w-8', withWordmark = false }: LogoProps) {
  const mark = (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="nexview-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ff3b4e" />
          <stop offset="55%" stopColor="#e11d2f" />
          <stop offset="100%" stopColor="#8f0f1c" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="16" fill="#141019" />
      <rect
        x="2"
        y="2"
        width="60"
        height="60"
        rx="16"
        fill="none"
        stroke="url(#nexview-mark)"
        strokeWidth="2.5"
        strokeOpacity=".55"
      />
      <path
        d="M12 32c6.5-9 13.5-13.5 20-13.5S45.5 23 52 32c-6.5 9-13.5 13.5-20 13.5S18.5 41 12 32Z"
        fill="none"
        stroke="url(#nexview-mark)"
        strokeWidth="3.2"
        strokeLinejoin="round"
      />
      <path d="M28 25.5 40 32l-12 6.5Z" fill="url(#nexview-mark)" />
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
