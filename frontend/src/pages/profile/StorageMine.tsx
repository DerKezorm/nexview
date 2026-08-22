import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type {
  StorageEntry,
  StorageHouse,
  StorageMine as StorageMineData,
} from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Pagination } from '../../components/Pagination'
import { Card, ErrorBanner, Spinner } from '../../components/ui'
import { formatDateTime, formatSize } from '../../lib/format'

/**
 * Was belege ich – das Größte zuerst.
 *
 * Die Sortierung ist der eigentliche Zweck der Seite. Eine Liste nach Titel
 * oder Datum beantwortet die Frage nicht, die jemand hier stellt: „Wo steckt
 * mein Platz?" Eine einzige Serie kann so viel wiegen wie zweihundert Filme.
 *
 * **Für Administratoren steht hier etwas anderes.** Ihr persönliches Konto ist
 * per Definition null: Was sie holen, gehört dem Haus, und eine Grenze haben
 * sie ohnehin nicht. Ihnen „du belegst nichts" zu zeigen wäre richtig und
 * trotzdem wertlos – sie sehen deshalb den Hausbestand.
 *
 * Zwei getrennte Komponenten und keine Verzweigung in einer: Die Ansichten
 * unterscheiden sich in Inhalt, Abfrage *und* Funktionsumfang – nur die
 * Admin-Sicht hat Suche und Seiten.
 */
export function StorageMine() {
  const { user } = useAuth()
  return user?.role === 'admin' ? <Hausbestand /> : <EigenerSpeicher />
}

function EigenerSpeicher() {
  const { t, i18n } = useTranslation()
  const [suche, setSuche] = useState('')
  const [seite, setSeite] = useState(1)
  // "Nur Gesehene": die Kandidaten fürs Abgeben. Serverseitig gefiltert,
  // weil die Liste blättert – ein Seitenfilter würde die Seitenzahl belügen.
  const [nurGesehene, setNurGesehene] = useState(false)

  const abfrage = useQuery({
    // ⚠️ Der Schlüssel muss mit `useStorageStand` beginnen (`['storage-mine']`),
    // damit ein Zuschlagen an das Haus beide Stellen erneuert.
    queryKey: ['storage-mine', suche, seite, nurGesehene],
    queryFn: () =>
      api.get<StorageMineData>(
        '/api/storage/me?page=' +
          seite +
          '&q=' +
          encodeURIComponent(suche) +
          (nurGesehene ? '&gesehen=true' : ''),
      ),
    placeholderData: (vorher?: StorageMineData) => vorher,
  })

  // TanStack Query behält alte Daten, wenn ein Nachladen scheitert – ohne
  // failureReason bliebe ein Fehler unsichtbar.
  const fehler = abfrage.error ?? abfrage.failureReason
  if (abfrage.isLoading) return <Laden />
  if (fehler) return <Fehler fehler={fehler} />

  const daten = abfrage.data
  if (!daten) return null

  const ueberzogen =
    daten.limit_bytes !== null && daten.used_bytes >= daten.limit_bytes

  return (
    <div className="flex flex-col gap-5">
      <Card className="flex flex-col gap-1 p-5">
        <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
          {t('storage.usedLabel')}
        </p>
        <p
          className={
            'text-3xl font-semibold tabular-nums ' + (ueberzogen ? 'text-bad-500' : '')
          }
        >
          {/* Ohne Grenze steht nur die Zahl – „91 GB von unbegrenzt" wäre eine
              Formulierung ohne Aussage. */}
          {daten.limit_bytes === null
            ? formatSize(daten.used_bytes, i18n.language)
            : t('storage.usedOfLimit', {
                used: formatSize(daten.used_bytes, i18n.language),
                limit: formatSize(daten.limit_bytes, i18n.language),
              })}
        </p>
        <p className="text-sm text-mist-500">
          {t('storage.itemCount', { count: daten.items })}
        </p>
        {daten.limit_bytes === null ? (
          <p className="mt-2 text-sm text-mist-500">{t('storage.noLimitHint')}</p>
        ) : (
          ueberzogen && (
            <p className="mt-2 text-sm text-bad-500">{t('storage.overHint')}</p>
          )
        )}
        {/* ⚠️ Der wichtigste Satz der Seite: Abgeben macht **nicht** sofort
            frei. Der Posten zählt weiter, bis jemand entschieden hat - sonst
            wäre es ein Freifahrtschein, und niemand müsste je entscheiden. */}
        {daten.pending_bytes > 0 && (
          <p className="mt-2 text-sm text-warn-500">
            {t('storage.pendingHint', {
              size: formatSize(daten.pending_bytes, i18n.language),
            })}
          </p>
        )}
      </Card>

      {/* Die Suche erst, wenn es überhaupt etwas zu suchen gibt. Bei fünf
          Titeln wäre ein Suchfeld nur ein Bedienelement ohne Anlass. */}
      {daten.items > daten.per_page && (
        <input
          type="search"
          value={suche}
          onChange={(e) => {
            setSuche(e.target.value)
            setSeite(1)
          }}
          placeholder={t('storage.searchPlaceholder')}
          className="w-full rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none sm:max-w-md"
        />
      )}

      {daten.items === 0 ? (
        <Leer titel={t('storage.empty')} hinweis={t('storage.emptyHint')} />
      ) : (
        <Card className="flex flex-col gap-3 p-4">
          {/* Der Filter wohnt in der Kopfzeile der Liste, die er filtert –
              und nur, wenn es Gesehen-Daten überhaupt gibt: Ohne
              Media-Server-Verknüpfung fände „Nur Gesehene" nie etwas und
              sähe aus wie kaputt. */}
          <Kopf
            titel={t('storage.listTitle')}
            werkzeug={
              daten.watched_available ? (
                <>
                  {(
                    [
                      { wert: false, key: 'storage.filterAll' },
                      { wert: true, key: 'storage.filterSeen' },
                    ] as const
                  ).map((wahl) => (
                    <button
                      key={wahl.key}
                      type="button"
                      onClick={() => {
                        setNurGesehene(wahl.wert)
                        setSeite(1)
                      }}
                      aria-pressed={nurGesehene === wahl.wert}
                      className={
                        'rounded-full border px-3 py-1 text-xs font-medium transition-colors ' +
                        (nurGesehene === wahl.wert
                          ? 'border-accent-500/60 bg-accent-500/10 text-accent-400'
                          : 'border-ink-700 text-mist-400 hover:border-ink-600 hover:text-mist-100')
                      }
                    >
                      {t(wahl.key)}
                    </button>
                  ))}
                </>
              ) : undefined
            }
          />
          {/* ⚠️ Der Leer-Zustand lebt **in** der Karte: Stünde er außerhalb,
              verschwände mit ihm auch der Filter – und wer bei „Nur Gesehene"
              nichts findet, käme nie wieder auf „Alle" zurück. */}
          {daten.entries.length === 0 ? (
            <Leer
              titel={t(nurGesehene ? 'storage.noneSeen' : 'storage.noMatch')}
              hinweis={t(nurGesehene ? 'storage.noneSeenHint' : 'storage.noMatchHint')}
            />
          ) : (
            <Liste eintraege={daten.entries} abgebbar />
          )}
          {daten.matches > daten.per_page && (
            <Pagination
              seite={seite}
              seiten={Math.ceil(daten.matches / daten.per_page)}
              onSeite={setSeite}
            />
          )}
        </Card>
      )}
    </div>
  )
}

function Hausbestand() {
  const { t, i18n } = useTranslation()
  const [suche, setSuche] = useState('')
  const [seite, setSeite] = useState(1)

  const abfrage = useQuery({
    queryKey: ['storage-house', suche, seite],
    queryFn: () =>
      api.get<StorageHouse>(
        `/api/storage/house?page=${seite}&q=${encodeURIComponent(suche)}`,
      ),
    // Beim Blättern und Tippen die alte Seite stehen lassen – sonst blitzt bei
    // jedem Buchstaben der Ladekreis auf.
    placeholderData: (vorher?: StorageHouse) => vorher,
  })

  const fehler = abfrage.error ?? abfrage.failureReason
  if (abfrage.isLoading) return <Laden />
  if (fehler) return <Fehler fehler={fehler} />

  const daten = abfrage.data
  if (!daten) return null

  return (
    <div className="flex flex-col gap-5">
      <Card className="flex flex-col gap-1 p-5">
        <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
          {t('storage.houseLabel')}
        </p>
        <p className="text-3xl font-semibold tabular-nums">
          {formatSize(daten.used_bytes, i18n.language)}
        </p>
        <p className="text-sm text-mist-500">
          {t('storage.itemCount', { count: daten.items })}
          {/* **Kein „von X TB".** Nexview kennt die Plattengröße nicht: Radarr
              meldet nur den freien Platz, und was sonst noch auf demselben
              Träger liegt, sieht es nicht. Eine Gesamtzahl wäre geraten.
              Deshalb zwei gemessene Zahlen nebeneinander – und wenn mehrere
              Träger im Spiel sind, steht auch das da. */}
          {daten.free_bytes > 0 && (
            <span className="ml-2">
              ·{' '}
              {t(
                daten.free_volumes > 1 ? 'storage.freeSpaceVolumes' : 'storage.freeSpace',
                {
                  free: formatSize(daten.free_bytes, i18n.language),
                  count: daten.free_volumes,
                },
              )}
            </span>
          )}
        </p>
        <p className="mt-2 text-sm text-mist-500">{t('storage.adminHint')}</p>
      </Card>

      <input
        type="search"
        value={suche}
        onChange={(e) => {
          setSuche(e.target.value)
          // Nach einer neuen Suche wieder vorn anfangen – sonst steht man auf
          // Seite 7 einer Liste, die nur noch drei Zeilen hat.
          setSeite(1)
        }}
        placeholder={t('storage.searchPlaceholder')}
        className="w-full rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none sm:max-w-md"
      />

      {daten.entries.length === 0 ? (
        <Leer
          titel={t(suche ? 'storage.noMatch' : 'storage.houseEmpty')}
          hinweis={t(suche ? 'storage.noMatchHint' : 'storage.houseEmptyHint')}
        />
      ) : (
        <Card className="flex flex-col gap-3 p-4">
          <Kopf
            titel={t('storage.houseListTitle')}
            rechts={t('storage.houseCount', { count: daten.matches })}
          />
          <Liste eintraege={daten.entries} />
          {daten.matches > daten.per_page && (
            <Pagination
              seite={seite}
              seiten={Math.ceil(daten.matches / daten.per_page)}
              onSeite={setSeite}
            />
          )}
        </Card>
      )}
    </div>
  )
}

function Laden() {
  return (
    <div className="flex justify-center py-12">
      <Spinner />
    </div>
  )
}

function Fehler({ fehler }: { fehler: unknown }) {
  const { t } = useTranslation()
  return (
    <ErrorBanner
      message={fehler instanceof ApiError ? fehler.message : t('errors.generic')}
    />
  )
}

function Leer({ titel, hinweis }: { titel: string; hinweis: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center">
      <p className="text-mist-400">{titel}</p>
      <p className="mt-1 text-sm text-mist-600">{hinweis}</p>
    </div>
  )
}

function Kopf({
  titel,
  rechts,
  werkzeug,
}: {
  titel: string
  rechts?: string
  /** Bedienelemente rechts in der Kopfzeile – etwa der Gesehen-Filter. */
  werkzeug?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-ink-700 pb-3">
      <h3 className="font-medium">{titel}</h3>
      <span className="text-sm text-mist-600">{t('storage.listHint')}</span>
      {rechts && (
        <span className="ml-auto text-sm tabular-nums text-mist-600">{rechts}</span>
      )}
      {werkzeug && <span className="ml-auto flex gap-2">{werkzeug}</span>}
    </div>
  )
}

function Liste({
  eintraege,
  abgebbar = false,
}: {
  eintraege: StorageEntry[]
  /** Nur in der eigenen Liste – den Hausbestand gibt niemand ab. */
  abgebbar?: boolean
}) {
  return (
    <ul className="flex flex-col">
      {eintraege.map((eintrag) => (
        <PostenZeile key={eintrag.id} eintrag={eintrag} abgebbar={abgebbar} />
      ))}
    </ul>
  )
}

function PostenZeile({
  eintrag,
  abgebbar,
}: {
  eintrag: StorageEntry
  abgebbar: boolean
}) {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const wartet = eintrag.state === 'pending'

  /**
   * Abgeben und Zurücknehmen – zwei Richtungen, ein Aufruf.
   *
   * ⚠️ **Es passiert dabei nichts an der Datei**, und der Posten zählt bis zur
   * Entscheidung **weiter** mit. Sonst wäre Abgeben ein Freifahrtschein: Man
   * gäbe alles ab, wäre sofort frei, und niemand müsste je entscheiden. Genau
   * das sagt der Hinweistext darunter.
   */
  const wechseln = useMutation({
    mutationFn: (wunsch?: 'delete' | 'keep') =>
      api.post(
        `/api/storage/entries/${eintrag.id}/${wartet ? 'behalten' : 'abgeben'}`,
        wartet || !wunsch ? {} : { wish: wunsch },
      ),
    onSuccess: () => {
      setFrage(false)
      void queryClient.invalidateQueries({ queryKey: ['storage-mine'] })
      void queryClient.invalidateQueries({ queryKey: ['storage-releases'] })
    },
  })

  /**
   * Bei Staffeln geht dem Abgeben eine Frage voraus: löschen oder behalten?
   *
   * Einstufig, wie im Plan entschieden – der Wunsch reist mit der Abgabe,
   * damit der Admin **einmal** entscheidet und niemand zweimal gefragt wird.
   * Bei Filmen gibt es die Frage nicht: Ein Film wächst nicht, dort wäre
   * „behalten, aber nicht mehr laden" dasselbe wie gar nichts.
   */
  const [frage, setFrage] = useState(false)

  // Serien kommen aus Sonarr und tragen dort nur eine TVDB-Nummer. Die
  // TMDB-Nummer wird beim Abgleich nachgeschlagen, wo sie bekannt ist – wo
  // nicht, bleibt es bei reinem Text statt eines Links ins Leere.
  const ziel = eintrag.tmdb_id ? `/titel/${eintrag.media_type}/${eintrag.tmdb_id}` : null

  const untertitel =
    eintrag.season !== null
      ? t('storage.season', { number: eintrag.season })
      : t(eintrag.media_type === 'movie' ? 'common.movie' : 'common.series')

  return (
    <li className="flex items-center gap-3 border-b border-ink-800 py-2.5 last:border-b-0">
      {/* Das Auge macht die Behalten-Entscheidung leichter: Grün = schon
          gesehen (kann weg), Rot = noch nicht gesehen. Nur bei Filmen – die
          Gesehen-Daten sind Titel-genau, bei einer Staffel würde „gesehen“
          zu viel behaupten. Ohne Media-Server-Verknüpfung gibt es keine
          Daten – dann steht hier ein Fragezeichen-Auge mit dem Grund, statt
          dass ein rotes „nie gesehen“ behauptet, wo niemand nachsehen kann. */}
      {eintrag.media_type === 'movie' && (
        <span
          title={t(
            eintrag.watched === true
              ? 'storage.seen'
              : eintrag.watched === false
                ? 'storage.notSeen'
                : 'storage.seenUnknown',
          )}
          className={
            'shrink-0 ' +
            (eintrag.watched === true
              ? 'text-ok-500'
              : eintrag.watched === false
                ? 'text-bad-500'
                : 'text-mist-600')
          }
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
            {eintrag.watched === null || eintrag.watched === undefined ? (
              <text
                x="12"
                y="15.5"
                textAnchor="middle"
                fontSize="10"
                fill="currentColor"
                stroke="none"
                fontWeight="bold"
              >
                ?
              </text>
            ) : (
              <circle cx="12" cy="12" r="3" />
            )}
          </svg>
        </span>
      )}
      <div className="min-w-0 flex-1">
        {ziel ? (
          <Link to={ziel} className="line-clamp-1 font-medium hover:text-accent-500">
            {eintrag.title}
          </Link>
        ) : (
          <p className="line-clamp-1 font-medium">{eintrag.title}</p>
        )}
        {/* Der Pfad kommt nur beim Hausbestand mit – der Endpunkt liefert ihn
            ausschließlich an Administratoren. */}
        {eintrag.path && (
          <p className="mt-0.5 truncate font-mono text-[11px] text-mist-600">
            {eintrag.path}
          </p>
        )}
        <p className="text-xs text-mist-600">
          {untertitel}
          {/* „Wartet" gehört an die Zeile, nicht nur in eine Summe: Sonst sieht
              man nicht, welcher Titel eigentlich schon weg sein sollte. */}
          {wartet && (
            <span className="ml-1.5 text-warn-500">{t('storage.waiting')}</span>
          )}
          {eintrag.tier === 'uhd' && <span className="ml-1.5 text-accent-500">4K</span>}
          <span className="ml-1.5">
            ·{' '}
            {t('storage.measuredAt', {
              date: formatDateTime(eintrag.measured_at, i18n.language),
            })}
          </span>
        </p>
      </div>
      <span className="shrink-0 tabular-nums">
        {formatSize(eintrag.size_bytes, i18n.language)}
      </span>

      {abgebbar && (
        <button
          type="button"
          onClick={() => {
            if (!wartet && eintrag.season !== null) {
              setFrage(true)
              return
            }
            wechseln.mutate(undefined)
          }}
          disabled={wechseln.isPending}
          className={
            'shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors disabled:opacity-40 ' +
            (wartet
              ? 'border-warn-500/40 text-warn-500 hover:bg-warn-500/10'
              : 'border-ink-700 text-mist-400 hover:border-accent-600 hover:text-mist-100')
          }
        >
          {t(wartet ? 'storage.keepAfterAll' : 'storage.giveUp')}
        </button>
      )}

      <ConfirmDialog
        open={frage}
        title={t('storage.wishTitle')}
        description={
          <>
            <p className="font-medium">{eintrag.title}</p>
            <p className="mt-2">{t('storage.wishText')}</p>
          </>
        }
        confirmLabel={t('storage.wishDelete')}
        weitere={[
          {
            label: t('storage.wishKeep'),
            onClick: () => wechseln.mutate('keep'),
          },
        ]}
        loading={wechseln.isPending}
        onCancel={() => setFrage(false)}
        onConfirm={() => wechseln.mutate('delete')}
      />
    </li>
  )
}
