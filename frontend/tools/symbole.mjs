/* Die Symbole für den Home-Bildschirm einmal rastern.
 *
 * ⚠️ **Ohne sie gibt es auf dem iPhone keine Meldungen.** Apple liefert Web
 * Push nur an Seiten, die auf dem Home-Bildschirm liegen, und dorthin kommt
 * nur, was ein Manifest mit Symbolen hat. Ein fehlendes Symbol ist deshalb
 * kein hässliches Kachelbild, sondern eine Funktion, die stumm ausbleibt.
 *
 * ⚠️ **Zwei Sorten, und die zweite ist nicht dieselbe in anderer Größe.**
 * Android beschneidet ein „maskable"-Symbol auf eine Form seiner Wahl:
 * Kreis, Squircle, Tropfen. Alles außerhalb der inneren 80 Prozent kann
 * wegfallen. Wer dasselbe Bild für beide nimmt, verliert dort den Rand der
 * Kachel; deshalb steht das Zeichen dort kleiner in einer vollen Fläche.
 * Dasselbe Bild dient als Apple-Touch-Icon: iOS rundet die Ecken selbst und
 * legt unter durchsichtige Ecken schwarz.
 *
 * ⚠️ **Gerastert wird mit Playwright**, das für die Oberflächentests ohnehin
 * da ist. Das Frontend hat keine Bildbibliothek, und eine dafür nachzuziehen
 * wäre ein schweres Paket für fünf Bilder. Wer das Zeichen ändert, lässt
 * dieses Skript einmal laufen:  node tools/symbole.mjs
 */
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

/* Dasselbe Zeichen wie in public/logo.svg. ⚠️ Wer es hier ändert, ändert es
   dort mit, sonst sieht das Symbol auf dem Handy anders aus als das Logo in
   der Kopfzeile. */
const VERLAUF = `
  <defs><linearGradient id="nv" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#ff3b4e"/><stop offset="55%" stop-color="#e11d2f"/>
    <stop offset="100%" stop-color="#8f0f1c"/></linearGradient></defs>`
const ZEICHEN = `
  <path d="M12 32c6.5-9 13.5-13.5 20-13.5S45.5 23 52 32c-6.5 9-13.5 13.5-20 13.5S18.5 41 12 32Z"
        fill="none" stroke="url(#nv)" stroke-width="3.2" stroke-linejoin="round"/>
  <path d="M28 25.5 40 32l-12 6.5Z" fill="url(#nv)"/>`

/** Das gewöhnliche Symbol: die gerundete Kachel aus dem Logo. */
function kachel(groesse) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
    width="${groesse}" height="${groesse}">${VERLAUF}
    <rect x="2" y="2" width="60" height="60" rx="16" fill="#141019"/>
    <rect x="2" y="2" width="60" height="60" rx="16" fill="none"
          stroke="url(#nv)" stroke-width="2.5" stroke-opacity=".55"/>
    ${ZEICHEN}
  </svg>`
}

/** Das beschneidbare: volle Fläche, Zeichen in der sicheren Mitte. */
function vollflaechig(groesse, massstab) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
    width="${groesse}" height="${groesse}">${VERLAUF}
    <rect width="64" height="64" fill="#141019"/>
    <g transform="translate(32 32) scale(${massstab}) translate(-32 -32)">${ZEICHEN}</g>
  </svg>`
}

/* ⚠️ **Das Abzeichen ist einfarbig und wird eingefärbt.** Android legt es
   als kleine Silhouette in die Statusleiste: Alles, was nicht durchsichtig
   ist, wird weiß. Ein farbiges Bild würde dort zu einem weißen Klotz. */
function silhouette(groesse) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
    width="${groesse}" height="${groesse}">
    <path d="M8 32c7.5-10.5 15.5-15.5 24-15.5S48.5 21.5 56 32c-7.5 10.5-15.5 15.5-24 15.5S15.5 42.5 8 32Z"
          fill="none" stroke="#ffffff" stroke-width="4" stroke-linejoin="round"/>
    <path d="M27 24.5 41 32l-14 7.5Z" fill="#ffffff"/>
  </svg>`
}

const BILDER = [
  ['public/icon-192.png', kachel(192), 192],
  ['public/icon-512.png', kachel(512), 512],
  ['public/icon-maskable-512.png', vollflaechig(512, 0.72), 512],
  ['public/apple-touch-icon.png', vollflaechig(180, 0.82), 180],
  ['public/badge-96.png', silhouette(96), 96],
]

mkdirSync('public', { recursive: true })
const browser = await chromium.launch()
for (const [pfad, svg, groesse] of BILDER) {
  const seite = await browser.newPage({ viewport: { width: groesse, height: groesse } })
  await seite.setContent(`<body style="margin:0;background:transparent">${svg}</body>`)
  await seite.locator('svg').screenshot({ path: pfad, omitBackground: true })
  await seite.close()
  console.log('geschrieben:', pfad)
}
await browser.close()
