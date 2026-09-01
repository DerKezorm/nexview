import { useState } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * „Welche Serie meinst du?" - wenn TMDB keine TVDB-Kennung führt (Issue #5).
 *
 * ⚠️ **Die Form ist Seerrs Form, und zwar bewusst.** Wer von dort kommt, kennt
 * das Fenster mit den Vorschlägen und weiß sofort, was von ihm erwartet wird.
 * Ein eigener Weg wäre vielleicht klarer gewesen und ganz sicher fremder.
 *
 * Zwei Zutaten hat Seerr nicht, und beide kosten den Wiedererkennungswert
 * nichts:
 *
 * - **Oben steht, was angefragt wurde** - Poster, Jahr, Handlung aus TMDB.
 *   Gemessen an einem echten Fall: Zu „Still Water" (Thailand, 2026) bietet
 *   Sonarr „Still Waters" (Wales, 1995) an, ein Buchstabe Unterschied. Ohne
 *   die Gegenüberstellung klickt das jemand weg, ohne zu zögern; nebeneinander
 *   sieht man es sofort.
 * - **Unten steht, was ist, wenn nichts passt** - und bei einer Serie von
 *   1978 etwas anderes als bei einer von vorletzter Woche. „Versuch es
 *   später" wäre dort eine Vertröstung.
 *
 * ⚠️ **Nichts ist vorausgewählt.** Ein voreingestellter erster Treffer wäre
 * genau das Raten, das der Server bewusst unterlässt.
 */

export type Zuordnungsvorschlag = {
  tvdb_id: number
  title: string
  year: number | null
  overview: string
  poster_url: string | null
}

type Props = {
  /** Was der Mensch angefragt hat - zum Danebenhalten. */
  gesucht: {
    title: string
    year: string | null
    overview: string
    poster_url: string | null
  }
  vorschlaege: Zuordnungsvorschlag[]
  /** Ist der Titel jung genug, dass TheTVDB ihn noch nachträgt? */
  frisch: boolean
  laeuft: boolean
  onWaehlen: (tvdbId: number) => void
  onAbbrechen: () => void
}

export function SerienZuordnung({
  gesucht,
  vorschlaege,
  frisch,
  laeuft,
  onWaehlen,
  onAbbrechen,
}: Props) {
  const { t } = useTranslation()
  const [gewaehlt, setGewaehlt] = useState<number | null>(null)

  return (
    <div className="flex flex-col gap-4">
      <p className="rounded-lg border border-ink-700 bg-ink-800/60 px-3 py-2 text-sm text-mist-300">
        {t('request.match.intro')}
      </p>

      {/* Das Angefragte, hervorgehoben - der Bezugspunkt für alles darunter. */}
      <div className="flex gap-3 rounded-lg border border-accent-500 bg-ink-900 p-3">
        {gesucht.poster_url ? (
          <img
            src={gesucht.poster_url}
            alt=""
            className="h-[4.5rem] w-12 flex-none rounded-md object-cover"
          />
        ) : (
          <div className="h-[4.5rem] w-12 flex-none rounded-md border border-ink-700 bg-ink-800" />
        )}
        <div className="min-w-0">
          <p className="text-[0.65rem] font-semibold uppercase tracking-wider text-accent-400">
            {t('request.match.youAsked')}
          </p>
          <p className="font-semibold leading-tight">{gesucht.title}</p>
          {gesucht.year && (
            <p className="text-xs tabular-nums text-mist-500">{gesucht.year}</p>
          )}
          {gesucht.overview && (
            <p className="mt-1 line-clamp-3 text-xs text-mist-300">{gesucht.overview}</p>
          )}
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {vorschlaege.map((vorschlag) => {
          const aktiv = gewaehlt === vorschlag.tvdb_id
          return (
            <button
              key={vorschlag.tvdb_id}
              type="button"
              aria-pressed={aktiv}
              onClick={() => setGewaehlt(aktiv ? null : vorschlag.tvdb_id)}
              className={`flex gap-3 rounded-lg border p-2.5 text-left transition-colors ${
                aktiv
                  ? 'border-accent-500 bg-ink-800'
                  : 'border-ink-700 bg-ink-900 hover:bg-ink-800'
              }`}
            >
              {vorschlag.poster_url ? (
                <img
                  src={vorschlag.poster_url}
                  alt=""
                  className="h-[3.9rem] w-10 flex-none rounded-md object-cover"
                />
              ) : (
                <div className="h-[3.9rem] w-10 flex-none rounded-md border border-ink-700 bg-ink-800" />
              )}
              <div className="min-w-0">
                <p className="text-xs tabular-nums text-mist-500">
                  {vorschlag.year ?? t('request.match.yearUnknown')}
                </p>
                <p className="text-sm font-semibold leading-tight">{vorschlag.title}</p>
                {vorschlag.overview && (
                  <p className="mt-0.5 line-clamp-3 text-xs text-mist-300">
                    {vorschlag.overview}
                  </p>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {/* ⚠️ Der Satz, den Seerr nicht hat. Gemessen: In drei von vier Fällen
          ist die gesuchte Serie gar nicht in der Liste - dann muss dastehen,
          woran es liegt, sonst klickt jemand ratlos etwas Falsches an. */}
      <p className="rounded-lg border border-dashed border-ink-700 px-3 py-2 text-sm text-mist-500">
        <span className="font-semibold text-mist-300">
          {t('request.match.notThere')}
        </span>{' '}
        {frisch ? t('request.match.hintNew') : t('request.match.hintOld')}
      </p>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onAbbrechen}
          disabled={laeuft}
          className="rounded-full border border-ink-700 px-4 py-2 text-sm text-mist-300 hover:bg-ink-800 disabled:opacity-40"
        >
          {t('common.cancel')}
        </button>
        <button
          type="button"
          onClick={() => gewaehlt !== null && onWaehlen(gewaehlt)}
          disabled={gewaehlt === null || laeuft}
          className="rounded-full bg-accent-500 px-4 py-2 text-sm font-medium text-white hover:bg-accent-400 disabled:opacity-40"
        >
          {t('request.match.confirm')}
        </button>
      </div>
    </div>
  )
}
