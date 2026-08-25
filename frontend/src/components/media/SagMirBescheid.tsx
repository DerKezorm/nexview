import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { MediaType } from '../../api/types'
import { Button } from '../ui'

/**
 * „Sag mir Bescheid“ - auf einen Titel warten, ohne ihn anzufragen.
 *
 * Der Fall, den es bisher nicht gab: Ein Titel ist schon von jemand anderem
 * angefragt. Anfragen lässt er sich damit nicht mehr, und danach hörte der
 * Zweite nie wieder etwas davon.
 *
 * **Film und Serie stehen an verschiedenen Stellen**, und das hat einen Grund:
 *
 * - Beim **Film** erscheint der Knopf dort, wo sonst nur der Zustandssatz
 *   steht („wird gesucht“). Genau dann gibt es etwas zu warten.
 * - Bei der **Serie** steht er dauerhaft neben dem Anfrage-Knopf. Der
 *   Zustand „angefragt, nichts mehr zu tun“ tritt bei einer Serie praktisch
 *   nie ein - `nurWeitereStaffel` hält den Anfrage-Knopf offen, solange
 *   irgendeine Staffel unvollständig ist. Ein Knopf an einer Bedingung, die
 *   nie eintritt, wäre kein Feature.
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
  const [fehler, setFehler] = useState<string | null>(null)

  const umschalten = useMutation({
    mutationFn: () =>
      gemerkt
        ? api.delete(`/api/watch/${mediaType}/${tmdbId}`)
        : api.put(`/api/watch/${mediaType}/${tmdbId}`, {
            title,
            poster_url: posterUrl ?? null,
          }),
    onMutate: () => setFehler(null),
    onSuccess: () => setGemerkt((vorher) => !vorher),
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  return (
    <div className="flex flex-col gap-1">
      <Button
        type="button"
        variant={gemerkt ? 'ghost' : undefined}
        onClick={() => umschalten.mutate()}
        loading={umschalten.isPending}
      >
        {gemerkt ? t('watch.stop') : t('watch.start')}
      </Button>
      <span className="text-xs leading-relaxed text-mist-600">
        {gemerkt
          ? t(mediaType === 'tv' ? 'watch.activeSeries' : 'watch.activeMovie')
          : t(mediaType === 'tv' ? 'watch.hintSeries' : 'watch.hintMovie')}
      </span>
      {fehler && <span className="text-xs text-bad-500">{fehler}</span>}
    </div>
  )
}
