import { useQuery } from '@tanstack/react-query'
import { Fragment, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useWegKontext } from '../../hooks/useWegKontext'
import { useSearchParams } from 'react-router-dom'

import { api } from '../../api/client'
import type { ServerVergleichStand, VergleichAnsicht, VergleichZelle } from '../../api/types'
import { MediaServerLogo } from '../../components/MediaServerLogo'
import { Pagination } from '../../components/Pagination'
import { Umschalter } from '../../components/Umschalter'
import { AUSWAHL, Card, ErrorBanner, Spinner } from '../../components/ui'
import { providerName } from '../../lib/mediaserver'
import { NeuZuordnen } from './NeuZuordnen'

/**
 * Welcher Titel liegt auf welchem Server?
 *
 * Anlass war Issue #10: „458 Titel fehlen auf mindestens einem Server" sagte
 * nicht, welche. Hier stehen sie, eine Zeile je Titel, eine Spalte je Server.
 *
 * ⚠️ **Die Ansicht steht in der Adresse** (`?vergleich=jahr`). Die Befunde
 * „Ansehen" führen genau hierher und stellen die passende Ansicht ein; ohne
 * das landete man wieder vor der ganzen Bibliothek und müsste selbst suchen.
 * Die Wörter sind eine Zusage an `befunde.vergleich_ziel` im Backend.
 *
 * ⚠️ **Jede Ansicht bringt ihre Anleitung mit.** Eine Liste fehlender Titel
 * allein sagt nicht, was man tun soll. Die Schritte stehen über der Tabelle,
 * nicht in einem Hilfe-Knopf, den niemand öffnet.
 */

const ANSICHTEN: VergleichAnsicht[] = [
  'unterschiede',
  'andere_nummer',
  'jahr',
  'ohne_kennung',
  'nur_arr',
  'alle',
]

/** Nur Ansichten mit Nummern-Vergleich brauchen mehr als einen Server. */
const NUR_MIT_MEHREREN: VergleichAnsicht[] = ['unterschiede', 'andere_nummer', 'jahr']

function istAnsicht(wert: string | null): wert is VergleichAnsicht {
  return wert !== null && (ANSICHTEN as string[]).includes(wert)
}

export function ServerVergleich() {
  const { t } = useTranslation()
  const weg = useWegKontext()
  const [suchparameter] = useSearchParams()
  const ausAdresse = suchparameter.get('vergleich')
  const anker = useRef<HTMLDivElement>(null)

  const [ansicht, setAnsicht] = useState<VergleichAnsicht>(
    istAnsicht(ausAdresse) ? ausAdresse : 'unterschiede',
  )
  const [art, setArt] = useState<'alle' | 'movie' | 'tv'>('alle')
  const [fehltAuf, setFehltAuf] = useState('')
  const [suche, setSuche] = useState('')
  const [gesucht, setGesucht] = useState('')
  const [seite, setSeite] = useState(1)
  const [offen, setOffen] = useState<string | null>(null)
  /** Für welche Zeile steht „Neu zuordnen" offen? */
  const [zuordnen, setZuordnen] = useState<string | null>(null)

  // Ein „Ansehen" auf derselben Seite ändert nur die Adresse; der Zustand
  // muss nachziehen. Während des Renderns statt in einem Effekt, damit nicht
  // erst die alte Ansicht lädt.
  const [letzteAdresse, setLetzteAdresse] = useState(ausAdresse)
  if (ausAdresse !== letzteAdresse) {
    setLetzteAdresse(ausAdresse)
    if (istAnsicht(ausAdresse)) {
      setAnsicht(ausAdresse)
      setFehltAuf('')
      setSeite(1)
    }
  }

  useEffect(() => {
    if (istAnsicht(ausAdresse)) anker.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [ausAdresse])

  useEffect(() => {
    const marke = setTimeout(() => setGesucht(suche.trim()), 300)
    return () => clearTimeout(marke)
  }, [suche])

  const abfrage = useQuery({
    queryKey: ['server-vergleich', ansicht, art, fehltAuf, gesucht, seite],
    queryFn: () => {
      const p = new URLSearchParams({ ansicht, art, seite: String(seite) })
      if (fehltAuf) p.set('fehlt_auf', fehltAuf)
      if (gesucht) p.set('suche', gesucht)
      return api.get<ServerVergleichStand>(`/api/admin/analyse/server-vergleich?${p}`)
    },
    placeholderData: (vorher) => vorher,
  })

  // Jeder Filterwechsel führt zurück auf Seite 1, sonst landet man auf einer leeren.
  const filtern =
    <T,>(setzen: (wert: T) => void) =>
    (wert: T) => {
      setzen(wert)
      setSeite(1)
      setOffen(null)
    }

  if (abfrage.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }
  if (abfrage.isError || !abfrage.data) {
    return <ErrorBanner message={t('analyse.matrix.failed')} />
  }

  const stand = abfrage.data
  if (!stand.moeglich) return null

  const server = stand.server.map((s) => s.anbieter)
  const mehrere = server.length > 1
  const sichtbareAnsichten = ANSICHTEN.filter(
    (a) =>
      (mehrere || !NUR_MIT_MEHREREN.includes(a)) &&
      // Leere Sonderansichten weglassen - außer der gerade gewählten, sonst
      // verschwindet der Knopf unter dem Finger.
      (a === 'alle' || a === 'unterschiede' || a === ansicht || (stand.anzahl[a] ?? 0) > 0),
  )
  const hilfe =
    ansicht === 'alle'
      ? []
      : (t(`analyse.matrix.help.${ansicht}`, { returnObjects: true, ...weg }) as string[])
  const gefiltert = art !== 'alle' || fehltAuf !== '' || gesucht !== ''

  return (
    <div ref={anker} className="flex scroll-mt-24 flex-col gap-4">
      <p className="text-sm text-mist-500">{t('analyse.matrix.intro')}</p>

      {/* Erst die reine Anzahl: Sind die Bestände wirklich verschieden groß,
          oder nur anders zugeordnet? Ein Klick filtert auf „fehlt hier". */}
      <div
        className={
          'grid grid-cols-1 gap-3 ' + (server.length > 2 ? 'sm:grid-cols-3' : 'sm:grid-cols-2')
        }
      >
        {stand.server.map(({ anbieter, filme, serien, fehlen }) => {
          const aktiv = fehltAuf === anbieter
          return (
            <button
              key={anbieter}
              type="button"
              disabled={!mehrere}
              aria-pressed={aktiv}
              onClick={() => {
                if (ansicht !== 'unterschiede' && ansicht !== 'alle') setAnsicht('unterschiede')
                filtern(setFehltAuf)(aktiv ? '' : anbieter)
              }}
              className={
                'flex flex-col gap-1 rounded-2xl border px-4 py-3 text-left transition-colors disabled:cursor-default ' +
                (aktiv
                  ? 'border-accent-500 bg-accent-500/10'
                  : 'border-ink-700 bg-ink-850/60 enabled:hover:border-ink-600')
              }
            >
              <span className="flex items-center gap-2 text-sm font-semibold text-mist-100">
                <MediaServerLogo provider={anbieter} className="h-4 w-4 text-mist-300" />
                {providerName(anbieter)}
              </span>
              <span className="text-sm tabular-nums text-mist-400">
                {t('analyse.matrix.movies', { count: filme })} ·{' '}
                {t('analyse.matrix.series', { count: serien })}
              </span>
              {mehrere && (
                <span
                  className={
                    'text-xs tabular-nums ' + (fehlen > 0 ? 'text-bad-500' : 'text-mist-600')
                  }
                >
                  {fehlen > 0
                    ? t('analyse.matrix.missingHere', { count: fehlen })
                    : t('analyse.matrix.missingNone')}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <Umschalter
        wert={ansicht}
        wahl={sichtbareAnsichten}
        onChange={filtern(setAnsicht)}
        label={(a) =>
          a === 'alle'
            ? t('analyse.matrix.view.alle')
            : `${t(`analyse.matrix.view.${a}`, weg)} · ${stand.anzahl[a] ?? 0}`
        }
      />

      {hilfe.length > 0 && (
        <Card className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">{t('analyse.matrix.helpTitle')}</h3>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-mist-500">
            {hilfe.map((schritt) => (
              <li key={schritt}>{schritt}</li>
            ))}
          </ol>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={suche}
          onChange={(e) => {
            setSuche(e.target.value)
            setSeite(1)
          }}
          placeholder={t('analyse.matrix.search')}
          aria-label={t('analyse.matrix.search')}
          className="min-w-48 flex-1 rounded-full border border-ink-700 bg-ink-900 px-4 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-600 focus:outline-none"
        />
        <Umschalter
          wert={art}
          wahl={['alle', 'movie', 'tv'] as const}
          onChange={filtern(setArt)}
          label={(eintrag) => t(`analyse.matrix.kind.${eintrag}`)}
        />
        {mehrere && ansicht !== 'nur_arr' && (
          <label className="flex items-center gap-2 text-sm text-mist-500">
            {t('analyse.matrix.missingFilter')}
            <select
              value={fehltAuf}
              onChange={(e) => filtern(setFehltAuf)(e.target.value)}
              className={AUSWAHL}
            >
              <option value="">{t('analyse.matrix.anyServer')}</option>
              {server.map((anbieter) => (
                <option key={anbieter} value={anbieter}>
                  {providerName(anbieter)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <p className="-mt-1 text-sm text-mist-400">
        {t('analyse.matrix.summary', { count: stand.gesamt })}
      </p>

      {stand.zeilen.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-5 py-10 text-center text-sm text-mist-500">
          {gefiltert
            ? t('analyse.matrix.emptyFilter')
            : ansicht === 'unterschiede'
              ? t('analyse.matrix.empty')
              : t('analyse.matrix.emptyView')}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-ink-700">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-ink-700 text-left text-xs tracking-wide text-mist-500 uppercase">
                <th className="px-3 py-2.5 font-medium">{t('analyse.matrix.colTitle')}</th>
                {server.map((anbieter) => (
                  <th key={anbieter} className="px-3 py-2.5 text-center font-medium">
                    <span className="inline-flex items-center gap-1.5">
                      <MediaServerLogo provider={anbieter} className="h-3.5 w-3.5" />
                      {providerName(anbieter)}
                    </span>
                  </th>
                ))}
                <th className="px-3 py-2.5 font-medium">{t('analyse.matrix.colMatch')}</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {stand.zeilen.map((zeile) => {
                const aufgeklappt = offen === zeile.kennung
                const klappbar = zeile.zuordnung !== 'arr'
                // Neu zuordnen lässt sich nur, was ein Server unter einer
                // anderen Nummer führt - „fehlt" hat nichts, das man umstellen
                // könnte.
                const korrigierbar = server.some((anbieter) => {
                  const zelle = zeile.zellen[anbieter]
                  return (
                    !!zelle?.schluessel &&
                    (zelle.zustand === 'andere_nummer' || zelle.zustand === 'anders_erkannt')
                  )
                })
                return (
                  <Fragment key={zeile.kennung}>
                    <tr
                      onClick={
                        klappbar ? () => setOffen(aufgeklappt ? null : zeile.kennung) : undefined
                      }
                      aria-expanded={klappbar ? aufgeklappt : undefined}
                      className={
                        'border-b border-ink-800 last:border-0 ' +
                        (klappbar ? 'cursor-pointer hover:bg-ink-850/60' : '')
                      }
                    >
                      <td className="px-3 py-2.5">
                        <span className="text-mist-100">{zeile.titel}</span>{' '}
                        {zeile.jahr !== null && (
                          <span className="text-mist-600 tabular-nums">({zeile.jahr})</span>
                        )}
                        <span className="ml-2 text-xs text-mist-600">
                          {t(`analyse.matrix.kindOne.${zeile.art}`)}
                        </span>
                        {zeile.jahr_uneinig && (
                          <span className="ml-2 rounded-full bg-warn-500/10 px-2 py-0.5 text-xs text-warn-500">
                            {t('analyse.matrix.yearBadge')}
                          </span>
                        )}
                        {zeile.ohne_kennung && (
                          <span className="ml-2 rounded-full bg-warn-500/10 px-2 py-0.5 text-xs text-warn-500">
                            {t('analyse.matrix.noIdBadge')}
                          </span>
                        )}
                        {/* ⚠️ Gleich in der Zeile, nicht erst aufgeklappt: Das ist
                            meist schon die ganze Antwort auf „warum fehlt der?". */}
                        {server
                          .filter((anbieter) => zeile.zellen[anbieter]?.zustand === 'anders_erkannt')
                          .map((anbieter) => {
                            const fremd = zeile.zellen[anbieter]
                            return (
                              <span key={anbieter} className="mt-0.5 block text-xs text-warn-500">
                                {t('analyse.matrix.recognisedAs', {
                                  server: providerName(anbieter),
                                  titel: fremd.jahr
                                    ? `${fremd.titel ?? '?'} (${fremd.jahr})`
                                    : (fremd.titel ?? '?'),
                                })}
                              </span>
                            )
                          })}
                      </td>
                      {server.map((anbieter) => (
                        <td key={anbieter} className="px-3 py-2.5 text-center">
                          <ZellenZeichen zustand={zeile.zellen[anbieter]?.zustand ?? 'fehlt'} />
                        </td>
                      ))}
                      <td className="px-3 py-2.5 text-mist-500">
                        {t(`analyse.matrix.match.${zeile.zuordnung}`, weg)}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {korrigierbar && (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation()
                              setZuordnen(zuordnen === zeile.kennung ? null : zeile.kennung)
                            }}
                            aria-expanded={zuordnen === zeile.kennung}
                            className="rounded-full border border-ink-700 px-3 py-1 text-xs font-semibold whitespace-nowrap text-mist-300 transition-colors hover:border-accent-500 hover:text-mist-100"
                          >
                            {t('analyse.matrix.rematch.button')}
                          </button>
                        )}
                      </td>
                    </tr>
                    {zuordnen === zeile.kennung && (
                      <tr className="border-b border-ink-800 bg-ink-900/60">
                        <td colSpan={server.length + 3} className="px-3 py-3">
                          <NeuZuordnen
                            zeile={zeile}
                            server={server}
                            onSchliessen={() => setZuordnen(null)}
                          />
                        </td>
                      </tr>
                    )}
                    {aufgeklappt && (
                      <tr className="border-b border-ink-800 bg-ink-900/60">
                        <td colSpan={server.length + 3} className="px-3 py-3">
                          <p className="mb-2 text-xs tracking-wide text-mist-600 uppercase">
                            {t('analyse.matrix.details')}
                          </p>
                          <dl className="flex flex-col gap-2.5">
                            {server.map((anbieter) => (
                              <div key={anbieter} className="text-xs">
                                <dt className="font-semibold text-mist-300">
                                  {providerName(anbieter)}
                                </dt>
                                <dd className="font-mono text-mist-500">
                                  <Nummern zelle={zeile.zellen[anbieter]} />
                                </dd>
                                <Pfade anbieter={anbieter} zelle={zeile.zellen[anbieter]} />
                              </div>
                            ))}
                          </dl>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <Pagination seite={stand.seite} seiten={stand.seiten} onSeite={setSeite} />

      <p className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-mist-500">
        <span className="flex items-center gap-1.5">
          <ZellenZeichen zustand="da" /> {t('analyse.matrix.present')}
        </span>
        <span className="flex items-center gap-1.5">
          <ZellenZeichen zustand="fehlt" /> {t('analyse.matrix.missing')}
        </span>
        {mehrere && (
          <span className="flex items-center gap-1.5">
            <ZellenZeichen zustand="andere_nummer" /> {t('analyse.matrix.otherId')}
          </span>
        )}
        {mehrere && (
          <span className="flex items-center gap-1.5">
            <ZellenZeichen zustand="anders_erkannt" /> {t('analyse.matrix.otherTitle')}
          </span>
        )}
      </p>
    </div>
  )
}

function ZellenZeichen({ zustand }: { zustand: VergleichZelle['zustand'] }) {
  const { t } = useTranslation()
  if (zustand === 'da') {
    return (
      <span className="font-bold text-ok-500" title={t('analyse.matrix.present')}>
        ✓
      </span>
    )
  }
  if (zustand === 'fehlt') {
    return (
      <span className="font-bold text-bad-500" title={t('analyse.matrix.missing')}>
        ✕
      </span>
    )
  }
  if (zustand === 'anders_erkannt') {
    return (
      <span className="font-bold text-bad-500" title={t('analyse.matrix.otherTitle')}>
        ⇄
      </span>
    )
  }
  return (
    <span className="font-bold text-warn-500" title={t('analyse.matrix.otherId')}>
      ≠
    </span>
  )
}

/**
 * Wo der Server den Titel liegen hat.
 *
 * ⚠️ **Ohne Pfad ist die Zeile oft wertlos.** „2BA" als Titel verrät nicht,
 * welcher Ordner auf dem Datenträger gemeint ist. `break-all`, weil Pfade
 * keine Leerzeichen zum Umbrechen haben und sonst die Tabelle sprengen.
 */
function Pfade({ anbieter, zelle }: { anbieter: string; zelle: VergleichZelle | undefined }) {
  const { t } = useTranslation()
  // Plex nennt Serienordner nur in der Detailabfrage. Erst hier, beim
  // Aufklappen, wird nachgeschlagen; das Backend merkt sich die Antwort.
  const nachschlagen =
    !!zelle && zelle.zustand !== 'fehlt' && zelle.pfade.length === 0 && !!zelle.schluessel
  const nachgeschlagen = useQuery({
    queryKey: ['server-vergleich-pfade', anbieter, zelle?.schluessel],
    queryFn: () =>
      api.get<{ pfade: string[] }>(
        `/api/admin/analyse/server-vergleich/pfade?${new URLSearchParams({
          anbieter,
          schluessel: zelle?.schluessel ?? '',
        })}`,
      ),
    enabled: nachschlagen,
    staleTime: Infinity,
    retry: false,
  })
  if (!zelle || zelle.zustand === 'fehlt') return null
  const pfade = zelle.pfade.length ? zelle.pfade : (nachgeschlagen.data?.pfade ?? [])
  if (nachschlagen && nachgeschlagen.isPending) {
    return (
      <dd className="flex items-center gap-1.5 text-mist-600">
        <Spinner className="h-3 w-3" /> {t('analyse.matrix.pathLoading')}
      </dd>
    )
  }
  if (pfade.length === 0) {
    return <dd className="text-mist-600 italic">{t('analyse.matrix.noPath')}</dd>
  }
  return (
    <>
      {pfade.map((pfad) => (
        <dd key={pfad} className="font-mono break-all text-mist-400 select-all">
          {pfad}
        </dd>
      ))}
    </>
  )
}

function Nummern({ zelle }: { zelle: VergleichZelle | undefined }) {
  const { t } = useTranslation()
  if (!zelle || zelle.zustand === 'fehlt') return <>{t('analyse.matrix.missing')}</>
  const teile = [
    ...zelle.tmdb.map((n) => `TMDB ${n}`),
    ...zelle.tvdb.map((n) => `TVDB ${n}`),
    ...zelle.imdb.map((n) => `IMDb ${n}`),
  ]
  return (
    <>
      {zelle.zustand === 'anders_erkannt' && (
        <span className="text-warn-500">{t('analyse.matrix.listedAs')} </span>
      )}
      {zelle.titel && <span className="text-mist-300">{zelle.titel} · </span>}
      {teile.length ? teile.join(' · ') : t('analyse.matrix.noIds')}
      {zelle.jahr !== null && <span className="text-mist-600"> · {zelle.jahr}</span>}
    </>
  )
}
