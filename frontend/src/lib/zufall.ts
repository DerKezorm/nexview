/**
 * Zufall, der sich beim Neuzeichnen nicht ändert.
 *
 * ⚠️ **Warum das nicht einfach `Math.random()` in einem `useMemo` sein darf.**
 * Genau so stand es an zwei Stellen, mit dem Kommentar „einmal mischen und
 * festhalten". Nur hält `useMemo` das nicht fest: React darf gemerkte Werte
 * jederzeit verwerfen und neu berechnen. Beim nächsten Mal fällt der Würfel
 * anders – und die Collage ordnet sich um. Also genau das Flackern, gegen das
 * der `useMemo` gedacht war.
 *
 * Der Ausweg trennt die zwei Dinge, die dort vermischt waren:
 *
 * 1. **Der Würfelwurf** passiert einmal nach dem Zeichnen ({@link useSaat}) –
 *    nicht währenddessen. Das Zeichnen bleibt damit berechenbar.
 * 2. **Die Auswahl** wird aus dieser Saat *gerechnet* und ist deshalb bei
 *    gleicher Saat immer dieselbe. Sie darf beliebig oft neu laufen.
 *
 * Nach außen bleibt alles wie vorher: bei jedem Öffnen der Seite eine andere
 * Anordnung, während des Betrachtens keine.
 */

import { useEffect, useState } from 'react'

/**
 * Eine Saat, die nach dem ersten Zeichnen einmal gezogen wird.
 *
 * Vor dem ersten Effekt steht sie auf 0 – dann ist die Auswahl kurz die
 * ungemischte. Für eine Hintergrund-Collage ist das unsichtbar; wichtiger ist,
 * dass das Zeichnen selbst keine Nebenwirkung hat.
 */
export function useSaat(): number {
  const [saat, setSaat] = useState(0)
  useEffect(() => {
    setSaat(Math.floor(Math.random() * 2 ** 31) || 1)
  }, [])
  return saat
}

/**
 * Ein berechenbarer Zufallsgenerator (mulberry32).
 *
 * Dieselbe Saat liefert immer dieselbe Folge – das ist hier der ganze Zweck.
 */
function generator(saat: number): () => number {
  let zustand = saat >>> 0
  return () => {
    zustand = (zustand + 0x6d2b79f5) >>> 0
    let t = zustand
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Eine Liste mischen – bei gleicher Saat immer gleich. */
export function mischen<T>(liste: readonly T[], saat: number): T[] {
  const alle = [...liste]
  if (!saat) return alle
  const wuerfel = generator(saat)
  for (let i = alle.length - 1; i > 0; i--) {
    const j = Math.floor(wuerfel() * (i + 1))
    ;[alle[i], alle[j]] = [alle[j], alle[i]]
  }
  return alle
}

/**
 * Einen Eintrag aus einer Liste ziehen – bei gleicher Saat immer denselben.
 *
 * ``strang`` unterscheidet mehrere Ziehungen aus derselben Saat: Ohne ihn
 * bekäme jede Rubrik denselben Wurf und damit dasselbe Bild.
 */
export function ziehen<T>(liste: readonly T[], saat: number, strang: number): T | null {
  if (liste.length === 0) return null
  if (!saat) return liste[0] ?? null
  const wuerfel = generator(saat + strang * 0x9e3779b9)
  return liste[Math.floor(wuerfel() * liste.length)] ?? null
}
