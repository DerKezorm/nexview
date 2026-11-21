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
 * Das Apple-Touch-Icon ist randlos wie das Symbol der iPhone-App: iOS rundet
 * die Ecken selbst und legt unter durchsichtige Ecken schwarz.
 *
 * ⚠️ **Gerastert wird mit Playwright**, das für die Oberflächentests ohnehin
 * da ist. Das Frontend hat keine Bildbibliothek, und eine dafür nachzuziehen
 * wäre ein schweres Paket für fünf Bilder. Wer das Zeichen ändert, lässt
 * dieses Skript einmal laufen:  node tools/symbole.mjs
 */
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

/* Dasselbe Zeichen wie in public/logo.svg und components/Logo.tsx, in der
   hellen Fassung (weisse Kachel). ⚠️ Wer es hier ändert, ändert es dort mit,
   sonst sieht das Symbol auf dem Handy anders aus als das Logo in der
   Kopfzeile. Die Quelle aller Fassungen ist nexapps/icons-vergleich/neu.py. */
const GRUND = '#fff'
const VERLAUF = `
  <defs><linearGradient id="g" gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
    <stop offset="0" stop-color="#ff8a95"/><stop offset=".5" stop-color="#e11d2f"/>
    <stop offset="1" stop-color="#8f0f1c"/></linearGradient></defs>`
const ZEICHEN = `
  <g fill="${GRUND}" stroke="url(#g)" stroke-width="3.4">
    <circle cx="20.5" cy="27" r="5"/><circle cx="27.5" cy="21.5" r="5.5"/>
    <circle cx="36.5" cy="21.5" r="5.5"/><circle cx="43.5" cy="27" r="5"/>
    <circle cx="32" cy="26.5" r="5.5"/></g>
  <path d="M17.5 31H46.5L42.8 52H21.2Z" fill="${GRUND}" stroke="url(#g)" stroke-width="4.2"
        stroke-linejoin="round"/>
  <path d="M27 31.5 27.8 51.5M37 31.5 36.2 51.5" stroke="#d90f25" stroke-width="3.6"/>
  <path d="M15.5 31H48.5" stroke="url(#g)" stroke-width="4.2" stroke-linecap="round"/>`

/** Das gewöhnliche Symbol: die gerundete Kachel aus dem Logo. */
function kachel(groesse) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
    width="${groesse}" height="${groesse}">${VERLAUF}
    <rect x="2" y="2" width="60" height="60" rx="16" fill="${GRUND}"/>
    <rect x="2.75" y="2.75" width="58.5" height="58.5" rx="15.3" fill="none"
          stroke="#e11d2f" stroke-opacity=".3" stroke-width="1.5"/>
    ${ZEICHEN}
  </svg>`
}

/** Das beschneidbare: volle Fläche, Zeichen in der sicheren Mitte. Der Becher
    sitzt etwas unter der Mitte (y 16 bis 53,5), darum wird um 32/34,75
    verkleinert und dorthin zurückgeschoben, wo die Mitte der Fläche ist. */
function vollflaechig(groesse, massstab) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
    width="${groesse}" height="${groesse}">${VERLAUF}
    <rect width="64" height="64" fill="${GRUND}"/>
    <g transform="translate(32 32) scale(${massstab}) translate(-32 -34.75)">${ZEICHEN}</g>
  </svg>`
}

/** Für iOS: randlos wie das App-Symbol der iPhone-App; iOS rundet selbst. */
function randlos(groesse) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="2 2 60 60"
    width="${groesse}" height="${groesse}">${VERLAUF}
    <rect x="2" y="2" width="60" height="60" fill="${GRUND}"/>
    ${ZEICHEN}
  </svg>`
}

/* ⚠️ **Das Abzeichen ist einfarbig und wird eingefärbt.** Android legt es
   als kleine Silhouette in die Statusleiste: Alles, was nicht durchsichtig
   ist, wird weiß. Ein farbiges Bild würde dort zu einem weißen Klotz. Darum
   die Körner als volle Wolke und die Streifen als Lücken im Becher. */
function silhouette(groesse) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
    width="${groesse}" height="${groesse}">
    <defs><mask id="m"><rect width="64" height="64" fill="#fff"/>
      <path d="M27 33 27.8 52M37 33 36.2 52" stroke="#000" stroke-width="3.6"/></mask></defs>
    <g fill="#ffffff">
      <circle cx="18.5" cy="26" r="6.5"/><circle cx="26.5" cy="18.5" r="7"/>
      <circle cx="37.5" cy="18.5" r="7"/><circle cx="45.5" cy="26" r="6.5"/>
      <circle cx="32" cy="24" r="7"/>
    </g>
    <path d="M13 29H51L46.5 55H17.5Z" fill="#ffffff" stroke="#ffffff" stroke-width="2"
          stroke-linejoin="round" mask="url(#m)"/>
  </svg>`
}

const BILDER = [
  ['public/icon-192.png', kachel(192), 192],
  ['public/icon-512.png', kachel(512), 512],
  ['public/icon-maskable-512.png', vollflaechig(512, 0.78), 512],
  ['public/apple-touch-icon.png', randlos(180), 180],
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
