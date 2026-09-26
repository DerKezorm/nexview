/**
 * Der Antrag „Konto löschen" trägt die Bestätigung mit.
 *
 * ⚠️ Der Server legt seit dem großen Prüfgang ohne `bestaetigt: true` keinen
 * Antrag mehr an (ein leerer POST erzeugte vorher ein echtes Ticket). Schickte
 * die Oberfläche das Feld nicht, bekäme jeder Nutzer nach der Rückfrage nur
 * noch einen Fehler, und kein Backend-Test sähe es.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  }
})

import { api } from '../../api/client'
import { rendern } from '../../test/rendern'
import { KontoLoeschen } from './KontoLoeschen'

const schicken = vi.mocked(api.post)

beforeEach(() => {
  vi.clearAllMocks()
  schicken.mockResolvedValue({})
  vi.mocked(api.get).mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    throw new Error(`Unerwartet: ${pfad}`)
  })
})

describe('Konto löschen', () => {
  it('schickt den Antrag erst nach der Rückfrage, und dann bestätigt', async () => {
    rendern(<KontoLoeschen />)
    const nutzer = userEvent.setup()

    await nutzer.click(await screen.findByRole('button', { name: 'Löschung beantragen' }))
    expect(schicken).not.toHaveBeenCalled()

    await nutzer.click(await screen.findByRole('button', { name: 'Antrag stellen' }))

    await waitFor(() =>
      expect(schicken).toHaveBeenCalledWith('/api/tickets/kontoaufloesung', { bestaetigt: true }),
    )
  })
})
