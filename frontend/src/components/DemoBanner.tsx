import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import { useConfig } from '../hooks/useConfig'

/** Hinweis, solange Nexview mit Beispieldaten statt echtem TMDB-Zugang läuft. */
export function DemoBanner() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: config } = useConfig()

  if (!config?.using_demo_data) return null

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
      <span>{t('discover.demoBanner')}</span>
      {user?.role === 'admin' && (
        <Link
          to="/admin/settings"
          className="ml-auto rounded-full border border-warn-500/50 px-3 py-1 text-xs font-semibold transition-colors hover:bg-warn-500/15"
        >
          {t('discover.demoBannerAdmin')}
        </Link>
      )}
    </div>
  )
}
