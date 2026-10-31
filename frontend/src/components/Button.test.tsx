import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'

import { Button } from './ui'

/** Macht, was Chromes Uebersetzer tut: jeder Textknoten wird durch <font><font>…</font></font> ersetzt. */
function translateLikeChrome(root: Node) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const texts: Text[] = []
  while (walker.nextNode()) texts.push(walker.currentNode as Text)
  for (const text of texts) {
    const outer = document.createElement('font')
    const inner = document.createElement('font')
    inner.textContent = text.data
    outer.appendChild(inner)
    text.replaceWith(outer)
  }
}

// Ein Knopf schaltet seinen Spinner ein, nachdem Chrome die Seite uebersetzt hat: React will ihn vor einen Textknoten
// setzen, den es nicht mehr gibt.
test('a translated button still shows its spinner', () => {
  const { rerender } = render(<Button>Test connection</Button>)
  translateLikeChrome(screen.getByRole('button'))

  rerender(<Button loading>Test connection</Button>)

  const button = screen.getByRole('button')
  expect(button.querySelector('svg.animate-spin')).not.toBeNull()
  expect(button).toBeDisabled()
  expect(button.textContent).toBe('Test connection')

  rerender(<Button>Test connection</Button>)
  expect(screen.getByRole('button').querySelector('svg.animate-spin')).toBeNull()
})
