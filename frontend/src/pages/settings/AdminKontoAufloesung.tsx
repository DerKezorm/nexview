import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { Button, ErrorBanner, Spinner } from '../../components/ui'
import { formatSize } from '../../lib/format'

type Posten = {
  id: number
  title: string
  tier: string
  season: number | null
  media_type: string
  size_bytes: number
}

type Laufende = {
  request_id: number
  title: string
  tier: string
  season: number | null
  dateien: number
  folgen: number
}

type Vorschau = {
  posten: Posten[]
  laufende: Laufende[]
  storniert: string[]
}

/**
 * Konto löschen – mit Entscheidung über den hinterlassenen Bestand.
 *
 * Bisher passierte beides stillschweigend: Die Posten fielen per
 * Datenbankregel ans Haus, und laufende Bestellungen luden **herrenlos
 * weiter**. Jetzt entscheidet der Administrator mit der Liste vor Augen:
 *
 * - Je Posten: Häkchen = ins Haus, kein Häkchen = löschen. „Alle markieren"
 *   für den häufigsten Fall (alles behalten).
 * - Je angefangener Staffel: behalten oder löschen – und beim Behalten, ob
 *   weitergeladen wird.
 * - Bestellungen ohne eine einzige Datei werden storniert; sie stehen zur
 *   Information dabei.
 */
export function AdminKontoAufloesung({
  benutzer,
  onSchliessen,
  onGeloescht,
}: {
  benutzer: User
  onSchliessen: () => void
  onGeloescht: () => void
}) {
  const { t, i18n } = useTranslation()

  const vorschau = useQuery({
    queryKey: ['aufloesung', benutzer.id],
    queryFn: () => api.get<Vorschau>(`/api/users/${benutzer.id}/aufloesung`),
    staleTime: 0,
  })

  // Häkchen = ins Haus. **Alles vorausgewählt** – Behalten ist die sichere
  // Vorgabe; Löschen ist der Schritt ohne Rückweg und will einzeln gewählt
  // sein.
  const [haus, setHaus] = useState<Set<number> | null>(null)
  const [staffeln, setStaffeln] = useState<Map<
    number,
    { behalten: boolean; weiter: boolean }
  > | null>(null)

  useEffect(() => {
    if (!vorschau.data || haus !== null) return
    setHaus(new Set(vorschau.data.posten.map((p) => p.id)))
    setStaffeln(
      new Map(
        vorschau.data.laufende.map((z) => [
          z.request_id,
          { behalten: true, weiter: true },
        ]),
      ),
    )
  }, [vorschau.data, haus])

  const loeschen = useMutation({
    mutationFn: () => {
      const daten = vorschau.data
      if (!daten || !haus || !staffeln) return Promise.reject(new Error('unvollständig'))
      return api.delete<void>(`/api/users/${benutzer.id}`, {
        haus: [...haus],
        loeschen: daten.posten.map((p) => p.id).filter((id) => !haus.has(id)),
        staffeln: [...staffeln.entries()].map(([request_id, wahl]) => ({
          request_id,
          behalten: wahl.behalten,
          weiter: wahl.behalten ? wahl.weiter : false,
        })),
      })
    },
    onSuccess: onGeloescht,
    onError: (fehler) => {
      // 409 heißt: Der Bestand hat sich geändert - neu laden, neu entscheiden.
      if (fehler instanceof ApiError && fehler.status === 409) {
        setHaus(null)
        setStaffeln(null)
        void vorschau.refetch()
      }
    },
  })

  const daten = vorschau.data
  const zuLoeschen = daten && haus ? daten.posten.filter((p) => !haus.has(p.id)) : []
  const loeschBytes = zuLoeschen.reduce((summe, p) => summe + p.size_bytes, 0)
  const alleMarkiert = daten && haus ? haus.size === daten.posten.length : false

  return (
    <Fenster
      offen
      titel={t('adminUsers.dissolveTitle', {
        name: benutzer.display_name || benutzer.username,
      })}
      onSchliessen={onSchliessen}
      fuss={
        <>
          <Button variant="ghost" onClick={onSchliessen} disabled={loeschen.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => loeschen.mutate()}
            loading={loeschen.isPending}
            disabled={!daten || !haus}
            className="bg-bad-500 hover:bg-bad-500/90"
          >
            {t('adminUsers.dissolveConfirm')}
          </Button>
        </>
      }
    >
      {vorschau.isLoading || !daten || !haus || !staffeln ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          {loeschen.error && (
            <ErrorBanner
              message={
                loeschen.error instanceof ApiError
                  ? loeschen.error.message
                  : t('errors.generic')
              }
            />
          )}

          {daten.posten.length > 0 && (
            <section>
              <div className="flex items-baseline justify-between gap-3">
                <h4 className="text-sm font-semibold">
                  {t('adminUsers.dissolveItems')}
                </h4>
                <button
                  type="button"
                  onClick={() =>
                    setHaus(
                      alleMarkiert
                        ? new Set()
                        : new Set(daten.posten.map((p) => p.id)),
                    )
                  }
                  className="text-sm text-mist-400 underline-offset-2 hover:text-accent-500 hover:underline"
                >
                  {t(alleMarkiert ? 'adminUsers.markNone' : 'adminUsers.markAll')}
                </button>
              </div>
              <p className="mt-1 text-sm text-mist-500">
                {t('adminUsers.dissolveItemsHint')}
              </p>
              <ul className="mt-2 flex flex-col">
                {daten.posten.map((posten) => (
                  <li key={posten.id}>
                    <label className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 hover:bg-ink-800">
                      <input
                        type="checkbox"
                        checked={haus.has(posten.id)}
                        onChange={() =>
                          setHaus((alt) => {
                            const neu = new Set(alt)
                            if (neu.has(posten.id)) neu.delete(posten.id)
                            else neu.add(posten.id)
                            return neu
                          })
                        }
                        className="h-4 w-4 shrink-0 accent-accent-500"
                      />
                      <span className="min-w-0 flex-1 truncate text-sm">
                        {posten.title}
                        {posten.season !== null && (
                          <span className="ml-1.5 text-mist-500">
                            {t('storage.season', { number: posten.season })}
                          </span>
                        )}
                        {posten.tier === 'uhd' && (
                          <span className="ml-1.5 text-accent-500">4K</span>
                        )}
                      </span>
                      <span className="shrink-0 text-sm tabular-nums text-mist-500">
                        {formatSize(posten.size_bytes, i18n.language)}
                      </span>
                      <span
                        className={
                          'w-20 shrink-0 text-right text-xs font-medium ' +
                          (haus.has(posten.id) ? 'text-mist-500' : 'text-bad-500')
                        }
                      >
                        {t(
                          haus.has(posten.id)
                            ? 'adminUsers.toHouse'
                            : 'adminUsers.toDelete',
                        )}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {daten.laufende.length > 0 && (
            <section>
              <h4 className="text-sm font-semibold">
                {t('adminUsers.dissolveRunning')}
              </h4>
              <p className="mt-1 text-sm text-mist-500">
                {t('adminUsers.dissolveRunningHint')}
              </p>
              <ul className="mt-2 flex flex-col gap-1">
                {daten.laufende.map((zeile) => {
                  const wahl = staffeln.get(zeile.request_id) ?? {
                    behalten: true,
                    weiter: true,
                  }
                  const setzen = (neu: { behalten: boolean; weiter: boolean }) =>
                    setStaffeln((alt) => new Map(alt).set(zeile.request_id, neu))
                  return (
                    <li
                      key={zeile.request_id}
                      className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg px-2 py-1.5"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm">
                        {zeile.title}
                        {zeile.season !== null ? (
                          <span className="ml-1.5 text-mist-500">
                            {t('storage.season', { number: zeile.season })}
                          </span>
                        ) : (
                          <span className="ml-1.5 text-mist-500">
                            {t('adminUsers.wholeSeries')}
                          </span>
                        )}
                        <span className="ml-1.5 text-xs text-mist-600">
                          {t('adminUsers.episodesLoaded', {
                            count: zeile.dateien,
                          })}
                        </span>
                      </span>
                      <label className="flex cursor-pointer items-center gap-1.5 text-sm">
                        <input
                          type="checkbox"
                          checked={wahl.behalten}
                          onChange={(e) =>
                            setzen({ ...wahl, behalten: e.target.checked })
                          }
                          className="h-4 w-4 accent-accent-500"
                        />
                        {t('adminUsers.keepFiles')}
                      </label>
                      <label
                        className={
                          'flex items-center gap-1.5 text-sm ' +
                          (wahl.behalten
                            ? 'cursor-pointer'
                            : 'cursor-not-allowed opacity-40')
                        }
                      >
                        <input
                          type="checkbox"
                          checked={wahl.behalten && wahl.weiter}
                          disabled={!wahl.behalten}
                          onChange={(e) =>
                            setzen({ ...wahl, weiter: e.target.checked })
                          }
                          className="h-4 w-4 accent-accent-500"
                        />
                        {t('adminUsers.keepFollowing')}
                      </label>
                    </li>
                  )
                })}
              </ul>
            </section>
          )}

          {daten.storniert.length > 0 && (
            <section>
              <h4 className="text-sm font-semibold">
                {t('adminUsers.dissolveCancelled')}
              </h4>
              <p className="mt-1 text-sm text-mist-500">
                {daten.storniert.join(' · ')}
              </p>
            </section>
          )}

          {daten.posten.length === 0 &&
            daten.laufende.length === 0 &&
            daten.storniert.length === 0 && (
              <p className="text-sm text-mist-500">
                {t('adminUsers.dissolveNothing')}
              </p>
            )}

          {/* Die Zusammenfassung mit Zahlen - eine Zahl wird gelesen, eine
              allgemeine Warnung wird weggeklickt. */}
          <p className="border-t border-ink-700 pt-3 text-sm text-mist-300">
            {t('adminUsers.dissolveSummary', {
              haus: haus.size,
              del: zuLoeschen.length,
              size: formatSize(loeschBytes, i18n.language),
            })}
          </p>
          {zuLoeschen.length > 0 && (
            <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
              {t('adminUsers.dissolveDeleteWarning')}
            </p>
          )}
        </div>
      )}
    </Fenster>
  )
}
