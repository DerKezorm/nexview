/**
 * Einstellungen → Anmeldung: drei Karten untereinander, wie der Bauplan
 * „Anmeldung mit OIDC und authentik“ (04) sie für jede nex-App festlegt.
 * Reihenfolge, Wörter und Verhalten kommen von dort; die Bauteile (Karte,
 * Schalter, Feld, Knopf, Rückfrage) sind Nexviews eigene.
 *
 * 1. Anmeldung: Passwort-Anmeldung (gesperrt an, solange kein Anbieter aktiv
 *    ist) und die öffentliche Adresse. Einen zweiten Faktor hat Nexview nicht.
 * 2. Anmeldeanbieter: die Liste mit Marken, Ziehgriff und dem Formular als
 *    Fenster.
 * 3. authentik in einem Schritt: Adresse, Token, „Einrichten“, der Blueprint
 *    und die Schritte samt Grund eines Fehlschlags. Ohne öffentliche Adresse
 *    schlägt die Karte die des Browsers vor und trägt nie eine geratene ein.
 *
 * Darunter, Nexviews eigene: die Sperrliste gelöschter Konten.
 */

import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api, downloadFile } from '../../api/client'
import type {
  AppSettings,
  AuthentikErgebnis,
  OidcAnbieter,
  OidcAnbieterForm,
  OidcFolgen,
  OidcSperre,
} from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Fenster } from '../../components/Fenster'
import { Button, ErrorBanner, Field, Section } from '../../components/ui'
import { BASIS } from '../../lib/basis'
import { adressFrageAus, type AdressFrage } from '../../lib/adressFrage'
import { AdressFrageText } from '../../components/AdressFrageText'
import {
  authentikIssuerChange,
  authentikProposal,
  authentikProxyWarning,
  authentikReasonKey,
  authentikRedirect,
  authentikStale,
  authentikStepKey,
  oidcErrorKey,
  oidcFormErrorKey,
  slugFromLabel,
  type AuthentikAddress,
} from '../../vendor/nexoidc/oidc'

const ANBIETER = ['oidc-admin-anbieter']

export function AdminOidcSettings() {
  const anbieter = useQuery({
    queryKey: ANBIETER,
    queryFn: () => api.get<OidcAnbieter[]>('/api/oidc/admin/providers'),
  })
  const liste = anbieter.data ?? []
  const aktiv = liste.some((eintrag) => eintrag.enabled)
  const gekoppelt = liste.some((eintrag) => eintrag.managed === 'nexsuite')
  return (
    <div className="flex flex-col gap-6">
      <AnmeldungKarte irgendeinerAktiv={aktiv} />
      <AnbieterKarte liste={anbieter.data ?? null} />
      <AuthentikKarte gekoppelt={gekoppelt} />
      <Sperrliste />
    </div>
  )
}

// --- Bauteile ------------------------------------------------------------------------------------

function Schalter({
  label,
  hinweis,
  checked,
  disabled,
  onChange,
}: {
  label: string
  hinweis?: ReactNode
  checked: boolean
  disabled?: boolean
  onChange: (wert: boolean) => void
}) {
  return (
    <label className="flex items-start gap-3 text-sm">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500 disabled:opacity-50"
      />
      <span>
        <span className="font-medium text-mist-200">{label}</span>
        {hinweis && <span className="block text-xs text-mist-500">{hinweis}</span>}
      </span>
    </label>
  )
}

function Kopieren({ text }: { text: string }) {
  const { t } = useTranslation()
  const [kopiert, setKopiert] = useState(false)
  return (
    <div className="mt-1 flex items-center gap-2">
      <code
        className="min-w-0 flex-1 break-all rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-xs text-mist-300"
        data-testid="rueckleitadresse"
      >
        {text}
      </code>
      <Button
        type="button"
        variant="ghost"
        onClick={() => {
          void navigator.clipboard?.writeText(text)
          setKopiert(true)
        }}
      >
        {kopiert ? t('anmeldung.copied') : t('anmeldung.copy')}
      </Button>
    </div>
  )
}

function Hinweis({ ton, children, testId }: { ton: 'warn' | 'ok'; children: ReactNode; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className={
        'rounded-xl border px-3 py-2 text-xs leading-relaxed ' +
        (ton === 'warn'
          ? 'border-warn-500/40 bg-warn-500/10 text-warn-500'
          : 'border-ok-500/40 bg-ok-500/10 text-ok-500')
      }
    >
      {children}
    </div>
  )
}

function fehlertext(fehler: unknown, t: (key: string, werte?: Record<string, unknown>) => string): string {
  if (!(fehler instanceof ApiError)) return t('errors.network')
  const schluessel = oidcFormErrorKey(fehler.code) ?? oidcErrorKey(fehler.code)
  return schluessel ? t(schluessel) : fehler.message
}

// --- Karte 1: Anmeldung -------------------------------------------------------------------------

function AnmeldungKarte({ irgendeinerAktiv }: { irgendeinerAktiv: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const einstellungen = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const [adresse, setAdresse] = useState('')
  const gespeichert = einstellungen.data?.public_url ?? ''
  useEffect(() => setAdresse(gespeichert), [gespeichert])
  const [frage, setFrage] = useState<AdressFrage | null>(null)
  const [meldung, setMeldung] = useState<{ ok: boolean; text: string } | null>(null)

  const speichern = useMutation({
    mutationFn: ({ bestaetigt, ...werte }: Partial<AppSettings> & { bestaetigt?: boolean }) =>
      api.put<AppSettings>(`/api/settings${bestaetigt ? '?confirm_address=true' : ''}`, werte),
    onMutate: () => setMeldung(null),
    onSuccess: (daten) => {
      queryClient.setQueryData(['settings'], daten)
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      setFrage(null)
      setMeldung({ ok: true, text: t('settings.saved') })
    },
    onError: (fehler) => {
      const gefragt = adressFrageAus(fehler)
      if (gefragt) {
        setFrage(gefragt)
        return
      }
      setMeldung({ ok: false, text: fehlertext(fehler, t) })
    },
  })

  const passwort = einstellungen.data?.password_login ?? true
  return (
    <Section title={t('oidc.admin.signInTitle')}>
      <Schalter
        label={t('oidc.admin.password')}
        hinweis={t('oidc.admin.passwordHint')}
        checked={passwort}
        // Ohne aktiven Anbieter käme niemand außer dem Betreiber herein: an.
        disabled={(passwort && !irgendeinerAktiv) || speichern.isPending}
        onChange={(wert) => speichern.mutate({ password_login: wert })}
      />
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          speichern.mutate({ public_url: adresse.trim() })
        }}
      >
        <div className="min-w-60 flex-1">
          <Field
            label={t('oidc.admin.address')}
            value={adresse}
            onChange={(event) => setAdresse(event.target.value)}
            placeholder="https://nexview.example.com"
            hint={t('oidc.admin.addressHint')}
          />
        </div>
        <Button type="submit" loading={speichern.isPending}>
          {t('common.save')}
        </Button>
      </form>
      {meldung &&
        (meldung.ok ? <Hinweis ton="ok">{meldung.text}</Hinweis> : <ErrorBanner message={meldung.text} />)}
      <ConfirmDialog
        open={frage !== null}
        title={t('oidc.admin.address')}
        description={frage ? <AdressFrageText frage={frage} /> : null}
        confirmLabel={t('common.save')}
        loading={speichern.isPending}
        onCancel={() => setFrage(null)}
        onConfirm={() => speichern.mutate({ public_url: adresse.trim(), bestaetigt: true })}
      />
    </Section>
  )
}

// --- Karte 2: Anmeldeanbieter -----------------------------------------------------------------

/** Derselbe Aussteller, wie auch immer am Ende geschrieben (`same_issuer` ohne Platzhalter). */
function gleicherAussteller(a: string, b: string): boolean {
  return a.trim().replace(/\/+$/, '') === b.trim().replace(/\/+$/, '')
}

function AnbieterKarte({ liste }: { liste: OidcAnbieter[] | null }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [bearbeiten, setBearbeiten] = useState<OidcAnbieter | 'neu' | null>(null)
  const [entfernen, setEntfernen] = useState<{ eintrag: OidcAnbieter; folgen: OidcFolgen } | null>(null)
  const [gezogen, setGezogen] = useState<number | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const eintraege = liste ?? []
  const nurLesen = eintraege.some((eintrag) => !eintrag.editable)
  const neuLaden = () => queryClient.invalidateQueries({ queryKey: ANBIETER })

  const ordnen = useMutation({
    mutationFn: (ids: number[]) => api.put('/api/oidc/admin/providers/order', { ids }),
    onSuccess: () => void neuLaden(),
    onError: (f) => setFehler(fehlertext(f, t)),
  })
  const folgen = useMutation({
    mutationFn: (eintrag: OidcAnbieter) =>
      api.get<OidcFolgen>(`/api/oidc/admin/providers/${eintrag.id}/impact`),
    onSuccess: (antwort, eintrag) => setEntfernen({ eintrag, folgen: antwort }),
    onError: (f) => setFehler(fehlertext(f, t)),
  })
  const weg = useMutation({
    mutationFn: (eintrag: OidcAnbieter) => api.delete(`/api/oidc/admin/providers/${eintrag.id}`),
    onSuccess: () => {
      setEntfernen(null)
      void neuLaden()
      void queryClient.invalidateQueries({ queryKey: ['oidc-knoepfe'] })
    },
  })

  function verschieben(von: number, nach: number) {
    if (von === nach || nach < 0 || nach >= eintraege.length) return
    const ids = eintraege.map((eintrag) => eintrag.id)
    const [genommen] = ids.splice(von, 1)
    ids.splice(nach, 0, genommen)
    ordnen.mutate(ids)
  }

  return (
    <Section title={t('oidc.admin.providersTitle')}>
      <p className="text-sm text-mist-500">{t('oidc.admin.providersText')}</p>
      {liste !== null && eintraege.length === 0 && (
        <p className="text-sm text-mist-400">{t('oidc.admin.empty')}</p>
      )}
      {eintraege.length > 0 && (
        <ul className="divide-y divide-ink-700 rounded-xl border border-ink-700" data-testid="anbieter-liste">
          {eintraege.map((eintrag, index) => (
            <li
              key={eintrag.id}
              className={'flex flex-wrap items-center gap-2 px-3 py-3 text-sm ' + (gezogen === index ? 'opacity-50' : '')}
              draggable={!nurLesen}
              onDragStart={() => setGezogen(index)}
              onDragEnd={() => setGezogen(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                if (gezogen !== null) verschieben(gezogen, index)
                setGezogen(null)
              }}
            >
              {!nurLesen && eintraege.length > 1 && (
                <button
                  type="button"
                  className="cursor-grab rounded px-1 text-mist-500 hover:text-mist-200"
                  aria-label={t('anmeldung.order', { name: eintrag.label })}
                  onKeyDown={(event) => {
                    if (event.key === 'ArrowUp') verschieben(index, index - 1)
                    if (event.key === 'ArrowDown') verschieben(index, index + 1)
                  }}
                >
                  <span aria-hidden="true">⠿</span>
                </button>
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-mist-100">{eintrag.label}</span>
                <span className="block truncate text-xs text-mist-500">{eintrag.issuer}</span>
              </span>
              <span className="flex flex-wrap items-center gap-1.5 text-xs">
                <span
                  className={
                    'rounded-full px-2 py-0.5 ' +
                    (eintrag.enabled ? 'bg-ok-500/10 text-ok-500' : 'border border-ink-700 text-mist-400')
                  }
                >
                  {eintrag.enabled ? t('oidc.admin.active') : t('oidc.admin.inactive')}
                </span>
                {eintrag.managed === 'authentik' && (
                  <span className="rounded-full border border-accent-500/40 px-2 py-0.5 text-accent-400">
                    {t('oidc.admin.managed')}
                  </span>
                )}
              </span>
              {eintrag.editable && (
                <span className="flex gap-1.5">
                  <Button variant="ghost" onClick={() => setBearbeiten(eintrag)}>
                    {t('oidc.admin.edit')}
                  </Button>
                  <Button
                    variant="ghost"
                    loading={folgen.isPending && folgen.variables?.id === eintrag.id}
                    onClick={() => {
                      setFehler(null)
                      folgen.mutate(eintrag)
                    }}
                  >
                    {t('oidc.admin.remove')}
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {!nurLesen && (
        <div>
          <Button onClick={() => setBearbeiten('neu')}>{t('oidc.admin.add')}</Button>
        </div>
      )}
      {fehler && <ErrorBanner message={fehler} />}
      {bearbeiten !== null && (
        <AnbieterFenster
          eintrag={bearbeiten === 'neu' ? null : bearbeiten}
          onSchliessen={() => setBearbeiten(null)}
          onGespeichert={() => {
            setBearbeiten(null)
            void neuLaden()
            void queryClient.invalidateQueries({ queryKey: ['oidc-knoepfe'] })
          }}
        />
      )}
      <ConfirmDialog
        open={entfernen !== null}
        title={t('oidc.admin.remove')}
        description={
          entfernen
            ? t('oidc.admin.removeConfirm', {
                name: entfernen.eintrag.label,
                count: entfernen.folgen.count,
                only: entfernen.folgen.only,
              })
            : ''
        }
        confirmLabel={t('oidc.admin.remove')}
        loading={weg.isPending}
        fehler={weg.isError ? fehlertext(weg.error, t) : undefined}
        onCancel={() => setEntfernen(null)}
        onConfirm={() => entfernen && weg.mutate(entfernen.eintrag)}
      />
    </Section>
  )
}

const LEER: OidcAnbieterForm = {
  label: '',
  slug: '',
  issuer: '',
  client_id: '',
  client_secret: '',
  scopes: 'openid profile email',
  enabled: true,
  auto_create: false,
  trusts_second_factor: true,
}

type Feldname = 'label' | 'slug' | 'issuer' | 'client_id' | 'client_secret' | 'scopes' | 'id'

/** Das Anbieter-Formular als Fenster, Felder in der Reihenfolge des Bauplans. */
function AnbieterFenster({
  eintrag,
  onSchliessen,
  onGespeichert,
}: {
  eintrag: OidcAnbieter | null
  onSchliessen: () => void
  onGespeichert: () => void
}) {
  const { t } = useTranslation()
  const einstellungen = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const [form, setForm] = useState<OidcAnbieterForm>(
    eintrag
      ? {
          label: eintrag.label,
          issuer: eintrag.issuer,
          client_id: eintrag.client_id,
          client_secret: '',
          scopes: eintrag.scopes,
          enabled: eintrag.enabled,
          auto_create: eintrag.auto_create,
          trusts_second_factor: eintrag.trusts_second_factor,
        }
      : LEER,
  )
  // Der Kurzname folgt dem Namen, bis jemand ihn selbst tippt.
  const [kurznameGetippt, setKurznameGetippt] = useState(false)
  const [mehr, setMehr] = useState(false)
  const [laeuft, setLaeuft] = useState(false)
  const [feldfehler, setFeldfehler] = useState<{ feld: Feldname; text: string } | null>(null)
  const [ausstellerFrage, setAusstellerFrage] = useState<number | null>(null)

  const kurzname = eintrag ? eintrag.slug : kurznameGetippt ? (form.slug ?? '') : slugFromLabel(form.label)
  const basis = (einstellungen.data?.public_url || window.location.origin + BASIS).replace(/\/+$/, '')
  const rueckleitung = eintrag ? eintrag.redirect_uri : `${basis}/api/oidc/${kurzname || '…'}/callback`
  const setzen = (aenderung: Partial<OidcAnbieterForm>) => setForm((jetzt) => ({ ...jetzt, ...aenderung }))

  function fehlerFuer(fehler: unknown): { feld: Feldname; text: string } {
    const feld =
      fehler instanceof ApiError && typeof fehler.data?.field === 'string'
        ? (fehler.data.field as Feldname)
        : 'id'
    return { feld, text: fehlertext(fehler, t) }
  }

  async function speichern() {
    setLaeuft(true)
    setFeldfehler(null)
    try {
      if (eintrag) await api.put(`/api/oidc/admin/providers/${eintrag.id}`, form)
      else await api.post('/api/oidc/admin/providers', { ...form, slug: kurzname })
      onGespeichert()
    } catch (fehler) {
      setFeldfehler(fehlerFuer(fehler))
    } finally {
      setLaeuft(false)
    }
  }

  async function absenden(event: FormEvent) {
    event.preventDefault()
    // Ein anderer Aussteller ist ein anderer Anbieter: erst sagen, wie viele
    // Konten sich neu verknüpfen müssen.
    if (eintrag && eintrag.links > 0 && !gleicherAussteller(eintrag.issuer, form.issuer)) {
      try {
        const antwort = await api.get<OidcFolgen>(
          `/api/oidc/admin/providers/${eintrag.id}/impact?issuer=${encodeURIComponent(form.issuer.trim())}`,
        )
        if (antwort.issuer_change > 0) {
          setAusstellerFrage(antwort.issuer_change)
          return
        }
      } catch (fehler) {
        setFeldfehler(fehlerFuer(fehler))
        return
      }
    }
    await speichern()
  }

  const fehlerAm = (feld: Feldname) =>
    feldfehler?.feld === feld ? (
      <p role="alert" className="-mt-2 text-xs text-bad-500">
        {feldfehler.text}
      </p>
    ) : null

  return (
    <Fenster
      offen
      titel={eintrag ? `${t('oidc.admin.edit')}: ${eintrag.label}` : t('oidc.admin.add')}
      onSchliessen={() => !laeuft && onSchliessen()}
      fuss={
        <>
          <Button variant="ghost" onClick={onSchliessen} disabled={laeuft}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="anbieter-formular" loading={laeuft}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form id="anbieter-formular" className="flex flex-col gap-4" onSubmit={(event) => void absenden(event)}>
        <Field
          label={t('oidc.admin.label')}
          value={form.label}
          onChange={(event) => setzen({ label: event.target.value })}
          autoFocus
        />
        {fehlerAm('label')}
        {eintrag ? (
          <p className="text-xs text-mist-500">
            {t('oidc.admin.slug')}: <code className="font-mono text-mist-300">{eintrag.slug}</code>
          </p>
        ) : (
          <>
            <Field
              label={t('oidc.admin.slug')}
              value={kurzname}
              onChange={(event) => {
                setKurznameGetippt(true)
                setzen({ slug: event.target.value })
              }}
              hint={t('oidc.admin.slugHint')}
            />
            {fehlerAm('slug')}
          </>
        )}
        <Field
          label={t('oidc.admin.issuer')}
          value={form.issuer}
          onChange={(event) => setzen({ issuer: event.target.value })}
          placeholder="https://auth.example.com/application/o/nexview/"
          hint={t('oidc.admin.issuerHint')}
        />
        {fehlerAm('issuer')}
        <Field
          label={t('oidc.admin.clientId')}
          value={form.client_id}
          onChange={(event) => setzen({ client_id: event.target.value })}
        />
        {fehlerAm('client_id')}
        <Field
          label={t('oidc.admin.secret')}
          type="password"
          value={form.client_secret}
          onChange={(event) => setzen({ client_secret: event.target.value })}
          placeholder={eintrag?.has_secret ? t('oidc.admin.secretKept') : ''}
          autoComplete="new-password"
        />
        {fehlerAm('client_secret')}
        <div>
          <p className="text-sm font-medium text-mist-300">{t('oidc.admin.redirect')}</p>
          <Kopieren text={rueckleitung} />
        </div>
        <Schalter label={t('oidc.admin.enabled')} checked={form.enabled} onChange={(enabled) => setzen({ enabled })} />
        <Schalter
          label={t('oidc.admin.autoCreate')}
          hinweis={t('oidc.admin.autoCreateHint')}
          checked={form.auto_create}
          onChange={(auto_create) => setzen({ auto_create })}
        />
        <Schalter
          label={t('oidc.admin.trustsSecondFactor')}
          hinweis={
            <>
              {t('oidc.admin.trustsSecondFactorHint')} {t('anmeldung.noTwoFactor')}
            </>
          }
          checked={form.trusts_second_factor}
          onChange={(trusts_second_factor) => setzen({ trusts_second_factor })}
        />
        <details
          open={mehr || feldfehler?.feld === 'scopes'}
          onToggle={(event) => setMehr((event.target as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer text-xs text-mist-400">{t('anmeldung.more')}</summary>
          <div className="mt-3">
            <Field
              label={t('oidc.admin.scopes')}
              value={form.scopes}
              onChange={(event) => setzen({ scopes: event.target.value })}
            />
            {fehlerAm('scopes')}
          </div>
        </details>
        {feldfehler?.feld === 'id' && <ErrorBanner message={feldfehler.text} />}
      </form>
      <ConfirmDialog
        open={ausstellerFrage !== null}
        title={t('oidc.admin.issuer')}
        description={t('oidc.admin.issuerChangeConfirm', { count: ausstellerFrage ?? 0 })}
        confirmLabel={t('common.save')}
        loading={laeuft}
        onCancel={() => setAusstellerFrage(null)}
        onConfirm={() => {
          setAusstellerFrage(null)
          void speichern()
        }}
      />
    </Fenster>
  )
}

// --- Karte 3: authentik in einem Schritt ------------------------------------------------------

function AuthentikKarte({ gekoppelt }: { gekoppelt: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [werte, setWerte] = useState({ url: '', token: '' })
  const [schritte, setSchritte] = useState<AuthentikErgebnis | null>(null)
  // Der Knopf hielt an, bevor er etwas schrieb: ein anderer Aussteller.
  const [rueckfrage, setRueckfrage] = useState<{ count: number; uebernehmen: boolean } | null>(null)
  // „Nur übernehmen“ hielt an: Die Adresse ändert eine Rückleitadresse.
  const [adressFrage, setAdressFrage] = useState<AdressFrage | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const adresse = useQuery({
    queryKey: ['oidc-authentik-adresse'],
    queryFn: () => api.get<AuthentikAddress>('/api/oidc/authentik/address'),
  })
  const daten = adresse.data ?? null

  // Ohne öffentliche Adresse bietet die Karte die des Browsers an, nie eine,
  // die der Server geraten hat.
  const vorschlag = authentikProposal(window.location.origin, BASIS)
  const fehlt = daten !== null && daten.public_url === '' && !gekoppelt
  const veraltet = daten ? authentikStale(daten) : null

  const auffrischen = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['oidc-authentik-adresse'] }),
      queryClient.invalidateQueries({ queryKey: ANBIETER }),
      queryClient.invalidateQueries({ queryKey: ['settings'] }),
      queryClient.invalidateQueries({ queryKey: ['oidc-knoepfe'] }),
    ])
  }

  const einrichten = useMutation({
    mutationFn: ({ bestaetigt, uebernehmen }: { bestaetigt: boolean; uebernehmen: boolean }) =>
      api.post<AuthentikErgebnis>('/api/oidc/authentik/setup', {
        url: werte.url.trim(),
        token: werte.token,
        confirm_issuer_change: bestaetigt,
        public_url: uebernehmen ? vorschlag : '',
      }),
    onMutate: () => {
      setSchritte(null)
      setRueckfrage(null)
      setFehler(null)
    },
    onSuccess: async (antwort, { uebernehmen }) => {
      // Kein Fehlschlag: Der Knopf wartet auf das Ja des Betreibers, nichts ist
      // geändert. Das Token bleibt für den zweiten Lauf stehen.
      const anzahl = authentikIssuerChange(antwort)
      if (anzahl !== null) {
        setRueckfrage({ count: anzahl, uebernehmen })
        return
      }
      setSchritte(antwort)
      setWerte((jetzt) => ({ ...jetzt, token: '' }))
      await auffrischen()
    },
    onError: (f) => setFehler(fehlertext(f, t)),
  })

  const nurUebernehmen = useMutation({
    mutationFn: (bestaetigt: boolean) =>
      api.post<AuthentikAddress>('/api/oidc/authentik/address', {
        public_url: vorschlag,
        confirm: bestaetigt,
      }),
    onMutate: () => {
      setAdressFrage(null)
      setFehler(null)
    },
    onSuccess: () => void auffrischen(),
    onError: (f) => {
      const gefragt = adressFrageAus(f)
      if (gefragt) {
        setAdressFrage(gefragt)
        return
      }
      if (f instanceof ApiError && f.code === 'public_url_invalid') {
        setFehler(t('oidc.authentik.why.unusable'))
        return
      }
      setFehler(fehlertext(f, t))
    },
  })

  const blueprint = useMutation({
    mutationFn: () => downloadFile('/api/oidc/authentik/blueprint', 'nexview-authentik.yaml'),
    onError: (f) => setFehler(fehlertext(f, t)),
  })

  const beschaeftigt = einrichten.isPending || nurUebernehmen.isPending
  return (
    <Section title={t('oidc.authentik.title')}>
      <p className="text-sm text-mist-500">{t('oidc.authentik.text')}</p>
      {gekoppelt && <p className="text-sm text-warn-500">{t('oidc.authentik.why.coupled')}</p>}
      {fehlt && daten && (
        <Hinweis ton="warn" testId="authentik-ohne-adresse">
          <p>{t('oidc.authentik.noAddress')}</p>
          <p className="mt-1 break-all font-mono text-sm text-mist-100" data-testid="authentik-vorschlag">
            {vorschlag}
          </p>
          <p className="mt-1 break-all">
            {t('oidc.admin.redirect')}: <code className="font-mono">{authentikRedirect(vorschlag, daten)}</code>
          </p>
          {authentikProxyWarning(vorschlag, daten) && (
            <p className="mt-1" data-testid="authentik-proxy-warnung">
              {t('oidc.authentik.proxyWarning', { address: daten.seen })}
            </p>
          )}
        </Hinweis>
      )}
      {veraltet && (
        <Hinweis ton="warn" testId="authentik-veraltet">
          {t('oidc.authentik.stale', { address: veraltet })}
        </Hinweis>
      )}
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (!gekoppelt) einrichten.mutate({ bestaetigt: false, uebernehmen: fehlt })
        }}
      >
        <Field
          label={t('oidc.authentik.url')}
          value={werte.url}
          onChange={(event) => setWerte({ ...werte, url: event.target.value })}
          placeholder="https://auth.example.com"
        />
        <Field
          label={t('oidc.authentik.token')}
          type="password"
          value={werte.token}
          onChange={(event) => setWerte({ ...werte, token: event.target.value })}
          autoComplete="off"
        />
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" loading={einrichten.isPending} disabled={gekoppelt || beschaeftigt}>
            {fehlt ? t('oidc.authentik.adopt') : t('oidc.authentik.run')}
          </Button>
          {fehlt && (
            <Button
              variant="ghost"
              loading={nurUebernehmen.isPending}
              disabled={beschaeftigt}
              onClick={() => nurUebernehmen.mutate(false)}
            >
              {t('oidc.authentik.adoptOnly')}
            </Button>
          )}
          <button
            type="button"
            disabled={gekoppelt}
            onClick={() => blueprint.mutate()}
            className="text-sm text-accent-400 underline-offset-2 hover:underline disabled:text-mist-500 disabled:no-underline"
          >
            {t('oidc.authentik.blueprint')}
          </button>
        </div>
      </form>
      {einrichten.isPending && (
        <p className="text-xs text-mist-400" aria-live="polite">
          {t('oidc.authentik.asking')}
        </p>
      )}
      {schritte && (
        <ol className="flex flex-col gap-1.5 text-sm" data-testid="authentik-schritte">
          {schritte.steps.map((schritt) => {
            const name = authentikStepKey(schritt.key)
            const warum = schritt.ok ? null : authentikReasonKey(schritt.reason)
            return (
              <li key={schritt.key} className="flex gap-2">
                <span className={schritt.ok ? 'text-ok-500' : 'text-bad-500'} aria-hidden="true">
                  {schritt.ok ? '✓' : '✗'}
                </span>
                <span>
                  <span className={schritt.ok ? 'text-mist-200' : 'font-medium text-bad-500'}>
                    {name ? t(name) : schritt.key}
                  </span>
                  {warum && (
                    <span className="block text-xs text-mist-400">
                      {t(warum, { status: schritt.status ?? 0, count: schritte.count ?? 0 })}
                    </span>
                  )}
                </span>
              </li>
            )
          })}
        </ol>
      )}
      {fehler && <ErrorBanner message={fehler} />}
      <ConfirmDialog
        open={rueckfrage !== null}
        title={t('oidc.authentik.title')}
        description={t('oidc.admin.issuerChangeConfirm', { count: rueckfrage?.count ?? 0 })}
        confirmLabel={t('oidc.authentik.run')}
        loading={einrichten.isPending}
        onCancel={() => setRueckfrage(null)}
        onConfirm={() => einrichten.mutate({ bestaetigt: true, uebernehmen: rueckfrage?.uebernehmen ?? false })}
      />
      <ConfirmDialog
        open={adressFrage !== null}
        title={t('oidc.admin.address')}
        description={adressFrage ? <AdressFrageText frage={adressFrage} /> : null}
        confirmLabel={t('common.save')}
        loading={nurUebernehmen.isPending}
        onCancel={() => setAdressFrage(null)}
        onConfirm={() => nurUebernehmen.mutate(true)}
      />
    </Section>
  )
}

// --- Nexviews eigene: die Sperrliste ----------------------------------------------------------

function Sperrliste() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const sperren = useQuery({
    queryKey: ['oidc-sperren'],
    queryFn: () => api.get<OidcSperre[]>('/api/oidc/admin/blocks'),
  })
  const aufheben = useMutation({
    mutationFn: (id: number) => api.delete(`/api/oidc/admin/blocks/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['oidc-sperren'] }),
  })
  const liste = sperren.data ?? []
  if (liste.length === 0) return null
  return (
    <Section title={t('anmeldung.blocksTitle')}>
      <p className="text-sm text-mist-500">{t('anmeldung.blocksIntro')}</p>
      <ul className="divide-y divide-ink-700 rounded-xl border border-ink-700">
        {liste.map((sperre) => (
          <li key={sperre.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-mist-200">{sperre.display || t('anmeldung.blockNoName')}</span>
              <span className="block truncate text-xs text-mist-500">{sperre.issuer}</span>
            </span>
            <Button
              variant="ghost"
              loading={aufheben.isPending && aufheben.variables === sperre.id}
              onClick={() => aufheben.mutate(sperre.id)}
            >
              {t('anmeldung.unblock')}
            </Button>
          </li>
        ))}
      </ul>
    </Section>
  )
}
