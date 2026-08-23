import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import { KIDS } from './kidsTheme'

/**
 * Der Seitengrund der Kinderansicht: eine weiche Collage aus Motiven, die
 * dieses Kind auch wirklich sehen darf.
 *
 * Ein einfarbiger Verlauf sieht leer aus. Die Bilder liegen deshalb stark
 * weichgezeichnet darunter – erkennbar als Farbe und Form, nie als Motiv,
 * sonst kämpften sie mit den Kacheln um die Aufmerksamkeit.
 *
 * Zwei Dinge, die beim ersten Anlauf schiefgingen und nicht zurückkommen
 * dürfen:
 *
 * 1. **`grid-rows-3` ist Pflicht.** Ohne feste Zeilen richtet sich jede
 *    Zeilenhöhe nach dem Bild darin, und `h-full` der Bilder hat dann nichts,
 *    woran es sich messen könnte – die Collage füllte die Fläche nicht.
 * 2. **Nicht zweimal abdunkeln.** 45 % Bildstärke unter einem 70 %-Verlauf
 *    ergibt rund 13 % sichtbares Bild; auf dem Schirm war davon nichts mehr zu
 *    erkennen. Jetzt steht das Bild voll, und darüber liegt genau eine
 *    Schicht, die es beruhigt.
 *
 * `absolute` statt `fixed`: Innerhalb der Eltern-Vorschau steckt die Ansicht
 * in einem Kasten, und ein am Fenster klebender Grund läge dort quer über der
 * halben Seite.
 */
export function KidsHintergrund({ quelle = '/api/kids' }: { quelle?: string }) {
  const bilder = useQuery({
    queryKey: [quelle, 'backdrops'],
    queryFn: () => api.get<string[]>(`${quelle}/backdrops`),
    staleTime: 60 * 60 * 1000,
    retry: false,
  })

  // Einmal mischen und festhalten: Ohne das ordnete sich die Collage bei jedem
  // Neuzeichnen neu, und der Grund flackerte beim Blättern.
  const auswahl = useMemo(() => {
    const alle = [...(bilder.data ?? [])]
    for (let i = alle.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[alle[i], alle[j]] = [alle[j], alle[i]]
    }
    return alle.slice(0, 9)
  }, [bilder.data])

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      {/* Der Verlauf trägt die Seite auch, solange oder falls keine Bilder
          kommen - er liegt deshalb ganz unten und nicht nur oben drauf. */}
      <div className="absolute inset-0" style={{ background: KIDS.seite }} />

      {auswahl.length > 0 && (
        <div className="absolute inset-0 grid grid-cols-3 grid-rows-3 blur-[72px]">
          {auswahl.map((bild, index) => (
            <img
              key={`${bild}-${index}`}
              src={bild}
              alt=""
              // `scale-150` schiebt die weichgezeichneten Ränder aus dem Bild:
              // ohne das zeichnet sich das Raster als helles Kreuz ab.
              className="h-full w-full scale-150 object-cover"
            />
          ))}
        </div>
      )}

      {/* Genau eine Schicht darüber: Sie nimmt den Farben die Wucht und hält
          die Schrift lesbar, ohne das Bild wieder wegzunehmen. */}
      <div className="absolute inset-0 bg-white/55" />
    </div>
  )
}
