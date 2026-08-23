import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Neuigkeiten } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { hatEintrag, WasNeuFenster } from './WasNeuFenster'

/**
 * „Alles, was neu ist" – der Hinweis nach einem Update.
 *
 * **Nur für Administratoren**: Sie haben das Update eingespielt, sie sollen
 * wissen, was es bringt. Benutzer sehen weder Balken noch Fenster.
 *
 * Der Balken steht oben wie der Plex-Hinweis und bleibt, bis er im Fenster
 * mit „Verstanden, nicht mehr anzeigen" quittiert wird – gespeichert wird
 * dabei die Fassung, nicht ein Haken. Nach dem nächsten Update erscheint er
 * deshalb von selbst wieder. „Schließen" lässt den Balken dagegen stehen.
 *
 * Der Inhalt steckt in `WasNeuFenster`, weil die Über-Seite dasselbe Fenster
 * öffnet: Sonst wären die vorgehaltenen Fassungen nach einem Klick auf
 * „Verstanden" für immer weg.
 */
export function WasNeuBanner() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [offen, setOffen] = useState(false)

  const stand = useQuery({
    queryKey: ['neuigkeiten'],
    queryFn: () => api.get<Neuigkeiten>('/api/about/neuigkeiten'),
    enabled: user?.role === 'admin',
    staleTime: Infinity,
  })

  const quittieren = useMutation({
    mutationFn: () => api.post<Neuigkeiten>('/api/about/neuigkeiten/gesehen', {}),
    onSuccess: (daten) => {
      queryClient.setQueryData(['neuigkeiten'], daten)
      setOffen(false)
    },
  })

  if (user?.role !== 'admin' || !stand.data?.offen) return null

  /* Kein redaktioneller Text zu dieser Fassung, kein Balken.
     Fehlerbehebungen bekommen keinen eigenen Eintrag - sie sammeln sich für
     die „Außerdem behoben"-Liste des nächsten größeren Releases. Ohne diese
     Regel poppte nach jedem Hotfix ein Hinweis auf, hinter dem nichts steht,
     und das Fenster zeigte die Neuerungen der Fassung davor. Vom Nutzer so
     festgelegt.
     Nicht quittiert zu werden ist dabei genau richtig: `changelog_gesehen`
     bleibt auf der letzten Fassung mit Text stehen, also erscheint der Balken
     beim nächsten Release mit Inhalt von selbst wieder. */
  if (!hatEintrag(t, stand.data.version)) return null

  return (
    <>
      <div className="relative z-10 border-b border-accent-500/40 bg-accent-500/10">
        <div className="mx-auto w-full max-w-7xl px-4 py-3 sm:px-6">
          <div className="flex flex-wrap items-center gap-3 text-sm text-accent-400">
            <span>
              <b>{t('whatsNew.title')}</b>{' '}
              {t('whatsNew.hint', { version: stand.data.version })}
            </span>
            <button
              type="button"
              onClick={() => setOffen(true)}
              className="ml-auto rounded-full border border-accent-500/50 px-3 py-1 text-xs font-semibold transition-colors hover:bg-accent-500/15"
            >
              {t('whatsNew.action')}
            </button>
          </div>
        </div>
      </div>

      <WasNeuFenster
        offen={offen}
        version={stand.data.version}
        zuletztGesehen={stand.data.zuletzt_gesehen}
        onSchliessen={() => setOffen(false)}
        onQuittieren={() => quittieren.mutate()}
        quittiertLaeuft={quittieren.isPending}
      />
    </>
  )
}
