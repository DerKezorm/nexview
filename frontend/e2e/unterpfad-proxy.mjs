/**
 * Nachgebauter Reverse Proxy für den Unterpfad-Test.
 *
 * Spielt den „Pförtner", hinter dem Nexview unter /nexview/… wohnt - in beiden
 * Betriebsarten, die es draußen gibt:
 *
 *   durchreichen   schickt die Anfrage mitsamt /nexview/… weiter
 *   abschneiden    entfernt /nexview und schickt nur den Rest
 *
 * ⚠️ Alles außerhalb der Basis antwortet mit 404 - mit Absicht. Bei einem
 * echten Proxy führt die Domain-Wurzel woandershin; würde Nexview auch nur
 * ein Bild oder einen API-Aufruf ohne Vorbau anfragen, soll der Test das
 * sehen statt es zu verschlucken.
 *
 * Bewusst nur Node-Bordmittel, keine neue Abhängigkeit.
 */

import http from 'node:http'

const port = Number(process.env.PROXY_PORT)
const ziel = Number(process.env.PROXY_ZIEL)
const basis = process.env.PROXY_BASIS || '/nexview'
const modus = process.env.PROXY_MODUS // 'durchreichen' | 'abschneiden'

if (!port || !ziel || !['durchreichen', 'abschneiden'].includes(modus)) {
  console.error('PROXY_PORT, PROXY_ZIEL und PROXY_MODUS (durchreichen|abschneiden) sind Pflicht.')
  process.exit(1)
}

http
  .createServer((anfrage, antwort) => {
    const innerhalb = anfrage.url === basis || anfrage.url.startsWith(`${basis}/`)
    if (!innerhalb) {
      antwort.statusCode = 404
      antwort.end(`outside ${basis} - a real proxy would route this elsewhere`)
      return
    }

    const pfad =
      modus === 'abschneiden' ? anfrage.url.slice(basis.length) || '/' : anfrage.url

    const weiter = http.request(
      {
        host: '127.0.0.1',
        port: ziel,
        path: pfad,
        method: anfrage.method,
        headers: { ...anfrage.headers, host: `127.0.0.1:${ziel}` },
      },
      (vomZiel) => {
        antwort.writeHead(vomZiel.statusCode, vomZiel.headers)
        vomZiel.pipe(antwort)
      },
    )
    weiter.on('error', () => {
      antwort.statusCode = 502
      antwort.end('backend not reachable')
    })
    anfrage.pipe(weiter)
  })
  .listen(port, '127.0.0.1', () => {
    console.log(`Unterpfad-Proxy (${modus}) auf ${port} -> ${ziel}${basis}`)
  })
