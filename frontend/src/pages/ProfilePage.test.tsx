/**
 * Was eine unbestätigte Adresse kostet, sagt das Profil so, wie es ist.
 *
 * Die Anmeldung mit Passwort lehnt ein Konto mit unbestätigter Adresse ab,
 * nur den Betreiber nicht: Ohne Mailserver stünde er sonst nach einer
 * Adressänderung vor der eigenen Installation. Das Profil soll ihm deshalb
 * keine Sperre androhen, allen anderen aber genau diese nennen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'

import de from '../i18n/de.json'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: {
      get: vi.fn(async (pfad: string) => (pfad === '/api/config' ? {} : [])),
      post: vi.fn(async () => ({})),
      put: vi.fn(async () => ({})),
      patch: vi.fn(async () => ({})),
      delete: vi.fn(async () => ({})),
    },
  }
})

const zustand = { user: null as null | Record<string, unknown>, updateUser: vi.fn() }
vi.mock('../auth/useAuth', () => ({ useAuth: () => zustand }))

import { ProfilePage } from './ProfilePage'

const KONTO = {
  id: 1,
  username: 'admin',
  role: 'admin',
  display_name: null,
  language: 'de',
  theme: 'dark',
  is_active: true,
  is_betreiber: true,
  email: 'neu@example.com',
  email_verified: false,
}

function zeigen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProfilePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  zustand.user = null
})

describe('Hinweis zur unbestätigten Adresse', () => {
  it('droht dem Betreiber keine Sperre an', async () => {
    zustand.user = { ...KONTO }
    zeigen()

    expect(await screen.findByText(de.profile.emailUnverifiedOwnerHint)).toBeInTheDocument()
    expect(screen.queryByText(de.profile.emailUnverifiedHint)).not.toBeInTheDocument()
  })

  it('nennt allen anderen die gesperrte Anmeldung, auch einem Administrator', async () => {
    zustand.user = { ...KONTO, id: 2, username: 'zweiter', is_betreiber: false }
    zeigen()

    expect(await screen.findByText(de.profile.emailUnverifiedHint)).toBeInTheDocument()
    expect(screen.queryByText(de.profile.emailUnverifiedOwnerHint)).not.toBeInTheDocument()
  })

  it('schweigt, sobald die Adresse bestätigt ist', async () => {
    zustand.user = { ...KONTO, email_verified: true }
    zeigen()

    expect(await screen.findByText(de.profile.emailVerified)).toBeInTheDocument()
    expect(screen.queryByText(de.profile.emailUnverifiedOwnerHint)).not.toBeInTheDocument()
  })
})
