/**
 * Texte vor der Weg-Wahl im Einrichtungsassistenten nennen keinen Dienst
 * beim Namen.
 *
 * ⚠️ `setup.startFreshText` und `setup.intro` standen vor der Wahl des
 * Beschaffungswegs (Radarr/Sonarr oder nexcrate, `BeschaffungStep`) und
 * nannten trotzdem "Radarr und Sonarr" - für wer sich später für nexcrate
 * entscheidet, ein falsches Versprechen. Die Texte müssen neutral bleiben;
 * `setup.startSeerrText` ist ausgenommen, weil der Seerr-Weg wirklich immer
 * Radarr/Sonarr aus Seerr übernimmt.
 */

import { describe, expect, it } from 'vitest'

import de from '../i18n/de.json'
import en from '../i18n/en.json'

const ARR = /Radarr|Sonarr/

describe('Setup-Texte vor der Weg-Wahl', () => {
  it.each([
    ['de', de],
    ['en', en],
  ])('%s: setup.startFreshText nennt keinen Dienst', (_sprache, baum) => {
    expect((baum as { setup: { startFreshText: string } }).setup.startFreshText).not.toMatch(ARR)
  })

  it.each([
    ['de', de],
    ['en', en],
  ])('%s: setup.intro nennt keinen Dienst', (_sprache, baum) => {
    expect((baum as { setup: { intro: string } }).setup.intro).not.toMatch(ARR)
  })
})
