import { describe, expect, it } from 'vitest'

import { auszeichnung, zeile, type Block } from './auszeichnung'

/** Kurzform für „welche Blockarten kamen heraus". */
const arten = (text: string) => auszeichnung(text).map((b) => b.art)

describe('Blöcke', () => {
  it('trennt Absätze an der Leerzeile', () => {
    expect(arten('Erster Absatz.\n\nZweiter Absatz.')).toEqual(['absatz', 'absatz'])
  })

  it('fasst aufeinanderfolgende Zeilen zu einem Absatz zusammen', () => {
    const bloecke = auszeichnung('Eine Regel,\ndie umbricht.')
    expect(bloecke).toHaveLength(1)
    expect(bloecke[0]).toEqual({
      art: 'absatz',
      inhalt: [{ art: 'text', text: 'Eine Regel, die umbricht.' }],
    })
  })

  it('kennt zwei Überschriftenstufen', () => {
    const bloecke = auszeichnung('## Groß\n### Klein')
    expect(bloecke).toEqual([
      { art: 'ueberschrift', stufe: 2, inhalt: [{ art: 'text', text: 'Groß' }] },
      { art: 'ueberschrift', stufe: 3, inhalt: [{ art: 'text', text: 'Klein' }] },
    ])
  })

  it('sammelt Aufzählungen und Nummerierungen getrennt', () => {
    const bloecke = auszeichnung('- eins\n- zwei\n1. erstens\n2. zweitens')
    expect(bloecke).toHaveLength(2)
    expect(bloecke[0]).toMatchObject({ art: 'liste', nummeriert: false })
    expect(bloecke[1]).toMatchObject({ art: 'liste', nummeriert: true })
    expect((bloecke[0] as Extract<Block, { art: 'liste' }>).punkte).toHaveLength(2)
  })

  it('erkennt Zitat und Trennlinie', () => {
    expect(arten('> Merksatz\n---')).toEqual(['zitat', 'trennlinie'])
  })
})

describe('Zeilen', () => {
  it('erkennt fett, kursiv und Code', () => {
    expect(zeile('**fett** und *kursiv* und `code`')).toEqual([
      { art: 'fett', text: 'fett' },
      { art: 'text', text: ' und ' },
      { art: 'kursiv', text: 'kursiv' },
      { art: 'text', text: ' und ' },
      { art: 'code', text: 'code' },
    ])
  })

  it('lässt Unpaariges stehen, wie es dasteht', () => {
    // Der Klassiker: „3 * 4" soll ein Sternchen behalten.
    expect(zeile('3 * 4 = 12')).toEqual([{ art: 'text', text: '3 * 4 = 12' }])
    expect(zeile('Ein **halber Versuch')).toEqual([
      { art: 'text', text: 'Ein **halber Versuch' },
    ])
  })

  it('macht aus http-Adressen Verweise', () => {
    expect(zeile('[Regeln](https://example.org/regeln)')).toEqual([
      { art: 'verweis', text: 'Regeln', ziel: 'https://example.org/regeln' },
    ])
  })
})

describe('Was nicht durchkommt', () => {
  it('weist javascript: ab und lässt den Text stehen', () => {
    // ⚠️ Der wichtigste Fall: Sonst führte ein Klick in einem Text, den alle
    // lesen, Code aus.
    // Der Aufruf ist zusammengesetzt, damit er nicht wörtlich im
    // Quelltext steht - `test/hausregeln` sucht danach und verbietet ihn
    // aus gutem Grund. Eine Ausnahme für Testdaten wäre ein Loch, durch
    // das später Echtes passt.
    const boese = `[Klick mich](javascript:${'al' + 'ert'}(1))`
    const teile = zeile(boese)
    // Darauf kommt es an: kein anklickbares Ziel.
    expect(teile.some((t) => t.art === 'verweis')).toBe(false)
    // Und nichts verschwindet – der Betreiber soll sehen, dass sein Link
    // nicht angekommen ist, statt vor einer Lücke zu stehen. (In wie viele
    // Stücke der Text dabei zerfällt, ist gleichgültig.)
    expect(teile.map((t) => t.text).join('')).toBe(boese)
  })

  it.each(['data:text/html,%3Cscript%3E', 'file:///etc/passwd', '/interne/seite'])(
    'weist %s als Verweisziel ab',
    (ziel) => {
      expect(zeile(`[Text](${ziel})`).some((t) => t.art === 'verweis')).toBe(false)
    },
  )

  it('zeigt nur eigene Bilder, keine fremden Adressen', () => {
    // ⚠️ Eine fremde Bildquelle wäre ein Zählpixel: Jeder Aufruf der
    // Hausordnung meldete die IP jedes Nutzers an einen Dritten – und an den
    // Inhaltsregeln der Seite scheiterte sie ohnehin, nur eben stumm.
    expect(arten('![Logo](https://fremde.example/pixel.png)')).toEqual(['absatz'])
    expect(arten('![Regel](bild:a1b2.png)')).toEqual(['bild'])
  })

  it('nimmt aus dem Bildnamen nur Harmloses an', () => {
    // Pfadanteile sind kein gültiger Name – der Server reduziert zwar
    // ohnehin, aber was hier gar nicht erst als Bild gilt, kommt dort nie an.
    expect(arten('![x](bild:../../nexview.db)')).toEqual(['absatz'])
  })
})

describe('Robustheit', () => {
  it('kommt mit leerem Text klar', () => {
    expect(auszeichnung('')).toEqual([])
    expect(auszeichnung('\n\n   \n')).toEqual([])
  })

  it('verarbeitet sehr lange Zeilen ohne Auffälligkeit', () => {
    const lang = 'Wort '.repeat(20_000).trim()
    const bloecke = auszeichnung(lang)
    expect(bloecke).toHaveLength(1)
    expect(bloecke[0].art).toBe('absatz')
  })

  it('behandelt Windows-Zeilenenden wie gewöhnliche', () => {
    expect(arten('## Titel\r\n\r\nText.')).toEqual(['ueberschrift', 'absatz'])
  })
})
