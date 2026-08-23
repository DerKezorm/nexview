import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { Fenster } from './Fenster'
import { Button } from './ui'

type Neuigkeiten = {
  version: string
  offen: boolean
}

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

      <Fenster
        offen={offen}
        titel={t('whatsNew.title')}
        unterzeile={t('whatsNew.subtitle', { version: stand.data.version })}
        onSchliessen={() => setOffen(false)}
        fuss={
          <>
            <Button variant="ghost" onClick={() => setOffen(false)}>
              {t('common.close')}
            </Button>
            <Button
              onClick={() => quittieren.mutate()}
              loading={quittieren.isPending}
            >
              {t('whatsNew.dismiss')}
            </Button>
          </>
        }
      >
        {/* Platzhalter – der echte Inhalt wird getrennt festgelegt. */}
        <div className="flex flex-col gap-3 text-sm leading-relaxed text-mist-300">
          <p>
            Lorem ipsum dolor sit amet, consetetur sadipscing elitr, sed diam
            nonumy eirmod tempor invidunt ut labore et dolore magna aliquyam
            erat, sed diam voluptua.
          </p>
          <p>
            At vero eos et accusam et justo duo dolores et ea rebum. Stet clita
            kasd gubergren, no sea takimata sanctus est Lorem ipsum dolor sit
            amet.
          </p>
          <p>
            Duis autem vel eum iriure dolor in hendrerit in vulputate velit
            esse molestie consequat, vel illum dolore eu feugiat nulla
            facilisis.
          </p>
        </div>
      </Fenster>
    </>
  )
}
