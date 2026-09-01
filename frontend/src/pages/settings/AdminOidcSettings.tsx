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
 *
 * ⚠️ **Was eingetragen ist, lässt sich ändern.** Lange schickte diese Seite
 * nur die zwei Schalter, obwohl das Backend längst alles entgegennimmt. Wer
 * sich beim Client-Geheimnis vertippte, hatte genau einen Weg: löschen und neu
 * anlegen – und davor warnt Nexview selbst, weil es Konten aussperren kann,
 * deren einziger Weg hinein dieser Anbieter ist. Ein Tippfehler darf nicht in
 * die gefährlichste Handlung der Seite zwingen.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { AppSettings, OidcAdminEintrag, OidcPruefErgebnis } from '../../api/types'
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

/**
 * Die Rückkehr-Adresse, so wie der Server sie bildet.
 *
 * ⚠️ **Muss mit `_als_admin` im Backend übereinstimmen** (öffentliche Adresse
 * + `/api/auth/oidc/<kürzel>/callback`). Eine hier abweichend gebaute Adresse
 * wäre der teuerste denkbare Anzeigefehler: Der Administrator trägt sie beim
 * Anbieter ein, und schief geht es erst beim ersten Anmeldeversuch – mit einer
 * Fehlermeldung des Anbieters, die auf Nexview nicht hinweist.
 */
function rueckkehrAdresse(oeffentlicheAdresse: string, slug: string): string {
  const basis = oeffentlicheAdresse.trim().replace(/\/+$/, '')
  const kuerzel = slug.trim().toLowerCase()
  if (!basis || !kuerzel) return ''
  return `${basis}/api/auth/oidc/${kuerzel}/callback`
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

/**
 * Die Rückkehr-Adresse zum Kopieren.
 *
 * Eigene Komponente, weil sie an zwei Stellen gebraucht wird – auf der Karte
 * eines eingetragenen Anbieters **und** im Anlege-Formular. Dort ist sie kein
 * Beiwerk: Wer beim Anbieter die Anwendung anlegt, muss sie eintragen, bevor
 * er dort fertig wird. Erst nach dem Speichern in Nexview zu erfahren, wie sie
 * lautet, heißt einmal hin und einmal zurück.
 */
function RueckkehrFeld({ adresse }: { adresse: string }) {
  const { t } = useTranslation()
  const [kopiert, setKopiert] = useState(false)

  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wider text-mist-600">
        {t('adminOidc.redirectUri')}
      </p>
      <div className="mt-1 flex items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-xs text-mist-300">
          {adresse}
        </code>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            void navigator.clipboard?.writeText(adresse)
            setKopiert(true)
          }}
        >
          {kopiert ? t('adminOidc.copied') : t('adminOidc.copy')}
        </Button>
      </div>
      <p className="mt-1 text-xs text-mist-600">{t('adminOidc.redirectUriHint')}</p>
    </div>
  )
}

/** Eine Karte je Anbieter: Schalter, Rückkehr-Adresse, Bearbeiten, Prüfen, Löschen. */
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
  const [bearbeiten, setBearbeiten] = useState(false)
  const [loeschen, setLoeschen] = useState(false)
  const [gefaehrdet, setGefaehrdet] = useState<string[]>([])

  // ⚠️ Abschalten sperrt genauso aus wie Löschen, deshalb antwortet das
  // Backend darauf mit demselben 409. Ohne diese Behandlung sähe der
  // Administrator nur ein rotes Banner ohne Ausweg - und ein Anbieter mit
  // gefährdeten Konten wäre über die Oberfläche gar nicht mehr abschaltbar.
  const [abschaltenBestaetigen, setAbschaltenBestaetigen] = useState<{
    werte: Partial<{ auto_create: boolean; enabled: boolean }>
    namen: string[]
  } | null>(null)

  const aendern = useMutation({
    mutationFn: ({
      werte,
      bestaetigt = false,
    }: {
      werte: Partial<{ auto_create: boolean; enabled: boolean }>
      bestaetigt?: boolean
    }) =>
      api.patch<OidcAdminEintrag>(
        `/api/admin/oidc/${eintrag.id}?bestaetigt=${bestaetigt}`,
        werte,
      ),
    onMutate: () => setFehler(null),
    onSuccess: () => {
      setAbschaltenBestaetigen(null)
      onAenderung()
    },
    onError: (caught, variablen) => {
      if (
        caught instanceof ApiError &&
        caught.code === 'oidc_would_lock_out_others' &&
        !variablen.bestaetigt
      ) {
        const namen = Array.isArray(caught.data?.gefaehrdet)
          ? (caught.data.gefaehrdet as { username: string }[]).map((k) => k.username)
          : []
        setAbschaltenBestaetigen({ werte: variablen.werte, namen })
        return
      }
      setAbschaltenBestaetigen(null)
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
    },
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
          <Button
            variant="ghost"
            onClick={() => {
              setFehler(null)
              setBearbeiten((offen) => !offen)
            }}
          >
            {bearbeiten ? t('common.close') : t('common.edit')}
          </Button>
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
        <div className="mt-3 flex flex-col gap-2">
          <p
            className={
              'rounded-xl border px-4 py-3 text-sm ' +
              (pruefung.ok
                ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
            }
          >
            {pruefung.ok
              ? t('adminOidc.probeOk')
              : t([`errors.byCode.${pruefung.code}`, 'adminOidc.probeFailed'])}
          </p>
          {/* ⚠️ Der Fallstrick, den ein grünes Prüfergebnis gerade nicht
              abdeckt: Der Anbieter antwortet tadellos und meldet die Adresse
              trotzdem als unbestätigt - dann verknüpft Nexview kein
              bestehendes Konto. Der Hinweis steht deshalb neben dem Ergebnis,
              das ihn nicht sieht. */}
          <p className="text-xs text-mist-500">{t('adminOidc.verifiedHint')}</p>
        </div>
      )}

      {bearbeiten && (
        <BearbeitenFormular
          eintrag={eintrag}
          onGespeichert={() => {
            setBearbeiten(false)
            onAenderung()
          }}
          onAbbrechen={() => setBearbeiten(false)}
        />
      )}

      <div className="mt-4 flex flex-col gap-3">
        <label className="flex items-start gap-3 text-sm">
          <input
            type="checkbox"
            checked={eintrag.enabled}
            onChange={(event) => aendern.mutate({ werte: { enabled: event.target.checked } })}
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
            onChange={(event) => aendern.mutate({ werte: { auto_create: event.target.checked } })}
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
          <RueckkehrFeld adresse={eintrag.rueckkehr_adresse} />
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

      <ConfirmDialog
        open={abschaltenBestaetigen !== null}
        title={t('adminOidc.disableTitle', { name: eintrag.label })}
        description={t('adminOidc.disableDescription')}
        warning={t('adminOidc.deleteLockout', {
          namen: (abschaltenBestaetigen?.namen ?? []).join(', '),
        })}
        confirmLabel={t('adminOidc.deleteAnyway')}
        onConfirm={() =>
          abschaltenBestaetigen &&
          aendern.mutate({ werte: abschaltenBestaetigen.werte, bestaetigt: true })
        }
        onCancel={() => setAbschaltenBestaetigen(null)}
        loading={aendern.isPending}
      />
    </div>
  )
}

/**
 * Einen eingetragenen Anbieter ändern.
 *
 * Dieselben Felder wie beim Anlegen, mit zwei Unterschieden:
 *
 * * **Das Kürzel fehlt.** Es steckt in der Rückkehr-Adresse, die beim Anbieter
 *   hinterlegt ist; das Backend nimmt es hier gar nicht erst entgegen.
 * * **Das Geheimnis darf leer bleiben.** Die Seite zeigt es nie an (nur
 *   „gesetzt"), also darf ein unangefasstes Feld es nicht löschen – das
 *   Backend behandelt leer als „behalten".
 *
 * ⚠️ **Die Adresse zu ändern ist keine Umbenennung.** Verknüpfungen hängen an
 * der Anbieter-Adresse; eine neue heißt für Nexview „anderer Anbieter", und
 * alles Bestehende passt nicht mehr dazu. Das Backend schreibt es ins
 * Protokoll – gelesen wird das erst, wenn jemand sucht, warum die
 * Verknüpfungen weg sind. Deshalb steht die Warnung hier, solange sie noch
 * etwas ändern kann: sobald das Feld abweicht, vor dem Speichern.
 *
 * Wird nur eingehängt, solange bearbeitet wird - so setzen sich die Felder
 * beim nächsten Öffnen von selbst auf den frischen Stand zurück.
 */
function BearbeitenFormular({
  eintrag,
  onGespeichert,
  onAbbrechen,
}: {
  eintrag: OidcAdminEintrag
  onGespeichert: () => void
  onAbbrechen: () => void
}) {
  const { t } = useTranslation()
  const [fehler, setFehler] = useState<string | null>(null)
  const [werte, setWerte] = useState({
    label: eintrag.label,
    issuer_url: eintrag.issuer_url,
    client_id: eintrag.client_id,
    client_secret: '',
  })

  // ⚠️ Adresse und Zugangsdaten sperren genauso aus wie ein Löschen - das
  // Backend antwortet darauf mit demselben 409. Ohne Bestätigungsweg wäre das
  // Formular für einen Anbieter mit gefährdeten Konten schlicht tot.
  const [bestaetigen, setBestaetigen] = useState<string[] | null>(null)

  const speichern = useMutation({
    mutationFn: (bestaetigt: boolean = false) =>
      api.patch<OidcAdminEintrag>(
        `/api/admin/oidc/${eintrag.id}?bestaetigt=${bestaetigt}`,
        {
          label: werte.label.trim(),
          issuer_url: werte.issuer_url.trim(),
          client_id: werte.client_id.trim(),
          client_secret: werte.client_secret,
        },
      ),
    onMutate: () => setFehler(null),
    onSuccess: () => {
      setBestaetigen(null)
      onGespeichert()
    },
    onError: (caught, bestaetigt) => {
      if (
        caught instanceof ApiError &&
        caught.code === 'oidc_would_lock_out_others' &&
        !bestaetigt
      ) {
        setBestaetigen(
          Array.isArray(caught.data?.gefaehrdet)
            ? (caught.data.gefaehrdet as { username: string }[]).map((k) => k.username)
            : [],
        )
        return
      }
      setBestaetigen(null)
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
    },
  })

  // Genauso normalisiert wie im Backend (`_normalisiert`): trimmen und den
  // Schrägstrich am Ende weg. Sonst warnte ein bloßes „https://sso.example.com/"
  // vor einem Bruch, den es gar nicht gibt - und eine Warnung, die zu oft
  // grundlos kommt, liest bald niemand mehr.
  const adresseGeaendert = werte.issuer_url.trim().replace(/\/+$/, '') !== eintrag.issuer_url

  function absenden(event: FormEvent) {
    event.preventDefault()
    speichern.mutate(false)
  }

  return (
    <form
      onSubmit={absenden}
      className="mt-4 flex flex-col gap-4 border-t border-ink-700 pt-4"
    >
      <p className="text-xs text-mist-500">{t('adminOidc.editIntro')}</p>

      <Field
        label={t('adminOidc.label')}
        value={werte.label}
        onChange={(e) => setWerte({ ...werte, label: e.target.value })}
        hint={t('adminOidc.labelHint')}
        required
      />
      <Field
        label={t('adminOidc.issuer')}
        value={werte.issuer_url}
        onChange={(e) => setWerte({ ...werte, issuer_url: e.target.value })}
        hint={t('adminOidc.issuerHint')}
        required
      />

      {adresseGeaendert && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {eintrag.verknuepfte > 0
            ? t('adminOidc.editIssuerWarning', { count: eintrag.verknuepfte })
            : t('adminOidc.editIssuerNote')}
        </p>
      )}

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
          placeholder={eintrag.client_secret_vorschau}
          hint={t('adminOidc.clientSecretKeep')}
          autoComplete="off"
        />
      </div>

      {fehler && <ErrorBanner message={fehler} />}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={speichern.isPending}>
          {t('common.save')}
        </Button>
        <Button type="button" variant="ghost" onClick={onAbbrechen}>
          {t('common.cancel')}
        </Button>
      </div>

      <ConfirmDialog
        open={bestaetigen !== null}
        title={t('adminOidc.editLockoutTitle')}
        description={t('adminOidc.editLockoutDescription')}
        warning={t('adminOidc.deleteLockout', { namen: (bestaetigen ?? []).join(', ') })}
        confirmLabel={t('adminOidc.deleteAnyway')}
        onConfirm={() => speichern.mutate(true)}
        onCancel={() => setBestaetigen(null)}
        loading={speichern.isPending}
      />
    </form>
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

  // Nur für die Vorschau der Rückkehr-Adresse. Dieselbe Abfrage wie im Bereich
  // „Adresse"; React Query bedient beide aus demselben Zwischenspeicher.
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const vorschau = rueckkehrAdresse(settings?.public_url ?? '', werte.slug)

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

          {/* ⚠️ **Vor dem Speichern, nicht danach.** Diese Adresse wird beim
              Anbieter gebraucht, und zwar während man dort die Anwendung
              anlegt - also bevor es hier überhaupt etwas zu speichern gibt.
              Grafana und Gitea zeigen sie aus demselben Grund schon im
              Formular. Sie hängt allein am Kürzel; sobald das steht, steht
              sie. */}
          {vorschau ? (
            <RueckkehrFeld adresse={vorschau} />
          ) : (
            <p className="text-xs text-mist-600">{t('adminOidc.redirectUriPending')}</p>
          )}

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

          {/* Der zweite Fallstrick neben der Auto-Anlage - und der leisere: Er
              kostet keine Konten, er verweigert nur die Verknüpfung, und zwar
              wortlos für den, der sich anmeldet. */}
          <p className="text-xs text-mist-500">{t('adminOidc.verifiedHint')}</p>

        <div>
          <Button type="submit" loading={anlegen.isPending}>
            {t('adminOidc.add')}
          </Button>
        </div>
      </form>
    </Section>
  )
}
