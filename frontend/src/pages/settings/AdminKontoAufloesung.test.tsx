/**
 * Konto löschen: der Zugang auf den Medienservern.
 *
 * ⚠️ Entschieden am 13.09.2026: Vorausgewählt ist nur, was eine
 * Nexview-Einladung angelegt hat. Was nicht geht, lässt sich nicht anhaken und
 * sagt, warum. An den Server geht genau die Auswahl, nicht mehr.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

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
import type { User } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminKontoAufloesung } from './AdminKontoAufloesung'

const holen = vi.mocked(api.get)
const loeschen = vi.mocked(api.delete)

const BENUTZER = { id: 5, username: 'kim', display_name: 'Kim' } as User

type Konto = {
  provider: string
  label: string
  konto: string
  name: string
  art: 'konto' | 'freigabe'
  aus_einladung: boolean
  vorausgewaehlt: boolean
  grund: string | null
}

const JELLYFIN: Konto = {
  provider: 'jellyfin',
  label: 'Jellyfin',
  konto: 'j-1',
  name: 'kim',
  art: 'konto',
  aus_einladung: true,
  vorausgewaehlt: true,
  grund: null,
}
const PLEX: Konto = {
  provider: 'plex',
  label: 'Plex',
  konto: 'p-1',
  name: 'kim-plex',
  art: 'freigabe',
  aus_einladung: false,
  vorausgewaehlt: false,
  grund: null,
}
const EMBY_VERWALTER: Konto = {
  provider: 'emby',
  label: 'Emby',
  konto: 'e-1',
  name: 'verwalter',
  art: 'konto',
  aus_einladung: false,
  vorausgewaehlt: false,
  grund: 'administrator',
}

function oeffnen(serverkonten: Konto[]) {
  holen.mockImplementation(((pfad: string) => {
    if (pfad === '/api/users/5/aufloesung') {
      return Promise.resolve({ posten: [], laufende: [], offen: [], serverkonten })
    }
    return Promise.reject(new Error(`unerwartet: ${pfad}`))
  }) as never)
  const geloescht = vi.fn()
  rendernSchlicht(
    <AdminKontoAufloesung benutzer={BENUTZER} onSchliessen={() => {}} onGeloescht={geloescht} />,
  )
  return geloescht
}

beforeEach(() => {
  holen.mockReset()
  loeschen.mockReset()
})

describe('der Zugang auf den Medienservern', () => {
  it('hakt nur an, was aus einer Nexview-Einladung stammt', async () => {
    oeffnen([JELLYFIN, PLEX])

    // Die Herkunft steht im Namen des Kästchens; so gehört sie sicher zur richtigen Zeile.
    const jellyfin = await screen.findByRole('checkbox', {
      name: /Konto „kim“ auf Jellyfin löschen.*aus einer Nexview-Einladung/,
    })
    const plex = screen.getByRole('checkbox', {
      name: /Freigabe auf Plex für „kim-plex“ entfernen.*selbst verknüpft/,
    })
    expect(jellyfin).toBeChecked()
    expect(plex).not.toBeChecked()
  })

  it('lässt nicht anhaken, was nicht geht, und sagt warum', async () => {
    oeffnen([EMBY_VERWALTER])

    const emby = await screen.findByRole('checkbox', { name: /Konto „verwalter“ auf Emby löschen/ })
    expect(emby).toBeDisabled()
    expect(emby).not.toBeChecked()
    expect(screen.getByText('geht nicht: Das Konto verwaltet den Server')).toBeInTheDocument()
  })

  it('schickt genau die Auswahl an den Server', async () => {
    const geloescht = oeffnen([JELLYFIN, PLEX, EMBY_VERWALTER])
    loeschen.mockResolvedValue(undefined)
    const b = userEvent.setup()

    await b.click(await screen.findByRole('checkbox', { name: /Konto „kim“ auf Jellyfin löschen/ }))
    await b.click(screen.getByRole('checkbox', { name: /Freigabe auf Plex/ }))
    await b.click(screen.getByRole('button', { name: 'Konto endgültig löschen' }))

    await waitFor(() =>
      expect(loeschen).toHaveBeenCalledWith(
        '/api/users/5',
        expect.objectContaining({ serverkonten: [{ provider: 'plex', konto: 'p-1' }] }),
      ),
    )
    await waitFor(() => expect(geloescht).toHaveBeenCalled())
  })

  it('warnt erst, wenn etwas angehakt ist', async () => {
    oeffnen([PLEX])
    const b = userEvent.setup()

    const plex = await screen.findByRole('checkbox', { name: /Freigabe auf Plex/ })
    expect(screen.queryByText(/samt Verlauf weg/)).not.toBeInTheDocument()
    await b.click(plex)
    expect(screen.getByText(/samt Verlauf weg/)).toBeInTheDocument()
  })
})
