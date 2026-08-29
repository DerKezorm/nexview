import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { TestResult } from '../../api/types'
import { BASIS, adresseOhneBasis } from '../../lib/basis'
import { Button, ErrorBanner, Field } from '../../components/ui'

/**
 * Schritt „Adresse" im Einrichtungsassistenten.
 *
 * Unter welcher Adresse ist Nexview von außen erreichbar? Der Server kann das
 * nicht selbst wissen, wenn er hinter einem Reverse Proxy steht - und ohne die
 * Angabe enthält jeder verschickte Link nur den hinteren Teil.
 */
export function AddressStep({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()

  // Mitsamt Unterpfad, falls einer gesetzt ist - window.location.origin allein
  // würde ihn verschlucken, und dann führte jeder verschickte Link ins Leere.
  const [url, setUrl] = useState(window.location.origin + BASIS)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  /** Erst ein bestandener Test schaltet Weiter frei. */
  const geprueft = testResult?.ok === true

  const testMutation = useMutation({
    mutationFn: () =>
      api.post<TestResult>('/api/settings/test/public-url', { url: url.trim() }),
    onMutate: () => setTestResult(null),
    onSuccess: setTestResult,
    onError: (caught) =>
      setTestResult({
        ok: false,
        message: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  const saveMutation = useMutation({
    mutationFn: () => api.put('/api/settings', { public_url: url.trim() }),
    onSuccess: onDone,
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.network')),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    setError(null)
    saveMutation.mutate()
  }

  return (
    <>
      <h2 className="text-xl font-bold tracking-tight">{t('setup.addressTitle')}</h2>
      <p className="mt-1.5 text-sm leading-relaxed text-mist-500">{t('setup.addressText')}</p>

      <form onSubmit={absenden} className="mt-5 flex flex-col gap-4">
        <Field
          label={t('mail.publicUrl')}
          hint={t('setup.publicUrlHint')}
          value={url}
          onChange={(event) => {
            setUrl(event.target.value)
            setTestResult(null)
          }}
          placeholder="https://nexview.beispiel.de"
          autoComplete="off"
        />

        {adresseOhneBasis(url) && (
          <p className="text-sm text-bad-500">
            {t('mail.publicUrlBaseWarning', { base: BASIS })}
          </p>
        )}

        {error && <ErrorBanner message={error} />}

        {/* Weiter erst nach erfolgreichem Test: eine Bestaetigungsmail mit
            kaputtem Link ist so wertlos wie gar keine - und ohne bestaetigte
            Adresse kommt danach niemand mehr hinein. */}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!url.trim()}
          >
            {t('mail.testConnection')}
          </Button>
          <Button type="submit" loading={saveMutation.isPending} disabled={!geprueft}>
            {t('setup.saveAndContinue')}
          </Button>
        </div>

        {testResult && (
          <p className={'text-sm ' + (testResult.ok ? 'text-ok-500' : 'text-bad-500')}>
            {testResult.message}
          </p>
        )}

        <p className="text-xs text-mist-600">{t('setup.addressSkipHint')}</p>
      </form>
    </>
  )
}
