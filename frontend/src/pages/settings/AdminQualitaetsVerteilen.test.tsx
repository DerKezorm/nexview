/**
 * Der Dialog „Auf welche Instanzen?".
 *
 * ⚠️ **Aus einem echten Fehler (13.09.2026).** „Übernehmen" ging nur, wenn eine
 * Instanz dazukam oder wegfiel. Zeigte die ausgewählte Instanz „Update
 * verfügbar", blieb der Knopf gesperrt, und das Update ließ sich hier nicht
 * einspielen, obwohl der Server das Profil beim Übernehmen neu schreibt.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { rendernSchlicht } from '../../test/rendern'
import { AdminQualitaetsVerteilen } from './AdminQualitaetsVerteilen'
import type { Profil, Stand } from './qualitaetsprofile-typen'

const INSTANZEN = [
  { kennung: 'radarr-standard', name: 'Radarr FHD', typ: 'radarr' as const },
  { kennung: 'radarr-4k', name: 'Radarr 4K', typ: 'radarr' as const },
]

function zeigen(stand: Stand) {
  const speichern = vi.fn()
  const profil: Profil = {
    id: '1',
    name: 'FHD',
    typ: 'radarr',
    zweck: '',
    installationen: [{ instanz: 'radarr-standard', stand }],
  }
  rendernSchlicht(
    <AdminQualitaetsVerteilen
      profil={profil}
      instanzen={INSTANZEN}
      onSchliessen={vi.fn()}
      onSpeichern={speichern}
    />,
  )
  return { speichern, knopf: screen.getByRole('button', { name: 'Übernehmen' }) }
}

describe('AdminQualitaetsVerteilen', () => {
  it('spielt ein Update ein, ohne dass sich die Auswahl ändern muss', async () => {
    const { speichern, knopf } = zeigen('update')
    expect(knopf).toBeEnabled()
    expect(screen.getByText(/Wird neu geschrieben/)).toBeInTheDocument()

    await userEvent.setup().click(knopf)
    expect(speichern).toHaveBeenCalledWith([
      { instanz: 'radarr-standard', stand: 'update' },
      { instanz: 'radarr-4k', stand: 'nicht-installiert' },
    ])
  })

  it('hat nichts zu tun, wenn alles aktuell ist', () => {
    const { knopf } = zeigen('aktuell')
    expect(knopf).toBeDisabled()
  })

  it('warnt, bevor es Änderungen von Hand überschreibt', () => {
    const { knopf } = zeigen('angepasst')
    expect(knopf).toBeEnabled()
    expect(screen.getByText(/Änderungen von Hand/)).toBeInTheDocument()
  })
})
