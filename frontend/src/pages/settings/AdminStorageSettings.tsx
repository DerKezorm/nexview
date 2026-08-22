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
  const { t, i18n } = useTranslation();
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
    onSuccess: (daten) => {
      // Entwuerfe auf den gespeicherten Stand ziehen - der Notausgang setzt
      // die Betriebsart um, ohne dass der Entwurf vorher umgestellt wurde.
      setAn(daten.storage_enabled);
      setGrenze(
        daten.storage_default_limit_gb
          ? String(daten.storage_default_limit_gb)
          : "",
      );
      setBestaetigung(null);
      // Der Schalter aendert, was auf mehreren Seiten ueberhaupt existiert -
      // deshalb alles neu laden, nicht nur die Einstellungen.
      void queryClient.invalidateQueries();
    },
  });

  /**
   * Welcher Wechsel gerade auf Bestätigung wartet – `null` heißt keiner.
   *
   * ⚠️ **Jeder Wechsel der Betriebsart setzt die Konten zurück** – in beide
   * Richtungen, eine Regel statt einer Ausnahme. Ohne den Generalpardon wäre
   * jemand nach dem Einschalten schlagartig überzogen, wegen einer Historie,
   * von der er nicht wusste, dass sie mitzählt. Deshalb geht kein Wechsel
   * ohne diesen Dialog raus – und der nennt die Zahlen, nicht nur eine
   * Warnung: Ein allgemeiner Hinweis wird weggeklickt, eine Zahl wird
   * gelesen.
   */
  const [bestaetigung, setBestaetigung] = useState<"an" | "aus" | null>(null);

  // Was das Zuruecksetzen traefe - erst geholt, wenn der Dialog es braucht.
  const vorschau = useQuery({
    queryKey: ["storage-umbuchung"],
    queryFn: () =>
      api.get<{ count: number; bytes: number }>("/api/storage/umbuchung"),
    enabled: bestaetigung !== null,
    staleTime: 0,
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

    {/* Zweispaltig, sobald es rechts etwas zu zeigen gibt: Das Formular ist von
       Natur aus schmal – ein Eingabefeld und ein Haken werden durch Breite
       nicht besser, und lange Textzeilen lesen sich schlechter. Die
       Auswertungen dagegen leben von Breite; dort stehen Dateipfade.
       Beide Spalten gleich breit: Ungleiche Hälften lesen sich wie ein
       Versehen, und die Karten stehen dann nicht mehr auf einer Kante.
       Solange nichts gespeichert ist, bleibt es bei der schmalen Spalte –
       eine leere zweite wäre nur eine Lücke. */}
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

          {/* Bewusst eine Zweifach-Auswahl und kein Häkchen: Ein Haken namens
              „Speicher-Kontingente" ließe offen, ob die Stückzahl daneben noch
              gilt. Die Auswahl sagt es – es gilt genau eine Währung. */}
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">
              {t("storageAdmin.modeLabel")}
            </legend>
            {(
              [
                { wert: false, label: "modeCount", hint: "modeCountHint" },
                { wert: true, label: "modeStorage", hint: "modeStorageHint" },
              ] as const
            ).map((wahl) => (
              <label
                key={wahl.label}
                className={
                  "flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-3 " +
                  (an === wahl.wert
                    ? "border-accent-500/60 bg-accent-500/5"
                    : "border-ink-700 hover:border-ink-600")
                }
              >
                <input
                  type="radio"
                  name="storage-mode"
                  checked={an === wahl.wert}
                  onChange={() => setAn(wahl.wert)}
                  className="mt-0.5 h-4 w-4 accent-accent-500"
                />
                <span>
                  <span className="font-medium">
                    {t("storageAdmin." + wahl.label)}
                  </span>
                  <span className="mt-0.5 block text-sm text-mist-500">
                    {t("storageAdmin." + wahl.hint)}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>

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

          <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
            <Button
              onClick={() => {
                // Ein Wechsel der Betriebsart geht nie direkt raus - erst der
                // Dialog mit den Zahlen. Nur die Grenze zu ändern ist harmlos.
                if (an !== gespeichert) {
                  setBestaetigung(an ? "an" : "aus");
                  return;
                }
                speichern.mutate({
                  storage_enabled: an,
                  storage_default_limit_gb: grenzeZahl,
                });
              }}
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
            {/* Der Notausgang aus dem Plan: räumt den Zustand auf, ohne Code,
                Container oder Datenbank anzufassen. Nur sichtbar, solange es
                etwas abzuschalten gibt. */}
            {gespeichert && (
              <Button
                variant="ghost"
                onClick={() => setBestaetigung("aus")}
                className="ml-auto border-bad-500/40 text-bad-500 hover:bg-bad-500/10 hover:text-bad-500"
              >
                {t("storageAdmin.panic")}
              </Button>
            )}
          </div>
        </Card>

        <ConfirmDialog
          open={bestaetigung !== null}
          title={t(
            bestaetigung === "aus"
              ? "storageAdmin.resetTitleOff"
              : "storageAdmin.resetTitleOn",
          )}
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
          confirmLabel={t(
            bestaetigung === "aus"
              ? "storageAdmin.panic"
              : "storageAdmin.resetConfirmOn",
          )}
          loading={speichern.isPending}
          onCancel={() => setBestaetigung(null)}
          onConfirm={() =>
            vorschau.data &&
            speichern.mutate({
              storage_enabled: bestaetigung === "an",
              storage_default_limit_gb: grenzeZahl,
            })
          }
        />

        {/* Der Papierkorb steht unter den Einstellungen, weil er dieselbe Frage
            beantwortet wie der Schalter darüber: was passiert, wenn Nexview
            später löscht. Er hängt aber **nicht** am Schalter – die Auskunft
            ist auch dann richtig und nützlich, wenn Kontingente aus sind. */}
        <AdminPapierkorb />
      </div>

      {gespeichert && (
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
      )}
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
