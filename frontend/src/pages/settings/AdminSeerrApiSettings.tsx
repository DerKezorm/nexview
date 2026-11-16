/**
 * Seerr API — ein zweiter Port, auf dem Nexview wie Seerr antwortet (Issue #17).
 *
 * Ein Schalter und ein Port. Mehr gibt es nicht einzustellen, weil die
 * Schnittstelle keine eigenen Regeln hat: Wer über sie anfragt, fragt mit
 * dem Konto an, dem der Schlüssel gehört, mit dessen Rechten, Kontingent und
 * Freigabe. Den Schlüssel legt man im Profil unter „API-Token" an.
 *
 * ⚠️ **Der Zustand kommt vom Server, nicht vom Schalter.** „Eingeschaltet"
 * heißt noch nicht „lauscht": Der Port kann belegt sein, und im Container muss
 * er außerdem veröffentlicht sein. Die Seite sagt deshalb, was wirklich ist.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings } from '../../api/types'
import { Betont } from '../../components/Betont'
import { Button, Card, Spinner } from '../../components/ui'

export function AdminSeerrApiSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [an, setAn] = useState<boolean | null>(null)
  const [port, setPort] = useState('')
  const [meldung, setMeldung] = useState<{ ok: boolean; text: string } | null>(null)

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const settings = settingsQuery.data

  useEffect(() => {
    if (settings) {
      setAn(settings.seerr_api_enabled)
      setPort(String(settings.seerr_api_port))
    }
  }, [settings])

  const speichern = useMutation({
    mutationFn: (werte: { seerr_api_enabled: boolean; seerr_api_port?: number }) =>
      api.put<AppSettings>('/api/settings', werte),
    onSuccess: (neu) => {
      queryClient.setQueryData(['settings'], neu)
      setMeldung({ ok: true, text: t('settings.saved') })
      // Der Server öffnet den Port gleich nach dem Speichern, aber nicht
      // innerhalb dieser Antwort. Einmal nachfragen, dann stimmt die Zeile.
      window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['settings'] }), 1500)
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

  // Kommt der Port aus NEXVIEW_SEERR_PORT, hat ihn der Container
  // veröffentlicht. Das Feld ist dann gesperrt und wird nicht mitgeschickt.
  const portFest = settings.seerr_api_port_from_env
  const zahl = portFest ? settings.seerr_api_port : Number(port)
  const portGueltig = Number.isInteger(zahl) && zahl >= 1024 && zahl <= 65535
  const geaendert = an !== settings.seerr_api_enabled || zahl !== settings.seerr_api_port

  let zustand: { ok: boolean; text: string } | null = null
  if (settings.seerr_api_enabled && !geaendert) {
    if (settings.seerr_api_listening) {
      zustand = { ok: true, text: t('seerrApi.listening', { port: settings.seerr_api_port }) }
    } else if (settings.seerr_api_error === 'port_unavailable') {
      zustand = { ok: false, text: t('seerrApi.portUnavailable', { port: settings.seerr_api_port }) }
    } else {
      zustand = { ok: false, text: t('seerrApi.starting') }
    }
  }

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('seerrApi.title')}</h2>
        <p className="mt-1.5 text-sm text-mist-500">
          <Betont text={t('seerrApi.intro')} />
        </p>
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
          <span className="text-sm font-medium text-mist-100">{t('seerrApi.enable')}</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
            <Betont text={t(portFest ? 'seerrApi.enableHintEnv' : 'seerrApi.enableHint')} />
          </span>
        </span>
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-mist-100">{t('seerrApi.port')}</span>
        <input
          type="number"
          inputMode="numeric"
          min={1024}
          max={65535}
          value={portFest ? String(settings.seerr_api_port) : port}
          readOnly={portFest}
          aria-readonly={portFest}
          onChange={(event) => {
            setPort(event.target.value)
            setMeldung(null)
          }}
          className={
            'w-32 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100' +
            (portFest ? ' cursor-not-allowed opacity-70' : '')
          }
        />
        <span className="text-xs leading-relaxed text-mist-600">
          <Betont text={t(portFest ? 'seerrApi.portFromEnv' : 'seerrApi.portHint')} />
        </span>
      </label>

      {zustand && (
        <p className={'text-sm ' + (zustand.ok ? 'text-ok-500' : 'text-accent-400')}>{zustand.text}</p>
      )}

      <div className="flex flex-col gap-1.5 text-xs leading-relaxed text-mist-600">
        <p>
          <Betont text={t('seerrApi.keyHint')} />
        </p>
        <p>
          <Betont text={t('seerrApi.clients')} />
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() =>
            speichern.mutate(portFest ? { seerr_api_enabled: an } : { seerr_api_enabled: an, seerr_api_port: zahl })
          }
          loading={speichern.isPending}
          disabled={!geaendert || !portGueltig}
        >
          {t('common.save')}
        </Button>
        {!portGueltig && <span className="text-sm text-accent-400">{t('seerrApi.portInvalid')}</span>}
        {geaendert && portGueltig && !speichern.isPending && (
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
