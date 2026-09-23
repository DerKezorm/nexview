import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type {
  AppSettings,
  NexBitte,
  NexBitteStand,
  NexPruefbefund,
  TestResult,
} from "../api/types";
import { Button, ErrorBanner, Field } from "./ui";

type Props = {
  /** Wird gerufen, sobald ein Schlüssel liegt – gekoppelt oder von Hand. */
  onVerbunden?: (pruefung: NexPruefbefund[]) => void;
};

/**
 * Adresse, Schlüssel, Koppeln – an drei Stellen dieselben Handgriffe.
 *
 * Die Dienste-Seite, der Einrichtungsassistent (Bauplan 7.1) und der
 * Umstiegsassistent (7.3, Schritt 2) verbinden auf genau dieselbe Weise mit
 * nexcrate. Zweimal nachgebaut wäre es zweimal zu pflegen – und die Fallen
 * beim Koppeln sind zu fein dafür.
 *
 * ⚠️ **Koppeln fragt im Takt nach, den nexcrate vorgibt** (`poll_seconds`).
 * Das Geheimnis der Bitte bleibt im Server; der Browser kennt nur die Kennung
 * und den Code, den der Betreiber in nexcrate wiedererkennt.
 *
 * ⚠️ **Eine Bitte lässt sich nicht zurücknehmen** (nexbeat-Befund 14). Der
 * Knopf „Abbrechen" hört nur auf zu fragen; drüben verfällt sie von selbst.
 */
export function NexcrateVerbinden({ onVerbunden }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/api/settings"),
  });
  const settings = settingsQuery.data;

  const [adresse, setAdresse] = useState("");
  const [schluessel, setSchluessel] = useState("");
  const [probe, setProbe] = useState<TestResult | null>(null);
  const [bitte, setBitte] = useState<NexBitte | null>(null);
  const [bitteStand, setBitteStand] = useState<NexBitteStand | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  const gespeicherteAdresse = settings?.nexcrate_url;
  useEffect(() => {
    if (gespeicherteAdresse !== undefined) setAdresse(gespeicherteAdresse);
  }, [gespeicherteAdresse]);

  const auffrischen = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
    void queryClient.invalidateQueries({ queryKey: ["config"] });
    void queryClient.invalidateQueries({ queryKey: ["nexcrate", "status"] });
  }, [queryClient]);

  const speichern = useMutation({
    mutationFn: (patch: Partial<AppSettings>) => api.put<AppSettings>("/api/settings", patch),
    onSuccess: () => {
      auffrischen();
      onVerbunden?.([]);
    },
    onError: (error: Error) => setFehler(error.message),
  });

  const pruefen = useMutation({
    mutationFn: () =>
      api.post<TestResult>("/api/settings/nexcrate/test", {
        url: adresse,
        api_key: schluessel || undefined,
      }),
    onSuccess: (ergebnis) => setProbe(ergebnis),
    onError: (error: Error) => setFehler(error.message),
  });

  const koppeln = useMutation({
    mutationFn: () => api.post<NexBitte>("/api/settings/nexcrate/pairing", { url: adresse }),
    onSuccess: (offen) => {
      setBitte(offen);
      setBitteStand(null);
      setFehler(null);
    },
    onError: (error: Error) => setFehler(error.message),
  });

  useEffect(() => {
    if (!bitte) return;
    let lebt = true;
    const fragen = async () => {
      try {
        const stand = await api.get<NexBitteStand>(
          `/api/settings/nexcrate/pairing/${bitte.pairing_id}`,
        );
        if (!lebt) return;
        setBitteStand(stand);
        if (stand.gespeichert) {
          setBitte(null);
          auffrischen();
          onVerbunden?.(stand.pruefung ?? []);
        } else if (stand.state === "expired") {
          setBitte(null);
        }
      } catch {
        // Ein Aussetzer beim Nachfragen ist kein Abbruch – der nächste Takt zählt.
      }
    };
    const takt = window.setInterval(() => void fragen(), Math.max(1, bitte.poll_seconds) * 1000);
    void fragen();
    return () => {
      lebt = false;
      window.clearInterval(takt);
    };
  }, [bitte, auffrischen, onVerbunden]);

  return (
    <div className="flex flex-col gap-4">
      {fehler && <ErrorBanner message={fehler} />}

      <Field
        label={t("nexcrate.url")}
        value={adresse}
        onChange={(event) => setAdresse(event.target.value)}
        placeholder="https://nexcrate.example.com"
      />
      <Field
        label={t("nexcrate.key")}
        type="password"
        value={schluessel}
        onChange={(event) => setSchluessel(event.target.value)}
        placeholder={settings?.nexcrate_api_key_set ? settings.nexcrate_api_key : ""}
        hint={t("nexcrate.keyHint")}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="ghost"
          onClick={() => pruefen.mutate()}
          disabled={pruefen.isPending || !adresse}
        >
          {t("settings.test")}
        </Button>
        <Button
          type="button"
          onClick={() =>
            speichern.mutate({
              nexcrate_url: adresse,
              ...(schluessel ? { nexcrate_api_key: schluessel } : {}),
            })
          }
          disabled={speichern.isPending || !adresse}
        >
          {t("common.save")}
        </Button>
        {probe && (
          <span className={probe.ok ? "text-sm text-green-400" : "text-sm text-accent-400"}>
            {probe.message}
          </span>
        )}
      </div>

      <div className="rounded-xl border border-ink-700 p-4">
        <p className="font-medium text-mist-100">{t("nexcrate.pairTitle")}</p>
        <p className="mt-1 max-w-3xl text-sm text-mist-400">{t("nexcrate.pairHint")}</p>
        {!bitte && (
          <Button
            type="button"
            variant="ghost"
            className="mt-3"
            onClick={() => koppeln.mutate()}
            disabled={koppeln.isPending || !adresse}
          >
            {t("nexcrate.pairStart")}
          </Button>
        )}
        {bitte && (
          <div className="mt-3 flex flex-col items-start gap-2">
            <p className="text-sm text-mist-300">{t("nexcrate.pairWaiting")}</p>
            <p className="font-mono text-2xl tracking-widest text-mist-100">{bitte.code}</p>
            <Button type="button" variant="ghost" onClick={() => setBitte(null)}>
              {t("common.cancel")}
            </Button>
            <p className="text-xs text-mist-500">{t("nexcrate.pairCancelHint")}</p>
          </div>
        )}
        {bitteStand?.gespeichert && (
          <p className="mt-3 text-sm text-green-400">
            {t("nexcrate.pairDone", { count: bitteStand.fassungen })}
          </p>
        )}
        {bitteStand?.state === "expired" && (
          <p className="mt-3 text-sm text-accent-400">{t("nexcrate.pairExpired")}</p>
        )}
      </div>
    </div>
  );
}

/**
 * Die Standprüfung als Liste (Bauplan 7.2).
 *
 * ⚠️ **Ein `sperrt` ist kein Hinweis.** Es steht zuoberst und in der Farbe des
 * Fehlers; wer darüber hinwegliest, richtet eine nexcrate ein, die Nexview
 * nicht bedienen kann.
 */
export function Standpruefung({ befunde }: { befunde: NexPruefbefund[] }) {
  const { t } = useTranslation();
  if (befunde.length === 0) return null;
  const sortiert = [...befunde].sort((a, b) => (a.stufe === b.stufe ? 0 : a.stufe === "sperrt" ? -1 : 1));
  return (
    <ul className="flex flex-col gap-1" aria-label={t("nexcrate.pruefungTitle")}>
      {sortiert.map((befund) => (
        <li
          key={`${befund.code}:${JSON.stringify(befund.werte)}`}
          className={
            "rounded-lg border px-3 py-2 text-sm " +
            (befund.stufe === "sperrt"
              ? "border-bad-500/40 bg-bad-500/10 text-bad-500"
              : "border-ink-700 text-mist-300")
          }
        >
          {t(`nexcrate.pruefung.${befund.code}`, {
            defaultValue: befund.code,
            ...befund.werte,
          })}
        </li>
      ))}
    </ul>
  );
}
