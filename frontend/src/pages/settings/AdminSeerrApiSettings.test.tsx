/**
 * Die Seerr-API-Seite sagt, was wirklich ist - nicht nur, was eingeschaltet ist.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  }
})

import { api } from '../../api/client'
import type { AppSettings } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminSeerrApiSettings } from './AdminSeerrApiSettings'

function stand(zusatz: Partial<AppSettings> = {}): AppSettings {
  return {
    seerr_api_enabled: false,
    seerr_api_port: 5055,
    seerr_api_listening: false,
    seerr_api_error: null,
    ...zusatz,
  } as AppSettings
}

describe('AdminSeerrApiSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('nennt den Port, auf dem wirklich gelauscht wird', async () => {
    vi.mocked(api.get).mockResolvedValue(stand({ seerr_api_enabled: true, seerr_api_listening: true }) as never)
    rendernSchlicht(<AdminSeerrApiSettings />)
    expect(await screen.findByText(/5055/, { selector: 'p' })).toBeInTheDocument()
  })

  it('sagt es, wenn der Port belegt ist, statt "eingeschaltet" stehen zu lassen', async () => {
    vi.mocked(api.get).mockResolvedValue(
      stand({ seerr_api_enabled: true, seerr_api_error: 'port_unavailable' }) as never,
    )
    rendernSchlicht(<AdminSeerrApiSettings />)
    const zeile = await screen.findByText(/5055/, { selector: 'p' })
    expect(zeile.className).toContain('text-accent-400')
  })

  it('schickt Schalter und Port zusammen und laesst einen Port unter 1024 nicht durch', async () => {
    vi.mocked(api.get).mockResolvedValue(stand() as never)
    vi.mocked(api.put).mockResolvedValue(stand({ seerr_api_enabled: true, seerr_api_port: 5056 }) as never)
    rendernSchlicht(<AdminSeerrApiSettings />)

    const schalter = await screen.findByRole('checkbox')
    fireEvent.click(schalter)
    const feld = screen.getByRole('spinbutton')

    fireEvent.change(feld, { target: { value: '80' } })
    const knopf = screen.getAllByRole('button').at(-1)!
    expect(knopf).toBeDisabled()

    fireEvent.change(feld, { target: { value: '5056' } })
    expect(knopf).not.toBeDisabled()
    fireEvent.click(knopf)
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/api/settings', { seerr_api_enabled: true, seerr_api_port: 5056 }),
    )
  })
})
