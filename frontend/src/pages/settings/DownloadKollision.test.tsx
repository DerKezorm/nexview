/**
 * Die Warnung vor der geteilten Download-Kategorie.
 *
 * ⚠️ **Was hier auf dem Spiel steht, ist die Erkennbarkeit.** Der Fehler
 * selbst macht kein Geräusch: Anfragen hängen, Dateien landen falsch, und
 * nirgends steht etwas. Der Betreiber sucht beim Netz. Diese Warnung ist die
 * einzige Stelle, an der der wahre Grund je auftaucht — steht sie nicht da,
 * oder nennt sie die Symptome nicht, hat sie ihren Zweck verfehlt.
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
import type { DownloadKollision as Treffer } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { DownloadKollision } from './DownloadKollision'

const holen = vi.mocked(api.get)
const senden = vi.mocked(api.post)

function treffer(teil: Partial<Treffer> = {}): Treffer {
  return {
    schluessel: 'Sabnzbd|10.10.10.109:8080||movies|radarr-standard+radarr-uhd',
    programm: 'Sabnzbd auf 10.10.10.109:8080',
    kategorie: 'movies',
    ohne_kategorie: false,
    instanzen: ['Radarr FHD', 'Radarr 4K'],
    kennungen: ['radarr-standard', 'radarr-uhd'],
    ...teil,
  }
}

beforeEach(() => {
  holen.mockReset()
  senden.mockReset()
})

it('zeigt gar nichts, wenn alles getrennt ist', async () => {
  // ⚠️ Der Normalfall. Eine Warnfläche, die auch bei "alles gut" Platz
  // einnimmt, lernt der Betreiber zu übersehen.
  holen.mockResolvedValue({ kollisionen: [] })
  const { container } = rendernSchlicht(<DownloadKollision />)
  await waitFor(() => expect(holen).toHaveBeenCalled())
  expect(container).toBeEmptyDOMElement()
})

describe('wenn zwei Instanzen dieselbe Kategorie benutzen', () => {
  it('nennt beide Instanzen, die Kategorie und das Programm', async () => {
    // ⚠️ Alle drei Angaben zusammen — mit weniger kann niemand handeln.
    holen.mockResolvedValue({ kollisionen: [treffer()] })
    rendernSchlicht(<DownloadKollision />)

    const kopf = await screen.findByText(/Radarr FHD · Radarr 4K/)
    expect(kopf.textContent).toContain('movies')
    expect(kopf.textContent).toContain('Sabnzbd auf 10.10.10.109:8080')
  })

  it('beschreibt die Symptome, nicht nur die Ursache', async () => {
    // ⚠️ Das ist der eigentliche Wert: Wer die Symptome liest, erkennt seine
    // eigene erfolglose Suche wieder — und hört auf, beim Netz zu suchen.
    holen.mockResolvedValue({ kollisionen: [treffer()] })
    rendernSchlicht(<DownloadKollision />)

    expect(await screen.findByText(/bleiben auf .wird geladen. stehen/)).toBeInTheDocument()
    expect(screen.getByText(/nirgends ein Fehler/)).toBeInTheDocument()
  })

  it('sagt, dass die Kategorie drüben angelegt werden muss', async () => {
    // Radarr kann keine anlegen — ohne diesen Satz sucht man den Knopf dort.
    holen.mockResolvedValue({ kollisionen: [treffer()] })
    rendernSchlicht(<DownloadKollision />)
    expect(
      await screen.findByText(/Radarr und Sonarr können das nicht/),
    ).toBeInTheDocument()
  })

  it('nennt auch eine dritte Instanz', async () => {
    holen.mockResolvedValue({
      kollisionen: [
        treffer({
          instanzen: ['Radarr FHD', 'Radarr 4K', 'Sonarr FHD'],
          kennungen: ['radarr-standard', 'radarr-uhd', 'sonarr-standard'],
        }),
      ],
    })
    rendernSchlicht(<DownloadKollision />)
    expect(
      await screen.findByText(/Radarr FHD · Radarr 4K · Sonarr FHD/),
    ).toBeInTheDocument()
  })
})

describe('wenn gar keine Kategorie gesetzt ist', () => {
  it('sagt einen anderen Satz — der Fall ist schwerer', async () => {
    // ⚠️ Ohne Kategorie greift eine Instanz nach *allem* im Download-Programm,
    // nicht nur nach dem einer zweiten. Derselbe Text wäre dort falsch.
    holen.mockResolvedValue({
      kollisionen: [treffer({ kategorie: '', ohne_kategorie: true })],
    })
    rendernSchlicht(<DownloadKollision />)

    expect(await screen.findByText(/ohne eigene Kategorie/)).toBeInTheDocument()
    expect(screen.getByText(/greift eine Instanz nach allem/)).toBeInTheDocument()
    // Und die leere Kategorie erscheint nirgends als leeres Anführungszeichenpaar.
    expect(screen.queryByText(/„“/)).not.toBeInTheDocument()
  })
})

describe('wegklicken', () => {
  it('schickt die Kennung der Beteiligten mit', async () => {
    // ⚠️ Der Schlüssel trägt die beteiligten Instanzen. Kommt später eine
    // dritte dazu, ist das ein neuer Schlüssel — und wird wieder gewarnt.
    holen.mockResolvedValue({ kollisionen: [treffer()] })
    senden.mockResolvedValue(undefined)
    const b = userEvent.setup()
    rendernSchlicht(<DownloadKollision />)
    await b.click(await screen.findByRole('button', { name: 'Ist so gewollt' }))

    await waitFor(() =>
      expect(senden).toHaveBeenCalledWith(
        '/api/settings/instanzen/downloadkollision/ignorieren',
        { schluessel: 'Sabnzbd|10.10.10.109:8080||movies|radarr-standard+radarr-uhd' },
      ),
    )
  })

  it('sagt vorher, dass eine weitere Instanz wieder warnt', async () => {
    holen.mockResolvedValue({ kollisionen: [treffer()] })
    rendernSchlicht(<DownloadKollision />)
    expect(
      await screen.findByText(/Kommt später eine weitere Instanz.*wieder gewarnt/),
    ).toBeInTheDocument()
  })
})
