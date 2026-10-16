/**
 * Die Geräte eines Kindes: verbinden per QR-Code, ansehen, trennen.
 *
 * ⚠️ **Ein Kind koppelt sich nicht selbst.** Den Code erzeugen die Eltern hier,
 * das iPad des Kindes scannt ihn. Der Schlüssel gehört dem Kinderkonto und kann
 * nur, was das Kind auch im Browser kann.
 */

import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { Child } from '../../api/types'
import { AppKoppeln } from '../../components/AppKoppeln'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, ErrorBanner } from '../../components/ui'

type Geraet = {
  id: number
  name: string
  created_at: string
  last_used_at: string | null
}

export function KinderGeraete({ kind }: { kind: Child }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [offen, setOffen] = useState(false)
  const [trennen, setTrennen] = useState<Geraet | null>(null)
  const [fehler, setFehler] = useState('')
  const name = kind.display_name ?? kind.username
  const schluessel = ['kinder-geraete', kind.id]

  const geraete = useQuery({
    queryKey: schluessel,
    queryFn: () => api.get<Geraet[]>(`/api/children/${kind.id}/geraete`),
  })

  const auffrischen = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['kinder-geraete', kind.id] })
  }, [queryClient, kind.id])

  const entfernen = useMutation({
    mutationFn: (id: number) => api.delete(`/api/children/${kind.id}/geraete/${id}`),
    onSuccess: () => {
      setTrennen(null)
      auffrischen()
    },
    onError: (e: unknown) => setFehler(e instanceof ApiError ? e.message : t('errors.generic')),
  })

  return (
    <fieldset className="flex flex-col gap-3">
      <legend className="mb-1 text-sm font-medium text-mist-200">{t('appConnect.devices')}</legend>
      {fehler && <ErrorBanner message={fehler} />}
      <p className="text-xs text-mist-600">{t('appConnect.passwordKeeps')}</p>
      {geraete.data && geraete.data.length === 0 && (
        <p className="text-sm text-mist-600">{t('appConnect.noDevices')}</p>
      )}
      {geraete.data && geraete.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {geraete.data.map((g) => (
            <li
              key={g.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-4 py-2"
            >
              <span className="flex-1 text-sm text-mist-100">{g.name}</span>
              <span className="text-xs text-mist-600">
                {g.last_used_at
                  ? t('apikeys.lastUsed', { when: new Date(g.last_used_at).toLocaleDateString() })
                  : t('apikeys.neverUsed')}
              </span>
              <Button type="button" variant="ghost" onClick={() => setTrennen(g)}>
                {t('appConnect.disconnect')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button type="button" variant="ghost" className="self-start" onClick={() => setOffen(true)}>
        {t('appConnect.childButton')}
      </Button>

      <AppKoppeln
        offen={offen}
        pfad={`/api/children/${kind.id}/app-kopplung`}
        titel={t('appConnect.childTitle', { name })}
        anleitung={t('appConnect.childScan', { name })}
        verbundenText={() => t('appConnect.childConnected', { name })}
        onSchliessen={() => setOffen(false)}
        onVerbunden={auffrischen}
      />

      <ConfirmDialog
        open={trennen !== null}
        title={t('appConnect.disconnectTitle')}
        description={trennen?.name ?? ''}
        warning={t('appConnect.disconnectWarning')}
        confirmLabel={t('appConnect.disconnect')}
        loading={entfernen.isPending}
        onConfirm={() => trennen && entfernen.mutate(trennen.id)}
        onCancel={() => setTrennen(null)}
      />
    </fieldset>
  )
}
