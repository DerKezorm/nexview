import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { Befund, BefundBereich, BefundSchwere } from '../api/types'
import { formatSize } from '../lib/format'
import { Symbol } from './Symbol'

/**
 * Die Befundliste — einmal gebaut, an drei Stellen benutzt.
 *
 * Das Admin-Dashboard zeigt sie ungefiltert, die Analyse-Seite je Bereich.
 * Eine zweite, nachgebaute Fassung würde nach dem ersten Feinschliff anders
 * aussehen, und dann wüsste niemand mehr, welche die richtige ist.
 *
 * ⚠️ **Der Satz entsteht hier, nicht auf dem Server.** Der Server liefert
 * Kennung und Werte; übersetzt wird `befund.<kennung>.titel` (was ist) und
 * `befund.<kennung>.folge` (was daraus folgt). Ein serverseitig fertiger Satz
 * wäre in der Sprache des Servers und nicht in der des Lesers.
 *
 * ⚠️ **Die Folge ist Pflicht, nicht Zierde.** „Zwei Indexer antworten nicht"
 * sagt einem Betreiber nichts; „deshalb bleiben neue Serienanfragen liegen"
 * sagt ihm, ob er jetzt aufstehen muss. Ein Befund ohne Folge und ohne Ziel
 * ist eine Sorge, keine Hilfe.
 */

/**
 * Eine Dauer in Worten — „12 Minuten“, „3 Stunden“, „2 Tage“.
 *
 * ⚠️ **Nicht `formatRuntime` nehmen.** Der ist für Spielzeiten gebaut und
 * sagt bei einem zweitägigen Ausfall „48 Std.“ — richtig gerechnet und trotzdem
 * die falsche Auskunft. Hier zählt die Größenordnung, nicht die Genauigkeit.
 */
function dauerText(minuten: number, t: TFunction): string {
  if (minuten < 60) return t('befund.dauer.minuten', { count: minuten })
  if (minuten < 60 * 24) return t('befund.dauer.stunden', { count: Math.floor(minuten / 60) })
  return t('befund.dauer.tage', { count: Math.floor(minuten / (60 * 24)) })
}

const FARBEN: Record<BefundSchwere, { punkt: string; rahmen: string }> = {
  fehler: { punkt: 'bg-bad-500', rahmen: 'border-bad-500/40 bg-bad-500/5' },
  warnung: { punkt: 'bg-warn-500', rahmen: 'border-warn-500/40 bg-warn-500/5' },
  // Hinweise bekommen bewusst keine Signalfarbe: Sie sind Aufräum-Ideen, und
  // eine Wand aus Gelb macht die echten Warnungen daneben wertlos.
  hinweis: { punkt: 'bg-mist-600', rahmen: 'border-ink-700 bg-ink-850/60' },
}

function Zeile({ befund }: { befund: Befund }) {
  const { t, i18n } = useTranslation()
  const farbe = FARBEN[befund.schwere]

  // Der Server liefert rohe Bytes - eine Zahl mit zehn Stellen liest niemand.
  // Umgerechnet wird hier und nicht dort, weil die Einheit von der Sprache
  // abhängt und der Server die des Lesers nicht kennt.
  const werte = {
    ...befund.werte,
    // ⚠️ **`count` ist für i18next kein gewöhnlicher Wert**, sondern der
    // Schalter zwischen Einzahl und Mehrzahl (`_one` / `_other`). Ohne ihn
    // stünde in jedem Text „1 Anfragen“ — und der Server soll dafür nicht
    // zweimal dieselbe Zahl schicken.
    ...(typeof befund.werte.anzahl === 'number' ? { count: befund.werte.anzahl } : {}),
    // Der Server liefert rohe Bytes — eine Zahl mit zehn Stellen liest niemand.
    // Umgerechnet wird hier und nicht dort, weil die Einheit von der Sprache
    // abhängt und der Server die des Lesers nicht kennt.
    ...(typeof befund.werte.bytes === 'number'
      ? { bytes_lesbar: formatSize(befund.werte.bytes, i18n.language) }
      : {}),
    ...(typeof befund.werte.minuten === 'number'
      ? { dauer_lesbar: dauerText(befund.werte.minuten, t) }
      : {}),
  }

  return (
    <li className={'flex flex-col gap-2 rounded-2xl border px-4 py-3 sm:flex-row sm:items-start ' + farbe.rahmen}>
      <span className="flex items-center gap-3 sm:pt-1">
        <span
          className={'h-2.5 w-2.5 shrink-0 rounded-full ' + farbe.punkt}
          // Die Farbe allein trägt die Dringlichkeit nicht — wer sie nicht
          // unterscheiden kann, liest hier das Wort.
          aria-label={t(`befund.schwere.${befund.schwere}`)}
          role="img"
        />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-mist-100">
          {t(`befund.${befund.kennung}.titel`, werte)}
        </p>
        <p className="mt-0.5 text-sm text-mist-500">
          {t(`befund.${befund.kennung}.folge`, werte)}
        </p>
        {befund.wortlaut && (
          // Der Wortlaut von Radarr/Sonarr, unübersetzt. Als Zitat gesetzt,
          // damit man sieht: Das sagt die Instanz, nicht Nexview.
          <p className="mt-2 border-l-2 border-ink-700 pl-3 text-xs text-mist-600 italic">
            {befund.wortlaut}
          </p>
        )}
      </div>
      {befund.ziel && (
        <Link
          to={befund.ziel}
          className="shrink-0 self-start rounded-full border border-ink-700 px-3 py-1.5 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100"
        >
          {t('befund.hingehen')}
        </Link>
      )}
    </li>
  )
}

/**
 * Reihenfolge der Bereiche. Nicht alphabetisch, sondern nach Dringlichkeit:
 * Ein ausgefallener Dienst legt alles lahm, ein Aufräum-Hinweis nichts.
 */
const BEREICHE: BefundBereich[] = [
  'dienste',
  'platz',
  'nachschub',
  'bibliothek',
  'abgleich',
  'betrieb',
]

export function Befundliste({
  befunde,
  /** Was steht da, wenn nichts gefunden wurde. Ohne das bliebe die Seite leer. */
  leerText,
  /**
   * Nach Bereichen gruppieren, mit kleiner Überschrift je Gruppe.
   *
   * ⚠️ **Auf dem Dashboard ja, in der Analyse nein.** Dort zeigt jeder Reiter
   * ohnehin nur einen Bereich, und eine Überschrift „Dienste" über einer Liste
   * auf der Seite „Dienste" ist Papier. Auf dem Dashboard dagegen stehen
   * zwanzig Zeilen untereinander, und ohne Gliederung liest man sie nicht,
   * sondern überfliegt sie.
   */
  gruppiert = false,
}: {
  befunde: Befund[]
  leerText?: string
  gruppiert?: boolean
}) {
  const { t } = useTranslation()

  if (befunde.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-2xl border border-ink-700 bg-ink-850/60 px-4 py-8 text-center">
        <Symbol name="befund" className="h-6 w-6 text-ok-500" />
        <p className="text-sm text-mist-500">{leerText ?? t('befund.allesInOrdnung')}</p>
      </div>
    )
  }

  if (!gruppiert) {
    return (
      <ul className="flex flex-col gap-2">
        {befunde.map((befund) => (
          <Zeile key={befund.schluessel} befund={befund} />
        ))}
      </ul>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {BEREICHE.map((bereich) => {
        const darin = befunde.filter((b) => b.bereich === bereich)
        if (darin.length === 0) return null
        return (
          <section key={bereich} className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold tracking-wide text-mist-600 uppercase">
              {t(`befund.bereich.${bereich}`)}
            </h3>
            <ul className="flex flex-col gap-2">
              {darin.map((befund) => (
                <Zeile key={befund.schluessel} befund={befund} />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
