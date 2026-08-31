import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { HausordnungKnopf } from './HausordnungKnopf'

const config = vi.hoisted(() => ({ wert: {} as Record<string, unknown> }))

vi.mock('../hooks/useConfig', () => ({
  useConfig: () => ({ data: config.wert }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (schluessel: string) => schluessel }),
}))

function zeigen(stand: Record<string, unknown>) {
  config.wert = stand
  return render(
    <HausordnungKnopf offen={false} onOeffnen={() => {}} onSchliessen={() => {}} />,
  )
}

describe('Der §-Knopf', () => {
  it('bleibt weg, wenn es keine Hausordnung gibt', () => {
    zeigen({ hausordnung_vorhanden: false })
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('erscheint mit Punkt, solange noch nicht quittiert wurde', () => {
    const { container } = zeigen({
      hausordnung_vorhanden: true,
      hausordnung_fassung: 1,
      hausordnung_gelesen: null,
      hausordnung_quittierbar: true,
    })
    expect(screen.getByRole('button')).toHaveTextContent('§')
    // Der Punkt ist rein schmückend und trägt deshalb keinen Text.
    expect(container.querySelector('.bg-accent-500')).not.toBeNull()
  })

  it('verschwindet nach dem Quittieren', () => {
    zeigen({
      hausordnung_vorhanden: true,
      hausordnung_fassung: 1,
      hausordnung_gelesen: 1,
      hausordnung_quittierbar: true,
    })
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('kommt bei einer neuen Fassung zurück', () => {
    // ⚠️ Der Kern: Quittiert ist Fassung 1, veröffentlicht ist 2.
    const { container } = zeigen({
      hausordnung_vorhanden: true,
      hausordnung_fassung: 2,
      hausordnung_gelesen: 1,
      hausordnung_quittierbar: true,
    })
    expect(screen.getByRole('button')).toBeInTheDocument()
    expect(container.querySelector('.bg-accent-500')).not.toBeNull()
  })

  it('bleibt stehen, wenn das Abhaken abgeschaltet ist', () => {
    // Dann ist er kein Anstupser mehr, sondern der Zugang – und trägt keinen
    // Punkt, weil es nichts zu quittieren gibt.
    const { container } = zeigen({
      hausordnung_vorhanden: true,
      hausordnung_fassung: 1,
      hausordnung_gelesen: null,
      hausordnung_quittierbar: false,
    })
    expect(screen.getByRole('button')).toBeInTheDocument()
    // Kein Punkt: Er ließe sich nie wieder wegklicken.
    expect(container.querySelector('.bg-accent-500')).toBeNull()
  })
})
