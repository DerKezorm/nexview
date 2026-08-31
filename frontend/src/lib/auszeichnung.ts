/**
 * Die Auszeichnungssprache der Hausordnung: Text hinein, Baum heraus.
 *
 * ⚠️ **Warum das hier steht und kein Markdown-Paket eingebunden ist.**
 * `services/csp.py` baut seine ganze Begründung darauf auf, dass Nexview
 * nirgends fremdes Markup einsetzt – kein `dangerouslySetInnerHTML`, kein
 * `innerHTML`, kein `eval`. Ein Markdown-Paket liefert HTML, und wer das
 * anzeigen will, muss genau diese Linie überschreiten. Dann wäre der Satz
 * „Nexview rendert nirgends fremdes HTML" nicht mehr wahr, und die Regeln,
 * die darauf aufbauen, wären es auch nicht.
 *
 * Also die andere Richtung: Aus dem Text entsteht hier eine **Datenstruktur**,
 * und `Hausordnungstext.tsx` baut daraus React-Elemente. Aus einem Text kann
 * damit nie Markup werden – die einzige vertretbare Bauart in einer Anwendung,
 * die fremde Titel, Kommentare und jetzt eben auch einen Betreibertext anzeigt.
 *
 * ⚠️ **`Betont.tsx` bleibt unangetastet.** Naheliegend wäre gewesen, es dort
 * zu erweitern – aber `Betont` läuft im „Was ist neu"-Fenster, und eine
 * Änderung an seinem Muster ändert stillschweigend dessen Darstellung. Ob
 * dieses Fenster später hierher umzieht, ist eine eigene Entscheidung.
 *
 * Die Sprache ist bewusst klein: genau das, was ein Regeltext braucht.
 *
 * ```
 * ## Überschrift         ### Kleinere Überschrift
 * Absatz, Leerzeile trennt.
 * **fett**  *kursiv*  `code`
 * - Aufzählung          1. Nummeriert
 * > Zitat
 * [Text](https://…)     nur http und https
 * ![Bildtext](bild:a1b2.png)
 * ---                   Trennlinie
 * ```
 *
 * Alles Unpaarige bleibt stehen, wie es dasteht – „3 * 4" behält sein
 * Sternchen. Dieselbe Regel wie in `Betont.tsx`, und aus demselben Grund:
 * Wer einen Regeltext schreibt, meint meistens genau das, was er tippt.
 */

/** Ein Stück Zeile: gewöhnlicher Text oder eine Auszeichnung. */
export type Teil =
  | { art: 'text'; text: string }
  | { art: 'fett'; text: string }
  | { art: 'kursiv'; text: string }
  | { art: 'code'; text: string }
  | { art: 'verweis'; text: string; ziel: string }

/** Ein Block: eine Überschrift, ein Absatz, eine Liste … */
export type Block =
  | { art: 'ueberschrift'; stufe: 2 | 3; inhalt: Teil[] }
  | { art: 'absatz'; inhalt: Teil[] }
  | { art: 'liste'; nummeriert: boolean; punkte: Teil[][] }
  | { art: 'zitat'; inhalt: Teil[] }
  | { art: 'bild'; name: string; text: string }
  | { art: 'trennlinie' }

/**
 * Erlaubte Ziele eines Verweises.
 *
 * ⚠️ **Nur `http` und `https`.** Ein `javascript:`-Ziel würde beim Klick
 * Code ausführen – in einem Text, den ein Betreiber schreibt und alle lesen.
 * Alles andere (`data:`, `file:`, relative Pfade) hat in einer Hausordnung
 * nichts zu suchen und wird als gewöhnlicher Text stehen gelassen, damit
 * niemand rätselt, wo sein Link geblieben ist.
 */
function zielErlaubt(ziel: string): boolean {
  return /^https?:\/\/[^\s]+$/i.test(ziel)
}

/** Trennt an `**…**`, `*…*`, `` `…` `` und `[…](…)` – Klammern bleiben drin. */
const ZEILEN_MUSTER = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g

/** Eine Zeile in ihre Teile zerlegen. */
export function zeile(text: string): Teil[] {
  const teile: Teil[] = []
  for (const stueck of text.split(ZEILEN_MUSTER)) {
    if (!stueck) continue

    if (stueck.startsWith('**') && stueck.endsWith('**') && stueck.length > 4) {
      teile.push({ art: 'fett', text: stueck.slice(2, -2) })
      continue
    }
    if (stueck.startsWith('`') && stueck.endsWith('`') && stueck.length > 2) {
      teile.push({ art: 'code', text: stueck.slice(1, -1) })
      continue
    }
    if (
      stueck.startsWith('*') &&
      stueck.endsWith('*') &&
      !stueck.startsWith('**') &&
      stueck.length > 2
    ) {
      teile.push({ art: 'kursiv', text: stueck.slice(1, -1) })
      continue
    }

    const verweis = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(stueck)
    if (verweis) {
      const [, beschriftung, ziel] = verweis
      // Ein nicht erlaubtes Ziel wird zu gewöhnlichem Text – sichtbar, aber
      // ohne Wirkung. Stillschweigend zu schlucken wäre schlechter: Der
      // Betreiber soll merken, dass sein Link nicht angekommen ist.
      teile.push(
        zielErlaubt(ziel)
          ? { art: 'verweis', text: beschriftung, ziel }
          : { art: 'text', text: stueck },
      )
      continue
    }

    teile.push({ art: 'text', text: stueck })
  }
  return teile
}

/** `![Text](bild:name)` – Bilder stehen immer allein in ihrer Zeile. */
const BILD_MUSTER = /^!\[([^\]]*)\]\(bild:([A-Za-z0-9._-]+)\)$/

/**
 * Text in Blöcke zerlegen.
 *
 * Zeilenweise und ohne Zustand über den Absatz hinaus: Das ist der Grund,
 * warum ein Tippfehler hier nie mehr kaputtmachen kann als die eine Zeile,
 * in der er steht.
 */
export function auszeichnung(text: string): Block[] {
  const bloecke: Block[] = []
  const zeilen = text.replace(/\r\n?/g, '\n').split('\n')

  let absatz: string[] = []
  let liste: { nummeriert: boolean; punkte: Teil[][] } | null = null

  const absatzSchliessen = () => {
    if (absatz.length) {
      bloecke.push({ art: 'absatz', inhalt: zeile(absatz.join(' ')) })
      absatz = []
    }
  }
  const listeSchliessen = () => {
    if (liste) {
      bloecke.push({ art: 'liste', ...liste })
      liste = null
    }
  }
  const alleSchliessen = () => {
    absatzSchliessen()
    listeSchliessen()
  }

  for (const roh of zeilen) {
    const text = roh.trim()

    if (!text) {
      alleSchliessen()
      continue
    }

    if (/^-{3,}$/.test(text)) {
      alleSchliessen()
      bloecke.push({ art: 'trennlinie' })
      continue
    }

    const bild = BILD_MUSTER.exec(text)
    if (bild) {
      alleSchliessen()
      bloecke.push({ art: 'bild', text: bild[1], name: bild[2] })
      continue
    }

    const ueberschrift = /^(#{2,3})\s+(.+)$/.exec(text)
    if (ueberschrift) {
      alleSchliessen()
      bloecke.push({
        art: 'ueberschrift',
        stufe: ueberschrift[1].length === 2 ? 2 : 3,
        inhalt: zeile(ueberschrift[2]),
      })
      continue
    }

    const zitat = /^>\s?(.*)$/.exec(text)
    if (zitat) {
      alleSchliessen()
      bloecke.push({ art: 'zitat', inhalt: zeile(zitat[1]) })
      continue
    }

    const punkt = /^([-*]|\d+\.)\s+(.+)$/.exec(text)
    if (punkt) {
      absatzSchliessen()
      const nummeriert = /\d/.test(punkt[1])
      // Wechselt die Art, fängt eine neue Liste an – sonst stünden
      // Aufzählung und Nummerierung in einem Block.
      if (liste && liste.nummeriert !== nummeriert) listeSchliessen()
      if (!liste) liste = { nummeriert, punkte: [] }
      liste.punkte.push(zeile(punkt[2]))
      continue
    }

    listeSchliessen()
    absatz.push(text)
  }

  alleSchliessen()
  return bloecke
}
