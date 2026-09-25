/**
 * Nach einer Anfrage lädt auch „Woran es hängt“ neu (Rundgang 2, R2-4).
 *
 * Gemessen an der Live-Instanz am 25.09.2026: Nach dem Anfragen fehlte der
 * Abschnitt auf der Titelseite, bis man neu lud; zwischen Anfrage und Neuladen
 * ging kein Abruf an `/api/beschaffung/warum/…`.
 */

import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { anfragenStandNeuLaden } from './refresh'

describe('anfragenStandNeuLaden', () => {
  it('erklärt „Woran es hängt“ für veraltet', () => {
    const client = new QueryClient()
    const warum = ['beschaffung', 'warum', 'movie', 945937]
    client.setQueryData(warum, { beantwortbar: true, bekannt: false, gruende: [] })

    anfragenStandNeuLaden(client)

    expect(client.getQueryState(warum)?.isInvalidated).toBe(true)
  })

  it('lässt den Papierkorb in Ruhe', () => {
    const client = new QueryClient()
    const papierkorb = ['beschaffung', 'papierkorb']
    client.setQueryData(papierkorb, [])

    anfragenStandNeuLaden(client)

    expect(client.getQueryState(papierkorb)?.isInvalidated).toBe(false)
  })
})
