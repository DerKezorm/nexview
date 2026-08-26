/**
 * „Was liegt herum?" — die Aufräum-Liste.
 *
 * Zwei Aufrufstellen, eine Tabelle: die Statistik zeigt die ganze Bibliothek
 * **samt Hausbestand**, das Profil nur das, was dem angemeldeten Konto
 * zugerechnet ist. Der Unterschied steckt allein in `pfad` — die Darstellung
 * ist dieselbe, und das soll sie bleiben.
 *
 * ⚠️ **Die Grundlage steht über der Tabelle, nicht darunter und nicht im
 * Kleingedruckten.** Ohne sie liest sich „seit einem halben Jahr niemand
 * angesehen" als Tatsache — dabei heißt es nur „keines der verknüpften
 * Konten". Wer über ein nicht verknüpftes Konto schaut, ist für Nexview
 * unsichtbar, und sein Lieblingsfilm steht dann hier.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { formatDate, formatSize } from '../lib/format'
import { titlePath } from '../lib/routes'
import { ConfirmDialog } from './ConfirmDialog'
import { Umschalter } from './Umschalter'
import { ErrorBanner, Spinner } from './ui'

export type AufraeumPosten = {
  posten_id: number
  media_type: string
  tmdb_id: number | null
  tvdb_id: number | null
  season: number | null
  tier: string
  title: string
  size_bytes: number
  state: string
  besitzer: string | null
  zuletzt_gesehen: string | null
  gesehen_von: string[]
  bewertung: number | null
  bewertungen: number
  liegt_seit: string | null
  loescht_am: string | null
  tage_uebrig: number | null
}

export type AufraeumListe = {
  posten: AufraeumPosten[]
  gesamt_anzahl: number
  gesamt_bytes: number
  monate: number
  ohne_datum: number
  grundlage: {
    konten_gesamt: number
    konten_verknuepft: number
    ohne_verknuepfung: string[]
    vollstaendig: boolean
  }
}

/** Wie lange darf es her sein? Bewusst wenige Stufen — mehr wäre Spielerei. */
const ZEITRAEUME = [3, 6, 12, 24]

export function AufraeumTabelle({
  pfad,
  schluessel,
  eigene = false,
}: {
  pfad: string
  schluessel: string
  /** Eigene Sicht: dann steht bei leerer Liste etwas anderes da. */
  eigene?: boolean
}) {
  const { t, i18n } = useTranslation()
  const [monate, setMonate] = useState(6)
  const [frage, setFrage] = useState<AufraeumPosten | null>(null)
  const [suche, setSuche] = useState('')
  const [art, setArt] = useState<'alle' | 'movie' | 'tv'>('alle')
  const [stand, setStand] = useState<'alle' | 'vorgemerkt'>('alle')
  const queryClient = useQueryClient()

  /**
   * Die Messung sofort laufen lassen.
   *
   * Gebaut für genau einen Moment: den ersten nach einem Update. Bis der
   * stündliche Abgleich das Datei-Datum nachgetragen hat, kann die Liste
   * nichts sagen — und eine Stunde zu warten, ohne zu wissen worauf, ist die
   * schlechteste aller Auskünfte.
   */
  /**
   * Zum Löschen vormerken — mit Frist, statt sofort.
   *
   * ⚠️ Der harmlosere Weg ist bewusst der **Hauptknopf** der Rückfrage.
   * Löschen hat keinen Rückweg; wer versehentlich die falsche Taste trifft,
   * soll bei der Variante landen, die sich noch zwei Wochen lang zurücknehmen
   * lässt.
   */
  const vormerken = useMutation({
    mutationFn: (posten: AufraeumPosten) =>
      api.post(`/api/storage/entries/${posten.posten_id}/vormerken`, { tage: 14 }),
    onSuccess: () => {
      setFrage(null)
      queryClient.invalidateQueries({ queryKey: [schluessel] })
      queryClient.invalidateQueries({ queryKey: ['vorgemerkt'] })
    },
  })

  const sofortLoeschen = useMutation({
    mutationFn: (posten: AufraeumPosten) =>
      api.post(`/api/storage/entries/${posten.posten_id}/loeschen`),
    onSuccess: () => {
      setFrage(null)
      queryClient.invalidateQueries({ queryKey: [schluessel] })
    },
  })

  const zuruecknehmen = useMutation({
    mutationFn: (posten: AufraeumPosten) =>
      api.post(`/api/storage/entries/${posten.posten_id}/vormerkung-aufheben`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [schluessel] })
      queryClient.invalidateQueries({ queryKey: ['vorgemerkt'] })
    },
  })

  const abgleich = useMutation({
    mutationFn: () =>
      api.post<{ mit_datum: number; posten_gesamt: number }>('/api/storage/abgleich'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [schluessel] }),
  })

  // Die Suche wird verzögert an den Server gegeben - sonst löst jeder
  // Tastendruck eine Abfrage über tausende Posten aus.
  const [gesucht, setGesucht] = useState('')
  useEffect(() => {
    const marke = setTimeout(() => setGesucht(suche), 300)
    return () => clearTimeout(marke)
  }, [suche])

  const abfrage = useQuery({
    queryKey: [schluessel, monate, gesucht, art, stand],
    queryFn: () => {
      const p = new URLSearchParams({ monate: String(monate) })
      if (gesucht.trim()) p.set('suche', gesucht.trim())
      if (art !== 'alle') p.set('art', art)
      if (stand === 'vorgemerkt') p.set('nur_vorgemerkt', 'true')
      return api.get<AufraeumListe>(`${pfad}?${p}`)
    },
    // Beim Tippen die vorige Liste stehen lassen statt sie durch einen
    // Ladekreis zu ersetzen - sonst flackert die halbe Seite bei jedem Wort.
    placeholderData: (vorher) => vorher,
  })

  if (abfrage.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }
  if (abfrage.isError || !abfrage.data) {
    return <ErrorBanner message={t('cleanup.failed')} />
  }

  const {
    posten,
    gesamt_anzahl: anzahl,
    gesamt_bytes: bytes,
    grundlage,
    ohne_datum: ohneDatum,
  } = abfrage.data

  return (
    <div className="flex flex-col gap-4">
      {/* ⚠️ Beschreibung und Grundlage in **einem** Absatz, nicht in einem
          eigenen Kasten daneben. Was die Liste zeigt und worauf sie beruht,
          ist dieselbe Auskunft: „Seit einem halben Jahr niemand angesehen"
          heißt in Wahrheit „keines der verknüpften Konten", und getrennt
          gesetzt liest man das eine ohne das andere. */}
      <p className="text-mist-500">
        {t('cleanup.intro')}
        {!grundlage.vollstaendig && (
          <>
            {' '}
            <span className="text-warn-500">
              {t('cleanup.basis', {
                verknuepft: grundlage.konten_verknuepft,
                gesamt: grundlage.konten_gesamt,
                namen: grundlage.ohne_verknuepfung.join(', '),
              })}
            </span>
          </>
        )}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        {/* Die Suche zuerst: Bei tausenden Posten ist sie der schnellste Weg
            zu einem bestimmten Titel - schneller als jeder Filter. */}
        <input
          type="search"
          value={suche}
          onChange={(e) => setSuche(e.target.value)}
          placeholder={t('cleanup.searchPlaceholder')}
          aria-label={t('cleanup.searchPlaceholder')}
          className="min-w-48 flex-1 rounded-full border border-ink-700 bg-ink-900 px-4 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-600 focus:outline-none"
        />

        <Umschalter
          wert={art}
          wahl={['alle', 'movie', 'tv'] as const}
          onChange={setArt}
          label={(eintrag) => t(`cleanup.kind.${eintrag}`)}
        />

        <Umschalter
          wert={stand}
          wahl={['alle', 'vorgemerkt'] as const}
          onChange={setStand}
          label={(eintrag) => t(`cleanup.stand.${eintrag}`)}
        />

        {/* Bei „nur vorgemerkt" sagt der Zeitraum nichts mehr - dort zählt
            allein, dass eine Frist läuft. Stillgelegt statt versteckt: Ein
            Regler, der ohne Erklärung wegfällt, wirkt wie ein Fehler. */}
        <Umschalter
          deaktiviert={stand === 'vorgemerkt'}
          titel={stand === 'vorgemerkt' ? t('cleanup.periodOff') : undefined}
          wert={String(monate) as '3' | '6' | '12' | '24'}
          wahl={ZEITRAEUME.map(String) as ('3' | '6' | '12' | '24')[]}
          onChange={(neu) => setMonate(Number(neu))}
          beschriftung={t('cleanup.periodLabel')}
          label={(eintrag) => t('cleanup.months', { count: Number(eintrag) })}
        />
      </div>

      {anzahl > 0 && (
        <p className="-mt-1 text-sm text-mist-400">
          {t('cleanup.summary', { count: anzahl, size: formatSize(bytes, i18n.language) })}
        </p>
      )}

      {/* ⚠️ Eine Zeile, kein Kasten. Der Hinweis muss sichtbar sein - eine
          leere Liste könnte sonst heißen „alles in Ordnung", obwohl sie
          heißt „ich weiß es noch nicht". Aber er ist eine Fußnote und darf
          nicht mehr Platz einnehmen als die Tabelle darunter. */}
      {ohneDatum > 0 && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-mist-600">
          <span>{t('cleanup.noDateYet', { count: ohneDatum })}</span>
          {!eigene && (
            <button
              type="button"
              onClick={() => abgleich.mutate()}
              disabled={abgleich.isPending}
              className="text-accent-500 underline-offset-4 transition-colors hover:underline disabled:opacity-60"
            >
              {abgleich.isPending ? t('cleanup.syncing') : t('cleanup.syncNow')}
            </button>
          )}
        </p>
      )}

      {posten.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-5 py-10 text-center text-sm text-mist-500">
          {/* ⚠️ „Nichts liegt herum" wäre hier eine Lüge, solange Posten nur
              deshalb fehlen, weil ihr Alter noch unbekannt ist. Leer heißt
              dann nicht „alles in Ordnung", sondern „ich weiß es noch nicht" -
              und die Zeile darüber sagt genau das. Beides zugleich stehen zu
              lassen wäre ein Widerspruch auf derselben Seite. */}
          {ohneDatum > 0
            ? t('cleanup.emptyUnknown')
            : eigene
              ? t('cleanup.emptyMine')
              : t('cleanup.empty')}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-ink-700">
          <table className="w-full min-w-[46rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-ink-700 text-left text-xs tracking-wide text-mist-500 uppercase">
                <th className="px-3 py-2.5 font-medium">{t('cleanup.colTitle')}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('cleanup.colSize')}</th>
                <th className="px-3 py-2.5 font-medium">{t('cleanup.colLastSeen')}</th>
                <th className="px-3 py-2.5 font-medium">{t('cleanup.colHere')}</th>
                <th className="px-3 py-2.5 font-medium">{t('cleanup.colWho')}</th>
                <th className="px-3 py-2.5 font-medium">{t('cleanup.colOwner')}</th>
                <th className="px-3 py-2.5 text-center font-medium">{t('cleanup.colRating')}</th>
                {!eigene && <th className="px-3 py-2.5" />}
              </tr>
            </thead>
            <tbody>
              {posten.map((eintrag) => (
                <tr
                  key={eintrag.posten_id}
                  className={
                    'border-b border-ink-800 last:border-0 ' +
                    // ⚠️ Vorgemerkt heißt **noch nicht gelöscht**. Die Zeile
                    // bleibt deshalb stehen und wird markiert, statt zu
                    // verschwinden: Wer es zurückdrehen will, muss sie
                    // wiederfinden.
                    (eintrag.loescht_am
                      ? 'bg-warn-500/10 hover:bg-warn-500/15'
                      : 'hover:bg-ink-900/60')
                  }
                >
                  <td className="px-3 py-2.5">
                    {eintrag.tmdb_id ? (
                      <Link
                        to={titlePath(eintrag.media_type as 'movie' | 'tv', eintrag.tmdb_id)}
                        className="font-medium text-mist-100 underline-offset-4 hover:underline"
                      >
                        {eintrag.title}
                      </Link>
                    ) : (
                      <span className="font-medium text-mist-100">{eintrag.title}</span>
                    )}
                    <span className="ml-2 text-xs text-mist-600">
                      {eintrag.season !== null
                        ? t('cleanup.season', { number: eintrag.season })
                        : t('common.movies')}
                      {eintrag.tier === 'uhd' && ' · 4K'}
                    </span>
                    {eintrag.tage_uebrig !== null && (
                      <span className="ml-2 rounded-full bg-warn-500/20 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-warn-500">
                        {eintrag.tage_uebrig === 0
                          ? t('cleanup.goingToday')
                          : t('cleanup.goingInDays', { count: eintrag.tage_uebrig })}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">
                    {formatSize(eintrag.size_bytes, i18n.language)}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {eintrag.zuletzt_gesehen ? (
                      formatDate(eintrag.zuletzt_gesehen.slice(0, 10), i18n.language)
                    ) : (
                      // ⚠️ Nicht „nie gesehen": Das wäre eine Behauptung über
                      // die Welt. Nexview weiß nur, dass es niemand von den
                      // verknüpften Konten war.
                      <span className="text-mist-600">{t('cleanup.neverSeenHere')}</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap text-mist-400">
                    {eintrag.liegt_seit
                      ? formatDate(eintrag.liegt_seit.slice(0, 10), i18n.language)
                      : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-mist-400">
                    {eintrag.gesehen_von.length > 0 ? eintrag.gesehen_von.join(', ') : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-mist-400">
                    {eintrag.besitzer ?? (
                      <span className="text-mist-600">{t('cleanup.house')}</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-center tabular-nums">
                    {eintrag.bewertung !== null ? (
                      <span title={t('cleanup.ratingCount', { count: eintrag.bewertungen })}>
                        {eintrag.bewertung.toLocaleString(i18n.language, {
                          minimumFractionDigits: 1,
                          maximumFractionDigits: 1,
                        })}
                      </span>
                    ) : (
                      <span className="text-mist-600">—</span>
                    )}
                  </td>
                  {!eigene && (
                    <td className="px-3 py-2.5 text-right">
                      {/* Läuft schon eine Frist, ist der Rückweg der einzige
                          sinnvolle Knopf - noch einmal löschen ginge nicht,
                          und ein toter Knopf wäre ein Rätsel. */}
                      {eintrag.loescht_am ? (
                        <button
                          type="button"
                          onClick={() => zuruecknehmen.mutate(eintrag)}
                          disabled={zuruecknehmen.isPending}
                          className="rounded-full border border-warn-500/60 px-3 py-1 text-xs whitespace-nowrap text-warn-500 transition-colors hover:bg-warn-500/15 disabled:opacity-60"
                        >
                          {t('cleanup.keepAfterAll')}
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setFrage(eintrag)}
                          className="rounded-full border border-ink-700 px-3 py-1 text-xs whitespace-nowrap text-mist-400 transition-colors hover:border-bad-500 hover:text-bad-500"
                        >
                          {t('cleanup.delete')}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={frage !== null}
        title={t('cleanup.deleteTitle')}
        description={
          <>
            <p className="font-medium">{frage?.title}</p>
            <p className="mt-2">{t('cleanup.deleteText')}</p>
          </>
        }
        warning={t('cleanup.deleteRecycleHint')}
        confirmLabel={t('cleanup.deleteGrace')}
        weitere={[
          {
            label: t('cleanup.deleteNow'),
            gefahr: true,
            onClick: () => frage && sofortLoeschen.mutate(frage),
          },
        ]}
        loading={vormerken.isPending || sofortLoeschen.isPending}
        onCancel={() => setFrage(null)}
        onConfirm={() => frage && vormerken.mutate(frage)}
      />

      {anzahl > posten.length && (
        <p className="text-xs text-mist-600">
          {t('cleanup.truncated', { shown: posten.length, total: anzahl })}
        </p>
      )}
    </div>
  )
}
