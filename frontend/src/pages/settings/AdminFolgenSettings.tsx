/**
 * Folgen-Pakete — der eine Schalter dazu.
 *
 * Dürfen Benutzer einzelne Folgen statt ganzer Staffeln anfragen? Mehr
 * braucht es nicht, weil ein Paket den ganz normalen Weg nimmt — mit
 * Kontingent (ein Platz wie eine Staffel), Freigabe und dem gewohnten
 * Dialog. Der Schalter ist für Häuser, die es schlicht halten wollen.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings } from '../../api/types'
import { Button, Card, Spinner } from '../../components/ui'

export function AdminFolgenSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [an, setAn] = useState<boolean | null>(null)
  const [meldung, setMeldung] = useState<{ ok: boolean; text: string } | null>(null)

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const settings = settingsQuery.data

  useEffect(() => {
    if (settings) setAn(settings.episode_requests_enabled)
  }, [settings])

  const speichern = useMutation({
    mutationFn: (wert: boolean) =>
      api.put<AppSettings>('/api/settings', { episode_requests_enabled: wert }),
    onSuccess: (neu) => {
      queryClient.setQueryData(['settings'], neu)
      // Am Schalter hängen die Aufklapp-Pfeile im Staffel-Wähler - die
      // kommen aus der Konfiguration.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      setMeldung({ ok: true, text: t('settings.saved') })
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  if (!settings || an === null) {
    return (
      <Card>
        <Spinner />
      </Card>
    )
  }

  const geaendert = an !== settings.episode_requests_enabled

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('episodeRequests.adminTitle')}</h2>
        <p className="mt-1.5 text-sm text-mist-500">{t('episodeRequests.adminIntro')}</p>
      </div>

      <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-ink-700 px-4 py-3 transition-colors hover:bg-ink-850">
        <input
          type="checkbox"
          checked={an}
          onChange={(event) => {
            setAn(event.target.checked)
            setMeldung(null)
          }}
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
        />
        <span>
          <span className="text-sm font-medium text-mist-100">
            {t('episodeRequests.adminEnable')}
          </span>
          <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
            {t('episodeRequests.adminEnableHint')}
          </span>
        </span>
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => speichern.mutate(an)}
          loading={speichern.isPending}
          disabled={!geaendert}
        >
          {t('common.save')}
        </Button>
        {geaendert && !speichern.isPending && (
          <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
        )}
        {meldung && (
          <span className={'text-sm ' + (meldung.ok ? 'text-ok-500' : 'text-accent-400')}>
            {meldung.text}
          </span>
        )}
      </div>
    </Card>
  )
}
