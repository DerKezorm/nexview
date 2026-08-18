import { useState } from 'react'

/**
 * Personenfoto mit Ersatzdarstellung.
 *
 * TMDB kennt zu manchen Personen kein Foto (dann ist die Adresse leer), und
 * ganz selten führt eine Adresse ins Leere. Beides landet auf demselben
 * Ersatz: der erste Buchstabe des Namens - statt des hässlichen
 * Browser-Symbols für ein kaputtes Bild.
 */
export function PersonPhoto({
  url,
  name,
  className = '',
}: {
  url: string | null
  name: string
  className?: string
}) {
  const [fehlgeschlagen, setFehlgeschlagen] = useState(false)

  if (!url || fehlgeschlagen) {
    return (
      <span className="flex h-full w-full items-center justify-center bg-linear-to-br from-ink-800 to-ink-900 text-xl font-semibold text-mist-600">
        {name.slice(0, 1).toUpperCase()}
      </span>
    )
  }

  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      onError={() => setFehlgeschlagen(true)}
      className={'h-full w-full object-cover ' + className}
    />
  )
}
