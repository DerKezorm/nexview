/**
 * Die Rückverbindung — und die Vorschau, die sie ehrlich macht.
 *
 * ⚠️ **Der Fehler, den diese Seite verhindern soll, macht kein Geräusch.**
 * Radarr nennt dem Medienserver einen Pfad aus *seiner* Sicht. Stecken beide in
 * eigenen Containern, meint derselbe Film zwei verschiedene Pfade: Der Anruf
 * kommt an, wird bejaht — und nichts passiert. Jahrelang, ohne dass irgendwo
 * ein Fehler stünde.
 *
 * Geprüft wird deshalb, dass die Umschreibung **vor** dem Klick sichtbar ist,
 * und dass Nexview keine Verbindung anbietet, von der es weiß, dass sie ins
 * Leere ginge.
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
import type {
  MedienserverLuecke,
  MedienserverZugang,
  PfadZuordnung,
  VerbindungslageGesamt,
  VerbindungslageInstanz,
} from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { AdminMedienserverVerbindung } from './AdminMedienserverVerbindung'
import { anzeigename } from './medienservername'

const holen = vi.mocked(api.get)
const setzen = vi.mocked(api.put)
const senden = vi.mocked(api.post)

function zuordnung(teil: Partial<PfadZuordnung> = {}): PfadZuordnung {
  return { von: '', nach: '', hindernis: '', beispiel_arr: '', beispiel_server: '', ...teil }
}

function luecke(teil: Partial<MedienserverLuecke> = {}): MedienserverLuecke {
  return {
    provider: 'jellyfin',
    name: 'Jellyfin',
    url: 'http://nas:8096',
    selbst_moeglich: true,
    hindernis: '',
    zuordnung: zuordnung(),
    ...teil,
  }
}

function instanz(teil: Partial<VerbindungslageInstanz> = {}): VerbindungslageInstanz {
  return {
    kennung: 'radarr-fhd',
    name: 'Radarr FHD',
    erreichbar: true,
    fehlend: [luecke()],
    verbunden: [],
    ...teil,
  }
}

function zugang(teil: Partial<MedienserverZugang> = {}): MedienserverZugang {
  return {
    id: 1,
    provider: 'jellyfin',
    name: 'Jellyfin',
    url: 'http://nas:8096',
    braucht_schluessel: true,
    schluessel_da: false,
    ...teil,
  }
}

function lage(teil: Partial<VerbindungslageGesamt> = {}) {
  const daten: VerbindungslageGesamt = {
    server: [zugang()],
    instanzen: [instanz()],
    warnungen: [],
    ...teil,
  }
  holen.mockResolvedValue(daten)
}

beforeEach(() => {
  holen.mockReset()
  setzen.mockReset()
  senden.mockReset()
})

describe('die Pfad-Vorschau', () => {
  it('zeigt, was umgeschrieben wird — mit Beispiel', async () => {
    lage({
      instanzen: [
        instanz({
          fehlend: [
            luecke({
              zuordnung: zuordnung({
                von: '/movies',
                nach: '/data/Filme',
                beispiel_arr: '/movies/Dune (2021)',
                beispiel_server: '/data/Filme/Dune (2021)',
              }),
            }),
          ],
        }),
      ],
    })
    rendernSchlicht(<AdminMedienserverVerbindung />)

    expect(await screen.findByText('Pfade werden umgeschrieben:')).toBeInTheDocument()
    expect(screen.getByText('/movies')).toBeInTheDocument()
    expect(screen.getByText('/data/Filme')).toBeInTheDocument()
    expect(
      screen.getByText('(z. B. /movies/Dune (2021) → /data/Filme/Dune (2021))'),
    ).toBeInTheDocument()
  })

  it('sagt es, wenn nichts umzuschreiben ist', async () => {
    // ⚠️ Der häufigste Fall - und er muss vom Fehlerfall unterscheidbar sein.
    // „Keine Umschreibung" und „Zuordnung unklar" sehen sonst gleich aus.
    lage({
      instanzen: [
        instanz({ fehlend: [luecke({ zuordnung: zuordnung({ beispiel_arr: '/data/Filme' }) })] }),
      ],
    })
    rendernSchlicht(<AdminMedienserverVerbindung />)

    expect(
      await screen.findByText(/Beide Seiten sehen denselben Pfad \(\/data\/Filme\)/),
    ).toBeInTheDocument()
  })

  it('nennt das Hindernis beim Namen statt eines Rohschlüssels', async () => {
    lage({
      instanzen: [
        instanz({
          fehlend: [luecke({ zuordnung: zuordnung({ hindernis: 'kein_treffer' }) })],
        }),
      ],
    })
    rendernSchlicht(<AdminMedienserverVerbindung />)

    const text = await screen.findByText(/führt keine Bibliothek, die zu dieser Instanz passt/)
    expect(text).toBeInTheDocument()
    // Der Anbietername steht ausgeschrieben da, nicht „jellyfin".
    expect(text.textContent).toContain('Jellyfin')
  })

  it('fällt bei einem unbekannten Hindernis auf einen lesbaren Satz zurück', async () => {
    // ⚠️ Ein neuer Grund aus dem Hinterbau darf nicht als „pathIssue.xyz" in
    // der Oberfläche landen.
    lage({
      instanzen: [
        instanz({
          fehlend: [luecke({ zuordnung: zuordnung({ hindernis: 'etwas_ganz_neues' }) })],
        }),
      ],
    })
    rendernSchlicht(<AdminMedienserverVerbindung />)

    expect(
      await screen.findByText('Die Zuordnung lässt sich nicht ermitteln.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/pathIssue/)).not.toBeInTheDocument()
  })
})

describe('wann verbunden werden darf', () => {
  it('bietet das Verbinden an, wenn Zugang und Zuordnung stehen', async () => {
    lage()
    rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(
      await screen.findByRole('button', { name: 'Fehlende Verbindungen herstellen' }),
    ).toBeEnabled()
  })

  it('sperrt es, solange die Zuordnung nicht geklärt ist', async () => {
    // ⚠️ Genau das ist der stille Fehler: Die Verbindung ließe sich eintragen,
    // Radarr riefe an, der Medienserver bejahte - und suchte ins Leere.
    lage({
      instanzen: [
        instanz({
          fehlend: [luecke({ zuordnung: zuordnung({ hindernis: 'kein_treffer' }) })],
        }),
      ],
    })
    rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(
      await screen.findByRole('button', { name: 'Fehlende Verbindungen herstellen' }),
    ).toBeDisabled()
  })

  it('sperrt es, solange der Zugang fehlt', async () => {
    lage({ instanzen: [instanz({ fehlend: [luecke({ selbst_moeglich: false })] })] })
    rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(
      await screen.findByRole('button', { name: 'Fehlende Verbindungen herstellen' }),
    ).toBeDisabled()
  })

  it('meldet, was nicht geklappt hat, statt nur die Erfolge zu zählen', async () => {
    lage()
    senden.mockResolvedValue({ hergestellt: 2, gescheitert: ['Radarr UHD → Plex'] })
    const b = userEvent.setup()
    rendernSchlicht(<AdminMedienserverVerbindung />)
    await b.click(
      await screen.findByRole('button', { name: 'Fehlende Verbindungen herstellen' }),
    )

    expect(
      await screen.findByText(/2 hergestellt\. Nicht geklappt hat: Radarr UHD → Plex/),
    ).toBeInTheDocument()
  })
})

describe('bestehende Verbindungen, die aufgehört haben zu wirken', () => {
  it('steht ganz oben — mit richtiger Ein- und Mehrzahl', async () => {
    // ⚠️ i18next bildet die Mehrzahl über ``count``. Ein anders benannter Wert
    // ließ hier schon einmal die Rohkennung „mediaLink.brokenTitle" stehen.
    lage({ warnungen: [{ instanz: 'Radarr FHD', provider: 'plex', grund: 'unreachable' }] })
    const { unmount } = rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(
      await screen.findByText('Eine bestehende Verbindung antwortet nicht mehr'),
    ).toBeInTheDocument()
    unmount()

    lage({
      warnungen: [
        { instanz: 'Radarr FHD', provider: 'plex', grund: 'unreachable' },
        { instanz: 'Sonarr', provider: 'jellyfin', grund: 'kein_schluessel' },
      ],
    })
    rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(
      await screen.findByText('2 bestehende Verbindungen antworten nicht mehr'),
    ).toBeInTheDocument()
  })

  it('nennt zu jeder Warnung Instanz und Anbieter', async () => {
    lage({ warnungen: [{ instanz: 'Radarr FHD', provider: 'plex', grund: 'kein_schluessel' }] })
    rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(
      await screen.findByText('Radarr FHD → Plex: Der Schlüssel fehlt inzwischen.'),
    ).toBeInTheDocument()
  })

  it('erklärt, warum das sonst niemandem auffällt', async () => {
    lage({ warnungen: [{ instanz: 'Radarr FHD', provider: 'plex', grund: 'unreachable' }] })
    rendernSchlicht(<AdminMedienserverVerbindung />)
    expect(await screen.findByText(/melden das von sich aus nicht/)).toBeInTheDocument()
  })

  it('zeigt nichts, wenn alles in Ordnung ist', async () => {
    lage({ warnungen: [] })
    rendernSchlicht(<AdminMedienserverVerbindung />)
    await screen.findByText('Radarr FHD')
    expect(screen.queryByText(/antwortet nicht mehr/)).not.toBeInTheDocument()
  })
})

describe('der Schlüssel', () => {
  it('lässt sich ersetzen, wenn schon einer hinterlegt ist', async () => {
    // ⚠️ Genau das fehlte einmal: Ein hinterlegter Schlüssel zeigte nur noch
    // ein Abzeichen. Wer den falschen Wert eingefügt hatte, kam nicht mehr
    // heran.
    lage({ server: [zugang({ schluessel_da: true })] })
    const b = userEvent.setup()
    rendernSchlicht(<AdminMedienserverVerbindung />)

    expect(await screen.findByText('Schlüssel hinterlegt')).toBeInTheDocument()
    await b.click(screen.getByRole('button', { name: 'Ändern' }))

    const feld = screen.getByPlaceholderText('API-Schlüssel')
    // ⚠️ Leer, nicht vorbefüllt: Der Schlüssel wird nie zurückgeliefert.
    expect(feld).toHaveValue('')
    expect(feld).toHaveAttribute('type', 'password')
  })

  it('speichert erst, wenn wirklich etwas eingegeben wurde', async () => {
    lage()
    setzen.mockResolvedValue({})
    const b = userEvent.setup()
    rendernSchlicht(<AdminMedienserverVerbindung />)
    const speichern = await screen.findByRole('button', { name: 'Speichern' })

    expect(speichern).toBeDisabled()
    await b.type(screen.getByPlaceholderText('API-Schlüssel'), '   ')
    expect(speichern).toBeDisabled() // Leerzeichen sind keine Eingabe.

    await b.type(screen.getByPlaceholderText('API-Schlüssel'), 'abc123')
    await b.click(speichern)
    await waitFor(() =>
      expect(setzen).toHaveBeenCalledWith(
        '/api/settings/qualitaetsprofile/medienserver/schluessel',
        { server_id: 1, schluessel: '   abc123' },
      ),
    )
  })

  it('verlangt keinen Schlüssel, wo Nexview schon einen Zugang hat', async () => {
    lage({ server: [zugang({ provider: 'plex', name: 'Plex', braucht_schluessel: false })] })
    rendernSchlicht(<AdminMedienserverVerbindung />)

    expect(await screen.findByText('Zugang liegt vor')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('API-Schlüssel')).not.toBeInTheDocument()
  })
})

describe('der Anzeigename', () => {
  // ⚠️ Der Name eines Medienservers kommt aus dem Server selbst und ist oft
  // unbrauchbar: mal der Anbietername doppelt, mal eine Gerätekennung.
  it('lässt einen eigenen Namen stehen', () => {
    expect(anzeigename('jellyfin', 'Wohnzimmer')).toBe('Jellyfin (Wohnzimmer)')
  })

  it('wiederholt den Anbieternamen nicht', () => {
    expect(anzeigename('jellyfin', 'Jellyfin')).toBe('Jellyfin')
    expect(anzeigename('plex', 'plex')).toBe('Plex')
  })

  it('unterdrückt eine reine Gerätekennung', () => {
    expect(anzeigename('plex', 'a3f9c81e4b77')).toBe('Plex')
  })

  it('kommt ohne Namen aus', () => {
    expect(anzeigename('emby', '')).toBe('Emby')
  })
})
