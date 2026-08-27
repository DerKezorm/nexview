/**
 * Die zweite Achse: derselbe Titel in der 4K-Instanz.
 *
 * Erscheint **nur**, wenn eine zweite Instanz eingerichtet ist und der
 * Benutzer sie nutzen darf – sonst liefert der Server `status_uhd` gar nicht
 * erst. Wer kein 4K betreibt, sieht exakt das Bild von vorher.
 *
 * Auf Kacheln bewusst **kompakt**: nur „4K", der Zustand steckt in der Farbe,
 * der volle Text im Tooltip. Grund steht in `WatchedBadge` – ein Poster ist
 * der Grund, warum jemand hinsieht, und zwei ausgeschriebene Kästen
 * nebeneinander pflastern es zu. Auf 360 px reicht der Platz dafür ohnehin
 * nicht. Ausgeschrieben wird es nur dort, wo Platz ist: auf der Detailseite.
 */

import { useTranslation } from "react-i18next";

import type { MediaStatus } from "../../api/types";

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

export function UhdBadge({
  status,
  kompakt = false,
  className = "",
}: {
  status: MediaStatus;
  /** Nur „4K", Zustand über Farbe und Tooltip – für Kacheln und Listen. */
  kompakt?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  // „Noch nicht angefragt" heißt hier etwas anderes als bei der Hauptachse:
  // Die Standard-Fassung mag längst da sein – offen ist nur die 4K-Fassung.
  const zustand =
    status === "not_requested" ? t("uhd.open") : t(`status.${status}`);
  const voll = `4K · ${zustand}`;

  return (
    <span
      title={voll}
      aria-label={voll}
      className={
        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-[11px] " +
        "font-semibold whitespace-nowrap ring-1 backdrop-blur-sm " +
        PLATTE +
        " " +
        TONES[status] +
        " " +
        className
      }
    >
      <span className="tracking-wide">4K</span>
      {!kompakt && <span aria-hidden="true">·</span>}
      {!kompakt && <span>{zustand}</span>}
    </span>
  );
}
