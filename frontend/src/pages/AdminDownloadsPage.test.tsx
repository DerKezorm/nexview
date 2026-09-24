/**
 * Die Seite Downloads.
 *
 * ⚠️ **Was hier auf dem Spiel steht, sind die Knöpfe.** Entfernen löscht Daten
 * im Download-Programm, ein manueller Import legt Dateien in die Bibliothek,
 * gegen die Radarr etwas hat. Deshalb prüfen diese Tests vor allem, dass
 * vorher gefragt wird, dass genau das Richtige an den Server geht und dass ein
 * Einwand nie still übergangen wird.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
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

import { api } from '../api/client'
import type {
  DownloadAutomatik,
  DownloadHaenger,
  DownloadKandidat,
  DownloadLaufend,
  DownloadsStand,
} from '../api/types'
import i18n from '../i18n'
import { rendernSchlicht } from '../test/rendern'
import { AdminDownloadsPage } from './AdminDownloadsPage'

const holen = vi.mocked(api.get)
const senden = vi.mocked(api.post)
const setzen = vi.mocked(api.put)

function haenger(teil: Partial<DownloadHaenger> = {}): DownloadHaenger {
  return {
    id: 7,
    kennung: 'radarr-standard',
    instanz: 'Radarr',
    media_type: 'movie',
    titel: 'Beispielfilm',
    jahr: 2010,
    release: 'Beispielfilm.2010.1080p.BluRay-GRP',
    folgen: [],
    grund: 'sample',
    wortlaut: ['Sample'],
    zustand: 'importPending',
    meldestufe: 'warning',
    programmstand: 'completed',
    protokoll: 'torrent',
    programm: 'qBittorrent',
    groesse: 1000,
    erstmals_gesehen: '2026-09-12T20:00:00',
    haengt_seit: '2026-09-12T20:10:00',
    empfohlen: ['entfernen_neu_suchen', 'manuell_importieren'],
    weitere: ['entfernen', 'erneut_pruefen'],
    besteller: [{ anfrage_id: 3, name: 'kim' }],
    poster: null,
    ...teil,
  }
}

function laufend(teil: Partial<DownloadLaufend> = {}): DownloadLaufend {
  return {
    kennung: 'radarr-standard',
    instanz: 'Radarr',
    media_type: 'movie',
    titel: 'Zweiter Film',
    jahr: 2011,
    release: 'Zweiter.Film.2011.720p-GRP',
    folgen: [],
    fortschritt: 40,
    groesse: 1000,
    rest: 600,
    restzeit: '00:10:00',
    programm: 'qBittorrent',
    protokoll: 'torrent',
    programmstand: 'downloading',
    zustand: 'downloading',
    beobachtet: null,
    ...teil,
  }
}

function stand(teil: Partial<DownloadsStand> = {}): DownloadsStand {
  return {
    instanzen: [
      { kennung: 'radarr-standard', name: 'Radarr', media_type: 'movie', erreichbar: true, fehler: '' },
    ],
    haenger: [haenger()],
    laufend: [
      laufend(),
      laufend({
        titel: 'Dritter Film',
        release: 'Dritter.Film.2012.1080p-GRP',
        fortschritt: 100,
        rest: 0,
        restzeit: null,
        beobachtet: 'archiv',
      }),
    ],
    automatik_an: false,
    stand_am: '2026-09-12T20:20:00',
    ...teil,
  }
}

const AUTOMATIK: DownloadAutomatik = {
  an: false,
  regeln: [
    { grund: 'sample', aktion: null, erlaubt: ['entfernen_neu_suchen'] },
    { grund: 'kein_upgrade', aktion: null, erlaubt: ['entfernen'] },
  ],
  obergrenze: 2,
  fenster_stunden: 24,
  wiederholt_ab: 3,
  wiederholt_tage: 7,
}

function antworten({
  uebersicht = stand(),
  dateien = [] as DownloadKandidat[],
}: { uebersicht?: DownloadsStand; dateien?: DownloadKandidat[] } = {}) {
  holen.mockImplementation(((pfad: string) => {
    if (pfad === '/api/admin/downloads') return Promise.resolve(uebersicht)
    if (pfad === '/api/admin/downloads/automatik') return Promise.resolve(AUTOMATIK)
    if (pfad.startsWith('/api/admin/downloads/verlauf')) return Promise.resolve([])
    if (pfad.endsWith('/dateien')) return Promise.resolve(dateien)
    return Promise.reject(new Error(`unerwartet: ${pfad}`))
  }) as never)
}

beforeEach(() => {
  holen.mockReset()
  senden.mockReset()
  setzen.mockReset()
})

describe('eine hängende Karte', () => {
  it('sagt den Grund in Worten und zeigt den Wortlaut darunter', async () => {
    antworten()
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByText('Die Datei ist nur ein Sample')).toBeInTheDocument()
    expect(screen.getByText(/gehört gesperrt, dann kommt ein anderes/)).toBeInTheDocument()
    // Der Wortlaut der Instanz bleibt, wie er ist.
    expect(screen.getByText('Meldung von Radarr im Wortlaut')).toBeInTheDocument()
    expect(screen.getByText('Sample')).toBeInTheDocument()
    expect(screen.getByText('Angefragt von kim')).toBeInTheDocument()
  })

  it('zeigt vorn nur, was der Grund empfiehlt', async () => {
    antworten()
    const b = userEvent.setup()
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByRole('button', { name: 'Entfernen und neu suchen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Manuell importieren' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Nur entfernen' })).not.toBeInTheDocument()

    await b.click(screen.getByRole('button', { name: 'Weitere Möglichkeiten' }))
    expect(screen.getByRole('button', { name: 'Nur entfernen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Erneut versuchen' })).toBeInTheDocument()
  })

  it('fragt vor dem Entfernen und sperrt dann mit neuer Suche', async () => {
    antworten()
    senden.mockResolvedValue({ gesucht: true, befehl: '' })
    const b = userEvent.setup()
    rendernSchlicht(<AdminDownloadsPage />)

    await b.click(await screen.findByRole('button', { name: 'Entfernen und neu suchen' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Die geladenen Daten sind danach weg/)).toBeInTheDocument()
    // ⚠️ Erst die Rückfrage, dann der Aufruf - nie umgekehrt.
    expect(senden).not.toHaveBeenCalled()

    await b.click(within(dialog).getByRole('button', { name: 'Entfernen und neu suchen' }))
    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('/api/admin/downloads/7/entfernen', { neu_suchen: true }),
    )
    expect(
      await screen.findByText('Entfernt. Die Suche nach einem anderen Release läuft.'),
    ).toBeInTheDocument()
  })

  it('„Nur entfernen" sperrt nichts und sucht nicht', async () => {
    antworten()
    senden.mockResolvedValue({ gesucht: false, befehl: '' })
    const b = userEvent.setup()
    rendernSchlicht(<AdminDownloadsPage />)

    await b.click(await screen.findByRole('button', { name: 'Weitere Möglichkeiten' }))
    await b.click(screen.getByRole('button', { name: 'Nur entfernen' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Gesperrt wird nichts/)).toBeInTheDocument()
    await b.click(within(dialog).getByRole('button', { name: 'Nur entfernen' }))

    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('/api/admin/downloads/7/entfernen', { neu_suchen: false }),
    )
  })

  it('„Erneut versuchen" löscht nichts und geht an die Instanz', async () => {
    antworten()
    senden.mockResolvedValue({ gesucht: false, befehl: 'queued' })
    const b = userEvent.setup()
    rendernSchlicht(<AdminDownloadsPage />)

    await b.click(await screen.findByRole('button', { name: 'Weitere Möglichkeiten' }))
    await b.click(screen.getByRole('button', { name: 'Erneut versuchen' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Gelöscht wird nichts/)).toBeInTheDocument()
    await b.click(within(dialog).getByRole('button', { name: 'Erneut versuchen' }))

    await waitFor(() => expect(senden).toHaveBeenCalledWith('/api/admin/downloads/7/erneut'))
  })
})

describe('der manuelle Import', () => {
  const MIT_EINWAND: DownloadKandidat = {
    pfad: '/dl/sample.mkv',
    name: 'sample.mkv',
    groesse: 40 * 1024 ** 2,
    qualitaet: 'Bluray-1080p',
    sprachen: ['English'],
    zuordnung: 'Beispielfilm (2010)',
    folgen: [],
    zuordenbar: true,
    ablehnungen: [{ text: 'Sample', dauerhaft: true }],
  }
  const SAUBER: DownloadKandidat = {
    ...MIT_EINWAND,
    pfad: '/dl/film.mkv',
    name: 'film.mkv',
    groesse: 8 * 1024 ** 3,
    ablehnungen: [],
  }
  const OHNE_ZUORDNUNG: DownloadKandidat = {
    ...SAUBER,
    pfad: '/dl/fremd.mkv',
    name: 'fremd.mkv',
    zuordnung: '',
    zuordenbar: false,
  }

  async function oeffnen(dateien: DownloadKandidat[]) {
    antworten({ dateien })
    const b = userEvent.setup()
    rendernSchlicht(<AdminDownloadsPage />)
    await b.click(await screen.findByRole('button', { name: 'Manuell importieren' }))
    const fenster = await screen.findByRole('dialog', { name: 'Manuell importieren' })
    return { b, fenster }
  }

  it('wählt nur vor, was zugeordnet ist und keinen Einwand hat', async () => {
    const { fenster } = await oeffnen([MIT_EINWAND, SAUBER, OHNE_ZUORDNUNG])

    const kaestchen = await within(fenster).findAllByRole('checkbox')
    expect(kaestchen.map((k) => (k as HTMLInputElement).checked)).toEqual([false, true, false])
    expect(kaestchen[2]).toBeDisabled()
    expect(within(fenster).getByText(/keinem Titel zugeordnet/)).toBeInTheDocument()
    // Ein Sample erkennt man an der Größe - es darf nicht als „0 GiB" dastehen.
    expect(within(fenster).getByText(/40 MiB/)).toBeInTheDocument()
  })

  it('verlangt bei einem Einwand ein ausdrückliches Trotzdem', async () => {
    senden.mockResolvedValue({ gesucht: false, befehl: 'completed' })
    const { b, fenster } = await oeffnen([MIT_EINWAND, SAUBER])

    const [sample] = await within(fenster).findAllByRole('checkbox')
    await b.click(sample)
    const importieren = within(fenster).getByRole('button', { name: 'Importieren' })
    expect(importieren).toBeDisabled()

    await b.click(within(fenster).getByRole('checkbox', { name: /Trotzdem importieren/ }))
    expect(importieren).toBeEnabled()
    await b.click(importieren)

    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('/api/admin/downloads/7/importieren', {
        pfade: ['/dl/film.mkv', '/dl/sample.mkv'],
        trotzdem: true,
      }),
    )
    expect(await screen.findByText('Importiert.')).toBeInTheDocument()
  })

  it('ohne Einwand geht kein Trotzdem mit', async () => {
    senden.mockResolvedValue({ gesucht: false, befehl: 'started' })
    const { b, fenster } = await oeffnen([SAUBER])

    expect(within(fenster).queryByRole('checkbox', { name: /Trotzdem/ })).not.toBeInTheDocument()
    await b.click(within(fenster).getByRole('button', { name: 'Importieren' }))

    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith('/api/admin/downloads/7/importieren', {
        pfade: ['/dl/film.mkv'],
        trotzdem: false,
      }),
    )
    expect(
      await screen.findByText('Der Import läuft noch. Das Ergebnis zeigt die nächste Runde.'),
    ).toBeInTheDocument()
  })
})

describe('die Automatik', () => {
  it('bietet je Grund nur, was er erlaubt, und speichert erst auf Knopfdruck', async () => {
    antworten()
    setzen.mockResolvedValue({ ...AUTOMATIK, an: true })
    const b = userEvent.setup()
    rendernSchlicht(<AdminDownloadsPage />)

    const auswahl = await screen.findByRole('combobox', { name: 'Die Datei ist nur ein Sample' })
    expect([...(auswahl as HTMLSelectElement).options].map((o) => o.textContent)).toEqual([
      'Nichts tun',
      'Entfernen und neu suchen',
    ])
    const speichern = screen.getByRole('button', { name: 'Speichern' })
    expect(speichern).toBeDisabled()

    await b.click(screen.getByRole('button', { name: 'An' }))
    await b.selectOptions(auswahl, 'entfernen_neu_suchen')
    expect(setzen).not.toHaveBeenCalled()
    await b.click(speichern)

    await waitFor(() =>
      expect(setzen).toHaveBeenCalledWith('/api/admin/downloads/automatik', {
        an: true,
        regeln: { sample: 'entfernen_neu_suchen', kein_upgrade: null },
      }),
    )
    expect(await screen.findByText('Gespeichert.')).toBeInTheDocument()
  })
})

describe('die übrige Seite', () => {
  it('nennt Beobachtetes mit Grund und zeigt den Fortschritt', async () => {
    antworten()
    rendernSchlicht(<AdminDownloadsPage />)

    expect(
      await screen.findByText(
        'Wird beobachtet: Der Download ist ein Archiv und noch nicht entpackt',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Zweiter Film' })).toHaveAttribute(
      'aria-valuenow',
      '40',
    )
  })

  it('sagt, wenn nichts hängt, und nennt eine stumme Instanz', async () => {
    antworten({
      uebersicht: stand({
        haenger: [],
        instanzen: [
          { kennung: 'radarr-standard', name: 'Radarr', media_type: 'movie', erreichbar: true, fehler: '' },
          { kennung: 'sonarr-standard', name: 'Sonarr', media_type: 'tv', erreichbar: false, fehler: 'arr_unreachable' },
        ],
      }),
    })
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByText(/^Nichts hängt\./)).toBeInTheDocument()
    expect(screen.getByText(/Sonarr antwortet gerade nicht/)).toBeInTheDocument()
  })

  it('sagt bei leerem Läuft, dass oben noch etwas hängt', async () => {
    // ⚠️ Am Prüfstand hingen 16 Downloads, und darunter stand „Die Warteschlangen sind leer."
    antworten({ uebersicht: stand({ laufend: [] }) })
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByText('Sonst lädt gerade nichts.')).toBeInTheDocument()
  })

  it('nennt gescheiterte Downloads, ohne sie unter Läuft zu zeigen', async () => {
    // Rundgang-Befund 9: nexcrate lieferte 42 gescheiterte Downloads mit, und
    // sie standen unter „Läuft“ mit Fortschrittsbalken. Der Server lässt sie
    // dort weg und zählt sie.
    antworten({ uebersicht: stand({ laufend: [], haenger: [], gescheitert: 42 }) })
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByText('Gerade lädt nichts.')).toBeInTheDocument()
    expect(
      screen.getByText('42 gescheiterte Downloads laufen nicht mehr und stehen deshalb nicht hier.'),
    ).toBeInTheDocument()
  })

  it('nennt im NEX-Betrieb nexcrate statt Radarr und Sonarr', async () => {
    // Rundgang-Befund 8: Der Untertitel sagte auch im NEX-Betrieb „Was in
    // Radarr und Sonarr nicht weitergeht“.
    holen.mockImplementation(((pfad: string) => {
      if (pfad === '/api/config') return Promise.resolve({ beschaffung: 'nex' })
      if (pfad === '/api/admin/downloads') return Promise.resolve(stand({ instanzen: [] }))
      if (pfad === '/api/admin/downloads/automatik') return Promise.resolve(AUTOMATIK)
      if (pfad.startsWith('/api/admin/downloads/verlauf')) return Promise.resolve([])
      return Promise.reject(new Error(`unerwartet: ${pfad}`))
    }) as never)
    rendernSchlicht(<AdminDownloadsPage />)

    expect(
      await screen.findByText('Was in nexcrate nicht weitergeht, warum, und was dagegen hilft.'),
    ).toBeInTheDocument()
    expect(await screen.findByText('Es ist noch keine nexcrate verbunden.')).toBeInTheDocument()
    expect(screen.queryByText(/Radarr|Sonarr/)).toBeNull()
  })

  it('sagt ohne Hänger schlicht, dass nichts lädt', async () => {
    antworten({ uebersicht: stand({ laufend: [], haenger: [] }) })
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByText('Gerade lädt nichts.')).toBeInTheDocument()
  })

  it('holt die Texte in der eingestellten Sprache nach', async () => {
    // ⚠️ Die Texte stehen nicht im Grundpaket. Käme die zweite Sprache nicht
    // nach, stünden auf Englisch die Schlüssel auf dem Bildschirm.
    await i18n.changeLanguage('en')
    antworten()
    rendernSchlicht(<AdminDownloadsPage />)

    expect(await screen.findByText('Needs you')).toBeInTheDocument()
    expect(screen.getByText('The file is only a sample')).toBeInTheDocument()
  })
})
