import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { EpisodeInfo, QualityTier, SeasonDetail, SeasonInfo } from '../../api/types'
import { folgenKompakt } from '../../lib/format'
import { Spinner } from '../ui'

/**
 * Ist diese Staffel schon **ganz** vergeben – vorhanden oder komplett
 * angefragt?
 *
 * Je Stufe eine eigene Antwort: Staffel 3 in 1080p anzufragen ist etwas
 * anderes als Staffel 3 in 4K – zwei Instanzen, zwei Dateien. Fehlende
 * 4K-Felder heißen „unbekannt", nicht „belegt" – wie bei `status_uhd`.
 *
 * Laufende Folgen-Pakete zählen hier **nicht**: Eine Staffel mit zwei
 * vergebenen Folgen bleibt wählbar – der Rest gehört noch niemandem. Was
 * ein Paket belegt, steht in `requested_episodes`.
 */
export function staffelBelegt(staffel: SeasonInfo, tier: QualityTier): boolean {
  if (tier === 'uhd') {
    const gesamt = staffel.episodes_total_arr_uhd ?? staffel.episode_count
    return (
      Boolean(staffel.requested_uhd) ||
      (gesamt > 0 && (staffel.episodes_available_uhd ?? 0) >= gesamt)
    )
  }
  // ⚠️ Der Nenner kommt von **Sonarr**, nicht von TMDB - die beiden zaehlen
  // Folgen gern verschieden (Baywatch S1: 22 gegen 21), und mit der
  // TMDB-Zahl galt eine komplette Staffel ewig als unvollstaendig.
  const gesamt = staffel.episodes_total_arr ?? staffel.episode_count
  return (
    Boolean(staffel.requested) ||
    (gesamt > 0 && staffel.episodes_available >= gesamt)
  )
}

/** Die von Paketen belegten Folgen dieser Staffel – je Stufe. */
function belegteFolgen(staffel: SeasonInfo, tier: QualityTier): number[] {
  return (
    (tier === 'uhd' ? staffel.requested_episodes_uhd : staffel.requested_episodes) ?? []
  )
}

/** Ist diese eine Folge vergeben – vorhanden oder angefragt? */
function folgeBelegt(folge: EpisodeInfo, tier: QualityTier): boolean {
  if (tier === 'uhd') return Boolean(folge.requested_uhd) || Boolean(folge.available_uhd)
  return Boolean(folge.requested) || folge.available
}

/**
 * Das ehrliche Wort zu einer belegten Staffel oder Folge.
 *
 * „läuft" stand früher für jeden aktiven Zustand – auch fürs Warten auf
 * Freigabe, die noch abgelehnt werden kann, und für längst Geladenes. Wer
 * daneben liest, plant mit etwas, das es so nicht gibt. Deshalb entscheidet
 * jetzt der Status der belegenden Anfrage, nicht die Zahlen-Arithmetik.
 */
export function belegungsWort(status: string | null | undefined, vorhanden: boolean): string {
  if (vorhanden || status === 'downloaded') return 'request.seasonHere'
  if (status === 'pending_approval') return 'request.seasonPending'
  return 'request.seasonRunning'
}

type WaehlerProps = {
  tmdbId: number
  seasons: SeasonInfo[]
  tier: QualityTier
  /** Haus-Schalter: Ohne ihn gibt es keine Aufklapp-Pfeile – alles wie früher. */
  folgenErlaubt: boolean
  /** Ganz gewählte Staffeln. */
  staffeln: Set<number>
  /** Je Staffel die gewählten Folgen („Folgen-Paket"). */
  folgen: Map<number, Set<number>>
  onAuswahl: (staffeln: Set<number>, folgen: Map<number, Set<number>>) => void
  kuenftige: boolean
  onKuenftige: (wert: boolean) => void
}

/**
 * Der Staffel- und Folgen-Wähler des Anfrage-Formulars.
 *
 * Häkchen an der Staffel = ganze Staffel. Aufgeklappt = einzelne Folgen, aus
 * denen ein **Folgen-Paket** wird (eine Anfrage je Staffel, mit Folgenliste).
 * Mischformen gehen in einem Zug: Staffel 1 ganz, aus Staffel 2 nur zwei
 * Folgen.
 *
 * Belegtes wird angezeigt statt angeboten – sonst lehnte der Server die
 * Auswahl anschließend mit 409 ab. Und wer eine teilbelegte Staffel anhakt,
 * bekommt sichtbar den **Rest**: genau die Entscheidung aus dem Bauplan
 * („Kim fragt Staffel 2 an, Folge 5 läuft schon").
 */
export function StaffelFolgenWaehler({
  tmdbId,
  seasons,
  tier,
  folgenErlaubt,
  staffeln,
  folgen,
  onAuswahl,
  kuenftige,
  onKuenftige,
}: WaehlerProps) {
  const { t } = useTranslation()
  const [aufgeklappt, setAufgeklappt] = useState<Set<number>>(new Set())
  // Staffeln, deren „Rest" gewählt werden soll, sobald die Folgenliste da ist.
  const [restGewuenscht, setRestGewuenscht] = useState<Set<number>>(new Set())

  const alleWaehlbaren = seasons
    .filter((s) => !staffelBelegt(s, tier) && belegteFolgen(s, tier).length === 0)
    .map((s) => s.season_number)

  function staffelKlick(staffel: SeasonInfo) {
    const nummer = staffel.season_number
    const neueStaffeln = new Set(staffeln)
    const neueFolgen = new Map(folgen)
    if (neueStaffeln.has(nummer)) {
      neueStaffeln.delete(nummer)
    } else if (belegteFolgen(staffel, tier).length > 0 && folgenErlaubt) {
      // Teilbelegt: Das Häkchen heißt „der Rest" – sichtbar, nicht still.
      // Gewählt wird, sobald die Folgenliste geladen ist.
      setAufgeklappt((alt) => new Set(alt).add(nummer))
      setRestGewuenscht((alt) => new Set(alt).add(nummer))
      return
    } else {
      neueStaffeln.add(nummer)
      neueFolgen.delete(nummer)
    }
    onAuswahl(neueStaffeln, neueFolgen)
  }

  function folgenSetzen(nummer: number, menge: Set<number>, ganz: boolean) {
    const neueStaffeln = new Set(staffeln)
    const neueFolgen = new Map(folgen)
    neueStaffeln.delete(nummer)
    if (ganz) {
      neueFolgen.delete(nummer)
      neueStaffeln.add(nummer)
    } else if (menge.size === 0) {
      neueFolgen.delete(nummer)
    } else {
      neueFolgen.set(nummer, menge)
    }
    onAuswahl(neueStaffeln, neueFolgen)
  }

  return (
    <div className="flex flex-col gap-3">
      {seasons.length > 1 && (
        <button
          type="button"
          onClick={() => {
            // Nur das ganz Wählbare: Belegtes anzuhaken hieße, es gleich
            // darauf mit 409 abgelehnt zu bekommen. Teilbelegte Staffeln
            // bleiben der Handauswahl – „alle" soll kein Rest-Paket stiften,
            // das niemand gesehen hat.
            const alleDa = staffeln.size === alleWaehlbaren.length && kuenftige
            onAuswahl(alleDa ? new Set() : new Set(alleWaehlbaren), new Map())
            onKuenftige(!alleDa)
          }}
          className="self-start text-sm text-mist-400 underline-offset-2 hover:text-accent-500 hover:underline"
        >
          {t(staffeln.size > 0 && kuenftige ? 'request.seasonNone' : 'request.seasonAll')}
        </button>
      )}

      {/* Einspaltig. Zweispaltig lief die Lesereihenfolge über Kreuz –
          links 1, rechts 2, darunter 3 – und bei zwanzig Staffeln sucht
          man die gewünschte, statt sie zu finden. */}
      <ul className="flex flex-col">
        {seasons.map((staffel) => {
          const nummer = staffel.season_number
          const daZaehler =
            tier === 'uhd'
              ? (staffel.episodes_available_uhd ?? 0)
              : staffel.episodes_available
          const daGesamt =
            (tier === 'uhd'
              ? staffel.episodes_total_arr_uhd
              : staffel.episodes_total_arr) ?? staffel.episode_count
          const vorhanden = daGesamt > 0 && daZaehler >= daGesamt
          const vergeben = staffelBelegt(staffel, tier)
          const belegte = belegteFolgen(staffel, tier)
          const paket = folgen.get(nummer)
          const auf = aufgeklappt.has(nummer)
          const aufklappbar = folgenErlaubt && !vergeben

          return (
            <li key={nummer} className={auf ? 'rounded-lg bg-ink-900/60' : undefined}>
              <div
                className={
                  'flex items-center gap-3 rounded-lg px-2 py-2 ' +
                  (vergeben ? 'opacity-50' : 'hover:bg-ink-800')
                }
              >
                <input
                  type="checkbox"
                  checked={staffeln.has(nummer)}
                  ref={(el) => {
                    // Ein Paket ist „teils gewählt" – das Kästchen zeigt es an.
                    if (el) el.indeterminate = !staffeln.has(nummer) && Boolean(paket?.size)
                  }}
                  disabled={vergeben}
                  onChange={() => staffelKlick(staffel)}
                  aria-label={staffel.name}
                  className="h-4 w-4 shrink-0 accent-accent-500"
                />
                <span className="min-w-0 flex-1 truncate text-sm">{staffel.name}</span>
                <span className="shrink-0 text-xs text-mist-600">
                  {vergeben
                    ? t(
                        belegungsWort(
                          tier === 'uhd'
                            ? staffel.requested_status_uhd
                            : staffel.requested_status,
                          vorhanden,
                        ),
                      )
                    : paket?.size
                      ? t('request.episodesPicked', {
                          list: folgenKompakt([...paket]),
                        })
                      : belegte.length > 0
                        ? t('request.episodesTaken', { list: folgenKompakt(belegte) })
                        : t('request.episodes', { count: staffel.episode_count })}
                </span>
                {aufklappbar && (
                  <button
                    type="button"
                    onClick={() =>
                      setAufgeklappt((alt) => {
                        const neu = new Set(alt)
                        if (neu.has(nummer)) neu.delete(nummer)
                        else neu.add(nummer)
                        return neu
                      })
                    }
                    aria-expanded={auf}
                    aria-label={t('request.episodesToggle', { name: staffel.name })}
                    className="shrink-0 rounded px-1.5 py-1 text-xs text-mist-500 transition-transform hover:text-accent-500"
                  >
                    <span
                      className={'inline-block transition-transform ' + (auf ? 'rotate-90' : '')}
                      aria-hidden="true"
                    >
                      ▶
                    </span>
                  </button>
                )}
              </div>

              {auf && aufklappbar && (
                <FolgenAuswahl
                  tmdbId={tmdbId}
                  season={nummer}
                  tier={tier}
                  ganzGewaehlt={staffeln.has(nummer)}
                  paket={paket}
                  restGewuenscht={restGewuenscht.has(nummer)}
                  onRestErledigt={() =>
                    setRestGewuenscht((alt) => {
                      const neu = new Set(alt)
                      neu.delete(nummer)
                      return neu
                    })
                  }
                  onSetzen={(menge, ganz) => folgenSetzen(nummer, menge, ganz)}
                />
              )}
            </li>
          )
        })}
      </ul>

      {/* ⚠️ Ein eigener Haken, standardmäßig aus. Früher steckte das
          stillschweigend in „ganze Serie": Sonarr überwacht dann auch jede
          künftige Staffel, und mit Kontingenten ist das ein Blankoscheck
          über Speicher, den niemand beziffern kann. Für Folgen-Pakete gilt
          er nicht – ein Paket ist eine feste Liste. */}
      <label className="flex cursor-pointer items-start gap-3 border-t border-ink-800 px-2 pt-3">
        <input
          type="checkbox"
          checked={kuenftige}
          onChange={(event) => onKuenftige(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
        />
        <span>
          <span className="text-sm">{t('request.future')}</span>
          <span className="mt-0.5 block text-xs text-mist-600">
            {t('request.futureHint')}
          </span>
        </span>
      </label>
    </div>
  )
}

type FolgenProps = {
  tmdbId: number
  season: number
  tier: QualityTier
  ganzGewaehlt: boolean
  paket: Set<number> | undefined
  restGewuenscht: boolean
  onRestErledigt: () => void
  onSetzen: (menge: Set<number>, ganz: boolean) => void
}

/** Die Folgen einer Staffel zum Anhaken – erst geladen, wenn aufgeklappt. */
function FolgenAuswahl({
  tmdbId,
  season,
  tier,
  ganzGewaehlt,
  paket,
  restGewuenscht,
  onRestErledigt,
  onSetzen,
}: FolgenProps) {
  const { t } = useTranslation()

  // Derselbe Schlüssel wie die Staffel-Liste der Detailseite – wer dort schon
  // geschaut hat, wartet hier keine Sekunde.
  const query = useQuery({
    queryKey: ['season', tmdbId, season],
    queryFn: () => api.get<SeasonDetail>(`/api/detail/tv/${tmdbId}/season/${season}`),
    staleTime: 60 * 1000,
  })

  const folgenListe = query.data?.episodes ?? []
  const waehlbare = folgenListe
    .filter((folge) => !folgeBelegt(folge, tier))
    .map((folge) => folge.episode_number)

  // „Rest der Staffel": angefordert vom Staffel-Häkchen, gewählt sobald die
  // Liste da ist – sichtbar, nicht still.
  useEffect(() => {
    if (!restGewuenscht || query.data === undefined) return
    onSetzen(new Set(waehlbare), false)
    onRestErledigt()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restGewuenscht, query.data])

  if (query.isPending) {
    return (
      <p className="flex items-center gap-2 px-9 py-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }
  if (query.error || folgenListe.length === 0) {
    return <p className="px-9 py-2 text-sm text-mist-600">{t('detail.noEpisodes')}</p>
  }

  function klick(folge: EpisodeInfo) {
    // Eine ganz gewählte Staffel bricht beim ersten abgewählten Häkchen in
    // ein Paket auf – aus „alles" wird „alles außer dieser".
    const menge = ganzGewaehlt
      ? new Set(waehlbare)
      : new Set(paket ?? [])
    if (menge.has(folge.episode_number)) menge.delete(folge.episode_number)
    else menge.add(folge.episode_number)
    // Und andersherum: Sind alle wählbaren Folgen angehakt und läuft zu
    // keiner Folge eine fremde Anfrage, ist das die ganze Staffel – samt
    // künftiger Folgen. ⚠️ Entschieden an der frisch geladenen Folgenliste,
    // nicht an der Staffel-Übersicht: Nur hier steht, ob wirklich keine
    // Folge angefragt ist – „schon da" allein steht einer ganzen Staffel
    // nicht im Weg, eine fremde Anfrage schon (der Server sagte sonst 409).
    const keineAngefragten = folgenListe.every(
      (eintrag) => !(tier === 'uhd' ? eintrag.requested_uhd : eintrag.requested),
    )
    const ganz =
      keineAngefragten && menge.size === waehlbare.length && waehlbare.length > 0
    onSetzen(ganz ? new Set() : menge, ganz)
  }

  return (
    <ul className="flex flex-col pb-1">
      {folgenListe.map((folge) => {
        const belegt = folgeBelegt(folge, tier)
        const angehakt =
          !belegt && (ganzGewaehlt || Boolean(paket?.has(folge.episode_number)))
        const da = tier === 'uhd' ? Boolean(folge.available_uhd) : folge.available
        return (
          <li key={folge.episode_number}>
            <label
              className={
                'flex items-center gap-3 rounded px-2 py-1.5 pl-9 ' +
                (belegt ? 'opacity-50' : 'cursor-pointer hover:bg-ink-800')
              }
            >
              <input
                type="checkbox"
                checked={angehakt}
                disabled={belegt}
                onChange={() => klick(folge)}
                className="h-3.5 w-3.5 shrink-0 accent-accent-500"
              />
              <span className="min-w-0 flex-1 truncate text-sm">
                <span className="text-mist-600">{folge.episode_number}.</span>{' '}
                {folge.name}
              </span>
              {belegt && (
                <span className="shrink-0 text-xs text-mist-600">
                  {t(
                    belegungsWort(
                      tier === 'uhd'
                        ? folge.requested_status_uhd
                        : folge.requested_status,
                      da,
                    ),
                  )}
                </span>
              )}
            </label>
          </li>
        )
      })}
    </ul>
  )
}
