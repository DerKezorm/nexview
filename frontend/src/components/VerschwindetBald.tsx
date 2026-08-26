/**
 * „Verschwindet bald" — was zum Löschen vorgemerkt ist, mit Restzeit.
 *
 * ⚠️ **Für alle sichtbar, nicht nur für den Administrator.** Der ganze Sinn
 * der Schonfrist ist, dass der Haushalt sie mitbekommt: Eine Ankündigung, die
 * nur der liest, der sie ausgesprochen hat, ist keine. Wer den Titel in der
 * Zeit ansieht, hebt die Vormerkung von selbst auf — deshalb ist dieser
 * Abschnitt zugleich der Weg dorthin.
 *
 * Der Abschnitt verschwindet vollständig, wenn nichts vorgemerkt ist. Eine
 * leere Überschrift „Verschwindet bald" auf einer Startseite wäre ein
 * Schreckmoment ohne Anlass.
 */

import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { formatSize } from '../lib/format'
import { titlePath } from '../lib/routes'
import { Slider } from './Slider'

export type VorgemerkterPosten = {
  posten_id: number
  media_type: string
  tmdb_id: number | null
  season: number | null
  title: string
  size_bytes: number
  loescht_am: string
  tage_uebrig: number
  poster_url: string | null
}

export function VerschwindetBald() {
  const { t, i18n } = useTranslation()

  const abfrage = useQuery({
    queryKey: ['vorgemerkt'],
    queryFn: () => api.get<VorgemerkterPosten[]>('/api/storage/vorgemerkt'),
  })

  const posten = abfrage.data ?? []
  if (posten.length === 0) return null

  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold tracking-wide text-mist-500 uppercase">
        {t('cleanup.goingSoon')}
      </h2>
      <p className="mb-4 text-sm text-mist-500">{t('cleanup.goingSoonIntro')}</p>

      {/* Als Regal mit Covern, wie die übrigen Reihen der Startseite. Eine
          Textzeile würde hier untergehen — und gerade dieser Abschnitt soll
          auffallen, solange die Frist läuft. */}
      <Slider>
        {posten.map((eintrag, index) => (
          <Link
            key={eintrag.posten_id}
            to={
              eintrag.tmdb_id
                ? titlePath(eintrag.media_type as 'movie' | 'tv', eintrag.tmdb_id)
                : '#'
            }
            style={{ animationDelay: `${index * 90}ms` }}
            className="animate-nv-rise group w-40 shrink-0 snap-start sm:w-48"
          >
            <div className="relative aspect-[2/3] overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 transition-colors group-hover:border-accent-600">
              {eintrag.poster_url ? (
                <img
                  src={eintrag.poster_url}
                  alt=""
                  loading="lazy"
                  className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center px-3 text-center text-sm text-mist-600">
                  <span className="w-full break-words">{eintrag.title}</span>
                </div>
              )}

              <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-transparent to-transparent" />

              {/* Die Restzeit auf dem Bild, nicht daneben: Sie ist der Grund,
                  warum die Kachel hier steht. Warnfarbe, sobald es knapp wird —
                  „heute" ist etwas anderes als „in zwölf Tagen", und das soll
                  man sehen, ohne zu rechnen. */}
              <span
                className={
                  'absolute top-2 left-2 rounded-full px-2.5 py-1 text-xs font-semibold tabular-nums backdrop-blur-sm ' +
                  (eintrag.tage_uebrig <= 2
                    ? 'bg-bad-500/85 text-white'
                    : 'bg-warn-500/85 text-ink-950')
                }
              >
                {eintrag.tage_uebrig === 0
                  ? t('cleanup.goingToday')
                  : t('cleanup.goingInDays', { count: eintrag.tage_uebrig })}
              </span>
            </div>

            <p className="mt-2 line-clamp-2 text-sm leading-snug font-semibold">
              {eintrag.title}
            </p>
            <p className="truncate text-xs text-mist-600">
              {eintrag.season !== null
                ? t('cleanup.season', { number: eintrag.season })
                : t('common.movies')}
              {' · '}
              {formatSize(eintrag.size_bytes, i18n.language)}
            </p>
          </Link>
        ))}
      </Slider>
    </section>
  )
}
