/**
 * Die Seite, die in fremden Instanzen löscht.
 *
 * ⚠️ **Warum ausgerechnet diese Komponente Tests bekommt.** Überall sonst
 * schreibt Nexview nur, was es selbst angelegt hat. Hier nicht: Der Betreiber
 * hakt Profile und Muster an, und Nexview löscht sie in *seinem* Radarr — auch
 * die, die es nie angefasst hat. Was hier schiefgeht, ist in Radarr nicht
 * rückgängig zu machen.
 *
 * Geprüft wird deshalb nicht das Aussehen, sondern die Zusage: **Es geht genau
 * das weg, was angehakt war — und angehakt werden kann nur, was frei ist.**
 * Die drei Wege dahin (Auswahl, Häppchen, Sperre) sind einzeln geprüft, weil
 * jeder für sich die Zusage brechen könnte.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>(
    '../../api/client',
  )
  return {
    ...echt,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
  }
})

import { api } from '../../api/client'
import type { InstanzBestand, MusterBestand, ProfilBestand } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminArrBestand } from './AdminArrBestand'

const holen = vi.mocked(api.get)
const senden = vi.mocked(api.post)

function profil(teil: Partial<ProfilBestand> & { id: number; name: string }): ProfilBestand {
  const medien = teil.medien ?? 0
  const listen = teil.importlisten ?? 0
  const sammlungen = teil.sammlungen ?? 0
  return {
    unser: false,
    medien,
    importlisten: listen,
    sammlungen,
    // Frei ist, woran nichts hängt - so rechnet auch der Server.
    loeschbar: medien + listen + sammlungen === 0,
    grund: '',
    ...teil,
  }
}

function muster(
  teil: Partial<MusterBestand> & { id: number; name: string },
): MusterBestand {
  const benutzt = teil.benutzt_von ?? []
  const zumPlan = teil.gehoert_zu_plan ?? false
  return {
    benutzt_von: benutzt,
    gehoert_zu_plan: zumPlan,
    alter_vorsatz: false,
    im_dateinamen: false,
    loeschbar: benutzt.length === 0 && !zumPlan,
    ...teil,
  }
}

function instanz(teil: Partial<InstanzBestand> = {}): InstanzBestand {
  return {
    kennung: 'radarr-fhd',
    name: 'Radarr FHD',
    erreichbar: true,
    profile: [],
    muster: [],
    ...teil,
  }
}

/** Der Bestand, den die Seite beim Erscheinen holt. */
function bestandLiefern(...instanzen: InstanzBestand[]) {
  holen.mockResolvedValue(instanzen)
}

/** Die Instanz aufklappen — ohne das ist nichts zum Anhaken da. */
async function aufklappen(benutzer: ReturnType<typeof userEvent.setup>) {
  await benutzer.click(await screen.findByRole('button', { name: 'Ansehen' }))
}

/** Die Kästchen einer der beiden Listen. */
function kaesten(titel: 'Qualitätsprofile' | 'Erkennungsmuster') {
  const ueberschrift = screen.getByText(titel)
  const block = ueberschrift.closest('div')?.parentElement as HTMLElement
  return within(block).getAllByRole('checkbox') as HTMLInputElement[]
}

beforeEach(() => {
  holen.mockReset()
  senden.mockReset()
})

describe('was angehakt werden darf', () => {
  it('sperrt gebundene Profile und nennt den Grund', async () => {
    // ⚠️ Der Fall, der Radarr zu „is in use" bringt, ohne zu sagen von wem.
    // Eine Sammlung war der übersehene Grund - deshalb steht sie mit dabei.
    bestandLiefern(
      instanz({
        profile: [
          profil({ id: 1, name: 'Leer' }),
          profil({ id: 2, name: 'In Benutzung', medien: 812, sammlungen: 3 }),
        ],
      }),
    )
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)

    const [frei, gebunden] = kaesten('Qualitätsprofile')
    expect(frei).toBeEnabled()
    expect(gebunden).toBeDisabled()
    expect(screen.getByText('812 Medien · 0 Listen · 3 Sammlungen')).toBeInTheDocument()
  })

  it('sperrt Muster, die zu einem Bauplan gehören', async () => {
    // ⚠️ Der Fehler, der mehrere Löschrunden gekostet hat: Ein Bauplan bringt
    // Muster mit **null** Punkten mit. Niemand gibt ihnen Punkte, also sahen
    // sie ungenutzt aus - und das nächste Verteilen legte sie wieder an.
    bestandLiefern(
      instanz({
        muster: [
          muster({ id: 10, name: 'Wirklich frei' }),
          muster({ id: 11, name: 'Gehört zum Plan', gehoert_zu_plan: true }),
          muster({ id: 12, name: 'Bepunktet', benutzt_von: ['German HD'] }),
        ],
      }),
    )
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)

    const [frei, imPlan, bepunktet] = kaesten('Erkennungsmuster')
    expect(frei).toBeEnabled()
    expect(imPlan).toBeDisabled()
    expect(bepunktet).toBeDisabled()
    expect(screen.getByText('gehört zu einem deiner Profile')).toBeInTheDocument()
    expect(screen.getByText('benutzt von German HD')).toBeInTheDocument()
  })

  it('wählt mit „Alles Ungenutzte" nur die freien Einträge', async () => {
    bestandLiefern(
      instanz({
        profile: [profil({ id: 1, name: 'Leer' }), profil({ id: 2, name: 'Voll', medien: 5 })],
        muster: [
          muster({ id: 10, name: 'Frei' }),
          muster({ id: 11, name: 'Plan', gehoert_zu_plan: true }),
        ],
      }),
    )
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(screen.getByRole('button', { name: 'Alles Ungenutzte auswählen' }))

    expect(kaesten('Qualitätsprofile').map((k) => k.checked)).toEqual([true, false])
    expect(kaesten('Erkennungsmuster').map((k) => k.checked)).toEqual([true, false])
    expect(screen.getByRole('button', { name: '2 löschen' })).toBeEnabled()
  })
})

describe('was tatsächlich gelöscht wird', () => {
  it('schickt genau die angehakten Nummern — Profile zuerst', async () => {
    // ⚠️ Die Reihenfolge ist nicht kosmetisch: Ein Muster gilt als benutzt,
    // solange ein Profil ihm Punkte gibt. Kämen die Muster zuerst, lehnte die
    // Instanz sie ab - und der Betreiber sähe „ging nicht" ohne Grund.
    bestandLiefern(
      instanz({
        profile: [profil({ id: 7, name: 'Weg damit' })],
        muster: [muster({ id: 70, name: 'A' }), muster({ id: 71, name: 'B' })],
      }),
    )
    senden.mockResolvedValue({ geloescht_profile: [], geloescht_muster: [], abgelehnt: {} })
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)

    await benutzer.click(kaesten('Qualitätsprofile')[0])
    await benutzer.click(kaesten('Erkennungsmuster')[1]) // nur B, nicht A
    await benutzer.click(screen.getByRole('button', { name: '2 löschen' }))

    await waitFor(() => expect(senden).toHaveBeenCalledTimes(2))
    const pfad = '/api/settings/qualitaetsprofile/bestand/radarr-fhd/aufraeumen'
    expect(senden.mock.calls[0]).toEqual([pfad, { profil_ids: [7], muster_ids: [] }])
    expect(senden.mock.calls[1]).toEqual([pfad, { profil_ids: [], muster_ids: [71] }])
  })

  it('zerlegt viele Muster in Häppchen von 20', async () => {
    // ⚠️ Gemessen dauert ein Löschvorgang rund 0,4 s. 45 Muster in **einer**
    // Anfrage sind 18 Sekunden - noch harmlos; 131 waren knapp eine Minute und
    // liefen in die 60-Sekunden-Grenze eines Reverse Proxy. Danach wusste
    // niemand, wie viel schon weg war.
    const viele = Array.from({ length: 45 }, (_, n) => muster({ id: n + 1, name: `M${n}` }))
    bestandLiefern(instanz({ muster: viele }))
    senden.mockResolvedValue({ geloescht_profile: [], geloescht_muster: [], abgelehnt: {} })
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(screen.getByRole('button', { name: 'Alles Ungenutzte auswählen' }))
    await benutzer.click(screen.getByRole('button', { name: '45 löschen' }))

    await waitFor(() => expect(senden).toHaveBeenCalledTimes(3))
    const groessen = senden.mock.calls.map((c) => (c[1] as { muster_ids: number[] }).muster_ids.length)
    expect(groessen).toEqual([20, 20, 5])
    // Zusammen wieder genau die 45 - keins doppelt, keins verloren.
    const alle = senden.mock.calls.flatMap((c) => (c[1] as { muster_ids: number[] }).muster_ids)
    expect(new Set(alle).size).toBe(45)
  })

  it('erklärt, warum nach dem Löschen noch etwas frei ist', async () => {
    // ⚠️ Genau hier stand der Betreiber und hielt es für einen Fehler: „Ich
    // habe alles angehakt, und es bleibt immer etwas übrig." Richtig ist: Die
    // Muster der gelöschten Profile werden **erst danach** frei. Ohne diesen
    // Satz sieht Richtigkeit wie ein Fehler aus.
    bestandLiefern(instanz({ profile: [profil({ id: 7, name: 'Weg' })] }))
    senden.mockResolvedValue({
      geloescht_profile: ['Weg'],
      geloescht_muster: [],
      abgelehnt: {},
    })
    // Der Nachschlag nach dem Lauf: jetzt sind 12 Muster frei.
    holen.mockResolvedValueOnce([instanz({ profile: [profil({ id: 7, name: 'Weg' })] })])
    holen.mockResolvedValue([
      instanz({ muster: Array.from({ length: 12 }, (_, n) => muster({ id: n, name: `M${n}` })) }),
    ])

    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(kaesten('Qualitätsprofile')[0])
    await benutzer.click(screen.getByRole('button', { name: '1 löschen' }))

    expect(await screen.findByText(/12 weitere Einträge frei geworden/)).toBeInTheDocument()
  })

  it('nennt beim Namen, was die Instanz abgelehnt hat', async () => {
    bestandLiefern(instanz({ profile: [profil({ id: 7, name: 'Standard (Radarr)' })] }))
    senden.mockResolvedValue({
      geloescht_profile: [],
      geloescht_muster: [],
      abgelehnt: { 'Standard (Radarr)': 'is in use' },
    })
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(kaesten('Qualitätsprofile')[0])
    await benutzer.click(screen.getByRole('button', { name: '1 löschen' }))

    expect(
      await screen.findByText(/Nicht gelöscht: Standard \(Radarr\) \(is in use\)/),
    ).toBeInTheDocument()
  })

  it('löscht ohne Auswahl gar nichts', async () => {
    bestandLiefern(instanz({ profile: [profil({ id: 1, name: 'Leer' })] }))
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)

    expect(screen.getByRole('button', { name: '0 löschen' })).toBeDisabled()
    expect(senden).not.toHaveBeenCalled()
  })

  it('vergisst die Auswahl beim Wechsel der Instanz', async () => {
    // ⚠️ Die Nummern gelten je Instanz. Eine mitgeschleppte Auswahl löschte in
    // der zweiten Instanz das, was in der ersten dieselbe Nummer trug.
    bestandLiefern(
      instanz({ profile: [profil({ id: 1, name: 'A-frei' })] }),
      instanz({
        kennung: 'radarr-uhd',
        name: 'Radarr UHD',
        profile: [profil({ id: 1, name: 'B-frei' })],
      }),
    )
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    const [ersteAnsehen, zweiteAnsehen] = await screen.findAllByRole('button', {
      name: 'Ansehen',
    })

    await benutzer.click(ersteAnsehen)
    await benutzer.click(kaesten('Qualitätsprofile')[0])
    expect(screen.getByRole('button', { name: '1 löschen' })).toBeInTheDocument()

    await benutzer.click(screen.getByRole('button', { name: 'Zuklappen' }))
    await benutzer.click(zweiteAnsehen)
    expect(kaesten('Qualitätsprofile')[0].checked).toBe(false)
    expect(screen.getByRole('button', { name: '0 löschen' })).toBeDisabled()
  })
})

describe('während ein Lauf läuft', () => {
  it('sperrt Auswahl und Aufklappen, bis er durch ist', async () => {
    // ⚠️ Sonst verschiebt sich der Bestand unter der Auswahl: Der Betreiber
    // hakt weiter an, während die Nummern darunter schon gelöscht werden.
    let freigeben: (w: unknown) => void = () => {}
    const haengt = new Promise((aufloesen) => {
      freigeben = aufloesen
    })
    bestandLiefern(
      instanz({
        profile: [profil({ id: 1, name: 'Weg' })],
        muster: [muster({ id: 10, name: 'Auch weg' })],
      }),
    )
    senden.mockReturnValue(haengt as Promise<never>)
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(kaesten('Qualitätsprofile')[0])
    await benutzer.click(screen.getByRole('button', { name: '1 löschen' }))

    await waitFor(() => expect(screen.getByText('Wird entfernt …')).toBeInTheDocument())
    expect(kaesten('Erkennungsmuster')[0]).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Zuklappen' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Alles Ungenutzte auswählen' })).toBeDisabled()

    freigeben({ geloescht_profile: ['Weg'], geloescht_muster: [], abgelehnt: {} })
    await waitFor(() => expect(screen.queryByText('Wird entfernt …')).not.toBeInTheDocument())
  })
})

describe('umhängen statt löschen', () => {
  it('sagt die Folge, bevor geklickt wird', async () => {
    // ⚠️ Das neue Profil bewertet anders - Titel darunter merkt die Instanz zur
    // Aufwertung vor, und das löst Downloads aus. Wer das erst hinterher
    // erfährt, hat sie schon laufen.
    bestandLiefern(
      instanz({
        profile: [
          profil({ id: 1, name: 'Alt', medien: 812 }),
          profil({ id: 2, name: 'Neu', unser: true }),
        ],
      }),
    )
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(screen.getByRole('button', { name: 'Medien umhängen' }))

    expect(screen.getByText(/812 Medien liegen auf .Alt./)).toBeInTheDocument()
    expect(screen.getByText(/kann Downloads auslösen/)).toBeInTheDocument()
    // Solange kein Ziel gewählt ist, ist der Knopf tot.
    expect(screen.getByRole('button', { name: '812 umhängen' })).toBeDisabled()
  })

  it('bietet das Quellprofil nicht als Ziel an', async () => {
    // Auf sich selbst umhängen wäre ein Aufruf, der nichts tut - und danach
    // wäre das Profil immer noch nicht löschbar.
    bestandLiefern(
      instanz({
        profile: [
          profil({ id: 1, name: 'Alt', medien: 5 }),
          profil({ id: 2, name: 'Neu', unser: true }),
        ],
      }),
    )
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(screen.getByRole('button', { name: 'Medien umhängen' }))

    const auswahl = screen.getByRole('combobox')
    const ziele = within(auswahl)
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(ziele).toEqual(['Zielprofil wählen …', 'Neu — von Nexview'])
  })

  it('schickt Quelle und Ziel und meldet die Zahl zurück', async () => {
    bestandLiefern(
      instanz({
        profile: [
          profil({ id: 1, name: 'Alt', medien: 812 }),
          profil({ id: 2, name: 'Neu', unser: true }),
        ],
      }),
    )
    senden.mockResolvedValue({ umgehaengt: 812, grund: '' })
    const benutzer = userEvent.setup()
    rendernSchlicht(<AdminArrBestand />)
    await aufklappen(benutzer)
    await benutzer.click(screen.getByRole('button', { name: 'Medien umhängen' }))
    await benutzer.selectOptions(screen.getByRole('combobox'), '2')
    await benutzer.click(screen.getByRole('button', { name: '812 umhängen' }))

    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith(
        '/api/settings/qualitaetsprofile/bestand/radarr-fhd/umhaengen',
        { von: 1, nach: 2 },
      ),
    )
    expect(await screen.findByText('812 Medien umgehängt.')).toBeInTheDocument()
  })
})

describe('wenn die Instanz nicht antwortet', () => {
  it('zeigt keinen Bestand und keinen Löschknopf', async () => {
    // ⚠️ Ein leerer Bestand und ein nicht erreichbarer sehen gleich aus. Wer
    // das verwechselt, hält eine abgestürzte Instanz für aufgeräumt.
    bestandLiefern(instanz({ erreichbar: false }))
    rendernSchlicht(<AdminArrBestand />)

    expect(await screen.findByText('antwortet gerade nicht')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ansehen' })).not.toBeInTheDocument()
  })
})
