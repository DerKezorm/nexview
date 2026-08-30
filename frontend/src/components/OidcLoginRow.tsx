/**
 * „Anmelden über" – die Knöpfe der eingerichteten OIDC-Anbieter.
 *
 * Anders als die Medienserver-Reihe sind das volle Knöpfe mit Beschriftung
 * statt einer Logoreihe: Die Anbieter sind frei benannt („Familien-SSO",
 * „Google"), ein Logo gibt es nicht – und meistens steht hier ohnehin genau
 * einer.
 *
 * ⚠️ **Der Klick ist eine Navigation, kein API-Aufruf.** Der ganze Browser
 * fährt zum Anbieter und kommt über die Rückkehr-Adresse wieder – deshalb
 * `window.location` statt `fetch`, und deshalb braucht die Adresse den
 * Unterpfad-Vorbau (`mitBasis`).
 *
 * Die Liste kommt ohne Anmeldung vom Server; ist sie leer, existiert dieser
 * Block nicht – die Anmeldeseite sieht dann exakt aus wie immer.
 */

import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { OidcAnbieter } from '../api/types'
import { mitBasis } from '../lib/basis'
import { Button } from './ui'

export function OidcLoginRow() {
  const { t } = useTranslation()
  const { data: anbieter } = useQuery({
    queryKey: ['oidc-anbieter'],
    queryFn: () => api.get<OidcAnbieter[]>('/api/auth/oidc', { auth: false }),
    // Die Anmeldeseite kann lange offen stehen; die Knöpfe ändern sich nur,
    // wenn der Administrator etwas ändert. Kein Grund, ständig nachzufragen.
    staleTime: 5 * 60 * 1000,
  })

  if (!anbieter || anbieter.length === 0) return null

  return (
    <div className="mt-6 border-t border-ink-700 pt-5">
      <p className="text-center text-xs uppercase tracking-wider text-mist-600">
        {t('login.oidcHeading')}
      </p>
      <div className="mt-3 flex flex-col gap-2">
        {anbieter.map((eintrag) => (
          <Button
            key={eintrag.slug}
            variant="ghost"
            className="w-full"
            onClick={() => {
              window.location.href = mitBasis(
                `/api/auth/oidc/${encodeURIComponent(eintrag.slug)}/login`,
              )
            }}
          >
            {t('login.oidcWith', { name: eintrag.label })}
          </Button>
        ))}
      </div>
    </div>
  )
}
