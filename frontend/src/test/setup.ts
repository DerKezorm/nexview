/**
 * Was vor jedem Test gilt.
 *
 * Bewusst klein: Was hier steht, gilt für **alle** Tests, und eine Attrappe,
 * die überall wirkt, verdeckt irgendwann genau den Fehler, den ein Test
 * finden sollte. Alles Fallspezifische gehört in die einzelne Testdatei.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, vi } from 'vitest'

import i18n, { i18nStarten } from '../i18n'

// ⚠️ **Die Texte kommen nicht mehr von allein.** Seit jede Sprache einzeln
// nachgeladen wird, startet i18next erst, wenn jemand es anstößt - in der
// Anwendung tut das `main.tsx`. Ohne diese Zeilen liefe jeder Test gegen eine
// Oberfläche, die statt Sätzen ihre Schlüssel zeigt.
//
// Die Sprache steht hier ausdrücklich dabei und wird nicht vom Browser
// erfragt: Sonst hinge das Ergebnis daran, auf welche Sprache die Testumgebung
// gerade eingestellt ist.
beforeAll(async () => {
  await i18nStarten('de')
})

// Nach jedem Test das gerenderte Stück wieder abräumen. Ohne das sammeln sich
// die Bäume im Dokument, und `getByText` findet plötzlich zwei Treffer aus
// zwei verschiedenen Tests.
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  // ⚠️ Immer dieselbe Sprache. Sonst hinge das Ergebnis daran, welcher Test
  // zuletzt umgeschaltet hat - und ein Test, der allein grün ist und in der
  // Reihe rot, kostet mehr Zeit als er wert ist.
  void i18n.changeLanguage('de')

  // jsdom kennt beides nicht, React und die Slider benutzen es aber.
  if (!window.matchMedia) {
    window.matchMedia = ((abfrage: string) => ({
      matches: false,
      media: abfrage,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia
  }
  if (!window.HTMLElement.prototype.scrollIntoView) {
    window.HTMLElement.prototype.scrollIntoView = vi.fn()
  }
})
