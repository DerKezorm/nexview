/**
 * Der Rückkanal einer Instanz: was die Zeile dem Betreiber sagt.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

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
import type { WebhookInstanzStand, WebhookStand } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { WebhookZeile } from './WebhookZeile'

function stand(zusatz: Partial<WebhookInstanzStand> = {}): WebhookStand {
  return {
    basis: 'http://nexview.example.com',
    instanzen: [
      {
        kennung: 'radarr-standard',
        name: 'Radarr',
        media_type: 'movie',
        tier: 'standard',
        aktiv: true,
        eingetragen: true,
        bewiesen_am: null,
        zuletzt_angerufen_am: null,
        letztes_ereignis: '',
        geprueft_am: null,
        fehler: '',
        fehler_info: '',
        alter_eintrag: '',
        ...zusatz,
      },
    ],
  }
}

function zeigen() {
  rendernSchlicht(
    <WebhookZeile kennung="radarr-standard" dienst="radarr" onWunsch={() => {}} onProbe={() => {}} />,
  )
}

describe('WebhookZeile', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('nennt einen früheren Eintrag, den Nexview stehen lässt', async () => {
    // Er kann einer anderen Installation gehören (eine Kopie des
    // Datenverzeichnisses); ob er weg darf, entscheidet der Betreiber.
    vi.mocked(api.get).mockResolvedValue(
      stand({ alter_eintrag: 'http://alt.example.com/api/webhooks/arr/radarr-standard' }) as never,
    )
    zeigen()

    expect(
      await screen.findByText(/älterer Nexview-Eintrag noch http:\/\/alt\.example\.com/),
    ).toBeInTheDocument()
  })

  it('schweigt ohne früheren Eintrag', async () => {
    vi.mocked(api.get).mockResolvedValue(stand() as never)
    zeigen()

    expect(await screen.findByText(/Webhook/i)).toBeInTheDocument()
    expect(screen.queryByText(/älterer Nexview-Eintrag/)).not.toBeInTheDocument()
  })
})
