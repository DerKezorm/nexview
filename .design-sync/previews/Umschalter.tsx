import { Umschalter } from 'nexview-ui'

/**
 * Eine geschlossene Leiste, nicht mehrere Pillen: Sie zeigt auf einen Blick,
 * dass die Auswahl **eine** Frage beantwortet und genau eine Antwort gilt.
 */
export const MitBeschriftung = () => (
  <div className="bg-ink-950 p-6 flex flex-col gap-4">
    <Umschalter
      wert="woche"
      wahl={['woche', 'monat', 'jahr'] as const}
      onChange={() => {}}
      label={(e) => ({ woche: 'Woche', monat: 'Monat', jahr: 'Jahr' })[e]}
      beschriftung="Zeitraum"
    />
    <Umschalter
      wert="raster"
      wahl={['raster', 'liste'] as const}
      onChange={() => {}}
      label={(e) => ({ raster: 'Raster', liste: 'Liste' })[e]}
    />
  </div>
)

/** Stillgelegt bleibt sichtbar - ein Regler, der wegfaellt, wirkt wie ein Fehler. */
export const Stillgelegt = () => (
  <div className="bg-ink-950 p-6">
    <Umschalter
      wert="filme"
      wahl={['filme', 'serien'] as const}
      onChange={() => {}}
      label={(e) => ({ filme: 'Filme', serien: 'Serien' })[e]}
      deaktiviert
      titel="Serien sind auf diesem Server nicht eingerichtet"
    />
  </div>
)
