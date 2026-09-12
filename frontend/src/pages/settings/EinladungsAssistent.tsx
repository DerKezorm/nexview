/**
 * Einladen mit Assistent: wer, wozu Zugang, was darf die Person, was sieht sie beim Einlösen.
 *
 * ⚠️ **Die Regeln stehen nicht hier.** Ob ein Haken frei ist, sagt der Server
 * (`POST /api/users/rechte/bewerten`, `services/kontorechte.py`), und zwar
 * bei jeder Änderung neu. Stünde die Logik zusätzlich hier, liefe sie
 * auseinander, und der Assistent böte etwas an, das beim Anlegen anders
 * ausgeht. Das Anlegen fragt dieselbe Stelle noch einmal, das Einlösen später
 * ein drittes Mal.
 *
 * Dasselbe gilt für die Medienserver: Ob einer geht und welche Bibliotheken er
 * hat, fragt der Assistent den Server (`GET /api/users/invitations/server`).
 * Ob Nexview dort ein Konto anlegt oder nur freigibt, steht in der Antwort
 * (`art`) und nicht in einer Liste von Anbietern hier.
 */

import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, uebersetzeFehler } from '../../api/client'
import type {
  AppSettings,
  InvitationCreated,
  Kontingentwert,
  RechteBewertung,
  RechteWunsch,
  Role,
  ServerAuswahl,
} from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { MediaServerLogo } from '../../components/MediaServerLogo'
import { RechteHaken } from '../../components/RechteHaken'
import { Umschalter } from '../../components/Umschalter'
import { AUSWAHL, Button, ErrorBanner, Field, Spinner } from '../../components/ui'

const SCHRITTE = ['person', 'zugang', 'rechte', 'onboarding', 'pruefen'] as const
type Schritt = (typeof SCHRITTE)[number]

type Grenzart = 'standard' | 'eigen' | 'unbegrenzt'
const GRENZARTEN: readonly Grenzart[] = ['standard', 'eigen', 'unbegrenzt']
type Grenze = { art: Grenzart; zahl: number }

type Entwurf = RechteWunsch & {
  email: string
  filme: Grenze
  serien: Grenze
  speicher: Grenze
  /** "Nur Nexview" oder "Nexview und Medienserver". */
  mitServern: boolean
  /** Je gewähltem Anbieter die Kennungen der Bibliotheken. Fehlt er, ist er nicht gewählt. */
  server: Record<string, string[]>
}

const LEER: Entwurf = {
  email: '',
  role: 'user',
  auto_approve_movies: false,
  auto_approve_series: false,
  can_request_uhd_movies: false,
  can_request_uhd_series: false,
  auto_approve_uhd: false,
  // Vorbelegt: Gibt es eine veröffentlichte Hausordnung, soll man sie sehen.
  // Gibt es keine, sperrt der Server den Haken samt Grund.
  hausordnung: true,
  filme: { art: 'standard', zahl: 0 },
  serien: { art: 'standard', zahl: 0 },
  speicher: { art: 'standard', zahl: 0 },
  // Nichts vorgewählt, auch keine Bibliothek: Was die Person sehen darf,
  // entscheidet der Administrator bewusst, nicht eine Vorbelegung.
  mitServern: false,
  server: {},
}

const ROLLEN: readonly Role[] = ['user', 'approver', 'admin']
const ROLLENNAME: Record<string, string> = {
  user: 'adminUsers.roleUser',
  approver: 'adminUsers.roleApprover',
  admin: 'adminUsers.roleAdmin',
}
const ROLLENHINWEIS: Record<string, string> = {
  user: 'inviteWizard.roleUserHint',
  approver: 'inviteWizard.roleApproverHint',
  admin: 'inviteWizard.roleAdminHint',
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function alsWert(grenze: Grenze): Kontingentwert {
  if (grenze.art === 'standard') return 'standard'
  if (grenze.art === 'unbegrenzt') return 'unlimited'
  return Math.max(0, Math.floor(grenze.zahl))
}

// ---------------------------------------------------------------------------
// Kleine Bausteine
// ---------------------------------------------------------------------------

function Frage({ titel, unter, children }: { titel: string; unter: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="text-lg font-semibold text-mist-100">{titel}</h3>
        <p className="mt-1 text-sm text-mist-600">{unter}</p>
      </div>
      {children}
    </div>
  )
}

function Abschnitt({ titel, children }: { titel: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5">
      <h4 className="text-sm font-semibold text-mist-200">{titel}</h4>
      {children}
    </section>
  )
}

function Erklaerung({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
      {children}
    </p>
  )
}

function Warnung({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs leading-relaxed text-warn-500">
      {children}
    </div>
  )
}

function GrenzZeile({
  name,
  einheit,
  standard,
  grenze,
  gesperrt,
  onChange,
}: {
  name: string
  einheit?: string
  standard: number | null | undefined
  grenze: Grenze
  gesperrt: boolean
  onChange: (neu: Grenze) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="w-20 text-sm text-mist-300">{name}</span>
      <Umschalter
        wert={grenze.art}
        wahl={GRENZARTEN}
        onChange={(art) => onChange({ ...grenze, art })}
        label={(art) =>
          art === 'standard'
            ? standard == null
              ? t('inviteWizard.limitStandardNone')
              : t('inviteWizard.limitStandard', { wert: standard })
            : art === 'eigen'
              ? t('inviteWizard.limitOwn')
              : t('inviteWizard.limitUnlimited')
        }
        deaktiviert={gesperrt}
      />
      {grenze.art === 'eigen' && !gesperrt && (
        <span className="flex items-center gap-2">
          <input
            type="number"
            min={0}
            value={grenze.zahl}
            onChange={(ev) => onChange({ ...grenze, zahl: Math.max(0, Number(ev.target.value) || 0) })}
            className={`${AUSWAHL} w-24`}
            aria-label={t('inviteWizard.limitOwnLabel', { name })}
          />
          {einheit && <span className="text-xs text-mist-500">{einheit}</span>}
        </span>
      )}
    </div>
  )
}

/** Ein Medienserver im Schritt „Zugang“: gesperrt mit Grund, sonst mit seinen Bibliotheken. */
function ServerKarte({
  eintrag,
  gewaehlt,
  onGewaehlt,
  onBibliotheken,
}: {
  eintrag: ServerAuswahl
  /** Die gewählten Kennungen. `undefined` heißt: Der Server ist nicht gewählt. */
  gewaehlt: string[] | undefined
  onGewaehlt: (an: boolean) => void
  onBibliotheken: (kennungen: string[]) => void
}) {
  const { t } = useTranslation()
  const frei = eintrag.stand.frei
  const aktiv = frei && gewaehlt !== undefined
  return (
    <div
      className={
        'rounded-xl border p-3 ' +
        (aktiv ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900')
      }
    >
      <label
        className={'flex flex-wrap items-center gap-3 ' + (frei ? 'cursor-pointer' : 'cursor-not-allowed')}
      >
        <input
          type="checkbox"
          checked={aktiv}
          disabled={!frei}
          onChange={(ev) => onGewaehlt(ev.target.checked)}
          className="h-4 w-4 accent-accent-500 disabled:opacity-60"
        />
        <MediaServerLogo
          provider={eintrag.provider}
          className={'h-5 w-5 ' + (frei ? 'text-ok-500' : 'text-mist-600')}
        />
        <span className={'text-sm font-medium ' + (frei ? 'text-mist-100' : 'text-mist-600')}>
          {eintrag.label}
        </span>
        {frei && eintrag.name && <span className="text-xs text-mist-500">{eintrag.name}</span>}
        {!frei && eintrag.stand.grund && (
          <span className="text-xs text-mist-600">{t(`rechte.grund.${eintrag.stand.grund}`)}</span>
        )}
      </label>
      {aktiv && (
        <div className="mt-3 flex flex-col gap-2 pl-7">
          <p className="text-xs text-mist-500">
            {eintrag.art === 'freigabe'
              ? t('inviteWizard.serverShareHint', { service: eintrag.label })
              : t('inviteWizard.serverAccountHint')}
          </p>
          {eintrag.fehler ? (
            <p className="text-xs text-warn-500">{uebersetzeFehler(eintrag.fehler, 502)}</p>
          ) : eintrag.bibliotheken.length === 0 ? (
            <p className="text-xs text-mist-600">{t('inviteWizard.serverNoLibraries')}</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {eintrag.bibliotheken.map((bibliothek) => {
                const an = gewaehlt.includes(bibliothek.kennung)
                return (
                  <button
                    key={bibliothek.kennung}
                    type="button"
                    aria-pressed={an}
                    onClick={() =>
                      onBibliotheken(
                        an
                          ? gewaehlt.filter((kennung) => kennung !== bibliothek.kennung)
                          : [...gewaehlt, bibliothek.kennung],
                      )
                    }
                    className={
                      'rounded-full border px-3 py-1 text-xs font-medium transition-colors ' +
                      (an
                        ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                        : 'border-ink-700 bg-ink-850 text-mist-500 hover:text-mist-300')
                    }
                  >
                    {bibliothek.name}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Der Assistent
// ---------------------------------------------------------------------------

export function EinladungsAssistent({
  offen,
  onSchliessen,
}: {
  offen: boolean
  onSchliessen: () => void
}) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const [schritt, setSchritt] = useState<Schritt | 'gesendet'>('person')
  const [e, setE] = useState<Entwurf>(LEER)
  const [ergebnis, setErgebnis] = useState<InvitationCreated | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const setze = (teil: Partial<Entwurf>) => setE((alt) => ({ ...alt, ...teil }))

  const wunsch: RechteWunsch = {
    role: e.role,
    auto_approve_movies: e.auto_approve_movies,
    auto_approve_series: e.auto_approve_series,
    can_request_uhd_movies: e.can_request_uhd_movies,
    can_request_uhd_series: e.can_request_uhd_series,
    auto_approve_uhd: e.auto_approve_uhd,
    hausordnung: e.hausordnung,
  }

  // Liest nur - ein POST, weil der Wunsch im Körper steht und nicht in die
  // Adresse gehört. Die letzte Antwort bleibt stehen, bis die neue da ist:
  // Sonst flackerte bei jedem Haken der ganze Schritt.
  const bewertungQuery = useQuery({
    queryKey: ['einladung-bewerten', wunsch],
    queryFn: () => api.post<RechteBewertung>('/api/users/rechte/bewerten', wunsch),
    enabled: offen,
    placeholderData: (vorher) => vorher,
  })
  const b = bewertungQuery.data

  // Nur für die Beschriftung "Standard (10)": Welche Grenzen gelten im Haus?
  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
    enabled: offen,
  })
  const haus = settingsQuery.data

  // Welche Medienserver gehen, und welche Bibliotheken haben sie? Fragt die
  // Server selbst, deshalb erst, wenn der Assistent offen ist.
  const serverQuery = useQuery({
    queryKey: ['einladung-server'],
    queryFn: () => api.get<ServerAuswahl[]>('/api/users/invitations/server'),
    enabled: offen,
  })
  const angebot = serverQuery.data ?? []
  const gewaehlteServer = e.mitServern
    ? angebot.filter((eintrag) => eintrag.stand.frei && e.server[eintrag.provider] !== undefined)
    : []
  const ohneBibliothek = gewaehlteServer.filter(
    (eintrag) => (e.server[eintrag.provider] ?? []).length === 0,
  )
  const aufzaehlung = (teile: string[]) =>
    new Intl.ListFormat(i18n.language, { type: 'conjunction' }).format(teile)

  const senden = useMutation({
    mutationFn: () =>
      api.post<InvitationCreated>('/api/users/invitations', {
        ...wunsch,
        email: e.email.trim(),
        quota_movies_limit: alsWert(e.filme),
        quota_series_limit: alsWert(e.serien),
        storage_limit_gb: alsWert(e.speicher),
        server: gewaehlteServer.map((eintrag) => ({
          provider: eintrag.provider,
          bibliotheken: e.server[eintrag.provider] ?? [],
        })),
      }),
    onMutate: () => setFehler(null),
    onSuccess: (angelegt) => {
      setErgebnis(angelegt)
      setSchritt('gesendet')
      void queryClient.invalidateQueries({ queryKey: ['invitations'] })
    },
    onError: (caught) => setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  function schliessen() {
    // Immer von vorn: Eine halb ausgefüllte Einladung von vorhin wäre beim
    // nächsten Öffnen eine Überraschung, und Rechte übernimmt man nicht blind.
    setSchritt('person')
    setE(LEER)
    setErgebnis(null)
    setFehler(null)
    onSchliessen()
  }

  function serverWaehlen(provider: string, an: boolean) {
    const neu = { ...e.server }
    if (an) neu[provider] = []
    else delete neu[provider]
    setze({ server: neu })
  }

  const index = schritt === 'gesendet' ? SCHRITTE.length : SCHRITTE.indexOf(schritt)
  const weiterErlaubt =
    schritt === 'person'
      ? EMAIL.test(e.email.trim())
      : schritt === 'zugang'
        ? !e.mitServern || (gewaehlteServer.length > 0 && ohneBibliothek.length === 0)
        : b !== undefined
  const uhdDa = b !== undefined && b.auto_approve_uhd.grund !== 'no_uhd_instance'
  const schritteBeimEinloesen = [
    t('inviteWizard.stepWelcome'),
    ...gewaehlteServer
      .filter((eintrag) => eintrag.art === 'freigabe')
      .map((eintrag) => t('inviteWizard.stepLink', { service: eintrag.label })),
    t('inviteWizard.stepAccount'),
    ...(b?.hausordnung.wirkt ? [t('inviteWizard.stepHouseRules')] : []),
    t('inviteWizard.stepDone'),
  ]
  const zugangText = [
    'Nexview',
    ...gewaehlteServer.map((eintrag) =>
      t('inviteWizard.sumAccessEntry', {
        service: eintrag.label,
        bibliotheken: eintrag.bibliotheken
          .filter((bibliothek) => (e.server[eintrag.provider] ?? []).includes(bibliothek.kennung))
          .map((bibliothek) => bibliothek.name)
          .join(', '),
      }),
    ),
  ].join(', ')

  function grenzText(grenze: Grenze, standard: number | null | undefined, einheit = ''): string {
    if (grenze.art === 'unbegrenzt') return t('inviteWizard.limitUnlimited')
    if (grenze.art === 'eigen') return `${grenze.zahl}${einheit}`
    return standard == null
      ? t('inviteWizard.limitStandardNone')
      : t('inviteWizard.limitStandard', { wert: `${standard}${einheit}` })
  }

  const fuss =
    schritt === 'gesendet' ? (
      <div className="flex w-full justify-end">
        <Button type="button" onClick={schliessen}>
          {t('inviteWizard.close')}
        </Button>
      </div>
    ) : (
      <div className="flex w-full items-center justify-between">
        <Button
          type="button"
          variant="ghost"
          onClick={() => (index === 0 ? schliessen() : setSchritt(SCHRITTE[index - 1]))}
        >
          {index === 0 ? t('inviteWizard.cancel') : t('inviteWizard.back')}
        </Button>
        {schritt === 'pruefen' ? (
          <Button type="button" loading={senden.isPending} onClick={() => senden.mutate()}>
            {t('inviteWizard.send')}
          </Button>
        ) : (
          <Button
            type="button"
            disabled={!weiterErlaubt}
            onClick={() => setSchritt(SCHRITTE[index + 1])}
          >
            {t('inviteWizard.next')}
          </Button>
        )}
      </div>
    )

  return (
    <Fenster offen={offen} titel={t('inviteWizard.title')} onSchliessen={schliessen} fuss={fuss}>
      <div className="flex flex-col gap-5">
        {schritt !== 'gesendet' && (
          <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mist-600">
            {SCHRITTE.map((s, i) => (
              <li key={s} className="flex items-center gap-2">
                <span
                  className={
                    i === index ? 'font-semibold text-mist-100' : i < index ? 'text-ok-500' : ''
                  }
                >
                  {i + 1} {t(`inviteWizard.step.${s}`)}
                </span>
                {i < SCHRITTE.length - 1 && <span className="text-ink-600">›</span>}
              </li>
            ))}
          </ol>
        )}

        {schritt === 'person' && (
          <Frage titel={t('inviteWizard.personTitle')} unter={t('inviteWizard.personSub')}>
            <Field
              label={t('inviteWizard.email')}
              type="email"
              value={e.email}
              onChange={(ev) => setze({ email: ev.target.value })}
              placeholder="name@example.com"
              autoComplete="off"
              autoFocus
            />
            <fieldset className="flex flex-col gap-2">
              <legend className="sr-only">{t('adminUsers.role')}</legend>
              {ROLLEN.map((rolle) => (
                <label
                  key={rolle}
                  className={
                    'flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 ' +
                    (e.role === rolle
                      ? 'border-accent-500/60 bg-accent-500/10'
                      : 'border-ink-700 bg-ink-900')
                  }
                >
                  <input
                    type="radio"
                    name="einladung-rolle"
                    checked={e.role === rolle}
                    onChange={() => setze({ role: rolle })}
                    className="mt-1 h-4 w-4 shrink-0 accent-accent-500"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-mist-200">{t(ROLLENNAME[rolle])}</span>
                    <span className="block text-xs text-mist-500">{t(ROLLENHINWEIS[rolle])}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <Erklaerung>{t('inviteWizard.noChildren')}</Erklaerung>
          </Frage>
        )}

        {schritt === 'zugang' && (
          <Frage titel={t('inviteWizard.zugangTitle')} unter={t('inviteWizard.zugangSub')}>
            <Umschalter
              wert={e.mitServern ? 'mit' : 'nur'}
              wahl={['nur', 'mit'] as const}
              onChange={(wahl) => setze({ mitServern: wahl === 'mit' })}
              label={(wahl) =>
                wahl === 'nur' ? t('inviteWizard.onlyNexview') : t('inviteWizard.withServers')
              }
            />
            {e.mitServern &&
              (serverQuery.isError ? (
                <ErrorBanner
                  message={
                    serverQuery.error instanceof ApiError
                      ? serverQuery.error.message
                      : t('errors.generic')
                  }
                />
              ) : serverQuery.data === undefined ? (
                <p className="flex items-center gap-2 text-sm text-mist-500">
                  <Spinner /> {t('common.loading')}
                </p>
              ) : (
                <div className="flex flex-col gap-3">
                  {angebot.map((eintrag) => (
                    <ServerKarte
                      key={eintrag.provider}
                      eintrag={eintrag}
                      gewaehlt={e.server[eintrag.provider]}
                      onGewaehlt={(an) => serverWaehlen(eintrag.provider, an)}
                      onBibliotheken={(kennungen) =>
                        setze({ server: { ...e.server, [eintrag.provider]: kennungen } })
                      }
                    />
                  ))}
                  {gewaehlteServer.length === 0 && (
                    <Warnung>{t('inviteWizard.noServerChosen')}</Warnung>
                  )}
                  {ohneBibliothek.length > 0 && (
                    <Warnung>
                      {t('inviteWizard.noLibraryChosen', {
                        services: aufzaehlung(ohneBibliothek.map((eintrag) => eintrag.label)),
                      })}
                    </Warnung>
                  )}
                  <Erklaerung>{t('inviteWizard.serverNote')}</Erklaerung>
                </div>
              ))}
          </Frage>
        )}

        {schritt !== 'person' && schritt !== 'zugang' && schritt !== 'gesendet' && b === undefined && (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('common.loading')}
          </p>
        )}

        {schritt === 'rechte' && b !== undefined && (
          <Frage titel={t('inviteWizard.rechteTitle')} unter={t('inviteWizard.rechteSub')}>
            <Abschnitt titel={t('inviteWizard.quota')}>
              {b.kontingent.grund && (
                <p className="text-xs text-mist-600">
                  {t(`rechte.grund.${b.kontingent.grund}`)}
                </p>
              )}
              <GrenzZeile
                name={t('inviteWizard.quotaMovies')}
                standard={haus?.quota_default_movies}
                grenze={e.filme}
                gesperrt={!b.kontingent.frei}
                onChange={(filme) => setze({ filme })}
              />
              <GrenzZeile
                name={t('inviteWizard.quotaSeries')}
                standard={haus?.quota_default_series}
                grenze={e.serien}
                gesperrt={!b.kontingent.frei}
                onChange={(serien) => setze({ serien })}
              />
              <GrenzZeile
                name={t('inviteWizard.quotaStorage')}
                einheit={t('inviteWizard.unitGb')}
                standard={haus?.storage_default_limit_gb}
                grenze={e.speicher}
                gesperrt={!b.kontingent.frei}
                onChange={(speicher) => setze({ speicher })}
              />
            </Abschnitt>
            <Abschnitt titel={t('inviteWizard.approval')}>
              <RechteHaken
                label={t('inviteWizard.autoMovies')}
                stand={b.auto_approve_movies}
                wert={e.auto_approve_movies}
                onChange={(v) => setze({ auto_approve_movies: v })}
              />
              <RechteHaken
                label={t('inviteWizard.autoSeries')}
                stand={b.auto_approve_series}
                wert={e.auto_approve_series}
                onChange={(v) => setze({ auto_approve_series: v })}
              />
            </Abschnitt>
            {/* Ohne jede 4K-Instanz bleibt der Abschnitt weg - wie im Kontodialog.
                Drei gesperrte Haken mit demselben Grund wären nur Rauschen. */}
            {uhdDa && (
              <Abschnitt titel={t('inviteWizard.uhd')}>
                <RechteHaken
                  label={t('inviteWizard.uhdMovies')}
                  stand={b.can_request_uhd_movies}
                  wert={e.can_request_uhd_movies}
                  onChange={(v) => setze({ can_request_uhd_movies: v })}
                />
                <RechteHaken
                  label={t('inviteWizard.uhdSeries')}
                  stand={b.can_request_uhd_series}
                  wert={e.can_request_uhd_series}
                  onChange={(v) => setze({ can_request_uhd_series: v })}
                />
                <RechteHaken
                  label={t('inviteWizard.uhdAuto')}
                  stand={b.auto_approve_uhd}
                  wert={e.auto_approve_uhd}
                  onChange={(v) => setze({ auto_approve_uhd: v })}
                />
              </Abschnitt>
            )}
            <Erklaerung>{t('inviteWizard.oneRule')}</Erklaerung>
          </Frage>
        )}

        {schritt === 'onboarding' && b !== undefined && (
          <Frage titel={t('inviteWizard.onboardingTitle')} unter={t('inviteWizard.onboardingSub')}>
            <RechteHaken
              label={t('inviteWizard.houseRules')}
              stand={b.hausordnung}
              wert={e.hausordnung}
              onChange={(v) => setze({ hausordnung: v })}
            />
            <ol className="flex flex-col gap-1.5 rounded-xl border border-ink-700 bg-ink-900 p-3 text-sm text-mist-300">
              {schritteBeimEinloesen.map((name, i) => (
                <li key={name}>
                  <span className="mr-2 font-mono text-mist-600">{i + 1}</span>
                  {name}
                </li>
              ))}
            </ol>
            <Erklaerung>{t('inviteWizard.houseRulesNote')}</Erklaerung>
          </Frage>
        )}

        {schritt === 'pruefen' && b !== undefined && (
          <Frage titel={t('inviteWizard.reviewTitle')} unter={t('inviteWizard.reviewSub')}>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-mist-500">{t('inviteWizard.sumTo')}</dt>
              <dd className="break-all text-mist-100">{e.email.trim()}</dd>
              <dt className="text-mist-500">{t('inviteWizard.sumRole')}</dt>
              <dd className="text-mist-100">{t(ROLLENNAME[e.role])}</dd>
              <dt className="text-mist-500">{t('inviteWizard.sumAccess')}</dt>
              <dd className="text-mist-100">{zugangText}</dd>
              <dt className="text-mist-500">{t('inviteWizard.sumQuota')}</dt>
              <dd className="text-mist-100">
                {b.kontingent.frei
                  ? [
                      `${t('inviteWizard.quotaMovies')}: ${grenzText(e.filme, haus?.quota_default_movies)}`,
                      `${t('inviteWizard.quotaSeries')}: ${grenzText(e.serien, haus?.quota_default_series)}`,
                      `${t('inviteWizard.quotaStorage')}: ${grenzText(e.speicher, haus?.storage_default_limit_gb, ` ${t('inviteWizard.unitGb')}`)}`,
                    ].join(' · ')
                  : t('inviteWizard.sumQuotaNone')}
              </dd>
              <dt className="text-mist-500">{t('inviteWizard.sumApproval')}</dt>
              <dd className="text-mist-100">
                {e.role !== 'user'
                  ? t('inviteWizard.sumFromRole')
                  : [
                      b.auto_approve_movies.wirkt ? t('inviteWizard.sumAutoMovies') : null,
                      b.auto_approve_series.wirkt ? t('inviteWizard.sumAutoSeries') : null,
                    ]
                      .filter(Boolean)
                      .join(', ') || t('inviteWizard.sumWaits')}
              </dd>
              {uhdDa && (
                <>
                  <dt className="text-mist-500">{t('inviteWizard.sumUhd')}</dt>
                  <dd className="text-mist-100">
                    {b.can_request_uhd_movies.wirkt || b.can_request_uhd_series.wirkt
                      ? `${[
                          b.can_request_uhd_movies.wirkt ? t('inviteWizard.sumUhdMovies') : null,
                          b.can_request_uhd_series.wirkt ? t('inviteWizard.sumUhdSeries') : null,
                        ]
                          .filter(Boolean)
                          .join(', ')}, ${
                          b.auto_approve_uhd.wirkt
                            ? t('inviteWizard.sumUhdAuto')
                            : t('inviteWizard.sumUhdManual')
                        }`
                      : t('inviteWizard.sumNo')}
                  </dd>
                </>
              )}
              <dt className="text-mist-500">{t('inviteWizard.sumOnboarding')}</dt>
              <dd className="text-mist-100">{schritteBeimEinloesen.join(' › ')}</dd>
            </dl>
            {b.entfallen.length > 0 && (
              <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs leading-relaxed text-warn-500">
                <p className="font-semibold">{t('inviteWizard.dropped')}</p>
                <ul className="mt-1.5 flex flex-col gap-1">
                  {b.entfallen.map((name) => (
                    <li key={name}>{t(`inviteWizard.field.${name}`)}</li>
                  ))}
                </ul>
              </div>
            )}
            <Erklaerung>{t('inviteWizard.recheck')}</Erklaerung>
            {fehler && <ErrorBanner message={fehler} />}
          </Frage>
        )}

        {schritt === 'gesendet' && ergebnis && (
          <div className="flex flex-col gap-4">
            {ergebnis.mail_sent ? (
              <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
                {t('inviteWizard.sent', { email: ergebnis.email })}
              </p>
            ) : (
              <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3">
                <p className="text-sm font-medium text-warn-500">
                  {t('inviteWizard.mailFailedTitle')}
                </p>
                <p className="mt-1 text-xs text-mist-400">
                  {ergebnis.mail_error ?? t('adminUsers.mailFailed')}
                </p>
                {ergebnis.manual_link && (
                  <>
                    <p className="mt-2 text-xs text-mist-500">{t('adminUsers.manualLinkHint')}</p>
                    <code className="mt-1 block break-all rounded-lg bg-ink-900 px-3 py-2 text-xs text-mist-300">
                      {ergebnis.manual_link}
                    </code>
                    <Button
                      variant="ghost"
                      className="mt-2"
                      onClick={() => void navigator.clipboard?.writeText(ergebnis.manual_link ?? '')}
                    >
                      {t('adminUsers.copyLink')}
                    </Button>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </Fenster>
  )
}
