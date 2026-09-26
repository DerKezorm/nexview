/**
 * Die Übergabe warnt, wenn die eigene Adresse noch unbestätigt ist.
 *
 * Den Betreiber lässt die Anmeldung auch mit unbestätigter Adresse herein,
 * einen gewöhnlichen Administrator nicht. Wer den Haken so abgibt, kommt nach
 * dem Abmelden ohne Mailserver nicht wieder in die Installation.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import de from '../../i18n/de.json'
import type { User } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'

const ICH = {
  id: 1,
  username: 'admin',
  display_name: null,
  role: 'admin',
  is_active: true,
  is_betreiber: true,
  email: 'neu@example.com',
  email_verified: false,
} as unknown as User

const ANDERER = { ...ICH, id: 2, username: 'zweiter', is_betreiber: false } as User

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: {
      get: vi.fn(async (pfad: string) =>
        pfad === '/api/users/betreiber' ? { aus_umgebung: false } : [ICH, ANDERER],
      ),
      post: vi.fn(async () => ({})),
    },
  }
})

import { BetreiberUebergeben } from './BetreiberUebergeben'

async function frageOeffnen(me: User) {
  rendernSchlicht(<BetreiberUebergeben me={me} />)
  const auswahl = await screen.findByRole('combobox', { name: de.betreiber.pick })
  await screen.findByRole('option', { name: /zweiter/ })
  await userEvent.selectOptions(auswahl, '2')
  await userEvent.click(screen.getByRole('button', { name: de.betreiber.handOver }))
}

describe('Warnung vor der Übergabe', () => {
  it('nennt die Sperre, wenn die eigene Adresse unbestätigt ist', async () => {
    await frageOeffnen(ICH)

    expect(
      await screen.findByText(de.betreiber.confirmWarningUnverified, { exact: false }),
    ).toBeInTheDocument()
  })

  it('schweigt davon bei bestätigter Adresse', async () => {
    await frageOeffnen({ ...ICH, email_verified: true })

    expect(await screen.findByText(de.betreiber.confirmWarning)).toBeInTheDocument()
    expect(
      screen.queryByText(de.betreiber.confirmWarningUnverified, { exact: false }),
    ).not.toBeInTheDocument()
  })
})
