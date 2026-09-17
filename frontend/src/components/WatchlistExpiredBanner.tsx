import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import type { User } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { useMediaServerChallenge } from '../lib/useMediaServerChallenge'
import { MediaServerPrompt } from './MediaServerPrompt'
import { PasswortVerknuepfen } from '../pages/profile/MediaServerLink'
import { useConfig } from '../hooks/useConfig'
import { providerName } from '../lib/mediaserver'

/**
 * Hinweis für die Person, deren persönlicher Plex-Zugang abgelaufen ist.
 *
 * **Nur für sie selbst** - niemand sonst kann es beheben, und es ist eine
 * Angabe über ihr Konto. Der Administrator sieht es im Protokoll und in der
 * Glocke, aber nicht als Balken über der ganzen Anwendung.
 *
 * Nicht zu verwechseln mit dem Server-Zugang des Administrators
 * (``mediaserver_token`` in den Einstellungen). Hier geht es um das
 * persönliche Token, mit dem die eigene Merkliste gelesen wird.
 *
 * Die Anmeldung läuft **hier** ab und führt nicht auf die Profilseite: Der
 * Ablauf zeigt einen Code und einen Link, und beides muss sichtbar sein -
 * Browser blockieren das Popup häufig, am Handy praktisch immer. Ein Knopf,
 * der woanders hinführt, verlöre den halben Weg.
 */
export function WatchlistExpiredBanner() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const { data: config } = useConfig()
  const queryClient = useQueryClient()
  // Beim Administrator erneuert das Backend den Serverzugang gleich mit - die
  // Kacheln in den Einstellungen sollen das ohne Neuladen zeigen.
  const serverzugangNeuLesen = () =>
    void queryClient.invalidateQueries({ queryKey: ['mediaserver-zugang'] })
  /** Für welchen Anbieter ist das Passwortformular offen? */
  const [formular, setFormular] = useState<string | null>(null)

  const verbinden = useMediaServerChallenge({
    startPfad: '/api/watchlist/connect/start',
    abfragePfad: '/api/watchlist/connect/poll',
    onFertig: async () => {
      verbinden.abbrechen()
      serverzugangNeuLesen()
      // Den eigenen Stand neu holen, damit der Balken sofort verschwindet -
      // und nicht erst nach dem nächsten stündlichen Abgleich.
      updateUser(await api.get<User>('/api/auth/me'))
    },
  })

  // Wer nie verbunden war, bekommt hier nichts zu sehen. Der Wert ist bereits
  // so gebaut, dass er ohne Token nie wahr wird - die Prüfung steht trotzdem
  // hier, weil sie an dieser Stelle die eigentliche Aussage ist.
  if (!user?.watchlist_token_invalid) return null

  // ⚠️ **Welcher Server, steht jetzt dabei.** Bis 0.34.0 sagte der Balken nur
  // „Dein Medienserver-Zugang ist abgelaufen" und startete immer die
  // Plex-Anmeldung - auch als Emby den Zugang abgelehnt hatte. Gemeldet
  // genau so: Klick auf den Knopf, und es öffnet sich Plex.
  const abgelaufen = (user.mediaserver_accounts ?? [])
    .filter((konto) => konto.token_abgelehnt)
    .map((konto) => konto.provider)
  const mitPasswort = config?.mediaserver_password_login ?? []

  return (
    <div className="relative z-10 border-b border-bad-500/40 bg-bad-500/10">
      <div className="mx-auto w-full max-w-7xl px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center gap-3 text-sm text-bad-500">
          <span>
            {abgelaufen.length
              ? t('watchlistExpired.textFor', {
                  names: abgelaufen.map(providerName).join(', '),
                })
              : t('watchlistExpired.text')}
          </span>
          <span className="ml-auto flex flex-wrap gap-2">
            {(abgelaufen.length ? abgelaufen : ['plex']).map((anbieter) => (
              <button
                key={anbieter}
                type="button"
                onClick={() =>
                  mitPasswort.includes(anbieter)
                    ? setFormular(formular === anbieter ? null : anbieter)
                    : void verbinden.starten()
                }
                disabled={verbinden.laeuft}
                className="rounded-full border border-bad-500/50 px-3 py-1 text-xs font-semibold transition-colors hover:bg-bad-500/15 disabled:opacity-60"
              >
                {verbinden.laeuft && !mitPasswort.includes(anbieter)
                  ? t('watchlistExpired.running')
                  : abgelaufen.length
                    ? t('watchlistExpired.actionFor', { name: providerName(anbieter) })
                    : t('watchlistExpired.action')}
              </button>
            ))}
          </span>
        </div>

        {formular && (
          <div className="mt-3 max-w-md">
            <PasswortVerknuepfen
              provider={formular}
              name={providerName(formular)}
              onFertig={(aktualisiert) => {
                setFormular(null)
                updateUser(aktualisiert)
                serverzugangNeuLesen()
              }}
              onAbbrechen={() => setFormular(null)}
            />
          </div>
        )}

        {verbinden.fehler && (
          <p className="mt-2 text-sm text-bad-500">{verbinden.fehler}</p>
        )}

        {verbinden.start && (
          <MediaServerPrompt start={verbinden.start} onAbbrechen={verbinden.abbrechen} />
        )}
      </div>
    </div>
  )
}
