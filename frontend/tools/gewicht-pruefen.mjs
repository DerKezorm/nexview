/* Die Waage an der Tür: Wie schwer ist der erste Besuch?
 *
 *   node tools/gewicht-pruefen.mjs        (nach `npm run build`)
 *
 * ⚠️ **Warum es das gibt.** Die Oberfläche war über drei Fassungen hinweg
 * gewachsen — 1.065 kB, 1.276 kB, 1.390 kB — und niemandem fiel es auf, weil
 * nichts danach fragte. Der Bau warnte zwar ab 500 kB, aber eine Warnung, die
 * den Bau durchgehen lässt, liest nach dem dritten Mal keiner mehr. Diese
 * Prüfung bricht ab.
 *
 * ⚠️ **UND DIE GRENZE WIRD BEIM FEHLSCHLAG NICHT HOCHGESETZT.** Das ist der
 * ganze Sinn der Sache. Eine Waage, an der man das Gewicht verstellt, sobald
 * sie anschlägt, misst nichts mehr — sie bestätigt nur noch jeden Zustand.
 * Schlägt sie an, gehört das Gewicht zurück: Was neu dazugekommen ist und
 * nicht jeder sofort braucht, wird nachgeliefert statt mitgetragen (siehe die
 * `lazy(...)`-Liste in `src/App.tsx`). Soll die Grenze wirklich steigen, ist
 * das eine Entscheidung des Betreibers und keine Zeile nebenbei.
 *
 * Gemessen wird, was ein Besucher beim Öffnen wirklich herunterlädt: der
 * Einstieg samt allem, was fest daran hängt, das Stilblatt und **eine**
 * Sprache — die schwerere, denn welche es trifft, weiß man vorher nicht.
 * Nachgelieferte Seiten zählen nicht mit; sie sind ja genau das, was ein
 * normaler Nutzer nie holt.
 */

import { readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

/** Die Grenze in Kilobyte. Lies den Absatz oben, bevor du sie anfasst. */
const GRENZE_KB = 900

/* 1 kB = 1000 Bytes, nicht 1024 - dieselbe Rechnung, die Vite beim Bauen
 * ausgibt. Sonst stuenden im selben Protokoll zwei Zahlen fuer dieselbe Datei,
 * und der Vergleich mit den bisher aufgeschriebenen Staenden (1.065, 1.276,
 * 1.390 kB) ginge daneben. */
const KILO = 1000

const hier = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.join(hier, '..', 'dist')
const MANIFEST = path.join(DIST, '.vite', 'manifest.json')

let manifest
try {
  manifest = JSON.parse(readFileSync(MANIFEST, 'utf8'))
} catch {
  console.error(
    `Kein Bau gefunden (${path.relative(process.cwd(), MANIFEST)}).\n` +
      'Erst `npm run build`, dann diese Prüfung.',
  )
  process.exit(2)
}

const eintragSchluessel = Object.keys(manifest).find((k) => manifest[k].isEntry)
if (!eintragSchluessel) {
  console.error('Im Manifest steht kein Einstiegspunkt - stimmt die Bau-Einstellung noch?')
  process.exit(2)
}

/**
 * Alles einsammeln, was **fest** am Einstieg hängt.
 *
 * `imports` sind die festen Abhängigkeiten; die holt der Browser mit.
 * `dynamicImports` bleiben absichtlich draußen — das ist der Nachschub.
 */
const fest = new Set()
function folgen(schluessel) {
  if (fest.has(schluessel) || !manifest[schluessel]) return
  fest.add(schluessel)
  for (const naechster of manifest[schluessel].imports ?? []) folgen(naechster)
}
folgen(eintragSchluessel)

const dateien = new Set()
for (const schluessel of fest) {
  const eintrag = manifest[schluessel]
  if (eintrag.file) dateien.add(eintrag.file)
  for (const stil of eintrag.css ?? []) dateien.add(stil)
}

/**
 * Dazu eine Sprache — die schwerere.
 *
 * ⚠️ Über den Manifest-Schlüssel und nicht über den Dateinamen: Der trägt
 * einen Prüfstempel, der sich bei jedem Bau ändert. Wird eine dritte Sprache
 * ergänzt, zählt sie hier automatisch mit.
 */
const sprachen = Object.keys(manifest).filter((k) => /^src\/i18n\/[a-z-]+\.json$/.test(k))
if (sprachen.length === 0) {
  console.error(
    'Keine Sprachdatei als eigenes Stück gefunden.\n' +
      'Entweder liegen die Texte wieder im Grundpaket (dann ist der Gewinn weg),\n' +
      'oder der Pfad hat sich geändert. Beides gehört angesehen, nicht übergangen.',
  )
  process.exit(2)
}

const groesse = (datei) => statSync(path.join(DIST, datei)).size
const schwersteSprache = sprachen
  .map((k) => manifest[k].file)
  .sort((a, b) => groesse(b) - groesse(a))[0]
dateien.add(schwersteSprache)

// ---------------------------------------------------------------------------

const kb = (bytes) => (bytes / KILO).toFixed(2).padStart(9)
const liste = [...dateien].sort((a, b) => groesse(b) - groesse(a))

let summe = 0
let gepackt = 0
console.log('Was ein Besucher beim Öffnen herunterlädt:\n')
for (const datei of liste) {
  const bytes = groesse(datei)
  const zip = gzipSync(readFileSync(path.join(DIST, datei))).length
  summe += bytes
  gepackt += zip
  console.log(`  ${kb(bytes)} kB   (gepackt ${kb(zip)} kB)   ${datei}`)
}
console.log(`\n  ${kb(summe)} kB   (gepackt ${kb(gepackt)} kB)   zusammen`)
console.log(`  ${kb(GRENZE_KB * KILO)} kB${' '.repeat(24)}Grenze\n`)

if (summe > GRENZE_KB * KILO) {
  console.error(
    `Zu schwer: ${(summe / KILO).toFixed(2)} kB statt höchstens ${GRENZE_KB} kB.\n\n` +
      'Die Grenze wird NICHT hochgesetzt. Was neu dazugekommen ist und nicht\n' +
      'jeder Besucher sofort braucht, gehört in die `lazy(...)`-Liste in\n' +
      'src/App.tsx - dann kommt es erst, wenn jemand es öffnet.\n\n' +
      'Soll die Grenze wirklich steigen, ist das eine Frage an den Betreiber.',
  )
  process.exit(1)
}

console.log('In Ordnung.')
