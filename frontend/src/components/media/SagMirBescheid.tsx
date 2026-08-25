import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { MediaType } from '../../api/types'
import { Fenster } from '../Fenster'
import { Button, ErrorBanner } from '../ui'

/**
 * „Sag mir Bescheid“ - auf einen Titel warten, ohne ihn anzufragen.
 *
 * Der Fall, den es bisher nicht gab: Ein Titel ist schon von jemand anderem
 * angefragt. Anfragen lässt er sich damit nicht mehr, und danach hörte der
 * Zweite nie wieder etwas davon.
 *
 * **Ein Fenster statt einer Zeile unter dem Knopf.** Was die Funktion tut, ist
 * nicht selbsterklärend - „Sag mir Bescheid“ allein sagt nicht, worüber und
 * wie oft. Als zweite Zeile neben dem Knopf brach die Erklärung aber die
 * Knopfreihe um und stand dort dauerhaft, obwohl man sie genau einmal liest.
 * Im Fenster steht sie im richtigen Augenblick: wenn jemand gerade
 * entscheidet.
 *
 * **Nur beim Einschalten.** Das Beenden passiert sofort - es ist harmlos und
 * jederzeit umkehrbar, und eine Rückfrage dafür wäre Zeremonie.
 *
 * Film und Serie stehen an verschiedenen Stellen der Detailseite, und der
 * Satz im Fenster unterscheidet sich auch: Ein Film wird einmal gemeldet, eine
 * Serie dauerhaft verfolgt.
 */
export function SagMirBescheid({
  mediaType,
  tmdbId,
  title,
  posterUrl,
  aktiv,
}: {
  mediaType: MediaType
  tmdbId: number
  title: string
  posterUrl?: string | null
  /** Ist der Titel schon vorgemerkt? Kommt vom Server (`watching`). */
  aktiv: boolean
}) {
  const { t } = useTranslation()
  // Der Server ist die Wahrheit, aber die Antwort kommt erst beim nächsten
  // Laden der Seite. Bis dahin zählt, was gerade geklickt wurde - sonst
  // spränge der Knopf nach dem Klick zurück.
  const [gemerkt, setGemerkt] = useState(aktiv)
  const [offen, setOffen] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)

  const istSerie = mediaType === 'tv'

  const umschalten = useMutation({
    mutationFn: () =>
      gemerkt
        ? api.delete(`/api/watch/${mediaType}/${tmdbId}`)
        : api.put(`/api/watch/${mediaType}/${tmdbId}`, {
            title,
            poster_url: posterUrl ?? null,
          }),
    onMutate: () => setFehler(null),
    onSuccess: () => {
      setGemerkt((vorher) => !vorher)
      setOffen(false)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        onClick={() => {
          setFehler(null)
          // Beenden geht sofort, Einschalten erklärt sich vorher.
          if (gemerkt) umschalten.mutate()
          else setOffen(true)
        }}
        loading={umschalten.isPending && gemerkt}
      >
        {gemerkt ? t('watch.stop') : t('watch.start')}
      </Button>

      {/* Der Ausgang steht in der Fußzeile: ``Fenster`` blendet den
          Schließen-Knopf oben aus, sobald es eine gibt. */}
      <Fenster
        offen={offen}
        titel={t('watch.start')}
        unterzeile={title}
        onSchliessen={() => setOffen(false)}
        fuss={
          <>
            <Button
              variant="ghost"
              onClick={() => setOffen(false)}
              disabled={umschalten.isPending}
            >
              {t('common.cancel')}
            </Button>
            <Button onClick={() => umschalten.mutate()} loading={umschalten.isPending}>
              {t('watch.start')}
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-mist-300">
          {t(istSerie ? 'watch.explainSeries' : 'watch.explainMovie')}
        </p>
        {fehler && (
          <div className="mt-3">
            <ErrorBanner message={fehler} />
          </div>
        )}
      </Fenster>
    </>
  )
}
