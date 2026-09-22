/**
 * Die weiteren Achsen einer Kachel: derselbe Titel in einer anderen Fassung.
 *
 * Früher `UhdBadge` und fest auf „4K" – seit dem Fassungsmodell trägt jede
 * Karte eine Liste (`MediaItem.fassungen`), und der Name kommt aus der
 * Fassung. Im ARR-Betrieb steht dort weiterhin „4K"; die Hauptfassung
 * bekommt **kein** Abzeichen, ihr Zustand ist der der Kachel.
 *
 * Auf Kacheln bewusst **kompakt**: nur der Name, der Zustand steckt in der
 * Farbe, der volle Text im Tooltip. Grund steht in `WatchedBadge` – ein
 * Poster ist der Grund, warum jemand hinsieht, und zwei ausgeschriebene
 * Kästen nebeneinander pflastern es zu. Auf 360 px reicht der Platz dafür
 * ohnehin nicht. Ausgeschrieben wird es nur dort, wo Platz ist: auf der
 * Detailseite.
 */

import { useTranslation } from "react-i18next";

import type { FassungAchse, MediaStatus } from "../../api/types";
import { fassungName, fassungVon } from "../../lib/fassungen";
import { useConfig } from "../../hooks/useConfig";

/**
 * Immer dieselbe deckende dunkle Platte, die Farbe steckt in Schrift und Rand.
 *
 * Vorher faerbte der Zustand den Hintergrund selbst ein - mit 15 % Deckkraft.
 * Auf einem dunklen Poster ging das durch, auf einem hellen blieb davon ein
 * blasser, fast weisser Fleck uebrig und das Abzeichen war kaum zu lesen.
 * Dieselbe Loesung wie beim Bewertungs-Abzeichen (`RatingBadge`), das aus
 * genau diesem Grund auf `bg-ink-950` sitzt.
 */
const PLATTE = "bg-ink-950/85";
const TONES: Record<MediaStatus, string> = {
  not_requested: "text-mist-400 ring-ink-600",
  pending_approval: "text-warn-500 ring-warn-500/40",
  approved: "text-accent-400 ring-accent-500/40",
  requested: "text-accent-400 ring-accent-500/40",
  searching: "text-accent-400 ring-accent-500/50",
  downloaded: "text-ok-500 ring-ok-500/40",
  partial: "text-ok-500 ring-ok-500/40",
  in_library: "text-ok-500 ring-ok-500/40",
  rejected: "text-mist-500 ring-ink-600",
  failed: "text-bad-500 ring-bad-500/40",
  cancelled: "text-mist-500 ring-ink-600",
  deleted: "text-mist-500 ring-ink-600",
  deferred: "text-warn-500 ring-warn-500/40",
  blocked: "text-bad-500 ring-bad-500/40",
};

export function FassungBadge({
  fassung,
  kompakt = false,
  className = "",
}: {
  fassung: FassungAchse;
  /** Nur der Name, Zustand über Farbe und Tooltip – für Kacheln und Listen. */
  kompakt?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const name = fassungName(t, fassung);
  // „Noch nicht angefragt" heißt hier etwas anderes als bei der Hauptachse:
  // Die Hauptfassung mag längst da sein – offen ist nur diese.
  const zustand =
    fassung.status === "not_requested"
      ? t("uhd.open")
      : t(`status.${fassung.status}`);
  const voll = `${name} · ${zustand}`;

  return (
    <span
      title={voll}
      aria-label={voll}
      className={
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[11px] " +
        "font-semibold whitespace-nowrap ring-1 backdrop-blur-sm " +
        PLATTE +
        " " +
        TONES[fassung.status] +
        " " +
        className
      }
    >
      <span className="tracking-wide">{name}</span>
      {!kompakt && <span aria-hidden="true">·</span>}
      {!kompakt && <span>{zustand}</span>}
    </span>
  );
}

/**
 * Der Name einer Fassung als kleines Kürzel an einer Zeile – Speicherposten,
 * Aufräumliste, eigene Anfragen.
 *
 * Für die Hauptfassung steht dort nichts: Sonst trüge jede Zeile eines, und
 * die Liste sähe aus wie vorher mit doppelter Beschriftung.
 */
export function FassungKuerzel({
  kennung,
  className = "ml-1.5 text-accent-500",
}: {
  kennung: string | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const fassung = fassungVon(config, kennung);
  if (!fassung || fassung.haupt) return null;
  return <span className={className}>{fassungName(t, fassung)}</span>;
}
