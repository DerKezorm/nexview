/**
 * Texte, die in beiden Betriebsarten erscheinen, nennen den eigenen Weg.
 *
 * ⚠️ **Bis zum 23.09.2026 nannten 41 Texte Radarr und Sonarr, auch wenn über
 * nexcrate beschafft wurde** – „wird aus Radarr bzw. Sonarr entfernt", „Es ist
 * noch keine Radarr- oder Sonarr-Instanz eingerichtet". Keiner der Tests sah
 * es, weil jeder Test im Arr-Betrieb lief. Siehe `lib/weg.ts`.
 *
 * Drei Ebenen, weil jede für sich etwas übersieht: die Sprachdateien (gibt es
 * die NEX-Fassung, und nennt sie Radarr nicht?), i18next selbst (wählt der
 * Kontext sie wirklich, auch bei Mehrzahl und Listen?) und die Quelldateien
 * (reicht jede Stelle den Kontext weiter?). Dazu ein Bauteil, gezeichnet im
 * NEX-Betrieb.
 */

import i18next from 'i18next'
import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../api/client'
import type { AnalyseStand } from '../api/types'
import de from '../i18n/de.json'
import en from '../i18n/en.json'
import { WEG_TEXTE, wegKontext } from '../lib/weg'
import { AnalyseDienste } from '../pages/stats/AnalyseDienste'
import { rendernSchlicht } from './rendern'

const SPRACHEN: Record<string, unknown> = { de, en }
const ARR = /Radarr|Sonarr/

function knoten(baum: unknown, pfad: string): unknown {
  let wert: unknown = baum
  for (const teil of pfad.split('.')) {
    if (typeof wert !== 'object' || wert === null) return undefined
    wert = (wert as Record<string, unknown>)[teil]
  }
  return wert
}

/** Die NEX-Fassung eines Schlüssels – einfach, Liste oder Mehrzahl. */
function nexFassung(baum: unknown, schluessel: string): unknown[] {
  const einfach = knoten(baum, `${schluessel}_nex`)
  if (einfach !== undefined) return [einfach]
  return [knoten(baum, `${schluessel}_nex_one`), knoten(baum, `${schluessel}_nex_other`)]
}

function arrFassung(baum: unknown, schluessel: string): unknown[] {
  const einfach = knoten(baum, schluessel)
  if (einfach !== undefined) return [einfach]
  return [knoten(baum, `${schluessel}_one`), knoten(baum, `${schluessel}_other`)]
}

describe('die Sprachdateien', () => {
  it('kennen die Liste überhaupt', () => {
    // Bodenschwelle: Eine leere Liste liesse jede Pruefung darunter gruen.
    expect(WEG_TEXTE.length).toBeGreaterThanOrEqual(38)
  })

  for (const [sprache, baum] of Object.entries(SPRACHEN)) {
    for (const schluessel of WEG_TEXTE) {
      it(`${sprache}: ${schluessel} hat eine NEX-Fassung ohne Radarr und Sonarr`, () => {
        const texte = nexFassung(baum, schluessel)
        for (const text of texte) {
          expect(text, `${schluessel}_nex fehlt`).toBeDefined()
          expect(JSON.stringify(text)).not.toMatch(ARR)
        }
        // Der Arr-Text bleibt stehen - er ist der Rückfall und die Fassung
        // für alle, die bei Radarr und Sonarr bleiben.
        for (const text of arrFassung(baum, schluessel)) expect(text).toBeDefined()
      })
    }
  }
})

describe('i18next wählt die NEX-Fassung über den Kontext', () => {
  const t = i18next.getFixedT('de')
  const nex = wegKontext({ beschaffung: 'nex' })
  const arr = wegKontext({ beschaffung: 'arr' })

  it('einfach', () => {
    expect(t('requests.cancelText', { title: 'X', ...nex })).toContain('aus nexcrate')
    expect(t('requests.cancelText', { title: 'X', ...arr })).toContain('Radarr')
  })

  it('in der Mehrzahl', () => {
    expect(t('befund.bibliothek.geisterposten.titel', { count: 2, ...nex })).toBe(
      '2 Posten werden nicht mehr von nexcrate geführt',
    )
    expect(t('befund.bibliothek.geisterposten.titel', { count: 1, ...nex })).toBe(
      'Ein Posten wird nicht mehr von nexcrate geführt',
    )
  })

  it('als Liste', () => {
    const liste = t('analyse.matrix.help.nur_arr', { returnObjects: true, ...nex }) as string[]
    expect(liste[0]).toMatch(/^nexcrate hat/)
  })

  it('und fällt ohne NEX-Fassung auf den Grundtext zurück', () => {
    // Deshalb darf der Kontext auch an zusammengesetzten Schlüsseln hängen.
    expect(t('analyse.matrix.view.alle', nex)).toBe(t('analyse.matrix.view.alle'))
  })
})

/**
 * Die Quelldateien, die einen dieser Schlüssel nachschlagen.
 *
 * Grob, aber billig: Wer einen der Schlüssel nennt, muss den Kontext
 * überhaupt holen. Ob jede einzelne Stelle ihn weiterreicht, prüft das
 * gezeichnete Bauteil unten nur für eine.
 */
const DATEIEN = import.meta.glob('../**/*.{ts,tsx}', {
  query: '?raw',
  eager: true,
  import: 'default',
}) as Record<string, string>

/** Zusammengesetzte Schlüssel, so wie sie im Code stehen. */
const VORSILBEN = [
  '`befund.${',
  '`logs.modeDesc.${',
  '`analyse.matrix.view.${',
  '`analyse.matrix.match.${',
  '`analyse.matrix.help.${',
  'ereignis.hintKey',
  'schluessel.text',
]

describe('die Quelldateien', () => {
  const quellen = Object.entries(DATEIEN).filter(
    ([pfad]) => !/\.test\.tsx?$/.test(pfad) && !pfad.includes('/lib/weg.ts'),
  )

  it('sieht die Quelldateien überhaupt', () => {
    expect(quellen.length).toBeGreaterThan(100)
  })

  it('holen den Kontext, wo sie einen solchen Text nachschlagen', () => {
    const ohne: string[] = []
    let gesehen = 0
    for (const [pfad, text] of quellen) {
      const nennt =
        WEG_TEXTE.some(
          (k) => k !== 'notifications.instanceHealth' && (text.includes(`'${k}'`) || text.includes(`"${k}"`)),
        ) || VORSILBEN.some((v) => text.includes(v))
      if (!nennt) continue
      gesehen += 1
      if (!/useWegKontext\(\)|wegKontext\(config\)/.test(text)) ohne.push(pfad)
    }
    // Bodenschwelle: So viele Dateien schlagen heute einen dieser Texte nach.
    expect(gesehen).toBeGreaterThanOrEqual(20)
    expect(ohne).toEqual([])
  })
})

describe('ein Bauteil im NEX-Betrieb', () => {
  const holen = vi.mocked(api.get)

  beforeEach(() => {
    holen.mockReset()
  })

  it('sagt, dass nexcrate nicht verbunden ist, statt nach Radarr zu fragen', async () => {
    holen.mockImplementation(((pfad: string) =>
      pfad === '/api/config'
        ? Promise.resolve({ beschaffung: 'nex' })
        : Promise.reject(new Error(`unerwartet: ${pfad}`))) as never)
    const stand = { instanzen: [] } as unknown as AnalyseStand

    rendernSchlicht(<AnalyseDienste stand={stand} />)

    expect(await screen.findByText('nexcrate ist noch nicht verbunden.')).toBeInTheDocument()
    expect(screen.queryByText(/Radarr/)).toBeNull()
  })
})
