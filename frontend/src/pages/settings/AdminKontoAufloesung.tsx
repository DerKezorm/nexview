import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useWegKontext } from '../../hooks/useWegKontext'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { User } from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { Button, ErrorBanner, Spinner } from '../../components/ui'
import { formatSize } from '../../lib/format'
import { FassungKuerzel } from '../../components/media/FassungBadge'

type Posten = {
  id: number
  title: string
  fassung: string
  season: number | null
  media_type: string
  size_bytes: number
}

type Laufende = {
  request_id: number
  title: string
  fassung: string
  season: number | null
  dateien: number
  folgen: number
}

type Offene = {
  request_id: number
  title: string
  fassung: string
  season: number | null
}

/** Ein Zugang auf einem Medienserver, den das Löschen mitnehmen kann. */
type ServerKonto = {
  provider: string
  label: string
  konto: string
  name: string
  /** `konto` wird gelöscht, `freigabe` wird zurückgenommen. */
  art: 'konto' | 'freigabe'
  aus_einladung: boolean
  vorausgewaehlt: boolean
  /** Warum es nicht geht; `null` heißt: geht. */
  grund: string | null
}

type Vorschau = {
  posten: Posten[]
  laufende: Laufende[]
  /**
   * ⚠️ Bis 0.22 nur Titel - und ohne Rückfrage storniert. Jetzt mit Kennung,
   * damit der Administrator auch hier entscheidet statt nur zuzusehen.
   */
  offen: Offene[]
  serverkonten: ServerKonto[]
}

function serverSchluessel(konto: { provider: string; konto: string }): string {
  return `${konto.provider}:${konto.konto}`
}

/**
 * Konto löschen – mit Entscheidung über den hinterlassenen Bestand.
 *
 * Bisher passierte beides stillschweigend: Die Posten fielen per
 * Datenbankregel ans Haus, und laufende Bestellungen luden **herrenlos
 * weiter**. Jetzt entscheidet der Administrator mit der Liste vor Augen:
 *
 * - Je Zugang auf einem Medienserver: entfernen oder stehen lassen. Das
 *   passiert vor allem anderen; scheitert ein Server, bleibt alles stehen.
 * - Je Posten: Häkchen = ins Haus, kein Häkchen = löschen. „Alle markieren"
 *   für den häufigsten Fall (alles behalten).
 * - Je angefangener Staffel: behalten oder löschen – und beim Behalten, ob
 *   weitergeladen wird.
 * - Bestellungen ohne eine einzige Datei: stornieren oder weiterlaufen lassen
 *   und ans Haus geben. Bis 0.22 wurden sie ohne Rückfrage storniert - als
 *   einzige der drei Gruppen. Das widersprach dem eigenen Anspruch: Wo etwas
 *   entschieden werden kann, soll ein Mensch entscheiden.
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
  const weg = useWegKontext()

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
  /**
   * Häkchen = weiterlaufen lassen und ans Haus.
   *
   * ⚠️ **Hier ist die Vorgabe umgekehrt: nichts angehakt.** Bei den fertigen
   * Posten ist Behalten die sichere Wahl, weil eine Datei existiert, die man
   * verlieren könnte. Hier liegt nichts - dafür lädt jede behaltene Bestellung
   * weiter auf Kosten des Betreibers. Wer sie will, hakt sie an.
   */
  const [offenBehalten, setOffenBehalten] = useState<Set<number>>(new Set())
  /**
   * Häkchen = Zugang auf dem Server entfernen.
   *
   * ⚠️ **Vorausgewählt ist nur, was eine Nexview-Einladung angelegt hat**
   * (Entscheidung vom 13.09.2026). Ein selbst verknüpftes Konto hat die Person
   * schon länger, samt Verlauf; das hakt der Administrator bewusst an.
   */
  const [server, setServer] = useState<Set<string> | null>(null)

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
    setServer(
      new Set(
        vorschau.data.serverkonten.filter((k) => k.vorausgewaehlt).map(serverSchluessel),
      ),
    )
  }, [vorschau.data, haus])

  const loeschen = useMutation({
    mutationFn: () => {
      const daten = vorschau.data
      if (!daten || !haus || !staffeln || !server) {
        return Promise.reject(new Error('unvollständig'))
      }
      return api.delete<void>(`/api/users/${benutzer.id}`, {
        haus: [...haus],
        loeschen: daten.posten.map((p) => p.id).filter((id) => !haus.has(id)),
        staffeln: [...staffeln.entries()].map(([request_id, wahl]) => ({
          request_id,
          behalten: wahl.behalten,
          weiter: wahl.behalten ? wahl.weiter : false,
        })),
        offen_behalten: [...offenBehalten],
        serverkonten: daten.serverkonten
          .filter((k) => k.grund === null && server.has(serverSchluessel(k)))
          .map((k) => ({ provider: k.provider, konto: k.konto })),
      })
    },
    onSuccess: onGeloescht,
    onError: (fehler) => {
      // 409 heißt: Der Bestand oder die Serverkonten haben sich geändert -
      // neu laden, neu entscheiden.
      if (fehler instanceof ApiError && fehler.status === 409) {
        setHaus(null)
        setStaffeln(null)
        setServer(null)
        void vorschau.refetch()
      }
    },
  })

  const daten = vorschau.data
  const zuLoeschen = daten && haus ? daten.posten.filter((p) => !haus.has(p.id)) : []
  const loeschBytes = zuLoeschen.reduce((summe, p) => summe + p.size_bytes, 0)
  const alleMarkiert = daten && haus ? haus.size === daten.posten.length : false
  const serverGewaehlt =
    daten && server
      ? daten.serverkonten.filter((k) => k.grund === null && server.has(serverSchluessel(k)))
          .length
      : 0

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
      {vorschau.isLoading || !daten || !haus || !staffeln || !server ? (
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

          {daten.serverkonten.length > 0 && (
            <section>
              <h4 className="text-sm font-semibold">
                {t('adminUsers.dissolveServersTitle')}
              </h4>
              <p className="mt-1 text-sm text-mist-500">
                {t('adminUsers.dissolveServersHint')}
              </p>
              <ul className="mt-2 flex flex-col gap-1.5">
                {daten.serverkonten.map((konto) => {
                  const schluessel = serverSchluessel(konto)
                  const moeglich = konto.grund === null
                  const name = konto.name || konto.konto
                  return (
                    <li key={schluessel}>
                      <label
                        className={
                          'flex items-start gap-2.5 text-sm ' +
                          (moeglich ? 'cursor-pointer' : 'cursor-not-allowed opacity-60')
                        }
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                          checked={moeglich && server.has(schluessel)}
                          disabled={!moeglich}
                          onChange={(e) => {
                            const naechste = new Set(server)
                            if (e.target.checked) naechste.add(schluessel)
                            else naechste.delete(schluessel)
                            setServer(naechste)
                          }}
                        />
                        <span className="flex min-w-0 flex-col">
                          <span className="text-mist-100 [overflow-wrap:anywhere]">
                            {konto.art === 'konto'
                              ? t('adminUsers.dissolveServerAccount', {
                                  server: konto.label,
                                  name,
                                })
                              : t('adminUsers.dissolveServerShare', {
                                  server: konto.label,
                                  name,
                                })}
                          </span>
                          <span className="text-xs text-mist-500">
                            {konto.grund
                              ? t(`adminUsers.dissolveServerReason.${konto.grund}`)
                              : konto.aus_einladung
                                ? t('adminUsers.dissolveServerFromInvite')
                                : t('adminUsers.dissolveServerLinked')}
                          </span>
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
              {serverGewaehlt > 0 && (
                <p className="mt-2 rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
                  {t('adminUsers.dissolveServersWarning')}
                </p>
              )}
            </section>
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
                {t('adminUsers.dissolveItemsHint', weg)}
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
                        <FassungKuerzel kennung={posten.fassung} />
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

          {daten.offen.length > 0 && (
            <section>
              <h4 className="text-sm font-semibold">
                {t('adminUsers.dissolveOpenTitle')}
              </h4>
              <p className="mt-1 text-sm text-mist-500">
                {t('adminUsers.dissolveOpenIntro', weg)}
              </p>
              <ul className="mt-2 flex flex-col gap-1.5">
                {daten.offen.map((zeile) => (
                  <li key={zeile.request_id}>
                    <label className="flex items-start gap-2.5 text-sm">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
                        checked={offenBehalten.has(zeile.request_id)}
                        onChange={(e) => {
                          const naechste = new Set(offenBehalten)
                          if (e.target.checked) naechste.add(zeile.request_id)
                          else naechste.delete(zeile.request_id)
                          setOffenBehalten(naechste)
                        }}
                      />
                      <span>
                        <span className="text-mist-100">{zeile.title}</span>
                        {zeile.season !== null && (
                          <span className="ml-1.5 text-mist-600">
                            {t('adminUsers.dissolveSeason', { number: zeile.season })}
                          </span>
                        )}
                        <span className="ml-1.5 text-mist-600">
                          {offenBehalten.has(zeile.request_id)
                            ? t('adminUsers.dissolveOpenKeep')
                            : t('adminUsers.dissolveOpenCancel')}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {daten.posten.length === 0 &&
            daten.laufende.length === 0 &&
            daten.offen.length === 0 && (
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
              {t('adminUsers.dissolveDeleteWarning', weg)}
            </p>
          )}
        </div>
      )}
    </Fenster>
  )
}
