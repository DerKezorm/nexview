import { useTranslation } from 'react-i18next'

import type { MediaType } from '../api/types'
import { DemoBanner } from '../components/DemoBanner'
import { MediaSection } from '../components/media/MediaSection'
import { useConfig } from '../hooks/useConfig'

type DiscoverPageProps = {
  mediaType: MediaType
}

/**
 * Entdecken-Seite für genau einen Medientyp.
 *
 * Filme und Serien haben bewusst eigene Seiten: eigene Filter, eigene Daten
 * und später eigene Logik (Radarr bzw. Sonarr).
 */
export function DiscoverPage({ mediaType }: DiscoverPageProps) {
  const { t } = useTranslation()
  const { data: config } = useConfig()

  const titleKey = mediaType === 'movie' ? 'nav.discoverMovies' : 'nav.discoverSeries'
  const introKey = mediaType === 'movie' ? 'discover.introMovies' : 'discover.introSeries'

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t(titleKey)}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t(introKey)}</p>
      </header>

      <DemoBanner />

      <MediaSection
        mediaType={mediaType}
        titleKey={mediaType === 'movie' ? 'common.movies' : 'common.series'}
        searchQuery=""
        defaultRegion={config?.default_region ?? 'DE'}
        arrConfigured={
          mediaType === 'movie'
            ? (config?.radarr_configured ?? false)
            : (config?.sonarr_configured ?? false)
        }
      />
    </div>
  )
}
