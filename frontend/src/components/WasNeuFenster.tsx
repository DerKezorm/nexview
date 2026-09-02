import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Betont } from './Betont'
import { Fenster } from './Fenster'
import { Button } from './ui'
import { istEintrag } from './wasNeuEintrag'

/** Fassungen vergleichen: 0.9.0 ist **kleiner** als 0.10.0, nicht größer. */
function vergleicheFassungen(a: string, b: string): number {
  const zerlegen = (wert: string) => wert.split('.').map((teil) => Number(teil) || 0)
  const links = zerlegen(a)
  const rechts = zerlegen(b)
  for (let i = 0; i < Math.max(links.length, rechts.length); i++) {
    const unterschied = (links[i] ?? 0) - (rechts[i] ?? 0)
    if (unterschied !== 0) return unterschied
  }
  return 0
}

/** Wie viele Fassungen das Fenster vorhält. */
export const FASSUNGEN_IM_FENSTER = 5

/**
 * „Alles, was neu ist" – das Fenster mit den Neuerungen der letzten Fassungen.
 *
 * Oben eine Reihe kleiner Schalter, einer je vorgehaltener Fassung. Der Grund:
 * Wer eine Version überspringt — Container später aktualisiert, zwei Releases
 * an einem Tag —, hätte sonst nie erfahren, was in der übersprungenen steckte.
 * Der Balken erscheint nur einmal je Fassung, und danach wäre der Text weg.
 *
 * Fassungen, die seit dem letzten Quittieren dazugekommen sind, tragen einen
 * Punkt. Ohne den müsste man raten, welche man schon gelesen hat.
 *
 * ⚠️ **Quittiert wird immer die laufende Fassung**, egal welche gerade
 * gelesen wird. Alles andere wäre eine Falle: Wer sich 0.14.0 ansieht und auf
 * „Verstanden" drückt, würde sonst den Hinweis auf 0.16.0 verlieren, den er
 * noch gar nicht gelesen hat.
 */
export function WasNeuFenster({
  offen,
  version,
  zuletztGesehen,
  onSchliessen,
  onQuittieren,
  quittiertLaeuft = false,
}: {
  offen: boolean
  /** Die laufende Fassung – sie ist vorausgewählt. */
  version: string
  /** Bis wohin dieses Konto quittiert hat; `null` = noch nie. */
  zuletztGesehen?: string | null
  onSchliessen: () => void
  /** Fehlt sie, gibt es nur „Schließen" – so beim Aufruf über die Über-Seite. */
  onQuittieren?: () => void
  quittiertLaeuft?: boolean
}) {
  const { t } = useTranslation()
  const [gewaehlt, setGewaehlt] = useState<string | null>(null)

  /* Die Texte liegen in den Sprachdateien unter `whatsNew.entries`, nicht im
     Code: So lassen sie sich vor jedem Release schreiben, ohne dass jemand
     eine Komponente anfasst.
     ⚠️ **Nicht über einen zusammengesetzten Schlüssel suchen.** Eine
     Versionsnummer enthält Punkte, und i18next trennt seine Schlüssel genau
     daran — `whatsNew.entries.0.15.0` wird zu fünf Ebenen. Je nach Fassung
     kommt dabei der Schlüsseltext selbst zurück statt `null`, und der
     nächste Zugriff auf `.sections` reißt die ganze Oberfläche mit. Deshalb
     einmal das Verzeichnis holen und in JavaScript nachschlagen. */
  const alleEintraege = t('whatsNew.entries', {
    returnObjects: true,
    defaultValue: {},
  }) as Record<string, unknown>

  const fassungen = useMemo(() => {
    if (!alleEintraege || typeof alleEintraege !== 'object') return []
    return Object.keys(alleEintraege)
      .filter((nummer) => istEintrag(alleEintraege[nummer]))
      .sort((a, b) => vergleicheFassungen(b, a))
      .slice(0, FASSUNGEN_IM_FENSTER)
  }, [alleEintraege])

  // Vorausgewählt ist die laufende Fassung. Gibt es zu ihr keinen Text, die
  // neueste vorhandene - ein leeres Fenster hülfe niemandem.
  const aktiv = gewaehlt ?? (fassungen.includes(version) ? version : (fassungen[0] ?? version))
  const roh = alleEintraege?.[aktiv]
  const eintrag = istEintrag(roh) ? roh : null

  return (
    <Fenster
      offen={offen}
      titel={t('whatsNew.title')}
      unterzeile={t('whatsNew.subtitle', { version: aktiv })}
      onSchliessen={onSchliessen}
      fuss={
        <>
          <Button variant="ghost" onClick={onSchliessen}>
            {t('common.close')}
          </Button>
          {onQuittieren && (
            <Button onClick={onQuittieren} loading={quittiertLaeuft}>
              {t('whatsNew.dismiss')}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-6">
        {/* Nur zeigen, wenn es wirklich etwas zu wählen gibt - eine Reihe mit
            einem einzigen Schalter sieht nach einem Fehler aus. */}
        {fassungen.length > 1 && (
          <div className="flex flex-wrap items-center gap-2">
            {fassungen.map((nummer) => {
              const istAktiv = nummer === aktiv
              const neu =
                Boolean(zuletztGesehen) && vergleicheFassungen(nummer, zuletztGesehen!) > 0
              return (
                <button
                  key={nummer}
                  type="button"
                  onClick={() => setGewaehlt(nummer)}
                  aria-pressed={istAktiv}
                  className={
                    'flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold tabular-nums transition-colors ' +
                    (istAktiv
                      ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                      : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
                  }
                >
                  {neu && (
                    <span
                      className="h-1.5 w-1.5 rounded-full bg-accent-400"
                      aria-label={t('whatsNew.unread')}
                    />
                  )}
                  {nummer}
                </button>
              )
            })}
          </div>
        )}

        {eintrag ? (
          <>
            <p className="text-sm leading-relaxed text-mist-300">
              <Betont text={eintrag.lead} />
            </p>

            {/* Zuerst die Funktionen – und je Funktion der Weg dorthin. Ein
                Betreiber will nach dem Update nicht wissen, was sich im Code
                geändert hat, sondern was er jetzt anders machen kann. */}
            <ol className="flex flex-col gap-5">
              {eintrag.sections.map((abschnitt) => (
                <li key={abschnitt.title} className="border-l-2 border-accent-500/40 pl-4">
                  <h4 className="font-semibold">
                    <Betont text={abschnitt.title} />
                  </h4>
                  <p className="mt-0.5 font-mono text-xs text-accent-400">{abschnitt.where}</p>
                  <p className="mt-1.5 text-sm leading-relaxed text-mist-300">
                    <Betont text={abschnitt.body} />
                  </p>
                </li>
              ))}
            </ol>

            {/* Kleinkram klein: Es soll auffindbar sein, aber den Blick nicht
                von dem nehmen, worum es geht. */}
            {eintrag.small.length > 0 && (
              <div className="border-t border-ink-700 pt-4">
                <h4 className="text-xs font-semibold tracking-wide text-mist-500 uppercase">
                  {eintrag.smallTitle || t('whatsNew.smallFallback')}
                </h4>
                <ul className="mt-2 flex flex-col gap-1.5">
                  {eintrag.small.map((zeile) => (
                    <li
                      key={zeile}
                      className="flex gap-2 text-xs leading-relaxed text-mist-500"
                    >
                      <span aria-hidden="true">·</span>
                      <span>
                        <Betont text={zeile} />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          /* Kein Text zu dieser Fassung – dann führt der Verweis auf die
             Release-Seite, statt ein leeres Fenster zu zeigen. */
          <p className="text-sm leading-relaxed text-mist-300">
            {t('whatsNew.fallback')}{' '}
            <a
              href="https://github.com/DerKezorm/nexview/releases"
              target="_blank"
              rel="noreferrer"
              className="text-accent-400 underline underline-offset-2"
            >
              GitHub
            </a>
          </p>
        )}
      </div>
    </Fenster>
  )
}
