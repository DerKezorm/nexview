import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import type { PapierkorbBelegung, StorageOverview } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Button, Card, ErrorBanner, Spinner } from "../../components/ui";
import { formatSize } from "../../lib/format";
import { AdminPapierkorb } from "./AdminPapierkorb";
import { AdminStorageAbgaben } from "./AdminStorageAbgaben";
import { AdminStorageUsers } from "./AdminStorageUsers";

type Zeitraum = "day" | "week" | "month";

type Einstellungen = {
  quota_default_movies: number | null;
  quota_default_series: number | null;
  storage_default_limit_gb: number | null;
  quota_period: Zeitraum;
};

/** Die drei Standardwerte in der Reihenfolge, in der sie auf der Seite stehen. */
const GRENZEN = [
  { feld: "quota_default_movies", label: "defaultMovies", einheit: "stueck" },
  { feld: "quota_default_series", label: "defaultSeries", einheit: "stueck" },
  { feld: "storage_default_limit_gb", label: "defaultStorage", einheit: "GB" },
] as const;

type Feld = (typeof GRENZEN)[number]["feld"];

/**
 * Was im Auswahlfeld steht.
 *
 * ⚠️ **„Unbegrenzt" braucht einen eigenen Eintrag.** Als leeres Feld ist es
 * unauffindbar: Wer einmal eine Zahl getippt hat, sieht nicht, wie er wieder
 * dorthin zurückkommt – und beim Leeren mit den Pfeiltasten steht plötzlich
 * eine `-1` da. Dieselbe Lehre steckt schon im Benutzer-Editor.
 */
type Grenzentwurf = { modus: "unlimited" | "zahl"; zahl: string };

/** Datenbank-Wert → Entwurf. `null` heißt hier unbegrenzt. */
function alsEntwurf(wert: number | null): Grenzentwurf {
  return wert === null
    ? { modus: "unlimited", zahl: "" }
    : { modus: "zahl", zahl: String(wert) };
}

/** Entwurf → API. `-1` ist auf der Leitung das Zeichen für „unbegrenzt". */
function alsZahl(entwurf: Grenzentwurf): number {
  return entwurf.modus === "unlimited" ? -1 : Number(entwurf.zahl);
}

const ZEITRAEUME: Zeitraum[] = ["day", "week", "month"];

/**
 * Die Kontingente des Hauses: Standardwerte und Zeitraum.
 *
 * ⚠️ **Es gilt immer beides.** Bis 0.19 stand hier ein Umschalter zwischen
 * Stückzahl *und* Speicher – genau eine Währung, haus-weit. Der ist weg: Eine
 * Anfrage geht nur durch, wenn beide Grenzen noch Luft haben. Wer nur nach
 * einer begrenzen will, stellt die andere auf „unbegrenzt"; das ist eine Zahl
 * weniger zu erklären als eine Betriebsart.
 *
 * Hier gibt es **zwei** Zustände je Grenze (unbegrenzt / eigene Zahl), am
 * einzelnen Konto **drei** – dort kommt „Standard" dazu, also der Rückfall auf
 * genau diese Werte. Auf dieser Seite gäbe es nichts, worauf man zurückfallen
 * könnte.
 */
export function AdminStorageSettings() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();

  const abfrage = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<Einstellungen>("/api/settings"),
  });

  const [entwurf, setEntwurf] = useState<Record<Feld, Grenzentwurf> | null>(
    null,
  );
  const [zeitraum, setZeitraum] = useState<Zeitraum | null>(null);

  useEffect(() => {
    if (abfrage.data && entwurf === null) {
      setEntwurf({
        quota_default_movies: alsEntwurf(abfrage.data.quota_default_movies),
        quota_default_series: alsEntwurf(abfrage.data.quota_default_series),
        storage_default_limit_gb: alsEntwurf(
          abfrage.data.storage_default_limit_gb,
        ),
      });
      setZeitraum(abfrage.data.quota_period);
    }
  }, [abfrage.data, entwurf]);

  const speichern = useMutation({
    mutationFn: (werte: Record<string, number | string>) =>
      api.put<Einstellungen>("/api/settings", werte),
    onSuccess: (daten) => {
      setEntwurf({
        quota_default_movies: alsEntwurf(daten.quota_default_movies),
        quota_default_series: alsEntwurf(daten.quota_default_series),
        storage_default_limit_gb: alsEntwurf(daten.storage_default_limit_gb),
      });
      setZeitraum(daten.quota_period);
      // Die Standardwerte greifen für jedes Konto ohne eigene Zahl – die
      // Nutzerverwaltung und die Anfragen zeigen danach andere Grenzen an.
      void queryClient.invalidateQueries();
    },
  });

  // „Alles ins Haus" ist ein eigener Vorgang und keine Nebenwirkung des
  // Speicherns. Die Vorschau wird erst geholt, wenn der Dialog offen ist.
  const [fragtNachHaus, setFragtNachHaus] = useState(false);
  const vorschau = useQuery({
    queryKey: ["storage-umbuchung"],
    queryFn: () =>
      api.get<{ count: number; bytes: number }>("/api/storage/umbuchung"),
    enabled: fragtNachHaus,
    staleTime: 0,
  });

  const insHaus = useMutation({
    mutationFn: () =>
      api.post<{ count: number; bytes: number }>("/api/storage/umbuchung", {}),
    onSuccess: () => {
      setFragtNachHaus(false);
      void queryClient.invalidateQueries();
    },
  });

  if (abfrage.isLoading || !abfrage.data || entwurf === null || zeitraum === null) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  const gespeichert = abfrage.data;
  const zahlen = Object.fromEntries(
    GRENZEN.map(({ feld }) => [feld, alsZahl(entwurf[feld])]),
  ) as Record<Feld, number>;
  // Eine eigene Zahl muss auch eine sein - leer oder negativ geht nicht.
  const gueltig = GRENZEN.every(({ feld }) => {
    const wert = entwurf[feld];
    return (
      wert.modus === "unlimited" ||
      (wert.zahl.trim() !== "" &&
        Number.isInteger(Number(wert.zahl)) &&
        Number(wert.zahl) >= 0)
    );
  });
  const geaendert =
    zeitraum !== gespeichert.quota_period ||
    GRENZEN.some(({ feld }) => zahlen[feld] !== (gespeichert[feld] ?? -1));

  return (
    <div className="flex flex-col gap-5">
      {/* Gross und zuallererst: Das Feature ist in der Testphase. Wer hier
          Grenzen setzt und loescht, soll das wissen, bevor er es tut - und
          Fundstuecke sollen den kuerzesten Weg zu uns haben. */}
      <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3">
        <p className="text-sm font-semibold text-warn-500">
          {t("storageAdmin.devTitle")}
        </p>
        <p className="mt-1 text-sm leading-relaxed text-warn-500/90">
          {t("storageAdmin.devText")}{" "}
          <a
            href="https://github.com/DerKezorm/nexview/issues/new"
            target="_blank"
            rel="noreferrer"
            className="font-medium underline underline-offset-2 hover:text-warn-500"
          >
            {t("storageAdmin.devReport")}
          </a>
        </p>
      </div>

      {/* Zweispaltig: Das Formular ist von Natur aus schmal – drei
          Eingabefelder werden durch Breite nicht besser. Die Auswertungen
          dagegen leben von Breite; dort stehen Dateipfade. */}
      <div className="flex flex-col gap-5 lg:grid lg:grid-cols-2 lg:items-start">
        <div className="flex flex-col gap-5">
          <Card className="flex flex-col gap-4 p-5">
            <div>
              <h2 className="text-lg font-semibold">
                {t("storageAdmin.title")}
              </h2>
              <p className="mt-1 text-sm text-mist-500">
                {t("storageAdmin.intro")}
              </p>
            </div>

            <div>
              <h3 className="text-sm font-medium">
                {t("storageAdmin.defaultsTitle")}
              </h3>
              <div className="mt-2 flex flex-col gap-2">
                {GRENZEN.map(({ feld, label, einheit }) => {
                  const wert = entwurf[feld];
                  return (
                    <div key={feld} className="flex items-center gap-3">
                      <label
                        className="w-28 shrink-0 text-sm text-mist-400"
                        htmlFor={"grenze-" + feld}
                      >
                        {t("storageAdmin." + label)}
                      </label>
                      {/* ⚠️ „Unbegrenzt" ist ein eigener Eintrag, kein leeres
                          Feld. Wer einmal eine Zahl getippt hat, findet sonst
                          nicht mehr zurück – und mit den Pfeiltasten landet man
                          bei einer `-1`, die niemand eingegeben hat. */}
                      <select
                        id={"grenze-" + feld}
                        value={wert.modus}
                        onChange={(e) =>
                          setEntwurf({
                            ...entwurf,
                            [feld]: {
                              ...wert,
                              modus: e.target.value as Grenzentwurf["modus"],
                            },
                          })
                        }
                        className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm"
                      >
                        <option value="unlimited">
                          {t("storageAdmin.unlimited")}
                        </option>
                        <option value="zahl">{t("storageAdmin.ownValue")}</option>
                      </select>
                      {wert.modus === "zahl" && (
                        <>
                          <input
                            type="number"
                            min={0}
                            value={wert.zahl}
                            aria-label={t("storageAdmin." + label)}
                            onChange={(e) =>
                              setEntwurf({
                                ...entwurf,
                                [feld]: { modus: "zahl", zahl: e.target.value },
                              })
                            }
                            className="w-24 rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm tabular-nums"
                          />
                          <span className="text-sm text-mist-500">
                            {einheit === "GB" ? "GB" : t("storageAdmin.unitCount")}
                          </span>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
              <p className="mt-2 text-sm text-mist-500">
                {t("storageAdmin.defaultsHint")}
              </p>
            </div>

            {/* Der Zeitraum gilt haus-weit. Bis 0.19 stand er an jedem Konto
                einzeln – drei Konten mit drei Zeiträumen erklären aber
                niemandem mehr, was „3 Filme" bedeutet. */}
            <div className="border-t border-ink-700 pt-4">
              <label
                className="block text-sm font-medium"
                htmlFor="kontingent-zeitraum"
              >
                {t("storageAdmin.periodLabel")}
              </label>
              <select
                id="kontingent-zeitraum"
                value={zeitraum}
                onChange={(e) => setZeitraum(e.target.value as Zeitraum)}
                className="mt-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm"
              >
                {ZEITRAEUME.map((wert) => (
                  <option key={wert} value={wert}>
                    {t(
                      "storageAdmin.period" +
                        wert.charAt(0).toUpperCase() +
                        wert.slice(1),
                    )}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-sm text-mist-500">
                {t("storageAdmin.bothApply")}
              </p>
            </div>

            <ul className="flex flex-col gap-1.5 border-t border-ink-700 pt-4 text-sm text-mist-500">
              <li>· {t("storageAdmin.pointMeasure")}</li>
              <li>· {t("storageAdmin.pointHouse")}</li>
              <li>· {t("storageAdmin.pointNoLimit")}</li>
            </ul>

            {/* ⚠️ Die eine Bedingung, ohne die das ganze Kontingent nicht
                aufgeht – und sie ist nicht offensichtlich.

                Nexview löscht **ausschließlich** über Radarr und Sonarr; es
                sieht das Dateisystem nie. Wer einen Titel dort entfernt und
                die Datei behält, hat damit etwas geschaffen, das Nexview zwar
                **messen** kann (der Media-Server führt es weiter) aber nicht
                mehr **löschen**. Der Anfragende bliebe auf einer Belastung
                sitzen, die er nicht loswird.

                Rot, weil es eine Betriebsbedingung ist und keine Feinheit: Wer
                den Zusammenhang nicht kennt, richtet den Schaden ahnungslos an
                und merkt ihn erst Wochen später. */}
            <div className="rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3">
              <p className="text-sm font-semibold text-bad-500">
                {t("storageAdmin.mustStayTitle")}
              </p>
              <p className="mt-1 text-sm leading-relaxed text-bad-500/90">
                {t("storageAdmin.mustStayText")}
              </p>
            </div>

            {speichern.isError && (
              <ErrorBanner
                message={
                  speichern.error instanceof ApiError
                    ? speichern.error.message
                    : t("errors.generic")
                }
              />
            )}

            <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
              <Button
                onClick={() =>
                  speichern.mutate({ ...zahlen, quota_period: zeitraum })
                }
                disabled={!geaendert || !gueltig}
              >
                {speichern.isPending ? t("common.saving") : t("common.save")}
              </Button>
              {geaendert ? (
                <span className="text-sm text-warn-500">
                  {t("common.unsaved")}
                </span>
              ) : (
                speichern.isSuccess && (
                  <span className="text-sm text-ok-500">
                    {t("storageAdmin.saved")}
                  </span>
                )
              )}
            </div>
          </Card>

          {/* Ein eigener Vorgang, keine Nebenwirkung: Bis 0.19 lief das
              Zurücksetzen still beim Umschalten der Betriebsart mit. Etwas,
              das die Zurechnung des ganzen Hauses verwirft, gehört an einen
              eigenen Knopf – mit Zahlen im Dialog davor. */}
          <Card className="flex flex-col gap-3 p-5">
            <div>
              <h3 className="font-medium">{t("storageAdmin.houseTitle")}</h3>
              <p className="mt-1 text-sm text-mist-500">
                {t("storageAdmin.houseHint")}
              </p>
            </div>
            {insHaus.isError && (
              <ErrorBanner
                message={
                  insHaus.error instanceof ApiError
                    ? insHaus.error.message
                    : t("errors.generic")
                }
              />
            )}
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="ghost"
                onClick={() => setFragtNachHaus(true)}
                className="border-bad-500/40 text-bad-500 hover:bg-bad-500/10 hover:text-bad-500"
              >
                {t("storageAdmin.houseButton")}
              </Button>
              {insHaus.isSuccess && (
                <span className="text-sm text-ok-500">
                  {t("storageAdmin.resetDone")}
                </span>
              )}
            </div>
          </Card>

          <ConfirmDialog
            open={fragtNachHaus}
            title={t("storageAdmin.resetTitle")}
            description={
              vorschau.isLoading || !vorschau.data ? (
                <div className="flex justify-center py-4">
                  <Spinner />
                </div>
              ) : (
                <>
                  {/* Die Zahlen, nicht nur eine Warnung: "X Titel mit zusammen
                      Y GB" wird gelesen, "Achtung" wird weggeklickt. */}
                  <p>
                    {t("storageAdmin.resetText", {
                      count: vorschau.data.count,
                      size: formatSize(vorschau.data.bytes, i18n.language),
                    })}
                  </p>
                  <p className="mt-2 text-mist-500">
                    {t("storageAdmin.resetKeeps")}
                  </p>
                </>
              )
            }
            confirmLabel={t("storageAdmin.resetConfirm")}
            loading={insHaus.isPending}
            onCancel={() => setFragtNachHaus(false)}
            onConfirm={() => vorschau.data && insHaus.mutate()}
          />

          {/* Der Papierkorb steht hier, weil er dieselbe Frage beantwortet wie
              die Grenzen darüber: was passiert, wenn Nexview später löscht. */}
          <AdminPapierkorb />
        </div>

        <div className="flex flex-col gap-5">
          {/* Ganz oben, und das ist Absicht: Hier wartet jemand. Wer die Karte
              uebersieht, laesst ihn auf einer Belastung sitzen, die er
              losgeworden zu sein glaubt. */}
          <AdminStorageAbgaben />
          <Bestand />
          {/* Die Aufschlüsselung je Konto – und der einzige Eingriff, den es
              hier gibt: einen Titel dem Haus zuschlagen. Er löscht nichts. */}
          <AdminStorageUsers />
        </div>
      </div>
    </div>
  );
}

/** Was gerade erfasst ist - damit der Admin sieht, ob die Messung laeuft. */
function Bestand() {
  const { t, i18n } = useTranslation();

  const abfrage = useQuery({
    queryKey: ["storage-overview"],
    queryFn: () => api.get<StorageOverview>("/api/storage/overview"),
  });

  // Eigene Abfrage, weil die Summe je Ordner einen Netzwerk-Umlauf kostet:
  // Die Hauptzahlen sollen sofort da sein, diese Nebenangabe darf nachkommen.
  const papierkorb = useQuery({
    queryKey: ["papierkorb-belegung"],
    queryFn: () => api.get<PapierkorbBelegung>("/api/storage/recyclebin"),
  });

  if (!abfrage.data) return null;
  const daten = abfrage.data;

  // Solange die erste Messung laeuft, ist alles null - dann sagen wir das,
  // statt eine Reihe von Nullen zu zeigen, die nach einem Fehler aussieht.
  if (daten.total_bytes === 0) {
    return (
      <Card className="p-5">
        <p className="text-sm text-mist-500">{t("storageAdmin.pending")}</p>
      </Card>
    );
  }

  const personen = daten.shares.filter((a) => a.user_id !== null);

  return (
    <Card className="flex flex-col gap-3 p-5">
      <h3 className="font-medium">{t("storageAdmin.stateTitle")}</h3>
      <dl className="flex flex-col gap-2 text-sm">
        <div className="flex justify-between gap-4">
          <dt className="text-mist-500">{t("storage.totalLabel")}</dt>
          <dd className="tabular-nums">
            {formatSize(daten.total_bytes, i18n.language)}
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-mist-500">{t("storage.houseLabel")}</dt>
          <dd className="tabular-nums">
            {formatSize(daten.house_bytes, i18n.language)}
            <span className="ml-1.5 text-mist-600">
              ({t("storage.itemCount", { count: daten.house_items })})
            </span>
          </dd>
        </div>
        {/* **Der Papierkorb ist keine Freigabe.** Was dort liegt, ist von der
            Platte nicht verschwunden - es wartet nur auf das Ablaufen der
            Frist. Wer ihn nicht mitzählt, wundert sich, warum trotz Aufräumens
            nichts frei wird. Nur sichtbar, wenn wirklich etwas darin liegt:
            Eine Zeile mit null Bytes beantwortet keine Frage. */}
        {(papierkorb.data?.total_bytes ?? 0) > 0 && (
          <div className="flex justify-between gap-4">
            <dt className="text-mist-500">{t("storageAdmin.recycleBin")}</dt>
            <dd className="tabular-nums">
              {papierkorb.data?.incomplete ? "≥ " : ""}
              {formatSize(papierkorb.data?.total_bytes ?? 0, i18n.language)}
              <span className="ml-1.5 text-mist-600">
                ({papierkorb.data?.instances.map((zeile) => zeile.name).join(", ")})
              </span>
            </dd>
          </div>
        )}

        <div className="flex justify-between gap-4">
          <dt className="text-mist-500">{t("storageAdmin.assigned")}</dt>
          <dd className="tabular-nums">
            {formatSize(
              personen.reduce((summe, a) => summe + a.used_bytes, 0),
              i18n.language,
            )}
            <span className="ml-1.5 text-mist-600">
              ({t("storageAdmin.people", { count: personen.length })})
            </span>
          </dd>
        </div>
      </dl>
    </Card>
  );
}
