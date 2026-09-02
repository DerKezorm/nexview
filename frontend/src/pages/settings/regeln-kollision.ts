/**
 * Stoßen zwei Regeln zusammen? — die einzige echte Rechnung der Regelseite.
 *
 * ⚠️ **Sie liegt bewusst in einer eigenen Datei.** Erstens verlangt das die
 * Fast-Refresh-Regel (eine Komponentendatei exportiert nur Komponenten),
 * zweitens ist sie damit für sich prüfbar — und drittens ist sie die Stelle,
 * an der ein Fehler am teuersten wäre: Was sie meldet, liest der Betreiber
 * als Tatsache.
 *
 * ⚠️ **Der Kern sind die Grenzen: „von" schließt ein, „bis" schließt aus.**
 * Nur so überschneiden sich „ab 5" und „unter 5" nicht. Mit zwei
 * einschließenden Grenzen meldete die Oberfläche einen Widerspruch, den es
 * nicht gibt.
 *
 * Und sie geht überhaupt nur auf, weil Bedingungen ausschließlich mit UND
 * verknüpft sind: Jede Regel ist damit ein Kasten, und zwei Kästen stoßen
 * zusammen, wenn sich in *jeder* Dimension die Bereiche überschneiden. Mit
 * Klammern und ODER wäre das nicht mehr entscheidbar.
 */

export type FeldArt = 'zahl' | 'menge'

export type Bedingung = {
  feld: string
  von?: number | null
  bis?: number | null
  werte?: string[]
}

export type Feld = {
  kennung: string
  name: string
  art: FeldArt
  einheit?: string
  hinweis?: string
  werte?: { wert: string; name: string }[]
}

export type Entscheidung = 'freigeben' | 'ablehnen'

export type Regel = {
  id: number
  position: number
  name: string
  aktiv: boolean
  bedingungen: Bedingung[]
  entscheidung: Entscheidung
  hausbestand: boolean
  begruendung: string
  trotzdem_fragen: boolean
}

function bedingungFuer(regel: Regel, feld: string): Bedingung | undefined {
  return regel.bedingungen.find((b) => b.feld === feld)
}

/**
 * Können zwei Regeln denselben Titel treffen?
 *
 * ⚠️ **„von" schließt ein, „bis" schließt aus** — genau wie im Dienst. Mit zwei
 * einschließenden Grenzen meldete die Oberfläche „unter 5" und „ab 5" als
 * Widerspruch, den es nicht gibt.
 */
export function ueberschneiden(a: Regel, b: Regel, felder: Feld[]): boolean {
  for (const feld of felder) {
    const x = bedingungFuer(a, feld.kennung)
    const y = bedingungFuer(b, feld.kennung)
    if (!x || !y) continue
    if (feld.art === 'zahl') {
      const xv = x.von ?? -Infinity
      const xb = x.bis ?? Infinity
      const yv = y.von ?? -Infinity
      const yb = y.bis ?? Infinity
      if (!(xv < yb && yv < xb)) return false
    } else if (!(x.werte ?? []).some((w) => (y.werte ?? []).includes(w))) {
      return false
    }
  }
  return true
}

