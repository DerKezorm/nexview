/**
 * Das Wort auf der Pille.
 *
 * ⚠️ Ein fertig geladener Download, der nicht importiert wird, stand als
 * „Lädt · 100 %" in der Liste, tagelang. Der Test hält fest, dass die Liste
 * der Entscheider jetzt sagt, was los ist.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { StatusBadge } from './StatusBadge'

describe('StatusBadge', () => {
  it('sagt „Import hängt" statt „Lädt · 100 %", solange ein Download festsitzt', () => {
    render(<StatusBadge status="searching" fortschritt={100} importHaengt="sample" />)
    expect(screen.getByText('Import hängt')).toBeInTheDocument()
    expect(screen.queryByText(/Lädt/)).not.toBeInTheDocument()
  })

  it('ohne Grund bleibt es beim Fortschritt', () => {
    render(<StatusBadge status="searching" fortschritt={100} importHaengt={null} />)
    expect(screen.getByText('Lädt · 100 %')).toBeInTheDocument()
  })

  it('nur solange gesucht wird', () => {
    render(<StatusBadge status="downloaded" importHaengt="sample" />)
    expect(screen.queryByText('Import hängt')).not.toBeInTheDocument()
  })
})
