/**
 * Das eigene Konto mit den OIDC-Anbietern verknüpfen.
 *
 * Das Geschwister von `MediaServerLink`, mit einem grundsätzlichen
 * Unterschied im Ablauf: Verknüpfen ist hier eine **Reise des ganzen
 * Browsers** – erst holt die Seite die Absprungadresse (mit Sitzung, denn
 * eine Navigation trägt keinen Authorization-Kopf), dann fährt der Browser
 * zum Anbieter und kommt auf diese Seite zurück. Das Ergebnis steht dann als
 * `?oidc=verknuepft` bzw. `?oidc_fehler=...` in der Adresse, nicht in einer
 * API-Antwort – gelesen, angezeigt, aus der Adresse geräumt.
 *
 * Eine Zeile je Anbieter. Eine Verknüpfung zu einem Anbieter, den der
 * Administrator gelöscht hat, steht trotzdem da – sonst gäbe es keinen Weg,
 * sie loszuwerden. Getrennt wird deshalb über die Anbieter-Adresse, nie über
 * das Kürzel.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { OidcAnbieter, User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Button, Card, ErrorBanner } from '../../components/ui'
import i18n from '../../i18n'

/** Ergebnis der Rückkehr aus der Adresse lesen – und sie sofort säubern. */
function rueckkehrAusAdresse(): { ok: boolean; text: string } | null {
  const params = new URLSearchParams(window.location.search)
  const gut = params.get('oidc')
  const code = params.get('oidc_fehler')
  if (!gut && !code) return null
  params.delete('oidc')
  params.delete('oidc_fehler')
  const rest = params.toString()
  window.history.replaceState(
    null,
    '',
    window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash,
  )
  if (code) {
    const schluessel = `errors.byCode.${code}`
    return {
      ok: false,
      text: i18n.exists(schluessel) ? i18n.t(schluessel) : i18n.t('errors.generic'),
    }
  }
  return { ok: true, text: i18n.t('oidc.linked') }
}

export function OidcLinks() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const [ergebnis, setErgebnis] = useState<{ ok: boolean; text: string } | null>(
    rueckkehrAusAdresse,
  )
  const [fehler, setFehler] = useState<string | null>(null)
  /** Für welchen Anbieter läuft gerade der Absprung? */
  const [aktiv, setAktiv] = useState<string | null>(null)

  const { data: anbieter } = useQuery({
    queryKey: ['oidc-anbieter'],
    queryFn: () => api.get<OidcAnbieter[]>('/api/auth/oidc', { auth: false }),
  })

  const verknuepfen = useMutation({
    mutationFn: (slug: string) =>
      api.post<{ url: string }>(`/api/auth/oidc/${encodeURIComponent(slug)}/link/start`, {}),
    onMutate: (slug) => {
      setErgebnis(null)
      setFehler(null)
      setAktiv(slug)
    },
    onSuccess: ({ url }) => {
      // Ab hier übernimmt der Browser; zurück geht es auf diese Seite.
      window.location.href = url
    },
    onError: (caught) => {
      setAktiv(null)
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
    },
  })

  const trennen = useMutation({
    mutationFn: (issuer: string) =>
      api.delete<User>(`/api/auth/oidc/link?issuer=${encodeURIComponent(issuer)}`),
    onMutate: () => {
      setErgebnis(null)
      setFehler(null)
    },
    onSuccess: (aktualisiert) => {
      updateUser(aktualisiert)
      setErgebnis({ ok: true, text: t('oidc.unlinked') })
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  const verknuepft = user?.oidc_links ?? []
  const eingerichtet = anbieter ?? []
  // Wie beim Medienserver: Auch eine Verknüpfung ohne Anbieter-Eintrag gehört
  // in die Liste - sonst gäbe es keinen Weg, sie wieder loszuwerden.
  const fremde = verknuepft.filter(
    (z) => !eingerichtet.some((a) => a.issuer_url === z.issuer),
  )

  if (eingerichtet.length === 0 && verknuepft.length === 0) return null

  // Die letzte Verknüpfung zu lösen sperrt aus, wer weder Passwort noch einen
  // Medienserver-Weg hat. Der Server weist das ohnehin ab; der Hinweis hier
  // erspart den vergeblichen Klick.
  const sperrt =
    verknuepft.length <= 1 &&
    !user?.has_password &&
    (user?.mediaserver_accounts ?? []).length === 0

  return (
    <Card>
      <h2 className="text-lg font-semibold">{t('oidc.profileTitle')}</h2>
      <p className="mt-1.5 text-sm text-mist-500">{t('oidc.profileIntro')}</p>

      <div className="mt-4 flex flex-col gap-3">
        {eingerichtet.map((eintrag) => {
          const zeile = verknuepft.find((z) => z.issuer === eintrag.issuer_url)
          return (
            <AnbieterZeile
              key={eintrag.issuer_url}
              name={eintrag.label}
              display={zeile?.display ?? null}
              verknuepft={!!zeile}
              sperrt={!!zeile && sperrt}
              laeuft={
                (verknuepfen.isPending && aktiv === eintrag.slug) ||
                (trennen.isPending && trennen.variables === eintrag.issuer_url)
              }
              onVerknuepfen={() => verknuepfen.mutate(eintrag.slug)}
              onTrennen={() => trennen.mutate(eintrag.issuer_url)}
            />
          )
        })}

        {fremde.map((zeile) => (
          <AnbieterZeile
            key={zeile.issuer}
            // Der Eintrag des Administrators ist weg - dann ist die Adresse
            // der ehrlichste Name, den es noch gibt.
            name={zeile.issuer.replace(/^https?:\/\//, '')}
            display={zeile.display}
            verknuepft
            weg
            sperrt={sperrt}
            laeuft={trennen.isPending && trennen.variables === zeile.issuer}
            onVerknuepfen={() => {}}
            onTrennen={() => trennen.mutate(zeile.issuer)}
          />
        ))}
      </div>

      {ergebnis && (
        <p
          className={
            'mt-3 rounded-xl border px-4 py-3 text-sm ' +
            (ergebnis.ok
              ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
              : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
          }
        >
          {ergebnis.text}
        </p>
      )}
      {fehler && (
        <div className="mt-3">
          <ErrorBanner message={fehler} />
        </div>
      )}
    </Card>
  )
}

function AnbieterZeile({
  name,
  display,
  verknuepft,
  weg = false,
  sperrt,
  laeuft,
  onVerknuepfen,
  onTrennen,
}: {
  name: string
  display: string | null
  verknuepft: boolean
  weg?: boolean
  sperrt: boolean
  laeuft: boolean
  onVerknuepfen: () => void
  onTrennen: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium text-mist-200">{name}</span>
          {verknuepft ? (
            <span className="text-mist-500">
              {t('oidc.linkedAs')}{' '}
              <span className="text-mist-300">{display ?? t('oidc.linkedPlain')}</span>
            </span>
          ) : (
            <span className="text-mist-600">{t('oidc.notLinked')}</span>
          )}
        </span>

        {verknuepft ? (
          <Button variant="ghost" onClick={onTrennen} loading={laeuft} disabled={sperrt}>
            {t('oidc.unlink')}
          </Button>
        ) : (
          <Button variant="ghost" onClick={onVerknuepfen} loading={laeuft}>
            {t('oidc.link')}
          </Button>
        )}
      </div>

      {weg && <p className="text-xs text-mist-600">{t('oidc.providerGone')}</p>}
      {sperrt && verknuepft && (
        <p className="text-xs text-warn-500">{t('oidc.needsPassword')}</p>
      )}
    </div>
  )
}
