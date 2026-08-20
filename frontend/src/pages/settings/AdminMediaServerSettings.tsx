/**
 * Den Media-Server verbinden und die Vorgaben für neue Konten setzen.
 *
 * Bewusst *ohne* Feld für Adresse und Token: Der Administrator meldet sich bei
 * Plex an und wählt seinen Server aus einer Liste. Das erspart die Sucherei
 * nach dem Token, funktioniert auch hinter einem Reverse Proxy - und verhindert
 * nebenbei, dass beim ersten eigenen Plex-Login ein *zweites* Konto entsteht:
 * Beim Verbinden wird das eigene Konto gleich mitverknüpft.
 */

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  AppSettings,
  MediaServerBlock,
  MediaServerLibraryState,
  MediaServerOption,
  QuotaPeriod,
  User,
} from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { MediaServerPrompt } from '../../components/MediaServerPrompt'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { useMediaServerChallenge } from '../../lib/useMediaServerChallenge'

type Draft = {
  mediaserver_auto_import: boolean
  mediaserver_default_role: 'user' | 'approver'
  mediaserver_default_quota_movies: string
  mediaserver_default_quota_series: string
  mediaserver_default_quota_period: QuotaPeriod
  mediaserver_default_age: string
}

const EMPTY_DRAFT: Draft = {
  mediaserver_auto_import: true,
  mediaserver_default_role: 'user',
  mediaserver_default_quota_movies: '',
  mediaserver_default_quota_series: '',
  mediaserver_default_quota_period: 'week',
  mediaserver_default_age: '',
}

export function AdminMediaServerSettings() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { updateUser } = useAuth()

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [meldung, setMeldung] = useState<{ ok: boolean; text: string } | null>(null)
  const [auswahl, setAuswahl] = useState<MediaServerOption[] | null>(null)
  const [ausgeblendet, setAusgeblendet] = useState(0)
  const [pollToken, setPollToken] = useState<string | null>(null)

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const settings = settingsQuery.data

  const blocksQuery = useQuery({
    queryKey: ['mediaserver-blocks'],
    queryFn: () => api.get<MediaServerBlock[]>('/api/admin/mediaserver/blocks'),
  })

  const bibliothek = useQuery({
    queryKey: ['mediaserver-library'],
    queryFn: () => api.get<MediaServerLibraryState>('/api/admin/mediaserver/library'),
  })

  const abgleichen = useMutation({
    mutationFn: () =>
      api.post<MediaServerLibraryState>('/api/admin/mediaserver/library/refresh', {}),
    onSuccess: (stand) => {
      queryClient.setQueryData(['mediaserver-library'], stand)
      // Die Abzeichen hängen daran - ohne das bliebe die Entdecken-Seite auf
      // dem alten Stand, bis der Zwischenspeicher von selbst abläuft.
      void queryClient.invalidateQueries({ queryKey: ['discover'] })
      setMeldung({ ok: true, text: t('mediaserver.libraryCount', { count: stand.count }) })
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  useEffect(() => {
    if (!settings) return
    setDraft({
      mediaserver_auto_import: settings.mediaserver_auto_import,
      mediaserver_default_role: settings.mediaserver_default_role,
      mediaserver_default_quota_movies:
        settings.mediaserver_default_quota_movies?.toString() ?? '',
      mediaserver_default_quota_series:
        settings.mediaserver_default_quota_series?.toString() ?? '',
      mediaserver_default_quota_period: settings.mediaserver_default_quota_period,
      mediaserver_default_age: settings.mediaserver_default_age?.toString() ?? '',
    })
  }, [settings])

  const verbinden = useMediaServerChallenge<{
    status: string
    servers: MediaServerOption[]
    shared_hidden: number
  }>({
    startPfad: '/api/admin/mediaserver/connect/start',
    abfragePfad: '/api/admin/mediaserver/connect/poll',
    onFertig: (ergebnis) => {
      setAuswahl(ergebnis.servers)
      setAusgeblendet(ergebnis.shared_hidden)
    },
  })

  // Der Merkzettel wird für die Auswahl noch einmal gebraucht - der Vorgang
  // bleibt deshalb offen, bis ein Server gewählt ist.
  useEffect(() => {
    if (verbinden.start) setPollToken(verbinden.start.poll_token)
  }, [verbinden.start])

  const waehlen = useMutation({
    mutationFn: (machine_id: string) =>
      api.post<{
        user: User
        server_name: string
        server_url: string
        reachable: boolean
        warning: string | null
      }>('/api/admin/mediaserver/connect/select', {
        poll_token: pollToken,
        machine_id,
      }),
    onSuccess: (ergebnis) => {
      // Das eigene Konto wurde dabei verknüpft - sonst zeigte das Profil
      // weiterhin "nicht verbunden".
      updateUser(ergebnis.user)
      setAuswahl(null)
      setPollToken(null)
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
      // **Auch die Konfiguration**: An ihr hängt, ob es den Merklisten-Reiter
      // und den Plex-Anmeldeknopf überhaupt gibt. Sie liegt fünf Minuten im
      // Zwischenspeicher - ohne diese Zeile ist Plex verbunden, die
      // Oberfläche weiß es nur noch nicht, und es sieht aus, als fehle die
      // Funktion.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      // Ein unerreichbarer Server ist kein Fehler, aber eine Warnung wert.
      setMeldung(
        ergebnis.warning
          ? { ok: false, text: ergebnis.warning }
          : { ok: true, text: t('settings.saved') },
      )
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('settings.saveFailed'),
      }),
  })

  const trennen = useMutation({
    mutationFn: () => api.delete<void>('/api/admin/mediaserver/connection'),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
      // Beim Trennen genauso - sonst bliebe der Merklisten-Reiter stehen.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      setMeldung({ ok: true, text: t('mediaserver.disconnected') })
    },
  })

  const speichern = useMutation({
    mutationFn: (payload: Draft) =>
      api.put<AppSettings>('/api/settings', {
        ...payload,
        // Leere Felder bedeuten "unbegrenzt" bzw. "keine Altersgrenze".
        mediaserver_default_quota_movies: payload.mediaserver_default_quota_movies.trim(),
        mediaserver_default_quota_series: payload.mediaserver_default_quota_series.trim(),
        mediaserver_default_age: payload.mediaserver_default_age.trim(),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data)
      setMeldung({ ok: true, text: t('settings.saved') })
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('settings.saveFailed'),
      }),
  })

  const sperreLoesen = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/admin/mediaserver/blocks/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['mediaserver-blocks'] }),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setMeldung(null)
    speichern.mutate(draft)
  }

  if (settingsQuery.isPending) return <Spinner />

  const verbunden = settings?.mediaserver_configured ?? false
  const geaendert =
    !!settings &&
    (draft.mediaserver_auto_import !== settings.mediaserver_auto_import ||
      draft.mediaserver_default_role !== settings.mediaserver_default_role ||
      draft.mediaserver_default_quota_movies !==
        (settings.mediaserver_default_quota_movies?.toString() ?? '') ||
      draft.mediaserver_default_quota_series !==
        (settings.mediaserver_default_quota_series?.toString() ?? '') ||
      draft.mediaserver_default_quota_period !== settings.mediaserver_default_quota_period ||
      draft.mediaserver_default_age !== (settings.mediaserver_default_age?.toString() ?? ''))

  return (
    <div className="flex flex-col gap-6">
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('mediaserver.adminTitle')}</h2>
          <p className="mt-1.5 text-sm text-mist-500">{t('mediaserver.adminIntro')}</p>
        </div>

        {verbunden ? (
          <>
            <p className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm">
              <span className="text-mist-500">{t('mediaserver.connected')} </span>
              <span className="font-medium text-mist-200">{settings?.mediaserver_name}</span>
              <span className="ml-2 text-xs text-mist-600">{settings?.mediaserver_url}</span>
            </p>
            <div>
              <Button variant="ghost" onClick={() => trennen.mutate()} loading={trennen.isPending}>
                {t('mediaserver.disconnectServer')}
              </Button>
            </div>
          </>
        ) : auswahl ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-mist-500">{t('mediaserver.selectServer')}</p>
            {auswahl.length === 0 && (
              <p className="text-sm text-mist-600">{t('mediaserver.noServers')}</p>
            )}
            {/* Erklärt eine kürzere Liste als erwartet: Server, auf die nur
                geteilt wurde, stehen bewusst nicht zur Wahl. */}
            {ausgeblendet > 0 && (
              <p className="text-xs text-mist-600">
                {t('mediaserver.sharedHidden', { count: ausgeblendet })}
              </p>
            )}
            {auswahl.map((server) => (
              <button
                key={server.machine_id}
                type="button"
                onClick={() => waehlen.mutate(server.machine_id)}
                disabled={waehlen.isPending}
                className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-left text-sm hover:border-accent-500 disabled:opacity-60"
              >
                <span>
                  <span className="font-medium text-mist-200">{server.name}</span>
                  <span className="ml-2 text-xs text-mist-600">{server.url}</span>
                </span>
                {server.owned && (
                  <span className="text-xs text-accent-400">{t('mediaserver.owned')}</span>
                )}
              </button>
            ))}
          </div>
        ) : verbinden.start ? (
          <MediaServerPrompt start={verbinden.start} onAbbrechen={verbinden.abbrechen} />
        ) : (
          <div>
            <Button onClick={() => void verbinden.starten()} loading={verbinden.laeuft}>
              {t('mediaserver.connect')}
            </Button>
          </div>
        )}

        {verbinden.fehler && <ErrorBanner message={verbinden.fehler} />}
      </Card>

      {verbunden && (
        <Card className="flex flex-col gap-4">
          <h2 className="text-lg font-semibold">{t('mediaserver.defaults')}</h2>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                checked={draft.mediaserver_auto_import}
                onChange={(event) =>
                  setDraft({ ...draft, mediaserver_auto_import: event.target.checked })
                }
                className="mt-1 h-4 w-4 accent-accent-500"
              />
              <span>
                <span className="text-sm text-mist-200">{t('mediaserver.autoImport')}</span>
                <span className="mt-0.5 block text-xs text-mist-600">
                  {t('mediaserver.autoImportHint')}
                </span>
              </span>
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                {t('mediaserver.defaultRole')}
              </span>
              <select
                value={draft.mediaserver_default_role}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    mediaserver_default_role: event.target.value as Draft['mediaserver_default_role'],
                  })
                }
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                {/* "Administrator" fehlt hier bewusst - eine automatisch
                    angelegte Anmeldung darf niemals volle Rechte bekommen. */}
                <option value="user">{t('adminUsers.roleUser')}</option>
                <option value="approver">{t('adminUsers.roleApprover')}</option>
              </select>
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label={t('adminUsers.quotaMovies')}
                type="number"
                min={0}
                value={draft.mediaserver_default_quota_movies}
                onChange={(event) =>
                  setDraft({ ...draft, mediaserver_default_quota_movies: event.target.value })
                }
                hint={t('mediaserver.quotaHint')}
              />
              <Field
                label={t('adminUsers.quotaSeries')}
                type="number"
                min={0}
                value={draft.mediaserver_default_quota_series}
                onChange={(event) =>
                  setDraft({ ...draft, mediaserver_default_quota_series: event.target.value })
                }
                hint={t('mediaserver.quotaHint')}
              />
            </div>

            {/* Ohne den Zeitraum wäre die Zahl darüber bedeutungslos - "5 Filme"
                sagt nichts, solange offen ist: pro Tag, Woche oder Monat? */}
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                {t('adminUsers.quotaPeriod')}
              </span>
              <select
                value={draft.mediaserver_default_quota_period}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    mediaserver_default_quota_period: event.target
                      .value as Draft['mediaserver_default_quota_period'],
                  })
                }
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                <option value="day">{t('adminUsers.periodDay')}</option>
                <option value="week">{t('adminUsers.periodWeek')}</option>
                <option value="month">{t('adminUsers.periodMonth')}</option>
              </select>
            </label>

            <Field
              label={t('mediaserver.defaultAge')}
              type="number"
              min={0}
              max={21}
              value={draft.mediaserver_default_age}
              onChange={(event) =>
                setDraft({ ...draft, mediaserver_default_age: event.target.value })
              }
              hint={t('mediaserver.defaultAgeHint')}
            />

            {meldung && (
              <p
                className={
                  'rounded-xl border px-4 py-3 text-sm ' +
                  (meldung.ok
                    ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                    : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
                }
              >
                {meldung.text}
              </p>
            )}

            <div>
              {/* Ausdrücklicher Knopf statt Speichern beim Verlassen des
                  Feldes - ohne Rückmeldung weiß niemand, ob es ankam. */}
              <Button type="submit" loading={speichern.isPending} disabled={!geaendert}>
                {t('common.save')}
              </Button>
            </div>
          </form>
        </Card>
      )}

      {verbunden && (
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-semibold">{t('mediaserver.library')}</h2>
            <p className="mt-1.5 text-sm text-mist-500">{t('mediaserver.libraryIntro')}</p>
          </div>

          <p className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm">
            {bibliothek.data?.updated_at ? (
              <>
                <span className="font-medium text-mist-200">
                  {t('mediaserver.libraryCount', { count: bibliothek.data.count })}
                </span>
                <span className="ml-2 text-xs text-mist-600">
                  {formatDateTime(bibliothek.data.updated_at, i18n.language)}
                </span>
              </>
            ) : (
              <span className="text-mist-500">{t('mediaserver.libraryNever')}</span>
            )}
          </p>

          <div>
            <Button
              variant="ghost"
              onClick={() => abgleichen.mutate()}
              loading={abgleichen.isPending}
            >
              {t('mediaserver.syncNow')}
            </Button>
          </div>
        </Card>
      )}

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('mediaserver.blocks')}</h2>
          <p className="mt-1.5 text-sm text-mist-500">{t('mediaserver.blocksIntro')}</p>
        </div>

        {(blocksQuery.data ?? []).length === 0 ? (
          <p className="text-sm text-mist-600">{t('mediaserver.blocksEmpty')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {(blocksQuery.data ?? []).map((eintrag) => (
              <li
                key={eintrag.id}
                className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm"
              >
                <span className="text-mist-200">
                  {eintrag.username ?? eintrag.account_id}
                  <span className="ml-2 text-xs text-mist-600">{eintrag.provider}</span>
                </span>
                <Button
                  variant="ghost"
                  onClick={() => sperreLoesen.mutate(eintrag.id)}
                  loading={sperreLoesen.isPending && sperreLoesen.variables === eintrag.id}
                >
                  {t('mediaserver.unblock')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
