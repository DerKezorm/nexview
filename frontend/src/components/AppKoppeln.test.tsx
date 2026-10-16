/**
 * „iPhone-App verbinden": Code anlegen, nachfragen, aufräumen.
 *
 * Die zwei Zusagen des Fensters: Es zeigt nach dem Verbinden, unter welchem
 * Namen das Gerät steht, und es zieht einen Code zurück, den niemand gescannt
 * hat, sobald es zugeht.
 */

import { useState } from 'react'
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
import { AppKoppeln } from './AppKoppeln'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)
const loeschen = vi.mocked(api.delete)

const CODE = {
  kennung: 'k-1',
  gilt_bis: '2026-10-16T18:10:00',
  adresse: 'https://kino.example.com',
  link: 'nexview://connect?address=x&pairing=k-1&secret=s',
  qr_svg: '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
}

let stand = 'wartet'

beforeEach(() => {
  vi.clearAllMocks()
  stand = 'wartet'
  loeschen.mockResolvedValue(undefined)
  schicken.mockImplementation(async (pfad: string) => (pfad === '/api/me/app-kopplung' ? CODE : {}))
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/me/app-kopplung/k-1') {
      return { stand, geraet: stand === 'verbunden' ? 'iPhone von Jonas' : null }
    }
    return {}
  })
})

/** Wie im Profil: Das Fenster geht zu, indem der Aufrufer ``offen`` umschaltet. */
function Rahmen({ onVerbunden }: { onVerbunden: () => void }) {
  const [offen, setOffen] = useState(true)
  return (
    <AppKoppeln
      offen={offen}
      pfad="/api/me/app-kopplung"
      titel="iPhone-App verbinden"
      anleitung="Scanne den Code."
      verbundenText={(geraet) => `Verbunden: ${geraet}.`}
      onSchliessen={() => setOffen(false)}
      onVerbunden={onVerbunden}
    />
  )
}

function fenster(onVerbunden = vi.fn()) {
  const ergebnis = rendern(<Rahmen onVerbunden={onVerbunden} />)
  return { onVerbunden, ergebnis }
}

describe('iPhone-App verbinden', () => {
  it('legt einen Code mit der Adresse des Browsers an und zeigt ihn', async () => {
    fenster()
    expect(await screen.findByAltText('QR-Code zum Verbinden der App')).toBeInTheDocument()
    expect(schicken).toHaveBeenCalledWith('/api/me/app-kopplung', { adresse: window.location.origin })
    expect(screen.getByText('Die App verbindet sich mit https://kino.example.com.')).toBeInTheDocument()
  })

  it('meldet das Gerät, sobald die App den Schlüssel abgeholt hat', async () => {
    const { onVerbunden } = fenster()
    await screen.findByAltText('QR-Code zum Verbinden der App')
    stand = 'verbunden'
    expect(await screen.findByText('Verbunden: iPhone von Jonas.', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(onVerbunden).toHaveBeenCalled()
  })

  it('zieht einen ungescannten Code beim Schließen zurück', async () => {
    fenster()
    await screen.findByAltText('QR-Code zum Verbinden der App')
    await userEvent.click(screen.getByRole('button', { name: 'Schließen' }))
    await waitFor(() => expect(loeschen).toHaveBeenCalledWith('/api/me/app-kopplung/k-1'))
    expect(screen.queryByAltText('QR-Code zum Verbinden der App')).not.toBeInTheDocument()
  })

  it('zieht den Code auch zurück, wenn die Seite verlassen wird', async () => {
    const { ergebnis } = fenster()
    await screen.findByAltText('QR-Code zum Verbinden der App')
    ergebnis.unmount()
    expect(loeschen).toHaveBeenCalledWith('/api/me/app-kopplung/k-1')
  })

  it('sagt „abgelaufen“, wenn es den Code nicht mehr gibt, statt ewig zu warten', async () => {
    fenster()
    await screen.findByAltText('QR-Code zum Verbinden der App')
    holen.mockImplementation(async (pfad: string) => {
      if (pfad === '/api/me/app-kopplung/k-1') {
        throw new ApiError(404, 'Diesen Code gibt es nicht mehr.', 'app_pairing_not_found')
      }
      return {}
    })
    expect(await screen.findByText('Der Code ist abgelaufen.', {}, { timeout: 4000 })).toBeInTheDocument()
  })

  it('meldet keinen Erfolg, wenn die App keinen Schlüssel bekam', async () => {
    const { onVerbunden } = fenster()
    await screen.findByAltText('QR-Code zum Verbinden der App')
    stand = 'fehlgeschlagen'
    expect(await screen.findByText(/Die App hat keinen Schlüssel bekommen/, {}, { timeout: 4000 })).toBeInTheDocument()
    expect(onVerbunden).not.toHaveBeenCalled()
    expect(screen.queryByText(/Verbunden/)).not.toBeInTheDocument()
  })

  it('lässt einen abgeholten Code stehen', async () => {
    fenster()
    await screen.findByAltText('QR-Code zum Verbinden der App')
    stand = 'verbunden'
    await screen.findByText('Verbunden: iPhone von Jonas.', {}, { timeout: 4000 })
    await userEvent.click(screen.getByRole('button', { name: 'Fertig' }))
    await waitFor(() => expect(loeschen).not.toHaveBeenCalled())
  })
})
