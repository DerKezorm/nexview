/**
 * „Fernseher verbinden": Code prüfen, dann bestätigen oder ablehnen.
 *
 * Die Zusagen der Seite: Ein Code aus dem QR-Code wird von selbst geprüft,
 * ein Elternteil kann ein Kind wählen und schickt dann dessen Nummer mit, und
 * Ablehnen schickt nichts außer dem Code.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
    logout: vi.fn(() => Promise.resolve()),
    restoreSession: vi.fn(async () => true),
    setSessionLostHandler: vi.fn(),
  }
})

import { ApiError, api } from '../api/client'
import { rendern } from '../test/rendern'
import { GeraetVerbindenPage } from './GeraetVerbindenPage'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

const GEFUNDEN = {
  code: 'BCDF-GHJK',
  geraet: 'Wohnzimmer',
  app: 'nexcinema',
  gilt_bis: '2026-11-14T18:10:00',
  kinder: [{ id: 7, name: 'Mia' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    return {}
  })
  schicken.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/geraete-code/pruefen') return GEFUNDEN
    if (pfad === '/api/geraete-code/bestaetigen') return { geraet: 'Kinderzimmer', konto: 'Mia' }
    return undefined
  })
})

describe('GeraetVerbindenPage', () => {
  it('prüft einen Code aus dem QR-Code von selbst und genau einmal', async () => {
    rendern(<GeraetVerbindenPage />, { pfad: '/link?code=BCDF-GHJK' })
    expect(await screen.findByText('nexcinema')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Wohnzimmer')).toBeInTheDocument()
    expect(schicken).toHaveBeenCalledTimes(1)
    expect(schicken).toHaveBeenCalledWith('/api/geraete-code/pruefen', { code: 'BCDF-GHJK' })
  })

  it('bestätigt für ein Kind mit dessen Nummer und dem neuen Namen', async () => {
    const nutzer = userEvent.setup()
    rendern(<GeraetVerbindenPage />, { pfad: '/link?code=BCDF-GHJK' })
    const name = await screen.findByDisplayValue('Wohnzimmer')
    await nutzer.clear(name)
    await nutzer.type(name, 'Kinderzimmer')
    await nutzer.selectOptions(screen.getByRole('combobox'), '7')
    await nutzer.click(screen.getByRole('button', { name: 'Verbinden' }))

    await waitFor(() =>
      expect(schicken).toHaveBeenCalledWith('/api/geraete-code/bestaetigen', {
        code: 'BCDF-GHJK',
        child_id: 7,
        name: 'Kinderzimmer',
      }),
    )
    expect(await screen.findByText(/Verbunden: Kinderzimmer für Mia/)).toBeInTheDocument()
  })

  it('bestätigt ohne Kinderwahl für das eigene Konto', async () => {
    const nutzer = userEvent.setup()
    rendern(<GeraetVerbindenPage />, { pfad: '/link?code=BCDF-GHJK' })
    await screen.findByText('nexcinema')
    await nutzer.click(screen.getByRole('button', { name: 'Verbinden' }))
    await waitFor(() =>
      expect(schicken).toHaveBeenCalledWith('/api/geraete-code/bestaetigen', {
        code: 'BCDF-GHJK',
        child_id: null,
        name: 'Wohnzimmer',
      }),
    )
  })

  it('lehnt ab', async () => {
    const nutzer = userEvent.setup()
    rendern(<GeraetVerbindenPage />, { pfad: '/link?code=BCDF-GHJK' })
    await screen.findByText('nexcinema')
    await nutzer.click(screen.getByRole('button', { name: 'Ablehnen' }))
    await waitFor(() =>
      expect(schicken).toHaveBeenCalledWith('/api/geraete-code/ablehnen', { code: 'BCDF-GHJK' }),
    )
    expect(await screen.findByText(/Abgelehnt/)).toBeInTheDocument()
    expect(schicken).not.toHaveBeenCalledWith('/api/geraete-code/bestaetigen', expect.anything())
  })

  it('nimmt einen Code von Hand und zeigt den Fehler des Servers', async () => {
    const nutzer = userEvent.setup()
    schicken.mockRejectedValueOnce(
      new ApiError(404, 'Diesen Code gibt es nicht (mehr).', 'device_code_not_found'),
    )
    rendern(<GeraetVerbindenPage />, { pfad: '/link' })
    expect(schicken).not.toHaveBeenCalled()
    await nutzer.type(screen.getByLabelText('Code'), 'bcdf-ghjk')
    await nutzer.click(screen.getByRole('button', { name: 'Weiter' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Diesen Code gibt es nicht')
    expect(schicken).toHaveBeenCalledWith('/api/geraete-code/pruefen', { code: 'BCDF-GHJK' })
  })

  it('zeigt die Kinderwahl nur, wenn es Kinder gibt', async () => {
    schicken.mockImplementation(async () => ({ ...GEFUNDEN, kinder: [] }))
    rendern(<GeraetVerbindenPage />, { pfad: '/link?code=BCDF-GHJK' })
    await screen.findByText('nexcinema')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })
})
