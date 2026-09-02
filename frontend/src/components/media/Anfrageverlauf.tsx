import { useTranslation } from 'react-i18next'

import type { MediaRequest } from '../../api/types'
import { formatDate } from '../../lib/format'
import { gespeicherterFehler } from '../../api/client'

/**
 * Der Verlauf einer Anfrage - als Zeitleiste.
 *
 * „Warum dauert das?" ist die häufigste Frage, und Nexview kennt die Antwort
 * vollständig: wann angefragt, von wem freigegeben, wann an Radarr oder Sonarr
 * übergeben, wann zuletzt nachgesehen. Sichtbar war davon **ein einziges
 * Zustandswort**. Die Zeitleiste beantwortet die Frage, bevor sie gestellt
 * wird.
 *
 * Rein lesend: Alle Angaben lagen schon in der Anfrage, sie kamen nur nie bis
 * zum Anfragenden durch.
 *
 * Drei Zustände je Schritt, und die Farbe trägt die Aussage:
 *
 * - **erledigt** – grün mit Haken, mit Zeitpunkt
 * - **läuft gerade** – gelb, der einzige Schritt, der offen ist
 * - **kommt noch** – grau, ohne Zeitpunkt
 *
 * Ein abgebrochener Weg (abgelehnt, fehlgeschlagen, zurückgezogen) endet dort,
 * wo er endet: Die Schritte danach werden gar nicht erst gezeigt. Einen
 * grauen „Fertig"-Schritt unter eine Ablehnung zu setzen wäre ein Versprechen,
 * das niemand mehr einlöst.
 */

type Zustand = 'fertig' | 'laeuft' | 'offen' | 'gescheitert'

type Schritt = {
  schluessel: string
  titel: string
  zustand: Zustand
  wann?: string | null
  dazu?: string | null
}

function schritte(
  request: MediaRequest,
  t: (schluessel: string, werte?: Record<string, unknown>) => string,
  sprache: string,
): Schritt[] {
  const datum = (roh: string | null | undefined) =>
    roh ? formatDate(roh.slice(0, 10), sprache) : null

  const liste: Schritt[] = [
    {
      schluessel: 'requested',
      titel: t('verlauf.requested'),
      zustand: 'fertig',
      wann: datum(request.requested_at),
    },
  ]

  // Wer entschieden hat - gilt für Freigabe und Ablehnung gleichermaßen.
  // ⚠️ **Die Regel steht vor der Person.** Hat eine Regel entschieden, gab es
  // keinen Menschen, den man fragen könnte - und genau das ist die Auskunft,
  // die fehlt, wenn eine Anfrage von selbst durchläuft oder abprallt.
  const wer = request.regel_name
    ? t('verlauf.byRule', { name: request.regel_name })
    : request.approved_by_name
      ? t('verlauf.approvedBy', { name: request.approved_by_name })
      : null

  // --- Der Weg endet vorzeitig ---------------------------------------------
  if (request.status === 'rejected') {
    liste.push({
      schluessel: 'rejected',
      titel: t('verlauf.rejected'),
      zustand: 'gescheitert',
      wann: datum(request.approved_at),
      // ⚠️ Der Grund ist freiwillig, und meistens trägt niemand einen ein.
      // Dann muss dort trotzdem etwas stehen: Eine leere Zeile unter
      // „Abgelehnt" sieht aus, als hätte Nexview den Grund verloren - und
      // die nächste Frage wäre genau die, die hier beantwortet werden soll.
      dazu: [wer, request.rejection_reason || t('verlauf.noReason')]
        .filter(Boolean)
        .join(' · '),
    })
    return liste
  }
  if (request.status === 'deferred') {
    // Kein Abbruch, sondern ein Halt: „Ja im Prinzip, nur nicht jetzt." Der
    // Weg geht weiter, sobald wieder Platz ist - deshalb gelb und nicht rot,
    // und deshalb steht darunter, was als Nächstes passiert.
    liste.push({
      schluessel: 'deferred',
      titel: t('verlauf.deferred'),
      zustand: 'laeuft',
      dazu: [wer, t('verlauf.deferredHint')].filter(Boolean).join(' · '),
    })
    return liste
  }
  if (request.status === 'cancelled') {
    liste.push({
      schluessel: 'cancelled',
      titel: t('verlauf.cancelled'),
      zustand: 'gescheitert',
    })
    return liste
  }

  // --- Freigabe -------------------------------------------------------------
  const freigegeben = Boolean(request.approved_at) || request.status !== 'pending_approval'
  liste.push({
    schluessel: 'approved',
    titel: t('verlauf.approved'),
    zustand: freigegeben ? 'fertig' : 'laeuft',
    wann: datum(request.approved_at),
    // Ohne Namen war es die automatische Freigabe - eine Aussage, keine Lücke.
    dazu: freigegeben ? (wer ?? t('verlauf.approvedAuto')) : null,
  })
  if (!freigegeben) return liste

  // --- Suche ---------------------------------------------------------------
  if (request.status === 'failed') {
    liste.push({
      schluessel: 'failed',
      titel: t('verlauf.failed'),
      zustand: 'gescheitert',
      dazu: gespeicherterFehler(request.error_detail, request.error_message),
    })
    return liste
  }

  const geladen = request.status === 'downloaded' || request.status === 'deleted'
  liste.push({
    schluessel: 'searching',
    titel: t('verlauf.searching'),
    zustand: geladen ? 'fertig' : 'laeuft',
    // Beim Suchen ist der letzte Blick die interessante Zahl, nicht der Start:
    // Sie sagt, dass Nexview noch hinsieht.
    dazu:
      !geladen && request.last_checked_at
        ? t('verlauf.lastChecked', { wann: datum(request.last_checked_at) })
        : null,
  })

  liste.push({
    schluessel: 'done',
    titel: t('verlauf.done'),
    zustand: geladen ? 'fertig' : 'offen',
    wann: datum(request.completed_at),
  })

  // Wieder verschwunden: eigener Schritt hinter "Fertig", weil es danach
  // passiert ist und den vorherigen nicht ungeschehen macht.
  if (request.status === 'deleted') {
    liste.push({
      schluessel: 'deleted',
      titel: t('verlauf.deleted'),
      zustand: 'gescheitert',
    })
  }

  return liste
}

const RING: Record<Zustand, string> = {
  fertig: 'border-ok-500 bg-ok-500/15 text-ok-500',
  laeuft: 'border-warn-500 bg-warn-500/15 text-warn-500',
  offen: 'border-ink-700 bg-ink-850 text-mist-600',
  gescheitert: 'border-bad-500 bg-bad-500/15 text-bad-500',
}

const LINIE: Record<Zustand, string> = {
  fertig: 'bg-ok-500/50',
  laeuft: 'bg-warn-500/50',
  offen: 'bg-ink-700',
  gescheitert: 'bg-bad-500/50',
}

export function Anfrageverlauf({ request }: { request: MediaRequest }) {
  const { t, i18n } = useTranslation()
  const liste = schritte(request, t, i18n.language)

  return (
    <ol className="flex flex-col">
      {liste.map((schritt, nummer) => {
        const letzter = nummer === liste.length - 1
        return (
          <li key={schritt.schluessel} className="flex gap-3">
            {/* Kreis und Linie: die Linie gehört zum Schritt *darüber*, damit
                sie seine Farbe trägt und nicht die des nächsten. */}
            <div className="flex flex-col items-center">
              <span
                className={
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 text-xs font-bold ' +
                  RING[schritt.zustand]
                }
                aria-hidden="true"
              >
                {schritt.zustand === 'fertig'
                  ? '✓'
                  : schritt.zustand === 'gescheitert'
                    ? '×'
                    : nummer + 1}
              </span>
              {!letzter && (
                <span className={'w-0.5 flex-1 ' + LINIE[schritt.zustand]} aria-hidden="true" />
              )}
            </div>

            <div className={letzter ? 'pb-0' : 'pb-5'}>
              <p className="text-sm font-semibold text-mist-100">{schritt.titel}</p>
              {schritt.wann && <p className="text-xs text-mist-500">{schritt.wann}</p>}
              {schritt.dazu && (
                <p className="mt-0.5 text-xs leading-relaxed text-mist-400">{schritt.dazu}</p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * Derselbe Weg als Linie - der Verlauf in 1 px.
 *
 * Der Verlauf beantwortet „warum dauert das?", aber erst nach einem Klick.
 * Die Linie beantwortet die halbe Frage schon vorher: **wo steht es?** Aus
 * zwanzig Zeilen wird damit eine Leiter, die man von oben nach unten
 * überfliegen kann, ohne ein einziges Etikett zu lesen.
 *
 * ⚠️ **Sie misst die Wegstrecke, nicht das Tempo.** Vier Stationen, dieselben
 * wie oben: angefragt, freigegeben, wird gesucht, fertig. Ein echter
 * Ladefortschritt in Prozent stünde nur in Radarrs Warteschlange, und die
 * fragt Nexview bewusst nicht ab - siehe ``status_poller``. Wenn die Linie
 * also tagelang bei drei Vierteln steht, ist das keine Schwäche der Anzeige,
 * sondern die Wahrheit über die Anfrage.
 *
 * ⚠️ **Die Spur steht in jeder Zeile, auch wenn nichts darin liegt.** Eine
 * Linie, die nur bei aktiven Anfragen erscheint, macht Zeilen unterschiedlich
 * hoch - genau das, wogegen die feste Spaltenaufteilung in ``MyRequestsPage``
 * gebaut ist. Ein unbekannter Zustand füllt die Spur nicht, statt sich etwas
 * auszudenken.
 *
 * Stumm für Screenreader: Das Etikett daneben sagt denselben Zustand in
 * Worten, und zweimal dieselbe Aussage hilft niemandem. Dieselbe Regel gilt
 * für den Zustandskreis vor dem Titel.
 *
 * Setzt voraus, dass die Zeile ``relative`` ist.
 */
const VIERTEL: Partial<
  Record<MediaRequest['status'], { erreicht: number; zustand: Zustand }>
> = {
  pending_approval: { erreicht: 1, zustand: 'laeuft' },
  approved: { erreicht: 2, zustand: 'laeuft' },
  // Kommt auf einer Anfrage kaum vor, wird von ``MyRequestsPage`` aber
  // ausdrücklich neben ``searching`` behandelt - also als „unterwegs" gelesen.
  requested: { erreicht: 2, zustand: 'laeuft' },
  searching: { erreicht: 3, zustand: 'laeuft' },
  downloaded: { erreicht: 4, zustand: 'fertig' },
  // Ein Halt, kein Abbruch: Der Weg geht weiter, sobald wieder Platz ist.
  // Deshalb gelb wie „läuft" und nicht rot - genau wie im Verlauf.
  deferred: { erreicht: 1, zustand: 'laeuft' },
  rejected: { erreicht: 1, zustand: 'gescheitert' },
  cancelled: { erreicht: 1, zustand: 'gescheitert' },
  // Die Übergabe an Radarr/Sonarr ist gescheitert - freigegeben war es schon.
  failed: { erreicht: 2, zustand: 'gescheitert' },
  // War vollständig da und ist wieder verschwunden: volle Länge, aber rot.
  deleted: { erreicht: 4, zustand: 'gescheitert' },
}

// Volle Farbe statt der ``LINIE``-Werte oben: Die Zeitleiste im Fenster wird
// aus der Nähe gelesen, diese Linie im Vorbeifliegen über zwanzig Zeilen.
// Bei **einem** Pixel Höhe ist halbe Deckkraft nicht mehr zu erkennen -
// je feiner die Linie, desto weniger Deckkraft verträgt sie.
const BALKEN: Record<Zustand, string> = {
  fertig: 'bg-ok-500',
  laeuft: 'bg-warn-500',
  offen: 'bg-ink-700',
  gescheitert: 'bg-bad-500',
}

export function Anfragebalken({ status }: { status: MediaRequest['status'] }) {
  const stand = VIERTEL[status]

  return (
    <span
      className={
        'pointer-events-none absolute inset-x-3 bottom-1.5 h-px ' +
        'overflow-hidden bg-ink-700'
      }
      aria-hidden="true"
    >
      {stand && (
        <span
          className={
            'block h-full transition-[width] duration-500 ' +
            'motion-reduce:transition-none ' +
            BALKEN[stand.zustand]
          }
          style={{ width: `${(stand.erreicht / 4) * 100}%` }}
        />
      )}
    </span>
  )
}
