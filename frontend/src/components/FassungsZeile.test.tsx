/**
 * Die Gründe einer Fassung stehen als Satz da, nie als roher Code.
 *
 * Großer Prüfgang, 25.09.2026: Hinter jeder Fassung stand `fed_by_source`,
 * in beiden Sprachen gleich.
 */

import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import { changeLanguage } from '../i18n'
import { FassungsZeile } from './FassungsZeile'

const FASSUNG = {
  kennung: 'v_39000aa5',
  media_type: 'tv',
  name: 'Series',
  klasse: 'hd',
  bereit: false,
  gruende: ['fed_by_source'],
}

describe('FassungsZeile', () => {
  afterEach(async () => {
    await changeLanguage('de')
  })

  it.each([
    ['de', /Radarr oder Sonarr/],
    ['en', /Radarr or Sonarr/],
  ])('übersetzt fed_by_source (%s)', async (sprache, erwartet) => {
    await changeLanguage(sprache as 'de' | 'en')
    render(<ul><FassungsZeile fassung={FASSUNG} /></ul>)

    expect(screen.getByText(erwartet)).toBeInTheDocument()
    expect(screen.queryByText('fed_by_source')).not.toBeInTheDocument()
  })
})
