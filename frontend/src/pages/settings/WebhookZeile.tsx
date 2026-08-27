import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { WebhookProbe, WebhookStand } from "../../api/types";
import { Button } from "../../components/ui";

/**
 * Der Rückkanal einer Instanz: Haken, Beweis-Stand und Testen-Knopf.
 *
 * Sitzt bewusst **im Instanz-Block** der Diensteseite – die Kontrolle gehört
 * dorthin, wo der Eintrag in Radarr/Sonarr entsteht. Der Haken handelt
 * sofort: Einschalten heißt Probe, Beweis und Eintrag; Abwählen entfernt den
 * Eintrag rückstandsfrei. Deshalb dreht er einen Moment und zeigt danach den
 * wirklichen Zustand statt eines Wunsches.
 */
export function WebhookZeile({ kennung }: { kennung: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [probe, setProbe] = useState<WebhookProbe | null>(null);

  const standQuery = useQuery({
    queryKey: ["webhook-stand"],
    queryFn: () => api.get<WebhookStand>("/api/settings/webhooks"),
  });

  const haken = useMutation({
    mutationFn: (aktiv: boolean) =>
      api.patch<WebhookStand>(`/api/settings/webhooks/${kennung}`, { aktiv }),
    onSuccess: (stand) => {
      queryClient.setQueryData(["webhook-stand"], stand);
      setProbe(null);
    },
  });

  const test = useMutation({
    mutationFn: () =>
      api.post<WebhookProbe>(`/api/settings/webhooks/${kennung}/testen`),
    onSuccess: (ergebnis) => {
      setProbe(ergebnis);
      void queryClient.invalidateQueries({ queryKey: ["webhook-stand"] });
    },
  });

  const zeile = standQuery.data?.instanzen.find(
    (eintrag) => eintrag.kennung === kennung,
  );
  if (!zeile) {
    return null;
  }

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
          checked={zeile.aktiv}
          disabled={haken.isPending}
          onChange={(event) => haken.mutate(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
        />
        <span>
          <span className="text-sm font-medium text-mist-100">
            {t("settings.webhookUse")}
            {haken.isPending && (
              <span className="ml-2 text-xs font-normal text-mist-600">
                {t("settings.webhookWorking")}
              </span>
            )}
          </span>
          <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
            {t("settings.webhookVersionHint")}
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

      {zeile.aktiv && (
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
