import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../../api/client'
import type { TestResult } from '../../api/types'
import { Button, ErrorBanner, Field } from '../../components/ui'

type ServiceStepProps = {
  service: 'tmdb' | 'radarr' | 'sonarr'
  title: string
  description: ReactNode
  /** Radarr und Sonarr brauchen zusätzlich eine Adresse. */
  withUrl?: boolean
  urlPlaceholder?: string
  onDone: () => void
  onSkip: () => void
}

/**
 * Ein Dienst-Schritt des Einrichtungsassistenten.
 *
 * Gespeichert wird erst, wenn die Verbindung steht - so kann niemand mit
 * einem falschen Key weitergehen und sich später wundern.
 */
export function ServiceStep({
  service,
  title,
  description,
  withUrl = false,
  urlPlaceholder,
  onDone,
  onSkip,
}: ServiceStepProps) {
  const { t } = useTranslation()

  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [result, setResult] = useState<TestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const complete = apiKey.trim() !== '' && (!withUrl || url.trim() !== '')

  async function test() {
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.post<TestResult>(`/api/settings/test/${service}`, {
          api_key: apiKey.trim() || undefined,
          url: withUrl ? url.trim() || undefined : undefined,
        }),
      )
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t('errors.generic'))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const payload: Record<string, string> = { [`${service}_api_key`]: apiKey.trim() }
      if (withUrl) payload[`${service}_url`] = url.trim()
      await api.put('/api/settings', payload)
      onDone()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t('errors.generic'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-xl font-bold tracking-tight">{title}</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-mist-500">{description}</p>
      </div>

      {withUrl && (
        <Field
          label={t('settings.url')}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder={urlPlaceholder}
          autoComplete="off"
        />
      )}

      <Field
        label={t('settings.apiKey')}
        type="password"
        value={apiKey}
        onChange={(event) => setApiKey(event.target.value)}
        autoComplete="off"
      />

      {error && <ErrorBanner message={error} />}
      {result && (
        <p
          className={
            'rounded-xl border px-4 py-3 text-sm ' +
            (result.ok
              ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
              : 'border-bad-500/40 bg-bad-500/10 text-bad-500')
          }
          role="status"
        >
          {result.message}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="ghost" onClick={() => void test()} disabled={!complete}>
          {t('settings.test')}
        </Button>
        <Button type="button" onClick={() => void save()} loading={busy} disabled={!complete}>
          {t('setup.saveAndContinue')}
        </Button>
        <button
          type="button"
          onClick={onSkip}
          className="text-sm text-mist-500 underline-offset-4 transition-colors hover:text-mist-100 hover:underline"
        >
          {t('setup.later')}
        </button>
      </div>
    </div>
  )
}
