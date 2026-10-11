/**
 * Das Anbieter-Formular: kein Schalter für einen zweiten Faktor, den Nexview nicht hat.
 *
 * Der Bauplan führt „Der Anbieter prüft den zweiten Faktor selbst“ nur dort, wo die
 * App einen eigenen zweiten Faktor hat. Nexview hat keinen; der Wert bleibt trotzdem
 * an und geht beim Speichern unverändert mit.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
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
import type { OidcAnbieter } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminOidcSettings } from './AdminOidcSettings'

const eintrag: OidcAnbieter = {
  id: 4,
  slug: 'sso',
  label: 'Firmen-SSO',
  issuer: 'https://sso.example.com',
  client_id: 'nexview',
  has_secret: true,
  scopes: 'openid profile email',
  enabled: true,
  auto_create: false,
  trusts_second_factor: true,
  managed: '',
  position: 0,
  redirect_uri: 'https://nexview.example.com/api/oidc/sso/callback',
  registered_redirect_uri: '',
  redirect_stale: false,
  links: 0,
  editable: true,
}

beforeEach(() => {
  vi.mocked(api.get).mockReset()
  vi.mocked(api.put).mockReset()
  vi.mocked(api.post).mockReset()
  vi.mocked(api.get).mockImplementation(async (pfad: string) => {
    if (pfad === '/api/oidc/admin/providers') return [eintrag]
    if (pfad === '/api/settings') return { public_url: 'https://nexview.example.com', password_login: true }
    if (pfad === '/api/oidc/admin/blocks') return []
    if (pfad === '/api/oidc/authentik/address')
      return {
        public_url: 'https://nexview.example.com',
        seen: 'https://nexview.example.com',
        forwarded_proto: '',
        forwarded_host: '',
        proxy_ignored: false,
        callback_path: '/api/oidc/authentik/callback',
        redirect_uri: 'https://nexview.example.com/api/oidc/authentik/callback',
      }
    throw new Error(`unerwartet: ${pfad}`)
  })
  vi.mocked(api.put).mockResolvedValue({})
})

describe('Anbieter-Formular', () => {
  it('zeigt keinen Schalter für den zweiten Faktor und schickt den Wert unverändert mit', async () => {
    const user = userEvent.setup()
    rendernSchlicht(<AdminOidcSettings />)
    const liste = await screen.findByTestId('anbieter-liste')
    await user.click(within(liste).getAllByRole('button', { name: /^Bearbeiten$/ })[0])
    const fenster = await screen.findByRole('dialog')

    // Die anderen Schalter stehen da, dieser nicht.
    expect(within(fenster).getByText('Neue Personen bekommen ein Konto')).toBeInTheDocument()
    expect(within(fenster).queryByText(/zweiten Faktor/)).not.toBeInTheDocument()

    await user.click(within(fenster).getByRole('button', { name: /^Speichern$/ }))
    await waitFor(() => expect(api.put).toHaveBeenCalled())
    expect(api.put).toHaveBeenCalledWith(
      '/api/oidc/admin/providers/4',
      expect.objectContaining({ trusts_second_factor: true }),
    )
  })
})
