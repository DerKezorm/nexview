import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { AboutInfo, AppSettings } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Logo } from '../components/Logo'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDateTime } from '../lib/format'

/** Ein Verweis nach außen - immer in einem neuen Reiter. */
function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-accent-400 underline decoration-accent-400/40 underline-offset-4 transition-colors hover:text-accent-300 hover:decoration-accent-300"
    >
      {children}
    </a>
  )
}

/** Eine Zeile der Angaben-Tabelle. */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-ink-700/60 py-3 last:border-b-0">
      <dt className="text-sm text-mist-500">{label}</dt>
      <dd className="text-sm font-medium text-mist-100">{children}</dd>
    </div>
  )
}

/** Hinweis auf eine neuere Version - nur für Administratoren sichtbar. */
function UpdateNotice({ info }: { info: AboutInfo }) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()

  const check = useMutation({
    mutationFn: () => api.post<AboutInfo>('/api/about/check'),
    onSuccess: (frisch) => queryClient.setQueryData(['about'], frisch),
  })

  if (!info.update_checked) return null

  return (
    <Card className="mt-4">
      {info.update_available ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="rounded-full bg-accent-500/15 px-3 py-1 text-sm font-semibold text-accent-400">
            {t('about.updateAvailable', { version: info.latest_version })}
          </span>
          <ExternalLink href={info.release_url}>{t('about.showRelease')}</ExternalLink>
        </div>
      ) : (
        <p className="text-sm text-mist-500">{t('about.upToDate')}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <Button
          variant="ghost"
          onClick={() => check.mutate()}
          loading={check.isPending}
          className="!px-3 !py-1.5"
        >
          {t('about.checkNow')}
        </Button>
        {info.checked_at && (
          <span className="text-xs text-mist-600">
            {t('about.lastChecked', { when: formatDateTime(info.checked_at, i18n.language) })}
          </span>
        )}
      </div>

      {info.update_available && (
        <p className="mt-3 text-xs leading-relaxed text-mist-600">{t('about.updateHint')}</p>
      )}
    </Card>
  )
}

/**
 * Schalter für die tägliche Nachfrage bei GitHub - nur für Administratoren.
 *
 * Steht bewusst hier und nicht in den Einstellungen: wer wissen will, was da
 * nach außen geht, schaut auf die Seite, die das Ergebnis anzeigt.
 */
function UpdateCheckToggle() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })

  const speichern = useMutation({
    mutationFn: (an: boolean) => api.put<AppSettings>('/api/settings', { update_check: an }),
    onSuccess: (daten) => {
      queryClient.setQueryData(['settings'], daten)
      void queryClient.invalidateQueries({ queryKey: ['about'] })
    },
  })

  if (!settings.data) return null

  return (
    <Card className="mt-4">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={settings.data.update_check}
          onChange={(event) => speichern.mutate(event.target.checked)}
          disabled={speichern.isPending}
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
        />
        <span>
          <span className="text-sm font-medium text-mist-100">
            {t('settings.updateCheckLabel')}
          </span>
          <span className="mt-1 block text-xs leading-relaxed text-mist-600">
            {t('settings.updateCheckHint')}
          </span>
        </span>
      </label>
    </Card>
  )
}

/**
 * Über Nexview: Version, Herkunft, Update-Stand.
 *
 * Bewusst schlicht gehalten und über die Fußzeile erreichbar - sie ist die
 * Stelle, an der man so etwas sucht.
 */
export function AboutPage() {
  const { t } = useTranslation()
  const { user } = useAuth()

  const query = useQuery({
    queryKey: ['about'],
    queryFn: () => api.get<AboutInfo>('/api/about'),
    // Die Antwort ändert sich höchstens einmal am Tag; ein Nachladen bei
    // jedem Fensterwechsel wäre reine Verschwendung.
    staleTime: 60 * 60 * 1000,
  })

  if (query.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  }

  if (query.error) return <ErrorBanner message={(query.error as Error).message} />

  const info = query.data

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex flex-col items-center gap-3 py-6 text-center">
        <Logo className="h-14 w-14" />
        <h1 className="text-2xl font-bold text-mist-100">Nexview</h1>
        <p className="max-w-md text-sm leading-relaxed text-mist-500">{t('about.tagline')}</p>
      </div>

      <Card>
        <dl>
          <Row label={t('about.version')}>
            <span className="tabular-nums">{info.version}</span>
          </Row>
          <Row label={t('about.source')}>
            <ExternalLink href={info.repo_url}>{t('about.onGithub')}</ExternalLink>
          </Row>
          <Row label={t('about.releases')}>
            <ExternalLink href={info.release_url}>{t('about.allVersions')}</ExternalLink>
          </Row>
          <Row label={t('about.license')}>{info.license}</Row>
        </dl>
      </Card>

      <UpdateNotice info={info} />

      {user?.role === 'admin' && <UpdateCheckToggle />}
    </div>
  )
}
