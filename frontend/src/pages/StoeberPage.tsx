import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import type { Bestandsfilter, MediaItem, MediaType, RegalInfo } from '../api/types'
import { DemoBanner } from '../components/DemoBanner'
import { DetailModal } from '../components/media/DetailModal'
import { RegalReihe } from '../components/stoebern/RegalReihe'
import { AnsichtUmschalter, type Ansicht } from '../components/stoebern/Titelliste'
import { useConfig } from '../hooks/useConfig'
import { Umschalter } from '../components/Umschalter'
import { regalPath, stoeberFilterPath, stoeberPath } from '../lib/routes'

type StoeberPageProps = {
  mediaType: MediaType
}

/** Die drei Antworten auf "muss es heute Abend sofort laufen?". */
const BESTAND_WAHL: Bestandsfilter[] = ['egal', 'nur_vorhanden', 'nur_neu']

/** Ein Regal als bloßer Verweis - kostet keinen einzigen TMDB-Abruf. */
function RegalKachel({ mediaType, kennung }: { mediaType: MediaType; kennung: string }) {
  const { t } = useTranslation()
  return (
    <Link
      to={regalPath(mediaType, kennung)}
      className="rounded-xl border border-ink-700 bg-ink-850/60 px-4 py-3 text-sm font-semibold transition-colors hover:border-accent-500/60 hover:bg-ink-800"
    >
      {t(`stoebern.regal.${kennung}.titel`)}
    </Link>
  )
}

/**
 * Stöbern - der Rückkatalog in Regalen.
 *
 * Bewusst eine **eigene** Seite neben "Filme entdecken" und nicht deren
 * Umbau. Die Entdecken-Seite ist ein Erscheinungs-Radar: Sie kennt nur ein
 * Zeitfenster von höchstens einem Jahr und beantwortet damit die Frage "was
 * ist neu?". Die Frage "was schauen wir heute Abend?" richtet sich an den
 * ganzen Katalog und braucht einen anderen Aufbau.
 *
 * Der Grundsatz für jedes Regal: Es muss bei **leerer** Bibliothek genauso
 * funktionieren wie bei voller. Persönliche Regale kommen später obendrauf -
 * nie als Türsteher.
 */
export function StoeberPage({ mediaType }: StoeberPageProps) {
  const { t } = useTranslation()
  const { data: config } = useConfig()
  const navigate = useNavigate()
  const [bestand, setBestand] = useState<Bestandsfilter>('egal')
  const [ansicht, setAnsicht] = useState<Ansicht>('kacheln')
  const [selected, setSelected] = useState<MediaItem | null>(null)

  const { data: regale = [] } = useQuery({
    queryKey: ['regale', mediaType],
    queryFn: () => api.get<RegalInfo[]>(`/api/stoebern/regale/${mediaType}`),
    // ⚠️ Hier stand `Infinity`. Das war richtig, solange die Liste für alle
    // gleich war - seit sie von den eigenen Herzen und vom Sehverlauf abhängt,
    // ist es ein Fehler: Ein frisch gesetztes Herz änderte die Seite erst nach
    // einem harten Neuladen, und das sieht aus, als sei nichts passiert.
    // Zusätzlich wird sie beim Setzen eines Herzens ungültig gemacht (siehe
    // FavoriteButton) - der kurze Wert hier ist nur der Rückfall.
    staleTime: 60 * 1000,
  })

  // Persönliches steht schon vom Server aus vorn — es ist die Reihe, die
  // jemand am ehesten sucht. Aber es ist nie die *einzige*: Darunter kommen
  // immer die allgemeinen Regale, damit eine frische Installation ohne Herzen
  // und ohne Media-Server dieselbe Seite sieht.
  const reihen = regale.filter((regal) => regal.gruppe === 'reihe')
  const jahrzehnte = regale.filter((regal) => regal.kategorie === 'jahrzehnt')
  const genres = regale.filter((regal) => regal.kategorie === 'genre')

  const arrConfigured =
    mediaType === 'movie'
      ? (config?.radarr_configured ?? false)
      : (config?.sonarr_configured ?? false)

  return (
    <div className="flex flex-col gap-8">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('stoebern.titel')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('stoebern.intro')}</p>
      </header>

      <DemoBanner />

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <Umschalter
          wert={mediaType}
          wahl={['movie', 'tv'] as const}
          onChange={(neu) => navigate(stoeberPath(neu))}
          // Eigene Schlüssel statt common.series: das heißt dort "Serie"
          // (Einzahl) und wird als Medienart-Bezeichnung gebraucht.
          label={(eintrag) => t(eintrag === 'movie' ? 'stoebern.filme' : 'stoebern.serien')}
        />

        {/* Die Frage, die am Filmabend zuerst kommt. Anders als auf der
            Entdecken-Seite wird sie **serverseitig** beantwortet: Dort siebt
            erst der Browser, und aus zwanzig Kacheln werden bei gefüllter
            Bibliothek zwei. */}
        <Umschalter
          wert={bestand}
          wahl={BESTAND_WAHL}
          onChange={setBestand}
          beschriftung={t('stoebern.bestand.frage')}
          label={(eintrag) => t(`stoebern.bestand.${eintrag}`)}
        />

        {/* Die Darstellung gilt für alle Regale dieser Seite - sie gehört
            deshalb hier nach oben und nicht in eine Filterleiste. */}
        <AnsichtUmschalter wert={ansicht} onChange={setAnsicht} />

        <Link
          to={stoeberFilterPath(mediaType)}
          className="text-sm font-semibold text-accent-500 transition-colors hover:text-accent-400"
        >
          {t('stoebern.filter.knopf')}
        </Link>
      </div>

      {reihen.map((regal) => (
        <RegalReihe
          key={regal.kennung}
          mediaType={mediaType}
          kennung={regal.kennung}
          bestand={bestand}
          ansicht={ansicht}
          onQuickAdd={setSelected}
          titel={regal.titel}
        />
      ))}

      {jahrzehnte.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-xl font-bold tracking-tight">{t('stoebern.jahrzehnte')}</h2>
          <p className="-mt-2 text-sm text-mist-500">{t('stoebern.jahrzehnteHinweis')}</p>
          <div className="flex flex-wrap gap-3">
            {jahrzehnte.map((regal) => (
              <RegalKachel key={regal.kennung} mediaType={mediaType} kennung={regal.kennung} />
            ))}
          </div>
        </section>
      )}

      {genres.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-xl font-bold tracking-tight">{t('stoebern.genres')}</h2>
          <div className="flex flex-wrap gap-3">
            {genres.map((regal) => (
              <RegalKachel key={regal.kennung} mediaType={mediaType} kennung={regal.kennung} />
            ))}
          </div>
        </section>
      )}

      <DetailModal
        item={selected}
        onClose={() => setSelected(null)}
        arrConfigured={arrConfigured}
      />
    </div>
  )
}
