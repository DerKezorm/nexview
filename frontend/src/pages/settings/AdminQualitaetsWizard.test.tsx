/**
 * Der Assistent — und vor allem seine Verzweigung.
 *
 * ⚠️ **Was hier geprüft wird, ist eine Zusage an den Betreiber:** „Einfach" ist
 * genau der Weg, den es vorher gab. Wer ihn wählt, bekommt kein anderes Profil
 * als vor dem Umbau — sonst zeigte der Abgleich auf jeder Instanz plötzlich
 * Unterschiede zu Profilen, die niemand angefasst hat.
 *
 * ⚠️ **Und: Keine Frage, die ins Leere geht.** Schnittfassungen gibt es nur bei
 * Filmen, HDR nur bei 4K. Eine Frage zu stellen, deren Antwort nichts bewirkt,
 * wäre eine Behauptung von Einfluss, den es nicht gibt.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { rendernSchlicht } from '../../test/rendern'
import { AdminQualitaetsWizard } from './AdminQualitaetsWizard'
import type { Antworten } from './qualitaetsprofile-typen'

function zeigen(schonVorhanden: (a: Antworten) => string | null = () => null) {
  const anlegen = vi.fn()
  rendernSchlicht(
    <AdminQualitaetsWizard
      offen
      onAbbrechen={vi.fn()}
      onAnlegen={anlegen}
      schonVorhanden={schonVorhanden}
    />,
  )
  return { anlegen, benutzer: userEvent.setup() }
}

const weiter = (b: ReturnType<typeof userEvent.setup>) =>
  b.click(screen.getByRole('button', { name: 'Weiter' }))

/**
 * Eine Antwort anklicken.
 *
 * ⚠️ Der zugängliche Name eines Feldes ist Titel **und** Hinweis - „Einfach"
 * allein trifft ihn nie genau. Gesucht wird deshalb nach dem Anfang, und der
 * Text wird dafür entschärft: „Filme (Radarr)" wäre als Muster sonst eine
 * Klammergruppe und fände „Filme Radarr".
 */
function waehlen(b: ReturnType<typeof userEvent.setup>, anfang: string) {
  const entschaerft = anfang.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return b.click(screen.getByRole('radio', { name: new RegExp('^' + entschaerft) }))
}

/**
 * Eine Antwort innerhalb **einer** Frage anklicken.
 *
 * ⚠️ „Bevorzugen" steht im Herkunft-Schritt zweimal — einmal bei den
 * Release-Gruppen, einmal bei den Schnittfassungen. Der Name allein trifft
 * beide; erst die Frage darüber trennt sie.
 */
function inFrage(b: ReturnType<typeof userEvent.setup>, frage: string, anfang: string) {
  const feld = screen.getByText(frage).closest('fieldset') as HTMLElement
  return b.click(within(feld).getByRole('radio', { name: new RegExp('^' + anfang) }))
}

/** Die Fortschrittszeile - jeder Eintrag steht als „N Titel›" darin. */
function schrittfolge(): string[] {
  const liste = screen.getByRole('list')
  return Array.from(liste.querySelectorAll('li')).map(
    (e) => e.textContent?.replace(/^\d+\s*/, '').replace('›', '').trim() ?? '',
  )
}

/**
 * Vom ersten Schritt bis zum Abschluss durchklicken.
 *
 * Nur die Pflichteingaben werden gesetzt (Name, eine Sprache); alles andere
 * bleibt auf der Voreinstellung - genau so, wie jemand durchklickt, der die
 * Empfehlung nehmen will.
 */
async function durchklicken(
  b: ReturnType<typeof userEvent.setup>,
  { modus = 'Einfach', typ = 'Filme (Radarr)', aufloesung = '1080p' } = {},
) {
  await waehlen(b, modus)
  await weiter(b)
  await b.type(screen.getByLabelText('Name des Profils'), 'Wohnzimmer')
  await waehlen(b, typ)
  await weiter(b)
  await waehlen(b, aufloesung)
  await weiter(b) // Ziel -> Warten
  await weiter(b) // Warten -> Quelle
  await weiter(b) // Quelle -> Sprachen
  await b.click(screen.getByRole('checkbox', { name: /Deutsch/ }))
  await weiter(b) // Sprachen -> Feinheiten
  await weiter(b) // Feinheiten -> weiter
}

describe('die Verzweigung am Anfang', () => {
  it('fragt zuerst nach dem Umfang', async () => {
    zeigen()
    expect(
      screen.getByText('Wie ausführlich möchtest du das entscheiden?'),
    ).toBeInTheDocument()
    // Voreingestellt ist der bisherige Weg - wer nur „Weiter" drückt, bekommt
    // genau das, was er kennt.
    expect(screen.getByRole('radio', { name: /Einfach/ })).toBeChecked()
  })

  it('zeigt bei „einfach" acht Schritte ohne die Zusatzfragen', async () => {
    const { benutzer } = zeigen()
    await waehlen(benutzer, 'Einfach')
    expect(schrittfolge()).toEqual([
      'Umfang', 'Zweck', 'Ziel', 'Warten', 'Quelle', 'Sprachen', 'Feinheiten', 'Fertig',
    ])
  })

  it('schiebt bei „ausführlich" drei Schritte vor das Ende', async () => {
    const { benutzer } = zeigen()
    await waehlen(benutzer, 'Ausführlich')
    expect(schrittfolge()).toEqual([
      'Umfang', 'Zweck', 'Ziel', 'Warten', 'Quelle', 'Sprachen', 'Feinheiten',
      'Ton', 'Bild', 'Herkunft', 'Fertig',
    ])
  })

  it('nimmt die Zusatzschritte beim Zurückschalten wieder heraus', async () => {
    // ⚠️ Wer sich umentscheidet, darf nicht auf einem Schritt stehen bleiben,
    // den es nicht mehr gibt - dann zeigte der Assistent eine leere Seite.
    const { benutzer } = zeigen()
    await waehlen(benutzer, 'Ausführlich')
    expect(schrittfolge()).toHaveLength(11)
    await waehlen(benutzer, 'Einfach')
    expect(schrittfolge()).toHaveLength(8)
    // Und der Assistent steht weiterhin auf dem ersten Schritt.
    expect(screen.getByRole('button', { name: 'Abbrechen' })).toBeInTheDocument()
  })
})

describe('der einfache Weg', () => {
  it('führt ohne Zusatzfragen bis zum Abschluss', async () => {
    const { benutzer, anlegen } = zeigen()
    await durchklicken(benutzer)

    expect(screen.getByText(/Wohnzimmer. ist fertig vorbereitet/)).toBeInTheDocument()
    await benutzer.click(screen.getByRole('button', { name: 'In der Ablage anlegen' }))
    expect(anlegen).toHaveBeenCalledTimes(1)
  })

  it('gibt alle Zusatzantworten auf „egal" weiter', async () => {
    // ⚠️ Das ist die Zusage: Der einfache Weg ist der ausführliche **ohne
    // Antworten**. Stünde hier etwas anderes als „egal", bekämen bestehende
    // Profile beim nächsten Anlegen einen anderen Inhalt.
    const { benutzer, anlegen } = zeigen()
    await durchklicken(benutzer)
    await benutzer.click(screen.getByRole('button', { name: 'In der Ablage anlegen' }))

    const a = anlegen.mock.calls[0][0] as Antworten
    expect(a.modus).toBe('einfach')
    expect({
      ton: a.ton,
      x265: a.x265,
      sdr: a.sdr,
      fassungen: a.fassungen,
      barrierefrei: a.barrierefrei,
      regionale_gruppen: a.regionale_gruppen,
      asiatische_dienste: a.asiatische_dienste,
    }).toEqual({
      ton: 'egal',
      x265: 'egal',
      sdr: 'egal',
      fassungen: 'egal',
      barrierefrei: 'egal',
      regionale_gruppen: 'egal',
      asiatische_dienste: 'egal',
    })
  })
})

describe('der ausführliche Weg', () => {
  it('reicht jede zusätzliche Antwort weiter', async () => {
    const { benutzer, anlegen } = zeigen()
    await durchklicken(benutzer, { modus: 'Ausführlich' })

    // Ton
    expect(screen.getByText('Wie wichtig ist dir der Ton?')).toBeInTheDocument()
    await waehlen(benutzer, 'Guten Ton bevorzugen')
    await inFrage(benutzer, 'Fassungen mit Audiodeskription oder Gebärdensprache', 'Meiden')
    await weiter(benutzer)

    // Bild
    expect(screen.getByText('Gibt es Kodierungen, die du nicht willst?')).toBeInTheDocument()
    await inFrage(benutzer, 'x265 bei normaler Auflösung', 'Meiden')
    await weiter(benutzer)

    // Herkunft
    expect(screen.getByText('Woher sollen die Dateien am liebsten kommen?')).toBeInTheDocument()
    await inFrage(benutzer, 'Release-Gruppen deiner Sprache', 'Bevorzugen')
    await waehlen(benutzer, 'Mitnehmen')
    await weiter(benutzer)

    await benutzer.click(screen.getByRole('button', { name: 'In der Ablage anlegen' }))
    const a = anlegen.mock.calls[0][0] as Antworten
    expect(a.modus).toBe('ausfuehrlich')
    expect(a.ton).toBe('bevorzugen')
    expect(a.barrierefrei).toBe('meiden')
    expect(a.x265).toBe('meiden')
    expect(a.regionale_gruppen).toBe('bevorzugen')
    expect(a.asiatische_dienste).toBe('dazu')
  })

  it('fragt nach SDR nur bei 4K', async () => {
    // ⚠️ Bei 1080p gibt es kein HDR. Die Frage zu stellen hieße, Fassungen
    // ohne Grund auszuschließen.
    const { benutzer } = zeigen()
    await durchklicken(benutzer, { modus: 'Ausführlich', aufloesung: '1080p' })
    await weiter(benutzer) // Ton -> Bild
    expect(screen.queryByText('Fassungen ohne HDR')).not.toBeInTheDocument()
  })

  it('fragt bei 4K nach SDR', async () => {
    const { benutzer } = zeigen()
    await durchklicken(benutzer, { modus: 'Ausführlich', aufloesung: '4K' })
    await weiter(benutzer) // Ton -> Bild
    expect(screen.getByText('Fassungen ohne HDR')).toBeInTheDocument()
  })

  it('fragt nach Schnittfassungen nur bei Filmen', async () => {
    // Serien haben keine Kinofassung - IMAX und Criterion gibt es dort nicht.
    const { benutzer } = zeigen()
    await durchklicken(benutzer, { modus: 'Ausführlich', typ: 'Serien (Sonarr)' })
    await weiter(benutzer) // Ton -> Bild
    await weiter(benutzer) // Bild -> Herkunft
    expect(screen.queryByText('Besondere Schnittfassungen')).not.toBeInTheDocument()
    // Die anderen beiden Fragen des Schritts stehen aber sehr wohl da.
    expect(screen.getByText('Release-Gruppen deiner Sprache')).toBeInTheDocument()
  })

  it('fragt bei Filmen nach Schnittfassungen', async () => {
    const { benutzer } = zeigen()
    await durchklicken(benutzer, { modus: 'Ausführlich' })
    await weiter(benutzer)
    await weiter(benutzer)
    expect(screen.getByText('Besondere Schnittfassungen')).toBeInTheDocument()
  })
})

describe('was der Assistent nicht durchlässt', () => {
  it('lässt ohne Namen nicht weiter', async () => {
    // Sonst stünde in der Ablage eine namenlose Zeile.
    const { benutzer } = zeigen()
    await weiter(benutzer)
    expect(screen.getByRole('button', { name: 'Weiter' })).toBeDisabled()
    await benutzer.type(screen.getByLabelText('Name des Profils'), 'X')
    expect(screen.getByRole('button', { name: 'Weiter' })).toBeEnabled()
  })

  it('lässt ohne gewählte Sprache nicht weiter', async () => {
    const { benutzer } = zeigen()
    await weiter(benutzer)
    await benutzer.type(screen.getByLabelText('Name des Profils'), 'X')
    await weiter(benutzer)
    await weiter(benutzer)
    await weiter(benutzer)
    await weiter(benutzer)
    expect(screen.getByRole('button', { name: 'Weiter' })).toBeDisabled()
  })

  it('sperrt das Anlegen, wenn es das Rezept schon gibt', async () => {
    // ⚠️ Zwei gleiche Rezepte ergäben zwei Profile mit identischem Inhalt -
    // und beim Verteilen einen Namensstreit auf der Instanz.
    const { benutzer } = zeigen(() => 'Wohnzimmer 4K')
    await durchklicken(benutzer)
    expect(screen.getByText(/hast du schon: .Wohnzimmer 4K./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'In der Ablage anlegen' })).toBeDisabled()
  })
})
