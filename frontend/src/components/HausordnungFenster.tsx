/**
 * Die Hausordnung lesen – und abhaken.
 *
 * ⚠️ **Wird nachgeladen.** Der Knopf unten rechts steht auf jeder Seite und
 * wiegt ein paar Zeilen; dieses Fenster samt Anzeiger und Auszeichnung kommt
 * erst beim Klick. Wer nie draufdrückt, lädt es nie – und das ist die
 * Mehrheit, denn nach dem Abhaken ist der Knopf weg.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import type { HausordnungOeffentlich } from '../api/types'
import { Fenster } from './Fenster'
import { Hausordnungstext } from './Hausordnungstext'
import { Button, ErrorBanner, Spinner } from './ui'

export function HausordnungFenster({
  offen,
  onSchliessen,
}: {
  offen: boolean
  onSchliessen: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const stand = useQuery({
    queryKey: ['hausordnung'],
    queryFn: () => api.get<HausordnungOeffentlich>('/api/hausordnung'),
    enabled: offen,
  })

  const entscheiden = useMutation({
    mutationFn: (akzeptiert: boolean) =>
      api.post('/api/hausordnung/entscheidung', { akzeptiert }),
    onSuccess: () => {
      // Der Knopf hängt an der Konfiguration – ohne dieses Verwerfen bliebe er
      // bis zum nächsten Neuladen stehen.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      void queryClient.invalidateQueries({ queryKey: ['hausordnung'] })
      onSchliessen()
    },
  })

  const daten = stand.data
  const entschieden = daten ? daten.gelesen === daten.fassung : false

  return (
    <Fenster
      offen={offen}
      titel={daten?.titel || t('hausordnung.titel')}
      onSchliessen={onSchliessen}
      fuss={
        // Der Knopf erscheint nur, wenn der Betreiber das Abhaken erlaubt hat -
        // sonst schließt das Fenster wie jedes andere.
        daten?.quittierbar && !entschieden ? (
          <>
            {/* ⚠️ **Ablehnen steht links und ist der leisere Knopf.** Es hat
                keine technische Folge - der Betreiber sieht es und entscheidet
                selbst. Genau deshalb darf es nicht wie eine Drohung aussehen,
                aber auch nicht versteckt sein: Wer nicht zustimmen will, soll
                das sagen können, statt das Fenster wegzuklicken. */}
            <Button
              variant="ghost"
              onClick={() => entscheiden.mutate(false)}
              disabled={entscheiden.isPending}
            >
              {t('hausordnung.ablehnen')}
            </Button>
            <Button onClick={() => entscheiden.mutate(true)} loading={entscheiden.isPending}>
              {t('hausordnung.akzeptieren')}
            </Button>
          </>
        ) : undefined
      }
    >
      {stand.isPending ? (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      ) : stand.isError || !daten ? (
        <ErrorBanner message={t('hausordnung.nichtGeladen')} />
      ) : (
        <Hausordnungstext text={daten.inhalt} />
      )}
    </Fenster>
  )
}
