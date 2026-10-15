/**
 * Die Wahl der Fassungen auf Kacheln (Issue #13).
 *
 * Ein Haken steht für einen Namen: „4K" der Filme und der Serien sind einer,
 * und er setzt beide Kennungen. Kennungen, die gerade nicht zur Wahl stehen,
 * bleiben beim Umschalten erhalten. Die Frage „wann" gibt es erst, wenn etwas
 * angehakt ist.
 */

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { rendernSchlicht } from '../../test/rendern'
import { FassungenAufKacheln } from './FassungenAufKacheln'

const WAHL = [
  { name: '4K', kennungen: ['radarr-uhd', 'sonarr-uhd'] },
  { name: 'Kids', kennungen: ['kids'] },
]

function zeigen(gewaehlt: string[], setGewaehlt = vi.fn()) {
  rendernSchlicht(
    <FassungenAufKacheln
      wahl={WAHL}
      hauptName="HD"
      gewaehlt={gewaehlt}
      setGewaehlt={setGewaehlt}
      immer={false}
      setImmer={vi.fn()}
    />,
  )
  return setGewaehlt
}

describe('FassungenAufKacheln', () => {
  it('setzt mit einem Haken alle Kennungen desselben Namens und behält fremde', () => {
    const setGewaehlt = zeigen(['verschwunden'])
    fireEvent.click(screen.getByRole('checkbox', { name: '4K' }))
    expect(setGewaehlt).toHaveBeenCalledWith(['verschwunden', 'radarr-uhd', 'sonarr-uhd'])
  })

  it('nimmt mit dem Haken alle Kennungen des Namens wieder weg', () => {
    const setGewaehlt = zeigen(['radarr-uhd', 'sonarr-uhd', 'kids'])
    expect(screen.getByRole('checkbox', { name: '4K' })).toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: '4K' }))
    expect(setGewaehlt).toHaveBeenCalledWith(['kids'])
  })

  it('sperrt die Frage nach dem Wann, solange nichts angehakt ist', () => {
    zeigen([])
    for (const knopf of screen.getAllByRole('radio')) expect(knopf).toBeDisabled()
  })

  it('gibt die Frage frei, sobald etwas angehakt ist', () => {
    zeigen(['kids'])
    for (const knopf of screen.getAllByRole('radio')) expect(knopf).toBeEnabled()
  })
})
