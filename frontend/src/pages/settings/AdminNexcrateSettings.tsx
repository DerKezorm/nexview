import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type {
  AppSettings,
  Beschaffung,
  NexBitte,
  NexBitteStand,
  NexStand,
  TestResult,
} from "../../api/types";
import { Button, ErrorBanner, Field, Section, Spinner } from "../../components/ui";

/** Die beiden Betriebsarten, in der Reihenfolge der Seite. */
const MODI: Beschaffung[] = ["arr", "nex"];

/**
 * nexcrate als Beschaffungsweg: Betriebsart, Zugang, Koppeln, Stand.
 *
 * ⚠️ **Die Betriebsart ist ein Schalter für alles** – Filme und Serien
 * zugleich, kein Mischbetrieb. Sie steht ganz oben, weil alles darunter nur
 * im NEX-Betrieb etwas tut.
 *
 * ⚠️ **Koppeln fragt im Takt nach, den nexcrate vorgibt** (`poll_seconds`).
 * Das Geheimnis der Bitte bleibt im Server; der Browser kennt nur die
 * Kennung und den Code, den der Betreiber in nexcrate wiedererkennt.
 */
export function AdminNexcrateSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/settings"),
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

  const eingerichtet = Boolean(settings?.nexcrate_api_key_set && settings?.nexcrate_url);

  const standQuery = useQuery({
    queryKey: ["nexcrate", "status"],
    queryFn: () => api.get<NexStand>("/settings/nexcrate/status"),
    enabled: eingerichtet,
  });

  const auffrischen = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
    void queryClient.invalidateQueries({ queryKey: ["config"] });
    void queryClient.invalidateQueries({ queryKey: ["nexcrate", "status"] });
  }, [queryClient]);

  const speichern = useMutation({
    mutationFn: (patch: Partial<AppSettings>) => api.put<AppSettings>("/settings", patch),
    onSuccess: auffrischen,
    onError: (error: Error) => setFehler(error.message),
  });

  const pruefen = useMutation({
    mutationFn: () =>
      api.post<TestResult>("/settings/nexcrate/test", {
        url: adresse,
        api_key: schluessel || undefined,
      }),
    onSuccess: (ergebnis) => setProbe(ergebnis),
    onError: (error: Error) => setFehler(error.message),
  });

  const koppeln = useMutation({
    mutationFn: () => api.post<NexBitte>("/settings/nexcrate/pairing", { url: adresse }),
    onSuccess: (offen) => {
      setBitte(offen);
      setBitteStand(null);
      setFehler(null);
    },
    onError: (error: Error) => setFehler(error.message),
  });

  // Nachfragen im Takt, den nexcrate nennt – und nur, solange eine Bitte offen ist.
  useEffect(() => {
    if (!bitte) return;
    let lebt = true;
    const fragen = async () => {
      try {
        const stand = await api.get<NexBitteStand>(
          `/settings/nexcrate/pairing/${bitte.pairing_id}`,
        );
        if (!lebt) return;
        setBitteStand(stand);
        if (stand.gespeichert) {
          setBitte(null);
          auffrischen();
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
  }, [bitte, auffrischen]);

  if (settingsQuery.isLoading) return <Spinner />;

  const modus: Beschaffung = settings?.beschaffung ?? "arr";
  const stand = standQuery.data;

  return (
    <div className="mt-6 flex flex-col gap-6">
      {fehler && <ErrorBanner message={fehler} />}

      <Section title={t("nexcrate.modeSection")}>
        <p className="max-w-3xl text-sm text-mist-400">{t("nexcrate.modeHint")}</p>
        <div className="flex flex-wrap gap-3">
          {MODI.map((wert) => (
            <button
              key={wert}
              type="button"
              aria-pressed={modus === wert}
              onClick={() => speichern.mutate({ beschaffung: wert })}
              className={
                "max-w-sm rounded-xl border px-4 py-3 text-left transition-colors " +
                (modus === wert
                  ? "border-accent-500 bg-accent-700/15 text-mist-100"
                  : "border-ink-700 text-mist-300 hover:border-ink-600")
              }
            >
              <span className="block font-medium">{t(`nexcrate.mode.${wert}`)}</span>
              <span className="block text-xs text-mist-500">
                {t(`nexcrate.modeDetail.${wert}`)}
              </span>
            </button>
          ))}
        </div>
      </Section>

      <Section title={t("nexcrate.connectionSection")}>
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
              {/* nexbeat-Befund 14: Ein Programm kann seine Bitte nicht
                  zurücknehmen – sie steht in nexcrate, bis sie verfällt. */}
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
      </Section>

      {eingerichtet && (
        <Section title={t("nexcrate.statusSection")}>
          {standQuery.isLoading && <Spinner />}
          {stand && !stand.erreichbar && (
            <p className="text-sm text-accent-400">
              {t(`errors.byCode.${stand.fehler}`, { defaultValue: t("nexcrate.unreachable") })}
            </p>
          )}
          {stand?.erreichbar && (
            <>
              <p className="text-sm text-mist-300">
                {t("nexcrate.version", { version: stand.version, stage: stand.vertrag })}
              </p>
              {stand.update_verfuegbar && (
                <p className="text-sm text-amber-300">
                  {t("nexcrate.updateAvailable", { version: stand.update_version })}
                </p>
              )}
              {!stand.anime && <p className="text-sm text-mist-400">{t("nexcrate.noAnime")}</p>}
              {stand.web_url && (
                <a
                  className="text-sm text-accent-400 hover:underline"
                  href={stand.web_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("nexcrate.open")}
                </a>
              )}

              <div>
                <p className="font-medium text-mist-100">{t("nexcrate.versionsTitle")}</p>
                <ul className="mt-2 flex flex-col gap-2">
                  {stand.fassungen.map((fassung) => (
                    <li
                      key={fassung.kennung}
                      className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-700 px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-mist-100">{fassung.name}</span>
                      {fassung.klasse && (
                        <span className="rounded bg-ink-800 px-2 py-0.5 text-xs uppercase text-mist-300">
                          {fassung.klasse}
                        </span>
                      )}
                      <span className={fassung.bereit ? "text-green-400" : "text-amber-300"}>
                        {fassung.bereit ? t("nexcrate.ready") : t("nexcrate.notReady")}
                      </span>
                      {fassung.gruende.map((grund) => (
                        <span key={grund} className="text-xs text-mist-500">
                          {t(`nexcrate.reason.${grund}`, { defaultValue: grund })}
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              </div>

              {stand.probleme.length > 0 && (
                <div>
                  <p className="font-medium text-mist-100">{t("nexcrate.healthTitle")}</p>
                  <ul className="mt-2 flex flex-col gap-1 text-sm text-mist-300">
                    {stand.probleme.map((problem) => (
                      <li key={problem.code}>
                        {t(`nexcrate.health.${problem.code}`, { defaultValue: problem.code })}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </Section>
      )}
    </div>
  );
}
