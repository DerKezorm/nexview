/**
 * Die letzte Meldung, wenn die Oberfläche gar nicht erst hochkommt.
 *
 * ⚠️ **Warum es das braucht.** Die Sprachdatei kommt seit der Aufteilung übers
 * Netz. Bleibt sie aus — abgebrochene Verbindung, ein Proxy dazwischen, ein
 * halb ausgetauschter Server nach einem Update —, dann startet React nie, und
 * der Besucher sieht eine weiße Seite ohne jede Erklärung. Fest eingebaute
 * Texte konnten nicht fehlen; diese Möglichkeit ist neu, und sie darf nicht
 * stumm bleiben.
 *
 * ⚠️ **Auf Englisch, und das ist kein Versehen.** Übersetzen könnte diesen
 * Satz nur der Katalog, der gerade fehlt. Englisch ist die Sprache, in der
 * Nexview nach außen spricht.
 *
 * ⚠️ **Reines HTML, kein React, keine Stilklassen.** Was hier noch läuft, darf
 * so wenig wie möglich voraussetzen — das Stilblatt kann genauso gut fehlen
 * wie die Texte. Deshalb stehen die Farben direkt am Element.
 */

/** Farben aus dem dunklen Grundton - notgedrungen von Hand, siehe oben. */
const TEXT = '#e6e7ea'
const GEDAEMPFT = '#9aa0aa'
const RAND = '#3a3d45'
const FLAECHE = '#1a1c21'

export function startFehlgeschlagen(fehler: unknown): void {
  // Auch wenn niemand die Konsole aufmacht: Wer es doch tut, soll den
  // eigentlichen Fehler finden und nicht nur die freundliche Fassung davon.
  console.error('Nexview could not finish loading.', fehler)

  const wurzel = document.getElementById('root')
  if (!wurzel) return

  const kasten = document.createElement('div')
  kasten.style.cssText = [
    'min-height:100dvh',
    'display:flex',
    'flex-direction:column',
    'align-items:center',
    'justify-content:center',
    'gap:12px',
    'padding:24px',
    'text-align:center',
    'font-family:system-ui,-apple-system,sans-serif',
  ].join(';')

  const titel = document.createElement('p')
  titel.textContent = 'Nexview could not finish loading.'
  titel.style.cssText = `margin:0;font-size:17px;color:${TEXT}`

  const erklaerung = document.createElement('p')
  erklaerung.textContent =
    'A part of the page did not arrive. This is usually a brief network problem, ' +
    'and reloading fixes it. If it keeps happening, the server may be mid-update.'
  erklaerung.style.cssText = `margin:0;max-width:34rem;font-size:14px;line-height:1.6;color:${GEDAEMPFT}`

  const knopf = document.createElement('button')
  knopf.type = 'button'
  knopf.textContent = 'Reload'
  knopf.style.cssText =
    `margin-top:8px;padding:9px 22px;border-radius:12px;border:1px solid ${RAND};` +
    `background:${FLAECHE};color:${TEXT};font-size:14px;cursor:pointer`
  knopf.addEventListener('click', () => window.location.reload())

  kasten.append(titel, erklaerung, knopf)
  wurzel.replaceChildren(kasten)
}
