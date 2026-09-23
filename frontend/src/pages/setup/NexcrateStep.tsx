import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { ApiError, api } from "../../api/client";
import type { AppSettings, NexStand } from "../../api/types";
import {
  NexcrateVerbinden,
  Standpruefung,
} from "../../components/NexcrateVerbinden";
import { Button, ErrorBanner, Spinner } from "../../components/ui";
import { FassungsZeile } from "../../components/FassungsZeile";

/**
 * Der nexcrate-Schritt der Einrichtung (Bauplan 7.1).
 *
 * Verbinden, Stand prüfen, Fassungen lesen - und die eine Frage stellen, die
 * an einer Fassung hängt: Darf sie jeder anfragen?
 *
 * ⚠️ **Eine neue Fassung ist gesperrt, bis der Betreiber sie freigibt.** Der
 * Vorschlag hier öffnet `hd` und lässt alles andere zu; 4K ist teuer, und wer
 * es jedem freigibt, merkt es an der Platte, nicht an einem Haken.
 *
 * ⚠️ **Ein `sperrt` aus der Standprüfung hält an.** Eine nexcrate, die Nexview
 * nicht bedienen kann, soll nicht erst Fassungen anbieten.
 */
export function NexcrateStep({
  onDone,
  onSkip,
}: {
  onDone: () => void;
  onSkip: () => void;
}) {
  const { t } = useTranslation();
  const [fehler, setFehler] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [offen, setOffen] = useState<Record<string, boolean> | null>(null);

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/api/settings"),
  });
  const eingerichtet = Boolean(
    settingsQuery.data?.nexcrate_url &&
    settingsQuery.data?.nexcrate_api_key_set,
  );

  const standQuery = useQuery({
    queryKey: ["nexcrate", "status"],
    queryFn: () => api.get<NexStand>("/api/settings/nexcrate/status"),
    enabled: eingerichtet,
  });
  const stand = standQuery.data;
  // ⚠️ Über ``useMemo``: Eine frische leere Liste bei jedem Durchlauf würde den
  // Effekt darunter endlos wieder anwerfen.
  const fassungen = useMemo(() => stand?.fassungen ?? [], [stand]);

  // Der Vorschlag entsteht einmal, sobald die Fassungen da sind - danach
  // gehören die Haken dem Betreiber.
  useEffect(() => {
    if (offen !== null || fassungen.length === 0) return;
    setOffen(
      Object.fromEntries(fassungen.map((f) => [f.kennung, f.klasse === "hd"])),
    );
  }, [fassungen, offen]);

  const sperrt = (stand?.pruefung ?? []).some(
    (befund) => befund.stufe === "sperrt",
  );

  async function speichern() {
    setBusy(true);
    setFehler(null);
    try {
      // ⚠️ Eine Liste, kein Objekt: Der Server nimmt Kennung und Haken als
      // Paar entgegen und prüft jede Kennung einzeln.
      await api.put(
        "/api/settings/fassungen",
        fassungen.map((fassung) => ({
          kennung: fassung.kennung,
          offen_fuer_alle: offen?.[fassung.kennung] ?? false,
        })),
      );
      onDone();
    } catch (caught) {
      setFehler(
        caught instanceof ApiError ? caught.message : t("errors.generic"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-xl font-bold tracking-tight">
          {t("setup.nexcrateTitle")}
        </h2>
        <p className="mt-1.5 text-sm leading-relaxed text-mist-500">
          {t("setup.nexcrateText")}
        </p>
      </div>

      {fehler && <ErrorBanner message={fehler} />}

      <NexcrateVerbinden onVerbunden={() => void standQuery.refetch()} />

      {eingerichtet && standQuery.isLoading && <Spinner />}
      {stand && <Standpruefung befunde={stand.pruefung ?? []} />}

      {stand?.erreichbar && !sperrt && fassungen.length > 0 && (
        <div className="rounded-xl border border-ink-700 p-4">
          <p className="font-medium text-mist-100">
            {t("setup.nexcrateVersionsTitle")}
          </p>
          <p className="mt-1 text-sm leading-relaxed text-mist-500">
            {t("setup.nexcrateVersionsText")}
          </p>
          <ul className="mt-3 flex flex-col gap-2">
            {fassungen.map((fassung) => (
              <FassungsZeile
                key={fassung.kennung}
                fassung={fassung}
                labelFor={`offen-${fassung.kennung}`}
                haken={
                  <input
                    id={`offen-${fassung.kennung}`}
                    type="checkbox"
                    className="h-4 w-4 shrink-0 accent-accent-500"
                    checked={offen?.[fassung.kennung] ?? false}
                    onChange={(event) =>
                      setOffen((alt) => ({
                        ...(alt ?? {}),
                        [fassung.kennung]: event.target.checked,
                      }))
                    }
                  />
                }
              />
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => void speichern()}
          loading={busy}
          disabled={sperrt}
        >
          {t("setup.saveAndContinue")}
        </Button>
        <button
          type="button"
          onClick={onSkip}
          className="text-sm text-mist-500 underline-offset-4 transition-colors hover:text-mist-100 hover:underline"
        >
          {t("setup.later")}
        </button>
      </div>
    </div>
  );
}
