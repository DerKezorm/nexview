import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { WebhookProbe, WebhookStand } from "../../api/types";
import { Button } from "../../components/ui";

/**
 * Der Rückkanal einer Instanz: Haken, Beweis-Stand und Testen-Knopf.
 *
 * Der Haken ist hier nur ein **Wunsch**: Er wirkt erst mit dem
 * Speichern-Knopf der Instanz – dort läuft dann das Anlegen bzw. das
 * rückstandsfreie Entfernen. Vorher handelte er sofort beim Klick; das
 * passte nicht mehr, sobald das Formular einen eigenen Speichern-Knopf
 * hatte: zwei Klicks, zwei verschiedene Momente der Wahrheit.
 *
 * Der Testen-Knopf bleibt sofortig – er ändert nichts, er beweist nur. Sein
 * Ergebnis meldet er nach oben: Einschalten darf erst gespeichert werden,
 * wenn der Anruf nachweislich ankam.
 */
export function WebhookZeile({
  kennung,
  dienst,
  wunsch,
  onWunsch,
  onProbe,
}: {
  kennung: string;
  /** Nur für den Hinweistext – Radarr und Sonarr sind strikt getrennt. */
  dienst: "radarr" | "sonarr";
  /** Der ungespeicherte Haken-Wunsch; undefined heißt: wie gespeichert. */
  wunsch?: boolean;
  onWunsch: (aktiv: boolean) => void;
  onProbe: (angekommen: boolean) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [probe, setProbe] = useState<WebhookProbe | null>(null);

  const standQuery = useQuery({
    queryKey: ["webhook-stand"],
    queryFn: () => api.get<WebhookStand>("/api/settings/webhooks"),
  });

  const test = useMutation({
    mutationFn: () =>
      api.post<WebhookProbe>(`/api/settings/webhooks/${kennung}/testen`),
    onSuccess: (ergebnis) => {
      setProbe(ergebnis);
      onProbe(ergebnis.angekommen);
      void queryClient.invalidateQueries({ queryKey: ["webhook-stand"] });
    },
  });

  const zeile = standQuery.data?.instanzen.find(
    (eintrag) => eintrag.kennung === kennung,
  );
  if (!zeile) {
    return null;
  }

  const gewollt = wunsch ?? zeile.aktiv;
  const ungespeichert = gewollt !== zeile.aktiv;

  /** Hindernis-Kennung übersetzen, roher Zusatz (Version o. Ä.) unübersetzt. */
  const grund = (code: string, info?: string | null) => {
    const satz = t(`settings.webhookReason.${code}`, { defaultValue: code });
    return info ? `${satz} (${info})` : satz;
  };

  return (
    <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={gewollt}
          onChange={(event) => onWunsch(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
        />
        <span>
          <span className="text-sm font-medium text-mist-100">
            {t("settings.webhookUse")}
            {ungespeichert && (
              <span className="ml-2 text-xs font-normal text-warn-500">
                {t("settings.webhookPending")}
              </span>
            )}
          </span>
          <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
            {t(
              dienst === "radarr"
                ? "settings.webhookVersionHintRadarr"
                : "settings.webhookVersionHintSonarr",
            )}
          </span>
        </span>
      </label>

      {zeile.aktiv && (
        <p className="text-xs leading-relaxed text-mist-500">
          {zeile.bewiesen_am
            ? t("settings.webhookProven", {
                wann: new Date(zeile.bewiesen_am).toLocaleString(),
              })
            : t("settings.webhookNeverProven")}
          {zeile.zuletzt_angerufen_am && (
            <>
              {" · "}
              {t("settings.webhookLastCall", {
                wann: new Date(zeile.zuletzt_angerufen_am).toLocaleString(),
                ereignis: zeile.letztes_ereignis || "—",
              })}
            </>
          )}
        </p>
      )}

      {zeile.aktiv && zeile.fehler && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
          {grund(zeile.fehler, zeile.fehler_info)}
        </p>
      )}

      {/* Auch sichtbar, wenn der Haken erst gewollt ist: Genau dann braucht
          es den Beweis, bevor gespeichert werden darf. */}
      {(zeile.aktiv || gewollt) && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            onClick={() => test.mutate()}
            loading={test.isPending}
          >
            {t("settings.webhookTest")}
          </Button>
          {probe && (
            <span
              className={
                "text-sm " + (probe.angekommen ? "text-ok-500" : "text-bad-500")
              }
              role="status"
            >
              {probe.angekommen
                ? t("settings.webhookArrived", { ms: probe.dauer_ms ?? 0 })
                : grund(probe.fehler ?? "proof_failed", probe.info)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
