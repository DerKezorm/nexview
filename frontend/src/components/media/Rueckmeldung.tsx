import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { MediaType } from '../../api/types'
import { Fenster } from '../Fenster'
import { StarRating } from '../StarRating'
import { Button, ErrorBanner } from '../ui'

/**
 * Rückmeldung zur Qualität - Sterne in der Zeile, alles Weitere im Fenster.
 *
 * **Am Titel, nicht an der Anfrage.** Bis 0.19 durfte nur der Besteller
 * urteilen; wer denselben Film zwei Wochen später sah und merkte, dass die
 * Tonspur fehlt, hatte keine Möglichkeit, es zu sagen. Dabei geht es hier
 * nicht um Geschmack - dafür gibt es das Herz -, sondern um die Datei, und die
 * beurteilt jeder gleich gut, der sie gesehen hat.
 *
 * Derselbe Baustein steht an zwei Stellen: auf der Titelseite für alles
 * Vorhandene, und in „Meine Anfragen" als Abkürzung. Der Augenblick direkt
 * nach „Bereits geladen" ist der, in dem jemand bewertet - nähme man ihn dort
 * weg, kämen kaum noch Rückmeldungen.
 *
 * Ein Klick auf einen Stern setzt ihn **und** öffnet das Fenster: Wer bewertet,
 * hat oft auch etwas zu sagen.
 */

export type Rueckmeldungsstand = {
  rating: number
  comment?: string | null
  reply?: string | null
  outdated?: boolean
}

/** Ab hier (und darunter) bietet das Fenster an, ein Ticket daraus zu machen. */
const SCHWACH = 2

export function Rueckmeldung({
  mediaType,
  tmdbId,
  title,
  season,
  stand,
  onGespeichert,
  /** Gibt es zu diesem Titel schon ein offenes Ticket von mir? */
  ticketOffen = false,
}: {
  mediaType: MediaType
  tmdbId: number
  title: string
  season?: number | null
  stand?: Rueckmeldungsstand | null
  onGespeichert?: () => void
  ticketOffen?: boolean
}) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)
  const [sterne, setSterne] = useState(stand?.rating ?? 0)
  const [kommentar, setKommentar] = useState(stand?.comment ?? '')
  const [fehler, setFehler] = useState<string | null>(null)

  const bewertet = Boolean(stand)
  const veraltet = Boolean(stand?.outdated)

  const speichern = useMutation({
    mutationFn: () =>
      api.put(`/api/feedback/${mediaType}/${tmdbId}`, {
        rating: sterne,
        comment: kommentar.trim() || null,
        title,
        season: season ?? null,
      }),
    onMutate: () => setFehler(null),
    onSuccess: () => {
      setOffen(false)
      onGespeichert?.()
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  // Aus einem schwachen Urteil ein Ticket machen. Der Text ist schon
  // geschrieben - es wäre schade, ihn in einem Durchschnitt verschwinden zu
  // lassen, wenn jemand gerade beschreibt, was kaputt ist.
  const ticket = useMutation({
    mutationFn: () =>
      api.post('/api/tickets', {
        subject: t('feedback.ticketSubject', { title }),
        body: kommentar.trim() || t('feedback.ticketFallback', { stars: sterne }),
        media_type: mediaType,
        tmdb_id: tmdbId,
        media_title: title,
      }),
    onSuccess: () => {
      setOffen(false)
      onGespeichert?.()
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  return (
    <>
      <div className="flex shrink-0 items-center gap-2">
        <span className="text-xs text-mist-500">
          {t(bewertet ? 'feedback.yourRating' : 'feedback.question')}
        </span>
        <StarRating
          value={sterne}
          size="sm"
          onChange={(wert) => {
            setSterne(wert)
            setOffen(true)
          }}
        />
        {veraltet && (
          <button
            type="button"
            onClick={() => setOffen(true)}
            className="rounded-full border border-warn-500/40 bg-warn-500/10 px-2 py-0.5 text-xs text-warn-500 hover:border-warn-500/70"
          >
            {t('feedback.outdated')}
          </button>
        )}
        {stand?.reply && (
          <button
            type="button"
            onClick={() => setOffen(true)}
            className="text-xs text-accent-400 underline-offset-2 hover:underline"
          >
            {t('feedback.replyTitle')}
          </button>
        )}
      </div>

      <Fenster
        offen={offen}
        titel={t('feedback.question')}
        unterzeile={title}
        onSchliessen={() => setOffen(false)}
        fuss={
          <>
            <Button
              variant="ghost"
              onClick={() => setOffen(false)}
              disabled={speichern.isPending || ticket.isPending}
            >
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => speichern.mutate()}
              loading={speichern.isPending}
              disabled={sterne === 0}
            >
              {t('feedback.submit')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {veraltet && (
            <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
              {t('feedback.outdatedHint')}
            </p>
          )}

          <StarRating value={sterne} onChange={setSterne} />

          <textarea
            value={kommentar}
            onChange={(event) => setKommentar(event.target.value)}
            maxLength={1000}
            rows={4}
            placeholder={t('feedback.commentPlaceholder')}
            className="w-full rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-200 outline-none focus:border-accent-500"
          />

          {/* ⚠️ Nur bei einem schwachen Urteil - und nur, wenn zu diesem Titel
              nicht schon eines offen ist. Sonst bekäme der Betreiber zweimal
              dieselbe Sache auf den Tisch, und der Nutzer glaubte, sein erstes
              sei untergegangen.

              Eine Bewertung verschwindet in einem Durchschnitt; ein Ticket
              hat einen Zustand und bleibt offen, bis jemand es schließt. */}
          {sterne > 0 && sterne <= SCHWACH && !ticketOffen && (
            <div className="rounded-xl border border-ink-700 bg-ink-850/60 px-3 py-3">
              <p className="text-sm text-mist-300">{t('feedback.ticketOffer')}</p>
              <div className="mt-2">
                <Button
                  variant="ghost"
                  onClick={() => ticket.mutate()}
                  loading={ticket.isPending}
                >
                  {t('feedback.ticketButton')}
                </Button>
              </div>
            </div>
          )}

          {stand?.reply && (
            <div className="rounded-xl border border-ink-700 bg-ink-850/60 px-3 py-2">
              <p className="text-xs font-medium text-accent-500">{t('feedback.replyTitle')}</p>
              <p className="mt-0.5 text-sm text-mist-300">{stand.reply}</p>
            </div>
          )}

          {fehler && <ErrorBanner message={fehler} />}
        </div>
      </Fenster>
    </>
  )
}
