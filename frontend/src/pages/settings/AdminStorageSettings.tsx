import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import type { PapierkorbBelegung, StorageOverview } from "../../api/types";
import { Button, Card, ErrorBanner, Spinner } from "../../components/ui";
import { formatSize } from "../../lib/format";
import { AdminPapierkorb } from "./AdminPapierkorb";
import { AdminStorageUsers } from "./AdminStorageUsers";

type Einstellungen = {
  storage_enabled: boolean;
  storage_default_limit_gb: number | null;
};

/**
 * Speicher-Kontingente ein- und ausschalten.
 *
 * Der Schalter ist ein **Hauptschalter**: Ist er aus, verhaelt sich Nexview
 * wie vor dem Einbau - kein Reiter im Profil, keine Karte bei den Anfragen,
 * keine Verteilung in der Statistik, und gemessen wird auch nicht. Die
 * Funktion existiert dann schlicht nicht.
 */
export function AdminStorageSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const abfrage = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<Einstellungen>("/api/settings"),
  });

  const [an, setAn] = useState<boolean | null>(null);
  // Als Text, nicht als Zahl: „leer" heißt hier unbegrenzt, und das lässt
  // sich durch eine Zahl nicht ausdrücken. Dasselbe Muster wie bei den
  // Stückzahl-Kontingenten in der Nutzerverwaltung.
  const [grenze, setGrenze] = useState<string | null>(null);

  useEffect(() => {
    if (abfrage.data && an === null) {
      setAn(abfrage.data.storage_enabled);
      setGrenze(
        abfrage.data.storage_default_limit_gb
          ? String(abfrage.data.storage_default_limit_gb)
          : "",
      );
    }
  }, [abfrage.data, an]);

  const speichern = useMutation({
    mutationFn: (werte: {
      storage_enabled: boolean;
      storage_default_limit_gb: number;
    }) => api.put<Einstellungen>("/api/settings", werte),
    onSuccess: () => {
      // Der Schalter aendert, was auf mehreren Seiten ueberhaupt existiert -
      // deshalb alles neu laden, nicht nur die Einstellungen.
      void queryClient.invalidateQueries();
    },
  });

  if (abfrage.isLoading || an === null) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    );
  }

  const gespeichert = abfrage.data?.storage_enabled ?? false;
  const gespeicherteGrenze = abfrage.data?.storage_default_limit_gb;
  // -1 heißt „zurück auf unbegrenzt" - siehe UserUpdate im Backend.
  const grenzeZahl = grenze?.trim() ? Number(grenze) : -1;
  const grenzeGueltig = grenze?.trim()
    ? Number.isInteger(grenzeZahl) && grenzeZahl > 0
    : true;
  const geaendert =
    an !== gespeichert || grenzeZahl !== (gespeicherteGrenze ?? -1);

  return (
    /* Zweispaltig, sobald es rechts etwas zu zeigen gibt: Das Formular ist von
       Natur aus schmal – ein Eingabefeld und ein Haken werden durch Breite
       nicht besser, und lange Textzeilen lesen sich schlechter. Die
       Auswertungen dagegen leben von Breite; dort stehen Dateipfade.
       Beide Spalten gleich breit: Ungleiche Hälften lesen sich wie ein
       Versehen, und die Karten stehen dann nicht mehr auf einer Kante.
       Solange nichts gespeichert ist, bleibt es bei der schmalen Spalte –
       eine leere zweite wäre nur eine Lücke. */
    <div
      className={
        "flex flex-col gap-5 " +
        (gespeichert ? "lg:grid lg:grid-cols-2 lg:items-start" : "max-w-2xl")
      }
    >
      <div className="flex flex-col gap-5">
        <Card className="flex flex-col gap-4 p-5">
          <div>
            <h2 className="text-lg font-semibold">{t("storageAdmin.title")}</h2>
            <p className="mt-1 text-sm text-mist-500">
              {t("storageAdmin.intro")}
            </p>
          </div>

          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="checkbox"
              checked={an}
              onChange={(e) => setAn(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-accent-500"
            />
            <span>
              <span className="font-medium">{t("storageAdmin.enable")}</span>
              <span className="mt-0.5 block text-sm text-mist-500">
                {t("storageAdmin.enableHint")}
              </span>
            </span>
          </label>

          {/* Der Haken schaltet die **Währung** um - wer ihn setzt, verliert
            seine Grenzen nach Anzahl. Das gehört neben den Haken und nicht in
            eine Freigabemeldung hinterher. */}
          {an && !gespeichert && (
            <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
              {t("storageAdmin.switchWarning")}
            </p>
          )}

          {an && (
            <div className="border-t border-ink-700 pt-4">
              <label
                className="block text-sm font-medium"
                htmlFor="storage-default"
              >
                {t("storageAdmin.defaultLimit")}
              </label>
              <div className="mt-1.5 flex items-center gap-2">
                <input
                  id="storage-default"
                  type="number"
                  min={1}
                  value={grenze ?? ""}
                  onChange={(e) => setGrenze(e.target.value)}
                  placeholder={t("storageAdmin.unlimited")}
                  className="w-32 rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm tabular-nums"
                />
                <span className="text-sm text-mist-500">GB</span>
              </div>
              <p className="mt-1 text-sm text-mist-500">
                {t("storageAdmin.defaultLimitHint")}
              </p>
            </div>
          )}

          {/* Was das Einschalten konkret bedeutet - lieber vorher sagen als
            hinterher erklaeren. */}
          <ul className="flex flex-col gap-1.5 border-t border-ink-700 pt-4 text-sm text-mist-500">
            <li>· {t("storageAdmin.pointMeasure")}</li>
            <li>· {t("storageAdmin.pointHouse")}</li>
            <li>· {t("storageAdmin.pointNoLimit")}</li>
          </ul>

          {/* ⚠️ Die eine Bedingung, ohne die das ganze Kontingent nicht
              aufgeht – und sie ist nicht offensichtlich.

              Nexview löscht **ausschließlich** über Radarr und Sonarr; es
              sieht das Dateisystem nie. Wer einen Titel dort entfernt und die
              Datei behält, hat damit etwas geschaffen, das Nexview zwar
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

          <div className="flex items-center gap-3 border-t border-ink-700 pt-4">
            <Button
              onClick={() =>
                speichern.mutate({
                  storage_enabled: an,
                  storage_default_limit_gb: grenzeZahl,
                })
              }
              disabled={!geaendert || !grenzeGueltig}
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

        {/* Der Papierkorb steht unter den Einstellungen, weil er dieselbe Frage
            beantwortet wie der Schalter darüber: was passiert, wenn Nexview
            später löscht. Er hängt aber **nicht** am Schalter – die Auskunft
            ist auch dann richtig und nützlich, wenn Kontingente aus sind. */}
        <AdminPapierkorb />
      </div>

      {gespeichert && (
        <div className="flex flex-col gap-5">
          <Bestand />
          {/* Die Aufschlüsselung je Konto – und der einzige Eingriff, den es
              hier gibt: einen Titel dem Haus zuschlagen. Er löscht nichts. */}
          <AdminStorageUsers />
        </div>
      )}
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
