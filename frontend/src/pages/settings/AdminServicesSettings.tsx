import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings, ArrOptions, TestResult } from '../../api/types'
import { REGION_OPTIONS } from '../../components/media/FilterBar'
import { Button, Card, ErrorBanner, Field, Spinner } from '../../components/ui'
import { AdminMediaServerSettings } from './AdminMediaServerSettings'

type TestService = 'tmdb' | 'radarr' | 'sonarr'

/**
 * Ein Knopf je Dienst statt einer langen Liste.
 *
 * Untereinander waren das fünf Blöcke, in denen man das Gesuchte nur noch
 * durch Scrollen fand. "Allgemein" steht voran: Region, Sprache und
 * Beispieldaten gehören zu keinem einzelnen Dienst.
 */
type UnterTab = 'general' | 'tmdb' | 'radarr' | 'sonarr' | 'plex'

const UNTER_TABS: { value: UnterTab; labelKey: string }[] = [
  { value: 'general', labelKey: 'settings.generalSection' },
  { value: 'tmdb', labelKey: 'settings.tmdbSection' },
  { value: 'radarr', labelKey: 'settings.radarrSection' },
  { value: 'sonarr', labelKey: 'settings.sonarrSection' },
  { value: 'plex', labelKey: 'mediaserver.adminTitle' },
]

type Draft = {
  tmdb_api_key: string
  radarr_url: string
  radarr_api_key: string
  sonarr_url: string
  sonarr_api_key: string
  default_region: string
  default_language: string
  demo_mode: 'auto' | 'on' | 'off'
  /** Leerer String = kein Standardprofil. */
  default_movie_profile_id: string
  default_series_profile_id: string
  /**
   * Dürfen Benutzer den Zielordner selbst wählen? Je Dienst getrennt – Filme
   * und Serien haben unterschiedliche Ordnerstrukturen, und wer bei Serien
   * feste Pfade will, muss das nicht auch bei Filmen wollen.
   */
  movie_root_folder_choice: boolean
  series_root_folder_choice: boolean
  default_movie_root: string
  default_series_root: string
}

const EMPTY_DRAFT: Draft = {
  tmdb_api_key: '',
  radarr_url: '',
  radarr_api_key: '',
  sonarr_url: '',
  sonarr_api_key: '',
  default_region: 'DE',
  default_language: 'de',
  demo_mode: 'auto',
  default_movie_profile_id: '',
  default_series_profile_id: '',
  movie_root_folder_choice: true,
  series_root_folder_choice: true,
  default_movie_root: '',
  default_series_root: '',
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </Card>
  )
}

/**
 * Darf der Benutzer den Zielordner selbst wählen?
 *
 * Der Zielordner ist die einzige Auswahl beim Anfragen, die etwas über die
 * Ablage auf dem Server verrät. Wer das nicht jedem zumuten will, schaltet sie
 * ab; dann erscheint direkt darunter der feste Ordner.
 *
 * Steht bewusst bei Radarr bzw. Sonarr und nicht mehr im Allgemein-Block:
 * Filme und Serien haben unterschiedliche Ordnerstrukturen.
 */
function RootFolderChoice({
  checked,
  onChange,
}: {
  checked: boolean
  onChange: (value: boolean) => void
}) {
  const { t } = useTranslation()

  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
      />
      <span>
        <span className="text-sm font-medium text-mist-300">
          {t('settings.rootFolderChoice')}
        </span>
        <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
          {t('settings.rootFolderChoiceHint')}
        </span>
      </span>
    </label>
  )
}

/**
 * Auswahl des Standard-Qualitätsprofils.
 *
 * Die Liste kommt direkt aus Radarr bzw. Sonarr; solange dort kein Zugang
 * hinterlegt ist, gibt es nichts auszuwählen und das Feld bleibt gesperrt.
 */
function DefaultProfileField({
  mediaType,
  value,
  onChange,
  configured,
}: {
  mediaType: 'movie' | 'tv'
  value: string
  onChange: (value: string) => void
  configured: boolean
}) {
  const { t } = useTranslation()

  const optionsQuery = useQuery({
    queryKey: ['arr-options', mediaType],
    queryFn: () => api.get<ArrOptions>(`/api/arr/${mediaType}/options`),
    enabled: configured,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const profile = optionsQuery.data?.quality_profiles ?? []

  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {t('settings.defaultProfile')}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={!configured || optionsQuery.isPending || profile.length === 0}
        className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-60"
      >
        <option value="">{t('settings.defaultProfileNone')}</option>
        {profile.map((eintrag) => (
          <option key={eintrag.id} value={String(eintrag.id)}>
            {eintrag.name}
          </option>
        ))}
      </select>
      <span className="text-xs text-mist-600">
        {configured ? t('settings.defaultProfileHint') : t('settings.defaultProfileMissing')}
      </span>
    </label>
  )
}

/**
 * Standard-Zielordner - nur sichtbar, wenn die Auswahl abgeschaltet ist.
 *
 * Denn nur dann muss überhaupt jemand entscheiden, wohin geladen wird. Dürfen
 * die Benutzer selbst wählen, wäre das Feld nur eine Vorauswahl mehr, die man
 * erklären müsste.
 */
function DefaultRootField({
  mediaType,
  value,
  onChange,
  configured,
}: {
  mediaType: 'movie' | 'tv'
  value: string
  onChange: (value: string) => void
  configured: boolean
}) {
  const { t } = useTranslation()

  const optionsQuery = useQuery({
    queryKey: ['arr-options', mediaType],
    queryFn: () => api.get<ArrOptions>(`/api/arr/${mediaType}/options`),
    enabled: configured,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const ordner = optionsQuery.data?.root_folders ?? []

  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {t('settings.defaultRoot')}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={!configured || optionsQuery.isPending || ordner.length === 0}
        className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-60"
      >
        {/* Leer heißt: der erste Ordner aus Radarr/Sonarr. */}
        <option value="">{t('settings.defaultRootFirst')}</option>
        {ordner.map((eintrag) => (
          <option key={eintrag.path} value={eintrag.path}>
            {eintrag.path}
          </option>
        ))}
      </select>
      <span className="text-xs text-mist-600">
        {configured ? t('settings.defaultRootHint') : t('settings.defaultProfileMissing')}
      </span>
    </label>
  )
}

export function AdminServicesSettings() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [unterTab, setUnterTab] = useState<UnterTab>('general')
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [testResults, setTestResults] = useState<Partial<Record<TestService, TestResult>>>({})

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })

  /**
   * Nur die nicht-geheimen Werte vorbelegen - Key-Felder bleiben leer, damit
   * ein versehentliches Speichern den Key nicht überschreibt.
   *
   * Und nur **einmal**: die Abfragedaten kommen bei jedem Hintergrund-Abgleich
   * als neues Objekt zurück, sonst wird mitten im Tippen zurückgesetzt.
   */
  const vorbelegt = useRef(false)

  useEffect(() => {
    const data = settingsQuery.data
    if (!data || vorbelegt.current) return
    vorbelegt.current = true
    setDraft({
      ...EMPTY_DRAFT,
      radarr_url: data.radarr_url,
      sonarr_url: data.sonarr_url,
      default_region: data.default_region,
      default_language: data.default_language,
      demo_mode: data.demo_mode,
      default_movie_profile_id: data.default_movie_profile_id?.toString() ?? '',
      default_series_profile_id: data.default_series_profile_id?.toString() ?? '',
      movie_root_folder_choice: data.movie_root_folder_choice,
      series_root_folder_choice: data.series_root_folder_choice,
      default_movie_root: data.default_movie_root,
      default_series_root: data.default_series_root,
    })
  }, [settingsQuery.data])

  const saveMutation = useMutation({
    mutationFn: (payload: Partial<Draft>) => api.put<AppSettings>('/api/settings', payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data)
      // Startseite und Konfiguration neu holen: Region oder Demo-Modus
      // können sich geändert haben.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      void queryClient.invalidateQueries({ queryKey: ['discover'] })
      void queryClient.invalidateQueries({ queryKey: ['genres'] })
      setMessage({ ok: true, text: t('settings.saved') })
      setTestResults({})
    },
    onError: (error) =>
      setMessage({
        ok: false,
        text: error instanceof ApiError ? error.message : t('settings.saveFailed'),
      }),
  })

  const removeKeyMutation = useMutation({
    mutationFn: (name: 'tmdb_api_key' | 'radarr_api_key' | 'sonarr_api_key') =>
      api.delete<AppSettings>(`/api/settings/secret/${name}`),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data)
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      void queryClient.invalidateQueries({ queryKey: ['discover'] })
      void queryClient.invalidateQueries({ queryKey: ['genres'] })
      setTestResults({})
      setMessage({ ok: true, text: t('settings.keyRemoved') })
    },
  })

  const testMutation = useMutation({
    mutationFn: (service: TestService) =>
      api.post<TestResult>(`/api/settings/test/${service}`, {
        api_key: draft[`${service}_api_key`] || undefined,
        url: service === 'tmdb' ? undefined : draft[`${service}_url`] || undefined,
      }),
    onSuccess: (result, service) => setTestResults((current) => ({ ...current, [service]: result })),
    onError: (error, service) =>
      setTestResults((current) => ({
        ...current,
        [service]: {
          ok: false,
          message: error instanceof ApiError ? error.message : t('errors.generic'),
        },
      })),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setMessage(null)
    saveMutation.mutate(draft)
  }

  const update = (patch: Partial<Draft>) => setDraft((current) => ({ ...current, ...patch }))
  const settings = settingsQuery.data

  /**
   * Zeile mit "Verbindung testen" und - falls ein Key hinterlegt ist -
   * "Key entfernen". Bewusst eine Funktion und keine eigene Komponente:
   * sonst würde React die Knöpfe bei jedem Tastendruck neu einhängen.
   */
  const testRow = (service: TestService) => {
    const result = testResults[service]
    const pending = testMutation.isPending && testMutation.variables === service

    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="ghost"
          onClick={() => testMutation.mutate(service)}
          loading={pending}
        >
          {t('settings.test')}
        </Button>
        {settings?.[`${service}_api_key_set`] && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => removeKeyMutation.mutate(`${service}_api_key`)}
            loading={removeKeyMutation.isPending}
          >
            {t('settings.removeKey')}
          </Button>
        )}
        {result && (
          <span className={'text-sm ' + (result.ok ? 'text-ok-500' : 'text-bad-500')} role="status">
            {result.message}
          </span>
        )}
      </div>
    )
  }

  if (settingsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  return (
    <div className="max-w-3xl">
      <p className="text-sm text-mist-500">{t('settings.intro')}</p>

      {/* Zweite Reihe: ein Knopf je Dienst. Bewusst optisch leichter als die
          Reiter darüber, damit die Ebenen unterscheidbar bleiben. */}
      <div className="mt-5 flex flex-wrap gap-2" role="tablist">
        {UNTER_TABS.map((eintrag) => (
          <button
            key={eintrag.value}
            type="button"
            role="tab"
            aria-selected={unterTab === eintrag.value}
            onClick={() => setUnterTab(eintrag.value)}
            className={
              'rounded-lg border px-3 py-1.5 text-sm transition ' +
              (unterTab === eintrag.value
                ? 'border-accent-500 bg-accent-500/10 text-accent-400'
                : 'border-ink-700 text-mist-500 hover:border-ink-600 hover:text-mist-300')
            }
          >
            {t(eintrag.labelKey)}
          </button>
        ))}
      </div>

      {/* Der Media-Server bringt eigenes Speichern und eigene Abläufe mit -
          er steht deshalb außerhalb dieses Formulars. */}
      {unterTab === 'plex' && (
        <div className="mt-6">
          <AdminMediaServerSettings />
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className={'mt-6 flex-col gap-5 ' + (unterTab === 'plex' ? 'hidden' : 'flex')}
      >
        {unterTab === 'tmdb' && (
        <Section title={t('settings.tmdbSection')}>
          <Field
            label={t('settings.tmdbKey')}
            type="password"
            value={draft.tmdb_api_key}
            onChange={(event) => update({ tmdb_api_key: event.target.value })}
            placeholder={settings?.tmdb_api_key_set ? settings.tmdb_api_key : ''}
            autoComplete="off"
            hint={settings?.tmdb_api_key_set ? t('settings.keySetHint') : t('settings.tmdbHint')}
          />

          {testRow('tmdb')}
        </Section>
        )}

        {unterTab === 'general' && (
        <Section title={t('settings.generalSection')}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-mist-300">{t('settings.region')}</span>
              <select
                value={draft.default_region}
                onChange={(event) => update({ default_region: event.target.value })}
                className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                {REGION_OPTIONS.map((region) => (
                  <option key={region} value={region}>
                    {region}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-mist-300">{t('settings.language')}</span>
              <select
                value={draft.default_language}
                onChange={(event) => update({ default_language: event.target.value })}
                className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                <option value="de">Deutsch</option>
                <option value="en">English</option>
              </select>
              <span className="text-xs leading-relaxed text-mist-600">
                {t('settings.languageHint')}
              </span>
            </label>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-mist-300">{t('settings.demoMode')}</span>
            <select
              value={draft.demo_mode}
              onChange={(event) => update({ demo_mode: event.target.value as Draft['demo_mode'] })}
              className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="auto">{t('settings.demoAuto')}</option>
              <option value="on">{t('settings.demoOn')}</option>
              <option value="off">{t('settings.demoOff')}</option>
            </select>
          </label>

        </Section>
        )}

        {unterTab === 'radarr' && (
        <Section title={t('settings.radarrSection')}>
          <Field
            label={t('settings.url')}
            value={draft.radarr_url}
            onChange={(event) => update({ radarr_url: event.target.value })}
            placeholder="http://192.168.1.10:7878"
            autoComplete="off"
          />
          <Field
            label={t('settings.apiKey')}
            type="password"
            value={draft.radarr_api_key}
            onChange={(event) => update({ radarr_api_key: event.target.value })}
            placeholder={settings?.radarr_api_key_set ? settings.radarr_api_key : ''}
            hint={settings?.radarr_api_key_set ? t('settings.keySetHint') : t('settings.arrHint')}
            autoComplete="off"
          />
          <DefaultProfileField
            mediaType="movie"
            value={draft.default_movie_profile_id}
            onChange={(value) => update({ default_movie_profile_id: value })}
            configured={settings?.radarr_api_key_set ?? false}
          />
          <RootFolderChoice
            checked={draft.movie_root_folder_choice}
            onChange={(value) => update({ movie_root_folder_choice: value })}
          />
          {!draft.movie_root_folder_choice && (
            <DefaultRootField
              mediaType="movie"
              value={draft.default_movie_root}
              onChange={(value) => update({ default_movie_root: value })}
              configured={settings?.radarr_api_key_set ?? false}
            />
          )}
          {testRow('radarr')}
        </Section>
        )}

        {unterTab === 'sonarr' && (
        <Section title={t('settings.sonarrSection')}>
          <Field
            label={t('settings.url')}
            value={draft.sonarr_url}
            onChange={(event) => update({ sonarr_url: event.target.value })}
            placeholder="http://192.168.1.10:8989"
            autoComplete="off"
          />
          <Field
            label={t('settings.apiKey')}
            type="password"
            value={draft.sonarr_api_key}
            onChange={(event) => update({ sonarr_api_key: event.target.value })}
            placeholder={settings?.sonarr_api_key_set ? settings.sonarr_api_key : ''}
            hint={settings?.sonarr_api_key_set ? t('settings.keySetHint') : t('settings.arrHint')}
            autoComplete="off"
          />
          <DefaultProfileField
            mediaType="tv"
            value={draft.default_series_profile_id}
            onChange={(value) => update({ default_series_profile_id: value })}
            configured={settings?.sonarr_api_key_set ?? false}
          />
          <RootFolderChoice
            checked={draft.series_root_folder_choice}
            onChange={(value) => update({ series_root_folder_choice: value })}
          />
          {!draft.series_root_folder_choice && (
            <DefaultRootField
              mediaType="tv"
              value={draft.default_series_root}
              onChange={(value) => update({ default_series_root: value })}
              configured={settings?.sonarr_api_key_set ?? false}
            />
          )}
          {testRow('sonarr')}
        </Section>
        )}

        {message && !message.ok && <ErrorBanner message={message.text} />}
        {message?.ok && (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {message.text}
          </p>
        )}

        <div>
          <Button type="submit" loading={saveMutation.isPending}>
            {t('common.save')}
          </Button>
        </div>
      </form>
    </div>
  )
}
