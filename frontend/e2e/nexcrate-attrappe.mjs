/**
 * Eine nexcrate, die nur so viel kann, wie der Umstiegsassistent fragt.
 *
 * ⚠️ **Nicht die Attrappe aus den Python-Tests.** Die hängt auf HTTP-Ebene
 * unter dem echten Client im selben Prozess; hier läuft ein eigener Server,
 * weil der Ende-zu-Ende-Test ein echtes Backend in einem eigenen Prozess
 * bedient. Die Felder sind dieselben gemessenen — Quelle ist
 * `homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md`.
 *
 * Geantwortet wird auf genau vier Adressen: `/system`, `/versions`,
 * `/titles/lookup` und `/health`. Alles andere ist `404` mit nexcrates flacher
 * Fehlerform — dann fällt im Test auf, wenn der Assistent mehr braucht, als
 * hier steht.
 */

import { createServer } from 'node:http'

export const SCHLUESSEL = 'nxv_e2e_key_only_for_tests_aaaaaaaaaaaaaaaaaaaa'

export const FILM_HD = 'v_6a0763e8'
export const FILM_UHD = 'v_b4272077'
export const SERIE_HD = 'v_96e766c4'
export const SERIE_UHD = 'v_66260bea'

const SYSTEM = {
  app: 'nexcrate',
  version: '0.1.0',
  contract: { major: 1, stage: 'V5' },
  installation_id: 'e2e77y2nj5lqkwsd',
  scopes: ['read', 'request', 'operate'],
  capabilities: {
    movies: true,
    series: true,
    music: true,
    calendar: true,
    events: true,
    stream: true,
    event_types: [],
    kinds: {
      movie: { read: true, request: true, operate: true, ratings: false },
      series: { read: true, request: true, operate: true, ratings: false },
    },
    wishes_search_at_once: true,
    anime: true,
  },
  web_url: null,
  links: {},
  update: { current: '0.1.0', latest: null, available: null, checked_at: null },
}

const fassung = (id, kind, name, order, tier) => ({
  version_id: id,
  kind,
  name,
  order,
  tier,
  ready: true,
  reasons: [],
})

const VERSIONEN = [
  fassung(FILM_HD, 'movie', 'Movies', 1, 'hd'),
  fassung(FILM_UHD, 'movie', 'Movies 4K', 2, 'uhd'),
  fassung(SERIE_HD, 'series', 'Series', 1, 'hd'),
  fassung(SERIE_UHD, 'series', 'Series 4K', 2, 'uhd'),
]

/**
 * Startet die Attrappe und gibt `{ url, stoppen }` zurück.
 *
 * Port 0 heißt: das Betriebssystem sucht einen freien. Ein fester Port wäre
 * eine Verabredung mit allem, was sonst noch auf dem Rechner läuft.
 */
export async function attrappeStarten() {
  const server = createServer((anfrage, antwort) => {
    const pfad = (anfrage.url || '').split('?')[0].replace(/^\/api\/v1/, '')
    const senden = (status, koerper) => {
      antwort.writeHead(status, { 'content-type': 'application/json' })
      antwort.end(JSON.stringify(koerper))
    }

    // ⚠️ Der Schlüssel gehört in die Kopfzeile, nicht in die Adresse (N2) –
    // und zwar als `Authorization: Bearer …`, so wie der echte Client es
    // schickt. Ein anderer Kopfzeilenname hier ließe den Test eine Attrappe
    // prüfen statt nexcrates Vertrag.
    if (anfrage.headers.authorization !== `Bearer ${SCHLUESSEL}`) {
      senden(401, { code: 'key_rejected', message: 'key rejected', params: {} })
      return
    }

    if (pfad === '/system') return senden(200, SYSTEM)
    if (pfad === '/versions') return senden(200, { items: VERSIONEN })
    if (pfad === '/health') return senden(200, { items: [] })
    if (pfad === '/titles/lookup') {
      let roh = ''
      anfrage.on('data', (stueck) => (roh += stueck))
      anfrage.on('end', () => {
        const items = JSON.parse(roh || '{}').items || []
        // Eine frische Installation hat nichts, was nexcrate kennen könnte.
        senden(200, { items: items.map(() => ({ known: false })) })
      })
      return
    }
    senden(404, { code: 'path_unknown', message: pfad, params: {} })
  })

  await new Promise((fertig) => server.listen(0, '127.0.0.1', fertig))
  const { port } = server.address()
  return {
    url: `http://127.0.0.1:${port}`,
    stoppen: () => new Promise((fertig) => server.close(fertig)),
  }
}
