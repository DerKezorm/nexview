/**
 * Der §-Knopf unten rechts – und das Fenster, das er öffnet.
 *
 * ⚠️ **Er springt nicht von selbst auf.** Wer die Hausordnung noch nie gelesen
 * hat, sieht einen Punkt am Knopf – wie an der Glocke bei neuen Meldungen.
 * Ein Fenster, das sich beim Anmelden selbst öffnet, ist genau das, was man an
 * anderen Anwendungen hasst; und wer aufgehalten wird, klickt weg statt zu
 * lesen.
 *
 * ⚠️ **Das Fenster kommt per `lazy`.** Fest daran hängt nur der Knopf. Nach
 * dem Abhaken verschwindet er ganz, und der dauerhafte Weg zum Text ist der
 * Verweis in der Fußzeile.
 */

import { Suspense, lazy, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useConfig } from '../hooks/useConfig'

const HausordnungFenster = lazy(() =>
  import('./HausordnungFenster').then((m) => ({ default: m.HausordnungFenster })),
)

/** Gibt es etwas zu lesen, das dieses Konto noch nicht quittiert hat? */
export function useHausordnung() {
  const { data: config } = useConfig()
  const vorhanden = config?.hausordnung_vorhanden ?? false
  const quittierbar = config?.hausordnung_quittierbar ?? true
  const nichtQuittiert =
    vorhanden &&
    (config?.hausordnung_gelesen == null ||
      config.hausordnung_gelesen < (config?.hausordnung_fassung ?? 0))

  return {
    vorhanden,
    // ⚠️ **Der Punkt nur, wenn man ihn auch loswerden kann.** Ist das Abhaken
    // abgeschaltet, gibt es nichts zu quittieren - der Punkt bliebe für immer
    // stehen, und einen Hinweis, den man nie loswird, lernt man zu übersehen.
    // Dann ist der Knopf kein Anstupser mehr, sondern schlicht der Zugang.
    ungelesen: nichtQuittiert && quittierbar,
    knopfSichtbar: vorhanden && (nichtQuittiert || !quittierbar),
  }
}

export function HausordnungKnopf({
  offen,
  onOeffnen,
  onSchliessen,
}: {
  offen: boolean
  onOeffnen: () => void
  onSchliessen: () => void
}) {
  const { t } = useTranslation()
  const { vorhanden, ungelesen, knopfSichtbar } = useHausordnung()
  const [jeGeoeffnet, setzeJeGeoeffnet] = useState(false)

  if (!vorhanden) return null

  return (
    <>
      {knopfSichtbar && (
        <button
          type="button"
          onClick={() => {
            setzeJeGeoeffnet(true)
            onOeffnen()
          }}
          aria-label={t('hausordnung.oeffnen')}
          title={t('hausordnung.oeffnen')}
          className="fixed bottom-5 right-5 z-40 flex h-12 w-12 items-center justify-center rounded-full border border-ink-700 bg-ink-850/95 text-xl font-semibold text-mist-200 shadow-lg backdrop-blur transition-colors hover:border-accent-500 hover:text-mist-100 sm:bottom-6 sm:right-6"
        >
          {/* Das Paragraphenzeichen als Schrift, nicht als Zeichnung: In
              12 Pixeln zerfiele ein gezeichnetes § zu einem Fleck, hier ist
              es groß genug und trägt die Bedeutung allein. */}
          <span aria-hidden="true">§</span>
          {ungelesen && (
            <span
              aria-hidden="true"
              className="absolute right-1 top-1 h-2.5 w-2.5 rounded-full bg-accent-500 ring-2 ring-ink-950"
            />
          )}
        </button>
      )}

      {/* Erst nach dem ersten Öffnen überhaupt einbinden - sonst lädt das
          Nachladen schon beim Seitenaufbau. */}
      {(offen || jeGeoeffnet) && (
        <Suspense fallback={null}>
          <HausordnungFenster offen={offen} onSchliessen={onSchliessen} />
        </Suspense>
      )}
    </>
  )
}
