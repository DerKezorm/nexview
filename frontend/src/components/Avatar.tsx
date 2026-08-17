type AvatarProps = {
  url?: string | null
  name: string
  className?: string
}

/** Farbe aus dem Namen ableiten - so hat jeder dauerhaft dieselbe. */
const TONES = [
  'bg-accent-700 text-white',
  'bg-ink-700 text-mist-100',
  'bg-accent-600 text-white',
  'bg-ok-500/30 text-ok-500',
  'bg-warn-500/25 text-warn-500',
] as const

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/** Profilbild – oder die Initialen, solange keines hochgeladen wurde. */
export function Avatar({ url, name, className = 'h-8 w-8' }: AvatarProps) {
  if (url) {
    return (
      <img
        src={url}
        alt=""
        className={`${className} shrink-0 rounded-full border border-ink-700 object-cover`}
      />
    )
  }

  const tone = TONES[[...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % TONES.length]
  return (
    <span
      aria-hidden="true"
      className={`${className} flex shrink-0 items-center justify-center rounded-full border border-ink-700 text-xs font-bold ${tone}`}
    >
      {initials(name)}
    </span>
  )
}
