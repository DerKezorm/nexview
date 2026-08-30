/**
 * Der Verweis auf einen Unterreiter — und warum er ins Leere lief.
 *
 * ⚠️ **`?unter=qualitaet` landete immer auf „Allgemein".** Der Startwert wurde
 * korrekt aus der Adresse gelesen; überschrieben hat ihn der Rückfall dahinter.
 * Beim allerersten Zeichnen ist die Konfiguration noch nicht da, also gilt „es
 * gibt keine Radarr-Instanz", also fällt der Reiter „Qualitätsprofile" aus der
 * Liste — und der Rückfall sprang auf „Allgemein", bevor die Konfiguration
 * überhaupt eintraf.
 *
 * ⚠️ **Und `?unter=radarr` funktionierte.** Genau das hat die Ursache versteckt:
 * Betroffen ist jeder Unterreiter mit Bedingung, keiner ohne. Wer nur den
 * funktionierenden Fall probiert, hält den Verweis für heil.
 */

import { beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../../api/client'
import { rendernSchlicht } from '../../test/rendern'
import { AdminServicesSettings } from './AdminServicesSettings'

const holen = vi.mocked(api.get)

/**
 * Antworten je Adresse.
 *
 * ⚠️ Absichtlich **nicht** sofort aufgelöst: Der Fehler steckte im allerersten
 * Zeichnen, als noch keine Antwort da war. Ein Mock, der synchron liefert,
 * würde genau den Moment überspringen, um den es geht.
 */
/** Adressen, die eine Liste liefern. */
const LISTEN = [
  '/api/settings/qualitaetsprofile',
  '/api/settings/instanzen/gesundheit',
]

function antworten(config: Record<string, unknown>) {
  holen.mockImplementation((pfad: string) => {
    // Reihenfolge zaehlt: der laengere Pfad zuerst, sonst faengt '/api/config'
    // auch '/api/config/regions' ab.
    if (pfad.startsWith('/api/config/regions')) return Promise.resolve([])
    if (pfad.startsWith('/api/config')) return Promise.resolve(config)
    // Was die Seite durchlaeuft, muss eine Liste sein - ein leeres Objekt
    // laesst sie mit "map is not a function" abstuerzen.
    if (LISTEN.some((l) => pfad.startsWith(l))) return Promise.resolve([])
    return Promise.resolve({})
  })
}

/** Welcher Unterreiter steht auf „ausgewählt"? */
function aktiverReiter(): string | undefined {
  return screen
    .getAllByRole('tab')
    .find((r) => r.getAttribute('aria-selected') === 'true')?.textContent
    ?.trim()
}

beforeEach(() => {
  holen.mockReset()
})

it('bleibt auf dem Reiter aus der Adresse, auch wenn er eine Bedingung hat', async () => {
  antworten({ radarr_configured: true })
  rendernSchlicht(<AdminServicesSettings startUnter="qualitaet" />)

  await waitFor(() => {
    expect(aktiverReiter()).toContain('Qualitätsprofile')
  })
})

it('wechselt trotzdem weg, wenn es den Reiter wirklich nicht gibt', async () => {
  // ⚠️ Der Fall, für den der Rückfall gedacht ist: keine einzige Instanz. Ein
  // leerer Bereich ohne erkennbaren Grund wäre schlimmer als der Sprung
  // zurück - der Rückfall darf durch die Reparatur nicht verschwinden.
  antworten({ radarr_configured: false, sonarr_configured: false })
  rendernSchlicht(<AdminServicesSettings startUnter="qualitaet" />)

  await waitFor(() => {
    expect(holen).toHaveBeenCalled()
  })
  await waitFor(() => {
    expect(aktiverReiter()).not.toContain('Qualitätsprofile')
  })
})
