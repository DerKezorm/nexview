/* Die Lage eines Browsers: die Entscheidung, nicht die Browser-Anbindung.
 *
 * Jeder Test dreht genau eine Sache um. Die Reihenfolge der Gründe wird
 * ausdrücklich an den Fällen geprüft, in denen zwei Gründe zugleich zutreffen,
 * denn nur dort ändert ein Vertauschen etwas.
 */
import { describe, expect, it } from 'vitest'

import { lageAus } from './pushlage'
import type { Umstaende } from './pushlage'

/** Ein Rechner, auf dem alles stimmt. */
const GUT: Umstaende = {
  sicher: true,
  kannPush: true,
  istApple: false,
  alsApp: false,
  erlaubnis: 'granted',
  angemeldet: true,
  abgemeldet: false,
}

const mit = (teil: Partial<Umstaende>): Umstaende => ({ ...GUT, ...teil })

describe('lageAus', () => {
  it('meldet bereit, wenn Erlaubnis UND Anmeldung stehen', () => {
    expect(lageAus(GUT)).toBe('bereit')
  })

  it('meldet NICHT bereit, wenn die Erlaubnis steht und die Anmeldung fehlt', () => {
    expect(lageAus(mit({ angemeldet: false }))).toBe('erlaubt_ohne_anmeldung')
  })

  it('meldet abgemeldet, wenn der Mensch das Gerät selbst entfernt hat', () => {
    // ⚠️ Sonst meldete die Selbstheilung das Gerät beim nächsten Öffnen still
    // wieder an, weil die Erlaubnis im Browser noch steht.
    expect(lageAus(mit({ angemeldet: false, abgemeldet: true }))).toBe('abgemeldet')
  })

  it('ein angemeldetes Gerät ist bereit, auch wenn ein alter Merker steht', () => {
    expect(lageAus(mit({ angemeldet: true, abgemeldet: true }))).toBe('bereit')
  })

  it('meldet offen, solange der Browser nicht gefragt wurde', () => {
    expect(lageAus(mit({ erlaubnis: 'default', angemeldet: false }))).toBe('offen')
  })

  it('meldet abgelehnt, wenn der Browser Nein gesagt hat', () => {
    expect(lageAus(mit({ erlaubnis: 'denied', angemeldet: false }))).toBe('abgelehnt')
  })

  it('meldet unmoeglich, wenn dem Browser die Schnittstellen fehlen', () => {
    expect(lageAus(mit({ kannPush: false }))).toBe('unmoeglich')
  })

  it('nennt über http den wahren Grund, nicht die fehlende Schnittstelle', () => {
    /* ⚠️ Über http gibt es keinen Service Worker, beide Gründe treffen zu.
       „Dieser Browser kann keine Meldungen" schickte jemanden im Heimnetz auf
       die Suche nach einem anderen Browser, und der könnte es auch nicht. */
    expect(lageAus(mit({ sicher: false, kannPush: false }))).toBe('kein_https')
  })

  it('unmoeglich schlägt den Home-Bildschirm', () => {
    /* Ein iPhone mit zu altem iOS in einem gewöhnlichen Reiter: Beide Gründe
       treffen zu. „Leg es auf den Home-Bildschirm" schickte dort jemanden auf
       eine Fährte, die nie ankommt. */
    expect(
      lageAus(
        mit({
          kannPush: false,
          istApple: true,
          alsApp: false,
          erlaubnis: 'granted',
        }),
      ),
    ).toBe('unmoeglich')
  })

  it('verlangt auf Apple den Home-Bildschirm, auch bei erteilter Erlaubnis', () => {
    expect(lageAus(mit({ istApple: true, alsApp: false }))).toBe('kein_home')
  })

  it('ist auf Apple vom Home-Bildschirm aus ganz normal', () => {
    expect(lageAus(mit({ istApple: true, alsApp: true }))).toBe('bereit')
  })

  it('verlangt den Home-Bildschirm NUR auf Apple', () => {
    expect(lageAus(mit({ istApple: false, alsApp: false }))).toBe('bereit')
  })
})
