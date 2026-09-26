/**
 * Kinderkonto anlegen: das Pflichtfeld Alter.
 *
 * ⚠️ Das Feld hing an einer nativen HTML-Pflichtangabe (`required`). Blieb es
 * leer, blockte der Browser das Absenden mit seinem eigenen, unübersetzten
 * Hinweis - Nexviews `onSubmit` lief gar nicht erst, es gab weder eine
 * eigene Meldung noch einen Netzwerkaufruf. Seit der Reparatur prüft das
 * Formular selbst und zeigt `children.ageRequired`.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
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

import { api } from '../../api/client'
import { rendern } from '../../test/rendern'
import { Kinder } from './Kinder'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

/** Das Elternkonto, unter dem der Dialog angemeldet ist. */
const ELTERNTEIL = {
  id: 1,
  username: 'elternteil',
  display_name: 'Elternteil',
  role: 'user',
  is_active: true,
  is_betreiber: false,
  auto_approve: false,
  effective_auto_approve: false,
  can_approve: false,
  can_manage_children: true,
  parent_id: null,
  avatar_url: null,
  created_at: '2026-09-01T10:00:00',
  email: 'elternteil@example.com',
  email_verified: true,
  has_password: true,
} as unknown as import('../../api/types').User

beforeEach(() => {
  vi.clearAllMocks()
  schicken.mockResolvedValue({})
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/auth/me') return ELTERNTEIL
    if (pfad === '/api/children') return []
    if (pfad === '/api/children/wishes') return []
    if (pfad === '/api/children/genres') return ['action', 'comedy']
    if (pfad === '/api/config') return {}
    return {}
  })
})

describe('Kinderkonto anlegen: Pflichtfeld Alter', () => {
  it('zeigt eine eigene Meldung statt stillschweigend zu scheitern, und schickt nichts ab', async () => {
    const nutzer = userEvent.setup()
    rendern(<Kinder />, { pfad: '/profil/kinder' })

    await userEvent.click(await screen.findByText('Kinderkonto anlegen'))

    await nutzer.type(await screen.findByLabelText('Benutzername'), 'neuling')
    await nutzer.type(screen.getByLabelText('Passwort'), 'ein-langes-passwort')
    await nutzer.type(screen.getByLabelText('Passwort wiederholen'), 'ein-langes-passwort')
    // Das Alter bleibt bewusst leer.

    await nutzer.click(screen.getByRole('button', { name: 'Anlegen' }))

    expect(await screen.findByText('Bitte gib ein Alter an.')).toBeInTheDocument()
    expect(schicken).not.toHaveBeenCalledWith('/api/children', expect.anything())
  })

  it('legt das Konto an, sobald ein Alter eingetragen ist', async () => {
    const nutzer = userEvent.setup()
    rendern(<Kinder />, { pfad: '/profil/kinder' })

    await userEvent.click(await screen.findByText('Kinderkonto anlegen'))

    await nutzer.type(await screen.findByLabelText('Benutzername'), 'neuling')
    await nutzer.type(screen.getByLabelText('Alter'), '10')
    await nutzer.type(screen.getByLabelText('Passwort'), 'ein-langes-passwort')
    await nutzer.type(screen.getByLabelText('Passwort wiederholen'), 'ein-langes-passwort')

    await nutzer.click(screen.getByRole('button', { name: 'Anlegen' }))

    await waitFor(() => expect(schicken).toHaveBeenCalledWith('/api/children', expect.objectContaining({
      username: 'neuling',
      age: 10,
    })))
    expect(screen.queryByText('Bitte gib ein Alter an.')).not.toBeInTheDocument()
  })
})
