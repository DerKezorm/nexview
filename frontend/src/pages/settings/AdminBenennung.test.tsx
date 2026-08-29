/**
 * Die Seite, die Dateien auf der Platte anfasst.
 *
 * ⚠️ **Der eine Haken, hinter dem es ernst wird.** Schema setzen ist harmlos —
 * es gilt für das, was Radarr ab jetzt schreibt. „Auch vorhandene Dateien
 * umbenennen" dagegen benennt tausende Dateien um, bricht laufendes Seeding und
 * ist in Radarr nicht rückgängig zu machen. Was hier geprüft wird, ist der Weg
 * zu diesem Haken: dass niemand ihn versehentlich betätigt, dass die Folgen
 * vorher dastehen — und dass er **gesperrt** ist, solange der Lauf Schaden
 * anrichten würde.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
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
import type { Altnamen, BenennungStand, UmbenennenFortschritt } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminBenennung } from './AdminBenennung'

const holen = vi.mocked(api.get)
const setzen = vi.mocked(api.put)
const senden = vi.mocked(api.post)

const KEINE_ALTNAMEN: Altnamen = {
  gesamt: 0,
  im_dateinamen: 0,
  blockiert: 0,
  beispiele: [],
  blockierte_namen: [],
}

function stand(teil: Partial<BenennungStand> = {}): BenennungStand {
  return {
    kennung: 'radarr-fhd',
    name: 'Radarr FHD',
    dienst: 'radarr',
    umbenennen_an: true,
    datei_ist: '{Movie Title}',
    datei_soll: '{Movie CleanTitle} {(Release Year)}',
    ordner_ist: '{Movie Title}',
    ordner_soll: '{Movie Title} ({Release Year})',
    fassung: 'plex',
    erreichbar: true,
    meldet_medienserver: true,
    altnamen: KEINE_ALTNAMEN,
    lauf_offen: false,
    ...teil,
  }
}

/**
 * Was die Seite beim Erscheinen holt.
 *
 * ⚠️ Zwei verschiedene Abfragen laufen über `api.get`: die Liste und der
 * Fortschritt. Wer beide über denselben Rückgabewert bedient, prüft nichts —
 * deshalb wird nach dem Pfad unterschieden.
 */
function antworten(liste: BenennungStand[], fortschritt?: UmbenennenFortschritt) {
  holen.mockImplementation((pfad: string) => {
    if (pfad.endsWith('/fortschritt')) {
      return Promise.resolve(
        fortschritt ?? {
          laeuft: false,
          instanz: '',
          schritt: 'fertig',
          erledigt: 0,
          gesamt: 0,
          betroffen: 0,
          beispiele: [],
          fortgesetzt: false,
        },
      )
    }
    return Promise.resolve(liste)
  })
}

const aufklappen = (b: ReturnType<typeof userEvent.setup>) =>
  screen.findByRole('button', { name: 'Vergleichen' }).then((k) => b.click(k))

beforeEach(() => {
  holen.mockReset()
  setzen.mockReset()
  senden.mockReset()
})

describe('was abweicht, steht getrennt da', () => {
  // ⚠️ Ein neuer Dateiname ist harmlos, ein neuer Ordnername kann den
  // Gesehen-Status im Medienserver kosten. Eine gemeinsame Marke „Weicht ab"
  // nähme genau die Information weg, die für die Entscheidung zählt.
  it('nennt den Dateinamen, wenn nur er abweicht', async () => {
    antworten([stand({ ordner_ist: 'gleich', ordner_soll: 'gleich' })])
    rendernSchlicht(<AdminBenennung />)
    expect(await screen.findByText('Dateiname weicht ab')).toBeInTheDocument()
  })

  it('nennt den Ordnernamen, wenn nur er abweicht', async () => {
    antworten([stand({ datei_ist: 'gleich', datei_soll: 'gleich' })])
    rendernSchlicht(<AdminBenennung />)
    expect(await screen.findByText('Ordnername weicht ab')).toBeInTheDocument()
  })

  it('nennt beides, wenn beides abweicht', async () => {
    antworten([stand()])
    rendernSchlicht(<AdminBenennung />)
    expect(await screen.findByText('Datei- und Ordnername weichen ab')).toBeInTheDocument()
  })

  it('meldet Übereinstimmung, ohne einen Knopf anzubieten', async () => {
    antworten([
      stand({
        datei_ist: 'a', datei_soll: 'a', ordner_ist: 'b', ordner_soll: 'b',
      }),
    ])
    rendernSchlicht(<AdminBenennung />)
    expect(await screen.findByText('Entspricht der Empfehlung')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Vergleichen' })).not.toBeInTheDocument()
  })
})

describe('der Haken, der Dateien anfasst', () => {
  it('ist nicht vorausgewählt', async () => {
    // ⚠️ Vorausgewählt wäre er eine Falle: Wer nur das Schema setzen will,
    // stieße ohne es zu merken einen Lauf über die ganze Bibliothek an.
    antworten([stand()])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)

    expect(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeInTheDocument()
  })

  it('wird beim Wechsel der Instanz wieder abgewählt', async () => {
    // ⚠️ Der gefährlichste denkbare Übertrag: Der Haken bliebe stehen, und der
    // nächste Klick startete einen Umbenennlauf auf einer Instanz, für die ihn
    // niemand gesetzt hat. Deshalb setzt jedes Aufklappen die Auswahl neu.
    antworten([stand(), stand({ kennung: 'radarr-uhd', name: 'Radarr UHD' })])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    const [ersteVergleichen] = await screen.findAllByRole('button', { name: 'Vergleichen' })

    await b.click(ersteVergleichen)
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))
    expect(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ })).toBeChecked()

    await b.click(screen.getByRole('button', { name: 'Zuklappen' }))
    await b.click(screen.getAllByRole('button', { name: 'Vergleichen' })[1])
    expect(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeInTheDocument()
  })

  it('zeigt die Folgen erst, wenn er gesetzt ist — und benennt sie', async () => {
    antworten([stand()])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)

    expect(screen.queryByText(/Laufendes Seeding bricht/)).not.toBeInTheDocument()
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))
    expect(screen.getByText(/Laufendes Seeding bricht/)).toBeInTheDocument()
    // Und der Knopf sagt jetzt, dass mehr passiert als „übernehmen".
    expect(screen.getByRole('button', { name: 'Übernehmen und umbenennen' })).toBeInTheDocument()
  })

  it('warnt, wenn der Medienserver nichts erfährt', async () => {
    antworten([stand({ meldet_medienserver: false })])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))

    expect(screen.getByText(/sagt deinem Medienserver nichts/)).toBeInTheDocument()
  })

  it('schickt genau die drei Haken', async () => {
    antworten([stand()])
    setzen.mockResolvedValue(stand())
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))
    await b.click(screen.getByRole('button', { name: 'Übernehmen und umbenennen' }))

    await waitFor(() =>
      expect(setzen).toHaveBeenCalledWith('/api/settings/qualitaetsprofile/benennung', {
        kennung: 'radarr-fhd',
        // Der Dateiname weicht ab, also ist er beim Aufklappen vorgewählt.
        datei: true,
        // Der Ordnername nicht - seine Folgen sind schwerer.
        ordner: false,
        bestand: true,
      }),
    )
  })

  it('lässt nichts abschicken, wenn kein Haken gesetzt ist', async () => {
    antworten([stand({ datei_ist: 'gleich', datei_soll: 'gleich' })])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)

    // Nur der Ordner weicht ab, und der ist beim Aufklappen bewusst nicht
    // vorgewählt - also gibt es zunächst nichts zu tun.
    expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeDisabled()
  })
})

describe('der alte Vorsatz sperrt den Lauf', () => {
  const mitAltnamen = (teil: Partial<Altnamen>) =>
    stand({ altnamen: { ...KEINE_ALTNAMEN, ...teil } })

  it('hält den Lauf an, solange alte Musternamen in Dateinamen fließen', async () => {
    // ⚠️ Das ist kein Schönheitsfehler: Der Lauf schriebe „NXV - German DL" in
    // jeden Dateinamen der ganzen Bibliothek - und ihn loszuwerden wäre ein
    // zweiter kompletter Lauf.
    antworten([mitAltnamen({ gesamt: 16, im_dateinamen: 16, beispiele: ['NXV - German DL'] })])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))

    expect(screen.getByText(/Halt: 16 Erkennungsmuster/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Übernehmen und umbenennen' })).toBeDisabled()
  })

  it('sperrt nur den Bestandslauf, nicht das Setzen des Schemas', async () => {
    // Das Schema zu setzen ist auch mit alten Musternamen harmlos - es gilt
    // erst für das, was Radarr ab jetzt schreibt.
    antworten([mitAltnamen({ gesamt: 16, im_dateinamen: 16, beispiele: ['NXV - German DL'] })])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)

    expect(screen.getByRole('button', { name: 'Übernehmen' })).toBeEnabled()
  })

  it('bietet den Aufräumknopf an, solange er etwas bewirkt', async () => {
    antworten([
      mitAltnamen({ gesamt: 16, im_dateinamen: 16, blockiert: 3, blockierte_namen: ['NXV - DV'] }),
    ])
    senden.mockResolvedValue({ umbenannt: 13, altnamen: KEINE_ALTNAMEN })
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))
    await b.click(screen.getByRole('button', { name: 'Musternamen jetzt in Ordnung bringen' }))

    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith(
        '/api/settings/qualitaetsprofile/benennung/radarr-fhd/altnamen',
        {},
      ),
    )
  })

  it('bietet keinen Knopf an, der nichts bewirken kann', async () => {
    // ⚠️ Sind alle Muster blockiert, räumte er null davon auf - und der Lauf
    // bliebe für immer gesperrt, ohne dass irgendwo stünde, wie man hier
    // herauskommt. Dann lieber sagen, was zu tun ist.
    antworten([
      mitAltnamen({
        gesamt: 4, im_dateinamen: 4, blockiert: 4,
        blockierte_namen: ['NXV - DV', 'NXV - HDR10+'],
      }),
    ])
    const b = userEvent.setup()
    rendernSchlicht(<AdminBenennung />)
    await aufklappen(b)
    await b.click(screen.getByRole('checkbox', { name: /Auch vorhandene Dateien/ }))

    expect(
      screen.queryByRole('button', { name: 'Musternamen jetzt in Ordnung bringen' }),
    ).not.toBeInTheDocument()
    expect(screen.getByText(/Alle 4 halten den Vorsatz/)).toBeInTheDocument()
  })
})

describe('ein Lauf, den diese Seite nicht angestoßen hat', () => {
  it('wird gefunden und angezeigt', async () => {
    // ⚠️ Ohne das ist die Absicherung im Hintergrund wertlos: Wer neu lädt oder
    // von einem anderen Gerät hereinschaut, sah bisher gar nichts - während im
    // Hintergrund tausende Dateien umbenannt wurden.
    antworten([stand({ lauf_offen: true })], {
      laeuft: true,
      instanz: 'Radarr FHD',
      schritt: 'umbenennen',
      erledigt: 1200,
      gesamt: 3531,
      betroffen: 1200,
      beispiele: [],
      fortgesetzt: false,
    })
    rendernSchlicht(<AdminBenennung />)

    expect(await screen.findByText('Dateien werden umbenannt')).toBeInTheDocument()
    expect(screen.getByText(/Abbrechen ginge nur in Radarr selbst/)).toBeInTheDocument()
  })

  it('sagt es, wenn ein unterbrochener Lauf weitermacht', async () => {
    // Ein Lauf, der nach einem Neustart von selbst weiterläuft, wirkt sonst wie
    // ein Fehler statt wie die Rettung, die er ist.
    antworten([stand({ lauf_offen: true })], {
      laeuft: true,
      instanz: 'Radarr FHD',
      schritt: 'pruefen',
      erledigt: 400,
      gesamt: 3531,
      betroffen: 12,
      beispiele: [],
      fortgesetzt: true,
    })
    rendernSchlicht(<AdminBenennung />)

    expect(await screen.findByText(/Fortgesetzt/)).toBeInTheDocument()
    // Im Prüfschritt wird nur gelesen - das muss dastehen.
    expect(screen.getByText(/Nur gelesen/)).toBeInTheDocument()
  })
})

describe('wenn die Instanz nicht antwortet', () => {
  it('bietet nichts zum Ändern an', async () => {
    antworten([stand({ erreichbar: false })])
    rendernSchlicht(<AdminBenennung />)

    expect(await screen.findByText('Nicht erreichbar')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Vergleichen' })).not.toBeInTheDocument()
  })
})
