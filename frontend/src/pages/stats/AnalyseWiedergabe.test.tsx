/**
 * Die Kennzahl „Bibliothek berührt" — und was sie sagt, wenn sie nichts weiß.
 *
 * ⚠️ **Hier stand ein Widerspruch.** Bei leerem Bestand zeigte die Kachel
 * „0 %" und darunter „2 von 0 Titeln". Wer 2 gesehen hat, kann nicht 0 im
 * Bestand haben — die Zahl widerspricht sich selbst.
 *
 * ⚠️ **Und es ist kein Sonderfall.** `bestand_gesamt` ist auch dann null, wenn
 * der Bibliotheks-Abgleich noch nicht gelaufen ist oder Nexview die
 * Bibliotheken des Medienservers nicht lesen kann — also genau bei einem
 * frisch verbundenen Server. Der allererste Blick eines neuen Betreibers auf
 * diese Seite traf damit auf eine Angabe, die sich widerlegt.
 */

import { expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import type { WiedergabeStand } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AnalyseWiedergabe } from './AnalyseWiedergabe'

function stand(teil: Partial<WiedergabeStand> = {}): WiedergabeStand {
  return {
    monate: [],
    personen: [],
    beliebteste: [],
    bestand: [],
    angesehen: 0,
    bestand_gesamt: 0,
    konten_mit_daten: 0,
    spitzen: [],
    spitze_gesamt: 0,
    ...teil,
  }
}

it('nennt keinen Anteil, solange der Bestand unbekannt ist', () => {
  // Der gemeldete Fall: 2 Marker, aber noch keine gelesene Bibliothek.
  rendernSchlicht(<AnalyseWiedergabe stand={stand({ angesehen: 2, bestand_gesamt: 0 })} />)

  const kachel = screen.getByText('Bibliothek angesehen').closest('div')!
  // Weder die falsche Null noch der widersprüchliche Satz.
  expect(kachel.textContent).not.toContain('0 %')
  expect(kachel.textContent).not.toContain('2 von 0')
  // Stattdessen: ein Strich und der Grund.
  expect(kachel.textContent).toContain('—')
  expect(kachel.textContent).toContain('noch keine Bibliotheksdaten')
})

it('behält die Zahl der gesehenen Titel, auch ohne Bestand', () => {
  // ⚠️ Die Angabe ist nicht falsch, nur ihr Nenner fehlt. Sie wegzulassen
  // hieße, eine gemessene Tatsache zu verschweigen.
  rendernSchlicht(<AnalyseWiedergabe stand={stand({ angesehen: 2, bestand_gesamt: 0 })} />)

  expect(screen.getByText(/2 gesehen/)).toBeInTheDocument()
})

it('rechnet ganz normal, sobald der Bestand dasteht', () => {
  rendernSchlicht(<AnalyseWiedergabe stand={stand({ angesehen: 3, bestand_gesamt: 12 })} />)

  const kachel = screen.getByText('Bibliothek angesehen').closest('div')!
  expect(kachel.textContent).toContain('25 %')
  expect(kachel.textContent).toContain('3 von 12')
})
