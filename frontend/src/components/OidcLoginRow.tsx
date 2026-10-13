/**
 * Die Knöpfe der eingerichteten Anmelde-Anbieter – auf der Anmeldeseite und,
 * mit `invite`, auf der Einladungsseite.
 *
 * Aufbau und Wörter aus dem Bauplan „Anmeldung mit OIDC und authentik“ (04):
 * eine Trennlinie „oder“, darunter je aktivem Anbieter ein Knopf „Mit {{name}}
 * anmelden“ (Einladung: „Weiter mit {{name}}“) in der Reihenfolge der Liste.
 *
 * ⚠️ **Der Klick ist eine Navigation, kein API-Aufruf.** Der ganze Browser
 * fährt zum Anbieter und kommt über die Rückleitadresse wieder – deshalb
 * `window.location` statt `fetch`, und deshalb braucht die Adresse den
 * Unterpfad-Vorbau (`mitBasis`). Die Einladung reist als Schlüssel im
 * Anlauf-Cookie zum Anbieter und zurück (`?invite=` am Start), nie über die
 * Mailadresse.
 *
 * Die Liste kommt ohne Anmeldung vom Server; ist sie leer, gibt es diesen
 * Block nicht.
 */

import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { abmeldungAbwarten, api } from '../api/client'
import type { OidcKnopf } from '../api/types'
import { mitBasis } from '../lib/basis'
import { Button } from './ui'

/** Die aktiven Anbieter – dieselbe Abfrage für Anmelde-, Einladungs- und Kontoseite. */
function useOidcKnoepfe() {
  return useQuery({
    queryKey: ['oidc-knoepfe'],
    queryFn: () => api.get<OidcKnopf[]>('/api/oidc/providers', { auth: false }),
    // Die Seite kann lange offen stehen; die Knöpfe ändern sich nur, wenn der
    // Betreiber etwas ändert.
    staleTime: 5 * 60 * 1000,
  })
}

/** Wohin der Knopf führt: der Start beim Anbieter, mit Einladung. */
function startAdresse(slug: string, invite?: string): string {
  const pfad = `/api/oidc/${encodeURIComponent(slug)}/start`
  return mitBasis(invite ? `${pfad}?invite=${encodeURIComponent(invite)}` : pfad)
}

export function OidcLoginRow({
  invite,
  ohneTrennlinie = false,
}: {
  /** Der Schlüssel der Einladung: dann heißen die Knöpfe „Weiter mit …“. */
  invite?: string
  /** Ohne Passwortformular darüber braucht es kein „oder“. */
  ohneTrennlinie?: boolean
}) {
  const { t } = useTranslation()
  const { data: anbieter } = useOidcKnoepfe()

  if (!Array.isArray(anbieter) || anbieter.length === 0) return null

  return (
    <div className="mt-6" data-testid="oidc-knoepfe">
      {!ohneTrennlinie && (
        <div className="flex items-center gap-3 text-xs uppercase tracking-wider text-mist-600">
          <span className="h-px flex-1 bg-ink-700" aria-hidden="true" />
          <span>{t('oidc.login.or')}</span>
          <span className="h-px flex-1 bg-ink-700" aria-hidden="true" />
        </div>
      )}
      <div className="mt-4 flex flex-col gap-2">
        {anbieter.map((eintrag) => (
          <Button
            key={eintrag.slug}
            variant="ghost"
            className="w-full"
            onClick={async () => {
              // Wer sich eben abgemeldet hat: erst dessen Antwort, sonst löscht
              // sie das Cookie, das der Rückweg vom Anbieter setzt.
              await abmeldungAbwarten()
              window.location.href = startAdresse(eintrag.slug, invite)
            }}
          >
            {invite
              ? t('oidc.invite.button', { name: eintrag.label })
              : t('oidc.login.button', { name: eintrag.label })}
          </Button>
        ))}
      </div>
    </div>
  )
}
