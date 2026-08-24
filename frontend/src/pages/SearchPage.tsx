import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DemoBanner } from '../components/DemoBanner'
import { SuchErgebnis } from '../components/media/SuchErgebnis'
import { SearchInput } from '../components/SearchInput'
import { useConfig } from '../hooks/useConfig'

/**
 * Gezielte Suche nach einem Titel - über Filme und Serien hinweg.
 *
 * Was hier später noch dazukommt (z. B. Suche direkt in Radarr/Sonarr),
 * ist noch offen.
 */
export function SearchPage() {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const [search, setSearch] = useState('')

  const query = search.trim()
  const searching = query.length >= 2

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {t('nav.search')}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-1.5 text-mist-500">{t('discover.searchIntro')}</p>
        </div>
        <div className="max-w-xl">
          <SearchInput value={search} onChange={setSearch} />
        </div>
      </header>

      <DemoBanner />

      {!searching ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {t('discover.searchPrompt')}
        </p>
      ) : (
        <>
          <SuchErgebnis
            mediaType="movie"
            titleKey="common.movies"
            suche={query}
            arrConfigured={config?.radarr_configured ?? false}
          />
          <SuchErgebnis
            mediaType="tv"
            titleKey="common.seriesPlural"
            suche={query}
            arrConfigured={config?.sonarr_configured ?? false}
          />
        </>
      )}
    </div>
  )
}
