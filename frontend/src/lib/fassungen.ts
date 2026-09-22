import type { TFunction } from 'i18next'

import type {
  AppConfig,
  EpisodeInfo,
  Fassung,
  FassungAchse,
  FolgenFassung,
  MediaStatus,
  MediaType,
  SeasonInfo,
  StaffelFassung,
  User,
} from '../api/types'

/**
 * Fassungen in der Oberfläche.
 *
 * Eine Fassung ist **eine Art, in der ein Titel vorliegen kann** – im
 * ARR-Betrieb eine Instanz (Radarr, Radarr 4K), später eine Fassung aus
 * nexcrate („Deutsch", „3D"). Die Liste und die Rechte daran kommen vom
 * Server (`AppConfig.fassungen`); hier steht nur, wie die Oberfläche sie
 * benennt und findet.
 *
 * ⚠️ **Klasse ist nicht Fassung.** `uhd` ist eine Klasse und heißt in der
 * Anzeige „4K" – zwei Fassungen können dieselbe Klasse haben. Deshalb steht
 * der Name hier nie fest verdrahtet, sondern kommt aus der Fassung.
 */

/** Die Fassungen einer Medienart, in Anzeigereihenfolge (Hauptfassung zuerst). */
export function fassungenFuer(
  config: Pick<AppConfig, 'fassungen'> | undefined | null,
  mediaType: MediaType,
): Fassung[] {
  return (config?.fassungen ?? []).filter((f) => f.media_type === mediaType)
}

/**
 * Welche Fassungen darf dieses Konto anfragen?
 *
 * Die Leiter (Haus, Konto, Rolle) hat der Server schon gerechnet
 * (`Fassung.darf_anfragen`) – hier bleibt nur noch die Frage, ob die Quelle
 * dahinter steht. Die Hauptfassung bleibt auch ohne sie in der Liste: Sie ist
 * es, die eine Anfrage ohne Angabe bekommt, und das Formular sah auch vor den
 * Fassungen so aus.
 */
export function anfragbareFassungen(
  config: Pick<AppConfig, 'fassungen'> | undefined | null,
  mediaType: MediaType,
): Fassung[] {
  return fassungenFuer(config, mediaType).filter(
    (f) => f.darf_anfragen && (f.bereit || f.haupt),
  )
}

/** Eine Fassung über ihre Kennung – aus der Konfiguration. */
export function fassungVon(
  config: Pick<AppConfig, 'fassungen'> | undefined | null,
  kennung: string | null | undefined,
): Fassung | undefined {
  if (!kennung) return undefined
  return (config?.fassungen ?? []).find((f) => f.kennung === kennung)
}

/** Die Hauptfassung einer Medienart – ihr Zustand steht in `status`. */
export function hauptfassung(
  config: Pick<AppConfig, 'fassungen'> | undefined | null,
  mediaType: MediaType,
): Fassung | undefined {
  return fassungenFuer(config, mediaType).find((f) => f.haupt)
}

/**
 * Wie die Fassung im Text heißt.
 *
 * Im ARR-Betrieb sind es die zwei bekannten Stufen: „Standard" und „4K" –
 * dort heißt die Instanz zwar „Radarr 4K", aber im Anfrageformular ging es
 * immer um die Stufe, und daran ändert der Umbau nichts. Alles andere nennt
 * sich beim Namen.
 */
export function fassungName(t: TFunction, fassung: Fassung | FassungAchse): string {
  if (fassung.quelle === 'arr') {
    return fassung.klasse === 'uhd' ? t('uhd.tierUhd') : t('uhd.tierStandard')
  }
  return fassung.name
}

/**
 * Derselbe Name mit der Medienart darin – für Schalter, die beide Arten
 * nebeneinander zeigen („4K-Filme", „4K-Serien").
 */
export function fassungMitArt(t: TFunction, fassung: Fassung): string {
  if (fassung.quelle === 'arr' && fassung.klasse === 'uhd') {
    return t(fassung.media_type === 'movie' ? 'fassung.uhdMovies' : 'fassung.uhdSeries')
  }
  return t('fassung.mitArt', {
    name: fassungName(t, fassung),
    art: t(fassung.media_type === 'movie' ? 'common.movies' : 'common.seriesPlural'),
  })
}

/** Die Achse einer Fassung an einer Karte – oder nichts, wenn sie fehlt. */
export function achse(
  item: { fassungen?: FassungAchse[] } | null | undefined,
  kennung: string,
): FassungAchse | undefined {
  return item?.fassungen?.find((a) => a.kennung === kennung)
}

/**
 * Der Zustand eines Titels in einer Fassung.
 *
 * ⚠️ **Fehlt die Achse, ist der Zustand unbekannt – nicht „belegt".** Nicht
 * jede Kachel trägt die Liste mit (Favoriten etwa), und eine fehlende Achse
 * als „liegt schon vor" zu lesen sperrte eine Anfrage, die es geben darf.
 * Für die Hauptfassung gilt dann `status`, den es überall gibt.
 */
export function fassungStatus(
  item: { status?: MediaStatus; fassungen?: FassungAchse[] } | null | undefined,
  fassung: Pick<Fassung, 'kennung' | 'haupt'>,
): MediaStatus | null {
  const treffer = achse(item, fassung.kennung)
  if (treffer) return treffer.status
  if (fassung.haupt && item?.status) return item.status
  return null
}

/**
 * Die Staffel-Angaben einer Fassung.
 *
 * Wie oben: Fehlt die Fassung in der Liste, ist nichts bekannt. Für die
 * Hauptfassung fallen wir auf die alten Felder zurück – sie stehen auch an
 * Staffeln, die kein Detailaufruf angereichert hat.
 */
export function staffelFassung(
  staffel: SeasonInfo,
  fassung: Pick<Fassung, 'kennung' | 'haupt'>,
): StaffelFassung | null {
  const treffer = staffel.fassungen?.find((f) => f.kennung === fassung.kennung)
  if (treffer) return treffer
  if (!fassung.haupt) return null
  return {
    kennung: fassung.kennung,
    episodes_available: staffel.episodes_available ?? 0,
    requested: Boolean(staffel.requested),
    requested_episodes: staffel.requested_episodes ?? [],
    requested_status: staffel.requested_status ?? null,
    episodes_total: staffel.episodes_total_arr ?? null,
  }
}

/** Dasselbe für eine einzelne Folge. */
export function folgenFassung(
  folge: EpisodeInfo,
  fassung: Pick<Fassung, 'kennung' | 'haupt'>,
): FolgenFassung | null {
  const treffer = folge.fassungen?.find((f) => f.kennung === fassung.kennung)
  if (treffer) return treffer
  if (!fassung.haupt) return null
  return {
    kennung: fassung.kennung,
    available: Boolean(folge.available),
    requested: Boolean(folge.requested),
    requested_status: folge.requested_status ?? null,
  }
}

/**
 * Darf dieses Konto diese Fassung anfragen?
 *
 * Für Listen **fremder** Konten (Benutzerverwaltung): Dort steht die Leiter
 * nicht am Server-Antwortfeld, sondern muss aus Haus-Vorgabe und Konto
 * zusammengesetzt werden. Für das eigene Konto sagt es der Server
 * (`Fassung.darf_anfragen`).
 */
export function darfFassungAnfragen(
  user: Pick<User, 'can_approve' | 'fassung_rechte'> | null | undefined,
  fassung: Pick<Fassung, 'kennung' | 'offen_fuer_alle'>,
): boolean {
  if (!user) return false
  if (user.can_approve) return true
  if (fassung.offen_fuer_alle) return true
  return Boolean(
    user.fassung_rechte?.find((r) => r.kennung === fassung.kennung)?.anfragen,
  )
}
