import { useNavigate, useParams } from 'react-router-dom'

import { KidsTitel } from './KidsKatalog'

/**
 * Die Titelseite als eigene Route.
 *
 * Gebraucht wird sie für den Weg aus der Suche: Von dort führt kein
 * gemeinsamer Zustand zum Katalog. Der Inhalt ist derselbe `KidsTitel`, den
 * auch der Katalog und die Eltern-Vorschau zeigen – zwei Fassungen wären zwei
 * Wahrheiten.
 */
export function KidsTitlePage() {
  const navigate = useNavigate()
  const { mediaType, tmdbId } = useParams()

  if (mediaType !== 'movie' && mediaType !== 'tv') return null

  return (
    <KidsTitel
      quelle="/api/kids"
      vorschau={false}
      mediaType={mediaType}
      tmdbId={Number(tmdbId)}
      onZurueck={() => navigate(-1)}
    />
  )
}
