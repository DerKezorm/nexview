import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings, TestResult } from '../../api/types'
import { Button, Card, Field, Spinner } from '../../components/ui'

/**
 * Unter welcher Adresse ist Nexview von außen erreichbar?
 *
 * Bewusst ein eigener Bereich: die Adresse steckt zwar in jeder verschickten
 * Mail, hat aber mit dem Mailserver selbst nichts zu tun. Sie gilt genauso für
 * alles andere, was jemals nach außen verweist.
 */
export function AdminAddressSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [url, setUrl] = useState('')
  const [basisUrl, setBasisUrl] = useState('')
  const [result, setResult] = useState<TestResult | null>(null)
  const [basisResult, setBasisResult] = useState<TestResult | null>(null)

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })

  // Nur einmal vorbelegen: bei jedem Hintergrund-Abgleich würde sonst
  // überschrieben, was gerade getippt wird.
  const vorbelegt = useRef(false)
  useEffect(() => {
    if (!settingsQuery.data || vorbelegt.current) return
    vorbelegt.current = true
    setUrl(settingsQuery.data.public_url)
    setBasisUrl(settingsQuery.data.webhook_basis_url)
  }, [settingsQuery.data])

  const saveMutation = useMutation({
    mutationFn: () => api.put<AppSettings>('/api/settings', { public_url: url.trim() }),
    onMutate: () => setResult(null),
    onSuccess: (daten) => {
      queryClient.setQueryData(['settings'], daten)
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      setUrl(daten.public_url)
      setResult({ ok: true, message: t('settings.saved') })
    },
    onError: (error) =>
      setResult({
        ok: false,
        message: error instanceof ApiError ? error.message : t('settings.saveFailed'),
      }),
  })

  const testMutation = useMutation({
    mutationFn: () => api.post<TestResult>('/api/settings/test/public-url', { url: url.trim() }),
    onMutate: () => setResult(null),
    onSuccess: setResult,
    onError: (error) =>
      setResult({
        ok: false,
        message: error instanceof ApiError ? error.message : t('errors.generic'),
      }),
  })

  // Die Rueckkanal-Adresse hat ihren eigenen Speichern-Knopf: Sie ist ein
  // Sonderfall fuer wenige (Docker-Netz, Proxy-Schutz), und wer nur die
  // oeffentliche Adresse pflegt, soll sie gar nicht anfassen muessen.
  const basisSaveMutation = useMutation({
    mutationFn: () =>
      api.put<AppSettings>('/api/settings', { webhook_basis_url: basisUrl.trim() }),
    onMutate: () => setBasisResult(null),
    onSuccess: (daten) => {
      queryClient.setQueryData(['settings'], daten)
      setBasisUrl(daten.webhook_basis_url)
      setBasisResult({ ok: true, message: t('settings.saved') })
    },
    onError: (error) =>
      setBasisResult({
        ok: false,
        message: error instanceof ApiError ? error.message : t('settings.saveFailed'),
      }),
  })

  if (settingsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('mail.publicSection')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('mail.publicIntro')}</p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <Field
              label={t('mail.publicUrl')}
              value={url}
              onChange={(event) => {
                setUrl(event.target.value)
                setResult(null)
              }}
              placeholder="https://nexview.beispiel.de"
              hint={t('mail.publicUrlHint')}
              autoComplete="off"
            />
          </div>
          <Button
            type="button"
            onClick={() => saveMutation.mutate()}
            loading={saveMutation.isPending}
            className="mb-6"
          >
            {t('common.save')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!url.trim()}
            className="mb-6"
          >
            {t('mail.testConnection')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setUrl(window.location.origin)
              setResult(null)
            }}
            className="mb-6"
          >
            {t('mail.useCurrent')}
          </Button>
        </div>

        {result && (
          <p
            className={
              'rounded-xl border px-4 py-3 text-sm ' +
              (result.ok
                ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
            }
          >
            {result.message}
          </p>
        )}
      </Card>

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('mail.webhookBasisSection')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('mail.webhookBasisIntro')}</p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <Field
              label={t('mail.webhookBasisUrl')}
              value={basisUrl}
              onChange={(event) => {
                setBasisUrl(event.target.value)
                setBasisResult(null)
              }}
              placeholder="http://192.168.1.20:8080"
              hint={t('mail.webhookBasisUrlHint')}
              autoComplete="off"
            />
          </div>
          <Button
            type="button"
            onClick={() => basisSaveMutation.mutate()}
            loading={basisSaveMutation.isPending}
            className="mb-6"
          >
            {t('common.save')}
          </Button>
        </div>

        {basisResult && (
          <p
            className={
              'rounded-xl border px-4 py-3 text-sm ' +
              (basisResult.ok
                ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
            }
          >
            {basisResult.message}
          </p>
        )}
      </Card>
    </div>
  )
}
