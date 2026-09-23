import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { BeschaffungWarum } from "../../api/types";
import { useConfig } from "../../hooks/useConfig";
import { fassungName, fassungVon } from "../../lib/fassungen";

/**
 * Warum ein Titel noch nicht da ist – gefragt beim Beschaffungsweg (N28).
 *
 * ⚠️ **Der Grund steht je Fassung, nicht am Titel.** Im Prüfstand sagte der
 * Titelgrund „nichts gewollt", während eine Fassung sehr wohl gesucht wurde
 * (nexbeat-Befund 7). Gezeigt wird deshalb eine Zeile je Fassung.
 *
 * ⚠️ **Kein Satz vom Weg wird gezeigt.** Der Weg schickt eine Kennung mit
 * Werten; den Satz baut diese Datei aus `warum.grund.<code>`. Ein Grund, für
 * den es keinen Text gibt, steht als Kennung da – lesbar für den Betreiber,
 * und im Protokoll auffindbar.
 *
 * ⚠️ **Wo der Weg nichts weiß, steht nichts.** Radarr und Sonarr sagen es
 * nicht; dann fehlt der Abschnitt ganz, statt „kein Grund bekannt" zu
 * behaupten.
 */
export function WarumNochNicht({
  mediaType,
  tmdbId,
}: {
  mediaType: "movie" | "tv";
  tmdbId: number;
}) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const kann = config?.beschaffung_kann?.warum ?? false;

  const warum = useQuery({
    queryKey: ["beschaffung", "warum", mediaType, tmdbId],
    queryFn: () =>
      api.get<BeschaffungWarum>(`/api/beschaffung/warum/${mediaType}/${tmdbId}`),
    enabled: kann,
    // Der Grund ändert sich im Takt der Suche, nicht im Takt des Klickens.
    staleTime: 60_000,
  });

  /** Der Name einer Fassung – eine, die es nicht mehr gibt, steht für sich. */
  const namen = (kennung: string) => {
    const fassung = fassungVon(config, kennung);
    return fassung ? fassungName(t, fassung) : kennung;
  };

  const stand = warum.data;
  if (!kann || !stand?.beantwortbar || !stand.bekannt || stand.gruende.length === 0) {
    return null;
  }

  return (
    <section className="mt-5 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <h2 className="text-sm font-semibold text-mist-100">{t("warum.titel")}</h2>
      <ul className="mt-2 flex flex-col gap-1.5">
        {stand.gruende.map((grund) => (
          <li key={`${grund.fassung}:${grund.code}`} className="text-sm text-mist-300">
            <span className="text-mist-500">{namen(grund.fassung)}: </span>
            {t(`warum.grund.${grund.code}`, { defaultValue: grund.code, ...grund.werte })}
            {grund.darunter.length > 0 && (
              <span className="text-mist-500">
                {" – "}
                {grund.darunter
                  .map((code) =>
                    t(`nexcrate.reason.${code}`, { defaultValue: code }),
                  )
                  .join(", ")}
              </span>
            )}
          </li>
        ))}
      </ul>
      {!stand.automatisch && (
        <p className="mt-2 text-xs text-mist-500">{t("warum.automatikAus")}</p>
      )}
    </section>
  );
}
