/**
 * Der Satz zur Freigabe unter den Kontingenten folgt den Haken je Medienart.
 *
 * Neue Konten tragen den alten Sammelhaken auf aus und die Haken für Filme und
 * Serien einzeln. Die Seite las nur den Sammelhaken und schrieb „muss
 * freigegeben werden“ über eine Anfrage, die im selben Moment schon gesucht
 * wurde.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import de from '../i18n/de.json'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  const kontingent = {
    limit: null,
    used: 0,
    remaining: null,
    unlimited: true,
    exhausted: false,
    period: 'week',
    resets_at: null,
  }
  return {
    ...echt,
    api: {
      get: vi.fn(async (pfad: string) => {
        if (pfad === '/api/requests/quota') {
          return { movie: kontingent, tv: kontingent, auto_approve: false }
        }
        if (pfad === '/api/requests/mine') return []
        return {}
      }),
      post: vi.fn(async () => ({})),
      put: vi.fn(async () => ({})),
      patch: vi.fn(async () => ({})),
      delete: vi.fn(async () => ({})),
    },
  }
})

const zustand = { user: null as null | Record<string, unknown>, updateUser: vi.fn() }
vi.mock('../auth/useAuth', () => ({ useAuth: () => zustand }))

import { MyRequestsPage } from './MyRequestsPage'

const KONTO = {
  id: 2,
  username: 'kim',
  role: 'user',
  display_name: null,
  language: 'de',
  theme: 'dark',
  is_active: true,
  is_betreiber: false,
  auto_approve: false,
  effective_auto_approve: false,
  auto_approve_movies: true,
  auto_approve_series: true,
  effective_auto_approve_movies: true,
  effective_auto_approve_series: true,
}

function zeigen() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <MyRequestsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  zustand.user = null
})

describe('Hinweis zur Freigabe', () => {
  it('sagt „sofort freigegeben“, wenn beide Medienarten sofort durchgehen', async () => {
    zustand.user = { ...KONTO }
    zeigen()

    expect(await screen.findByText(de.myRequests.autoApprove)).toBeInTheDocument()
    expect(screen.queryByText(de.myRequests.needsApproval)).not.toBeInTheDocument()
  })

  it('nennt die Medienart, wenn nur eine sofort durchgeht', async () => {
    zustand.user = { ...KONTO, auto_approve_series: false, effective_auto_approve_series: false }
    zeigen()

    expect(await screen.findByText(de.myRequests.autoApproveMoviesOnly)).toBeInTheDocument()
    expect(screen.queryByText(de.myRequests.autoApprove)).not.toBeInTheDocument()
  })

  it('bleibt bei „muss freigegeben werden“, wenn keine sofort durchgeht', async () => {
    zustand.user = {
      ...KONTO,
      auto_approve_movies: false,
      auto_approve_series: false,
      effective_auto_approve_movies: false,
      effective_auto_approve_series: false,
    }
    zeigen()

    expect(await screen.findByText(de.myRequests.needsApproval)).toBeInTheDocument()
  })
})
