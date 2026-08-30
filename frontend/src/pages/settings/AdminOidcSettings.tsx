/**
 * Anmeldung über fremde Anbieter (OIDC) – die Verwaltung.
 *
 * Der Unterschied zur Konfigurationsdatei, die andere dafür anbieten: Hier
 * steht die Rückkehr-Adresse zum Kopieren, ein Prüf-Knopf sagt sofort, ob
 * unter der Adresse ein Anbieter antwortet, und das Löschen warnt mit Namen,
 * wen es aussperren würde.
 *
 * ⚠️ **Der Auto-Anlage-Schalter trägt seine Warnung im Text.** Bei einem
 * selbst gehosteten Anbieter ist er der Normalfall – wer dort ein Konto hat,
 * wurde vom selben Administrator angelegt. Bei Google oder Microsoft hat
 * *jeder Mensch* ein Konto; derselbe Schalter heißt dort „jeder bekommt ein
 * Nexview-Konto". Die Oberfläche sagt das an der Stelle, an der es kippen
 * kann, nicht in einer Doku, die niemand offen hat.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { OidcAdminEintrag, OidcPruefErgebnis } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Button, ErrorBanner, Field, Section } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'

const LEER = {
  slug: '',
  label: '',
  issuer_url: '',
  client_id: '',
  client_secret: '',
  auto_create: false,
}

export function AdminOidcSettings() {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const queryClient = useQueryClient()
  const [fehler, setFehler] = useState<string | null>(null)

  const { data: eintraege } = useQuery({
    queryKey: ['admin-oidc'],
    queryFn: () => api.get<OidcAdminEintrag[]>('/api/admin/oidc'),
  })

  const neuLaden = () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-oidc'] })
    // Die Anmeldeseite und das Profil zeichnen ihre Knöpfe aus derselben
    // Liste - nach jeder Änderung hier soll dort nicht der alte Stand stehen.
    void queryClient.invalidateQueries({ queryKey: ['oidc-anbieter'] })
  }

  return (
    <div className="flex flex-col gap-6">
      <Section title={t('adminOidc.title')}>
        <p className="text-sm text-mist-500">{t('adminOidc.intro')}</p>

        {/* Ohne öffentliche Adresse lässt der Server nichts anlegen - die
            Rückkehr-Adresse entstünde aus dem Nichts. Der Hinweis steht hier,
            damit niemand das Formular ausfüllt, um es dann abgewiesen zu
            bekommen. */}
        {config && !config.public_url_set && (
          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
            {t('adminOidc.needsPublicUrl')}
          </p>
        )}

        {(eintraege ?? []).map((eintrag) => (
          <AnbieterKarte key={eintrag.id} eintrag={eintrag} onAenderung={neuLaden} />
        ))}
        {eintraege && eintraege.length === 0 && config?.public_url_set && (
          <p className="text-sm text-mist-500">{t('adminOidc.empty')}</p>
        )}
      </Section>

      {config?.public_url_set && (
        <NeuerAnbieter
          onAngelegt={() => {
            setFehler(null)
            neuLaden()
          }}
          onFehler={setFehler}
        />
      )}

      <Sperrliste />
      {fehler && <ErrorBanner message={fehler} />}
    </div>
  )
}

/**
 * Gesperrte Identitäten – entstanden still beim Löschen eines Kontos.
 *
 * Die Karte erscheint nur, wenn es Einträge gibt: Eine dauerhaft sichtbare
 * leere Sperrliste wäre eine Einstellung ohne Gegenstand. Ohne diese Ansicht
 * wäre eine Sperre für immer.
 */
function Sperrliste() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: eintraege } = useQuery({
    queryKey: ['admin-oidc-blocks'],
    queryFn: () =>
      api.get<{ id: number; issuer: string; display: string | null }[]>(
        '/api/admin/oidc/blocks',
      ),
  })

  const aufheben = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/admin/oidc/blocks/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['admin-oidc-blocks'] }),
  })

  if (!eintraege || eintraege.length === 0) return null

  return (
    <Section title={t('adminOidc.blocksTitle')}>
      <p className="text-sm text-mist-500">{t('adminOidc.blocksIntro')}</p>
      <div className="flex flex-col gap-2">
        {eintraege.map((eintrag) => (
          <div
            key={eintrag.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3"
          >
            <span className="min-w-0 text-sm">
              <span className="font-medium text-mist-200">
                {eintrag.display ?? t('adminOidc.blockNoName')}
              </span>
              <span className="ml-2 break-all text-xs text-mist-600">{eintrag.issuer}</span>
            </span>
            <Button
              variant="ghost"
              onClick={() => aufheben.mutate(eintrag.id)}
              loading={aufheben.isPending && aufheben.variables === eintrag.id}
            >
              {t('adminOidc.unblock')}
            </Button>
          </div>
        ))}
      </div>
    </Section>
  )
}

/** Eine Karte je Anbieter: Schalter, Rückkehr-Adresse, Prüfen, Löschen. */
function AnbieterKarte({
  eintrag,
  onAenderung,
}: {
  eintrag: OidcAdminEintrag
  onAenderung: () => void
}) {
  const { t } = useTranslation()
  const [fehler, setFehler] = useState<string | null>(null)
  const [pruefung, setPruefung] = useState<OidcPruefErgebnis | null>(null)
  const [kopiert, setKopiert] = useState(false)
  const [loeschen, setLoeschen] = useState(false)
  const [gefaehrdet, setGefaehrdet] = useState<string[]>([])

  const aendern = useMutation({
    mutationFn: (werte: Partial<{ auto_create: boolean; enabled: boolean }>) =>
      api.patch<OidcAdminEintrag>(`/api/admin/oidc/${eintrag.id}`, werte),
    onMutate: () => setFehler(null),
    onSuccess: onAenderung,
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  const pruefen = useMutation({
    mutationFn: () =>
      api.post<OidcPruefErgebnis>(`/api/admin/oidc/${eintrag.id}/pruefen`, {}),
    onMutate: () => {
      setFehler(null)
      setPruefung(null)
    },
    onSuccess: setPruefung,
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  const entfernen = useMutation({
    mutationFn: (bestaetigt: boolean) =>
      api.delete<void>(`/api/admin/oidc/${eintrag.id}?bestaetigt=${bestaetigt}`),
    onSuccess: () => {
      setLoeschen(false)
      onAenderung()
    },
    onError: (caught) => {
      // Der 409 mit Namen ist keine Störung, sondern die eigentliche
      // Auskunft: Der Dialog zeigt die Betroffenen und fragt erneut.
      if (caught instanceof ApiError && caught.code === 'oidc_would_lock_out_others') {
        const namen = Array.isArray(caught.data?.gefaehrdet)
          ? (caught.data.gefaehrdet as { username: string }[]).map((k) => k.username)
          : []
        setGefaehrdet(namen)
        return
      }
      setLoeschen(false)
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
    },
  })

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-mist-100">
            {eintrag.label}
            <span className="ml-2 text-xs font-normal text-mist-600">{eintrag.slug}</span>
          </h3>
          <p className="mt-0.5 break-all text-xs text-mist-500">{eintrag.issuer_url}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={() => pruefen.mutate()} loading={pruefen.isPending}>
            {t('adminOidc.probe')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setGefaehrdet([])
              setLoeschen(true)
            }}
          >
            {t('common.delete')}
          </Button>
        </div>
      </div>

      {pruefung && (
        <p
          className={
            'mt-3 rounded-xl border px-4 py-3 text-sm ' +
            (pruefung.ok
              ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
              : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
          }
        >
          {pruefung.ok
            ? t('adminOidc.probeOk')
            : t([`errors.byCode.${pruefung.code}`, 'adminOidc.probeFailed'])}
        </p>
      )}

      <div className="mt-4 flex flex-col gap-3">
        <label className="flex items-start gap-3 text-sm">
          <input
            type="checkbox"
            checked={eintrag.enabled}
            onChange={(event) => aendern.mutate({ enabled: event.target.checked })}
            className="mt-0.5"
          />
          <span>
            <span className="font-medium text-mist-200">{t('adminOidc.enabled')}</span>
            <span className="block text-xs text-mist-500">{t('adminOidc.enabledHint')}</span>
          </span>
        </label>
        <label className="flex items-start gap-3 text-sm">
          <input
            type="checkbox"
            checked={eintrag.auto_create}
            onChange={(event) => aendern.mutate({ auto_create: event.target.checked })}
            className="mt-0.5"
          />
          <span>
            <span className="font-medium text-mist-200">{t('adminOidc.autoCreate')}</span>
            {/* Die Warnung steht am Schalter, nicht in einer Doku: Bei einem
                Welt-Anbieter heißt „an", dass jeder mit einem Konto dieses
                Dienstes ein Nexview-Konto bekommt. */}
            <span className="block text-xs text-warn-500">
              {t('adminOidc.autoCreateWarning')}
            </span>
          </span>
        </label>
      </div>

      {eintrag.rueckkehr_adresse && (
        <div className="mt-4">
          <p className="text-xs font-medium uppercase tracking-wider text-mist-600">
            {t('adminOidc.redirectUri')}
          </p>
          <div className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-xs text-mist-300">
              {eintrag.rueckkehr_adresse}
            </code>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                void navigator.clipboard?.writeText(eintrag.rueckkehr_adresse)
                setKopiert(true)
              }}
            >
              {kopiert ? t('adminOidc.copied') : t('adminOidc.copy')}
            </Button>
          </div>
          <p className="mt-1 text-xs text-mist-600">{t('adminOidc.redirectUriHint')}</p>
        </div>
      )}

      <p className="mt-3 text-xs text-mist-600">
        {t('adminOidc.linkedCount', { count: eintrag.verknuepfte })}
      </p>

      {fehler && (
        <div className="mt-3">
          <ErrorBanner message={fehler} />
        </div>
      )}

      <ConfirmDialog
        open={loeschen}
        title={t('adminOidc.deleteTitle', { name: eintrag.label })}
        description={t('adminOidc.deleteDescription')}
        warning={
          gefaehrdet.length > 0
            ? t('adminOidc.deleteLockout', { namen: gefaehrdet.join(', ') })
            : undefined
        }
        confirmLabel={
          gefaehrdet.length > 0 ? t('adminOidc.deleteAnyway') : t('common.delete')
        }
        onConfirm={() => entfernen.mutate(gefaehrdet.length > 0)}
        onCancel={() => setLoeschen(false)}
        loading={entfernen.isPending}
      />
    </div>
  )
}

/** Das Anlege-Formular - bewusst ohne Voreinstellungs-Zauber in v1 hinaus
    ueber Google/Microsoft: siehe `VOREINSTELLUNGEN`. */
const VOREINSTELLUNGEN: { name: string; issuer: string }[] = [
  { name: 'Google', issuer: 'https://accounts.google.com' },
  // Der Platzhalter bleibt sichtbar stehen - die Verzeichnis-Kennung kennt
  // nur der Admin, und eine halbe Adresse faellt beim Pruef-Knopf sofort auf.
  { name: 'Microsoft', issuer: 'https://login.microsoftonline.com/{tenant}/v2.0' },
]

function NeuerAnbieter({
  onAngelegt,
  onFehler,
}: {
  onAngelegt: () => void
  onFehler: (text: string | null) => void
}) {
  const { t } = useTranslation()
  const [werte, setWerte] = useState(LEER)

  const anlegen = useMutation({
    mutationFn: () =>
      api.post<OidcAdminEintrag>('/api/admin/oidc', {
        ...werte,
        slug: werte.slug.trim().toLowerCase(),
      }),
    onMutate: () => onFehler(null),
    onSuccess: () => {
      setWerte(LEER)
      onAngelegt()
    },
    onError: (caught) =>
      onFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    anlegen.mutate()
  }

  return (
    <Section title={t('adminOidc.addTitle')}>
      <p className="text-sm text-mist-500">{t('adminOidc.addIntro')}</p>
      <form onSubmit={absenden} className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs uppercase tracking-wider text-mist-600">
              {t('adminOidc.presets')}
            </span>
            {VOREINSTELLUNGEN.map((vorlage) => (
              <Button
                key={vorlage.name}
                type="button"
                variant="ghost"
                onClick={() =>
                  setWerte((alt) => ({
                    ...alt,
                    label: alt.label || vorlage.name,
                    slug: alt.slug || vorlage.name.toLowerCase(),
                    issuer_url: vorlage.issuer,
                  }))
                }
              >
                {vorlage.name}
              </Button>
            ))}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label={t('adminOidc.label')}
              value={werte.label}
              onChange={(e) => setWerte({ ...werte, label: e.target.value })}
              hint={t('adminOidc.labelHint')}
              required
            />
            <Field
              label={t('adminOidc.slug')}
              value={werte.slug}
              onChange={(e) => setWerte({ ...werte, slug: e.target.value })}
              hint={t('adminOidc.slugHint')}
              required
            />
          </div>
          <Field
            label={t('adminOidc.issuer')}
            value={werte.issuer_url}
            onChange={(e) => setWerte({ ...werte, issuer_url: e.target.value })}
            hint={t('adminOidc.issuerHint')}
            placeholder="https://sso.example.com"
            required
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label={t('adminOidc.clientId')}
              value={werte.client_id}
              onChange={(e) => setWerte({ ...werte, client_id: e.target.value })}
              autoComplete="off"
              required
            />
            <Field
              label={t('adminOidc.clientSecret')}
              type="password"
              value={werte.client_secret}
              onChange={(e) => setWerte({ ...werte, client_secret: e.target.value })}
              autoComplete="off"
              required
            />
          </div>

          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              checked={werte.auto_create}
              onChange={(e) => setWerte({ ...werte, auto_create: e.target.checked })}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-mist-200">{t('adminOidc.autoCreate')}</span>
              <span className="block text-xs text-warn-500">
                {t('adminOidc.autoCreateWarning')}
              </span>
            </span>
          </label>

        <div>
          <Button type="submit" loading={anlegen.isPending}>
            {t('adminOidc.add')}
          </Button>
        </div>
      </form>
    </Section>
  )
}
