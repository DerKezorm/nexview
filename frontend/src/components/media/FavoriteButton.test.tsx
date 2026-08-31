/**
 * Ein Herz, das nicht ankommt, sagt es selbst.
 *
 * ⚠️ **Der Fehler, den dieser Test verhindert, ist Stille.** Vorher passierte
 * bei einem fehlgeschlagenen Klick nichts Sichtbares: Das Herz sprang in den
 * alten Zustand zurück, und „nicht gemerkt" sah genauso aus wie
 * „danebengeklickt". Der Mensch klickt dann noch einmal. Und noch einmal.
 *
 * Geprüft wird beides: dass der Fehlschlag ankommt — und dass ein gelungener
 * Klick **nicht** warnt. Ein Knopf, der immer rot ist, wäre schlimmer als
 * einer, der schweigt.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { FavoriteButton } from './FavoriteButton'
import { api } from '../../api/client'
import { i18nStarten } from '../../i18n'

beforeAll(async () => {
  await i18nStarten('de')
})

afterEach(() => {
  vi.restoreAllMocks()
})

const TITEL = {
  media_type: 'movie' as const,
  tmdb_id: 603,
  title: 'The Matrix',
  poster_url: null,
}

function zeichnen() {
  // Kein Wiederholen: Sonst wartet der Test auf den zweiten Versuch.
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <FavoriteButton item={TITEL} markiert={false} />
    </QueryClientProvider>,
  )
}

describe('Herz', () => {
  it('sagt es, wenn der Klick nicht angekommen ist', async () => {
    vi.spyOn(api, 'post').mockRejectedValue(new Error('Server weg'))
    zeichnen()

    await userEvent.click(screen.getByRole('button'))

    await waitFor(() => {
      expect(screen.getByRole('button')).toHaveAccessibleName(
        /konnte nicht gespeichert werden/i,
      )
    })
  })

  it('warnt nicht, wenn es geklappt hat', async () => {
    vi.spyOn(api, 'post').mockResolvedValue(undefined as never)
    zeichnen()

    await userEvent.click(screen.getByRole('button'))

    // Kurz warten, damit ein fälschlich gesetzter Fehlerzustand auffiele.
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(screen.getByRole('button')).not.toHaveAccessibleName(
      /konnte nicht gespeichert werden/i,
    )
  })
})
