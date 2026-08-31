/**
 * Das Auffangnetz — geprüft an einem echten Absturz beim Zeichnen.
 *
 * ⚠️ **Der Fehler, den dieser Test verhindert, ist eine weiße Seite.** Ohne
 * das Netz entfernt React bei jedem Fehler beim Zeichnen die komplette
 * Anwendung aus `#root`: kein Text, kein Knopf, kein Hinweis. Der
 * wahrscheinlichste Auslöser ist ein Update bei offenem Tab — dann fehlt eine
 * nachgeladene Seite, und der Fehler entsteht mitten im Zeichnen.
 */

import { render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, expect, it, vi } from 'vitest'

import { Auffangnetz } from './Auffangnetz'
import { i18nStarten } from '../i18n'

beforeAll(async () => {
  await i18nStarten('de')
})

beforeEach(() => {
  // React schreibt den aufgefangenen Fehler selbst noch einmal in die Konsole.
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

function Faellt(): never {
  throw new Error('nachgeladene Seite kam nicht an')
}

it('zeigt eine Erklärung statt einer leeren Fläche', () => {
  render(
    <Auffangnetz>
      <Faellt />
    </Auffangnetz>,
  )

  expect(screen.getByRole('alert')).toBeInTheDocument()
  expect(screen.getByText(/lässt sich gerade nicht anzeigen/i)).toBeInTheDocument()
})

it('bietet genau einen sichtbaren Ausgang', () => {
  render(
    <Auffangnetz>
      <Faellt />
    </Auffangnetz>,
  )

  // Hausregel: jedes Fenster, jede Sackgasse braucht genau einen Weg heraus.
  const knoepfe = screen.getAllByRole('button')
  expect(knoepfe).toHaveLength(1)
  expect(knoepfe[0]).toHaveTextContent('Seite neu laden')
})

it('behält den eigentlichen Fehler für die Konsole', () => {
  render(
    <Auffangnetz>
      <Faellt />
    </Auffangnetz>,
  )

  const meldungen = (console.error as unknown as { mock: { calls: unknown[][] } }).mock.calls
  expect(
    meldungen.some((argumente) =>
      argumente.some((a) => typeof a === 'string' && a.includes('beim Zeichnen gescheitert')),
    ),
  ).toBe(true)
})

it('lässt heile Inhalte unangetastet durch', () => {
  // ⚠️ Ohne das wäre nicht bewiesen, dass das Netz nur im Fehlerfall greift —
  // ein Netz, das immer zuschlägt, wäre schlimmer als keins.
  render(
    <Auffangnetz>
      <p>alles in Ordnung</p>
    </Auffangnetz>,
  )

  expect(screen.getByText('alles in Ordnung')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
})
