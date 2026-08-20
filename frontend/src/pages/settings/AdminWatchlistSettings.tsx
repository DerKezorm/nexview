/**
 * Merklisten — die einzige Einstellung, die es dazu gibt.
 *
 * Ein Schalter: Dürfen Benutzer ihre Merkliste in Nexview sehen und daraus
 * anfragen? Mehr braucht es nicht, weil angefragt wird wie überall sonst —
 * mit Kontingent, Freigabe und dem gewohnten Dialog. Also gibt es hier weder
 * Rechte je Person noch eine Zielinstanz.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings } from '../../api/types'
import { Button, Card, Spinner } from '../../components/ui'

export function AdminWatchlistSettings() {
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
    if (settings) setAn(settings.watchlist_enabled)
  }, [settings])

  const speichern = useMutation({
    mutationFn: (wert: boolean) =>
      api.put<AppSettings>('/api/settings', { watchlist_enabled: wert }),
    onSuccess: (neu) => {
      queryClient.setQueryData(['settings'], neu)
      // Am Schalter hängt der Menüpunkt im Profil und der Filter in den
      // Anfragen - beides kommt aus der Konfiguration.
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

  const geaendert = an !== settings.watchlist_enabled

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('watchlist.adminTitle')}</h2>
        <p className="mt-1.5 text-sm text-mist-500">{t('watchlist.adminIntro')}</p>
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
            {t('watchlist.adminEnable')}
          </span>
          <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
            {t('watchlist.adminEnableHint')}
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
