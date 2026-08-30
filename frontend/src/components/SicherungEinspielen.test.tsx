/**
 * Die Vorschau vor dem Einspielen — und die Warnung zum Schlüssel.
 *
 * ⚠️ **Der Fall, der still zuschlägt.** Hier stand nur die halbe Frage: ob
 * *diese* Installation ihren Schlüssel aus der Umgebung nimmt. Ob im Archiv
 * einer liegt, wurde nie gefragt. Kommt das Archiv von einer Installation mit
 * `NEXVIEW_SECRET_KEY` und hat das Ziel die Variable nicht, sah die Vorschau
 * beruhigend aus — und bedeutete den schlimmsten Fall: Nexview erzeugt einen
 * neuen Schlüssel, und danach ist kein gespeicherter Zugang mehr lesbar.
 *
 * Der Betreiber hat dann keinen Anlass, den Schlüssel zu verdächtigen. Diese
 * Warnung ist die einzige Stelle, an der der wahre Grund je auftaucht.
 */

import { beforeEach, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
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

import { api } from '../api/client'
import { rendernSchlicht } from '../test/rendern'
import { SicherungEinspielen } from './SicherungEinspielen'

const senden = vi.mocked(api.post)

type Lage = { schluessel_im_archiv: boolean; schluessel_aus_umgebung: boolean }

function steckbrief(lage: Lage) {
  return {
    version: '0.25.0',
    erstellt: '2026-08-30T12:00:00+00:00',
    art: 'manuell',
    kommentar: 'vor dem Umzug',
    einspielbar: true,
    grund: 'ok',
    ...lage,
  }
}

/** Datei wählen, Passwort tippen, prüfen lassen — bis die Vorschau steht. */
async function vorschauZeigen(lage: Lage) {
  senden.mockResolvedValue(steckbrief(lage))
  rendernSchlicht(<SicherungEinspielen basis="/admin/sicherungen" onFertig={() => {}} />)

  const nutzer = userEvent.setup()
  await nutzer.upload(
    screen.getByLabelText(/Sicherungs-Archiv/i),
    new File([new Uint8Array([1, 2, 3])], 'sicherung.zip', { type: 'application/zip' }),
  )
  await nutzer.type(screen.getByLabelText(/Passwort des Archivs/i), 'geheim')
  await nutzer.click(screen.getByRole('button', { name: /prüfen/i }))

  // Erst wenn das Datum dasteht, ist die Vorschau gerendert.
  await screen.findByText(/vor dem Umzug/)
}

beforeEach(() => {
  senden.mockReset()
})

it('warnt laut, wenn weder Archiv noch Installation einen Schlüssel hat', async () => {
  // ⚠️ Der schlimmste Fall - und der, bei dem vorher nichts dastand.
  await vorschauZeigen({ schluessel_im_archiv: false, schluessel_aus_umgebung: false })

  const warnung = await screen.findByText(/kein Schlüssel, und diese Installation/)
  expect(warnung).toBeInTheDocument()
  // Nicht als milder Hinweis: Nach dem Klick ist kein Zugang mehr lesbar.
  expect(warnung.closest('p')?.className).toContain('bad-500')
})

it('warnt, wenn im Archiv kein Schlüssel liegt und die Variable stimmen muss', async () => {
  await vorschauZeigen({ schluessel_im_archiv: false, schluessel_aus_umgebung: true })

  expect(await screen.findByText(/liegt kein Schlüssel/)).toBeInTheDocument()
  // Nicht die Meldung fuer den anderen Fall - im Archiv ist ja keiner drin,
  // der uebergangen werden koennte.
  expect(screen.queryByText(/gewinnt die Variable/)).not.toBeInTheDocument()
})

it('warnt, wenn die Variable den Schlüssel aus dem Archiv übergeht', async () => {
  await vorschauZeigen({ schluessel_im_archiv: true, schluessel_aus_umgebung: true })

  expect(await screen.findByText(/gewinnt die Variable/)).toBeInTheDocument()
})

it('schweigt, wenn der Schlüssel im Archiv liegt und nichts ihn übergeht', async () => {
  // ⚠️ Eine Warnfläche, die auch im Normalfall dasteht, lernt man zu übersehen.
  await vorschauZeigen({ schluessel_im_archiv: true, schluessel_aus_umgebung: false })

  expect(screen.queryByText(/Schlüssel/)).not.toBeInTheDocument()
})
