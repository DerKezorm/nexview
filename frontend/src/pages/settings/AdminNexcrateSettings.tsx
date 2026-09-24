import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../../api/client";
import type { AppSettings, Beschaffung, NexFassungZeile, NexStand } from "../../api/types";
import { NexcrateVerbinden, Standpruefung } from "../../components/NexcrateVerbinden";
import { ErrorBanner, Section, Spinner } from "../../components/ui";
import { FassungsZeile } from "../../components/FassungsZeile";
import { FassungsRechte } from "../../components/FassungsRechte";
import { useConfig } from "../../hooks/useConfig";

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
 *
 * ⚠️ **Mit Anfragen, Posten oder Rechten schaltet nur der Assistent um.** Der
 * Server lehnt den Knopf dann ab (`beschaffung_switch_needs_assistant`);
 * `zumUmstieg` führt zu ihm, wenn sein Reiter da ist.
 */
export function AdminNexcrateSettings({ zumUmstieg }: { zumUmstieg?: () => void } = {}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/api/settings"),
  });
  const settings = settingsQuery.data;
  const { data: config } = useConfig();

  const [fehler, setFehler] = useState<string | null>(null);
  const eingerichtet = Boolean(settings?.nexcrate_api_key_set && settings?.nexcrate_url);

  const standQuery = useQuery({
    queryKey: ["nexcrate", "status"],
    queryFn: () => api.get<NexStand>("/api/settings/nexcrate/status"),
    enabled: eingerichtet,
  });

  const auffrischen = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["settings"] });
    void queryClient.invalidateQueries({ queryKey: ["config"] });
    void queryClient.invalidateQueries({ queryKey: ["nexcrate", "status"] });
  }, [queryClient]);

  const speichern = useMutation({
    mutationFn: (patch: Partial<AppSettings>) => api.put<AppSettings>("/api/settings", patch),
    onSuccess: auffrischen,
    onError: (error: Error) => setFehler(error.message),
  });

  // ⚠️ Anders als beim Einrichten (`NexcrateStep`) gibt es hier keinen
  // eigenen "Speichern"-Knopf mehr: Ein Haken speichert sofort, wie der
  // Betriebsart-Schalter darüber auch. Die Box selbst führt keinen eigenen
  // Stand - angezeigt wird, was zuletzt aus `/api/config` kam.
  const rechteMutation = useMutation({
    mutationFn: (eintrag: { kennung: string; offen_fuer_alle: boolean }) =>
      api.put("/api/settings/fassungen", [eintrag]),
    onSuccess: auffrischen,
    onError: (error: Error) => setFehler(error.message),
  });

  if (settingsQuery.isLoading) return <Spinner />;

  const modus: Beschaffung = settings?.beschaffung ?? "arr";
  const stand = standQuery.data;

  // ⚠️ Die Rechte sind eine reine Nexview-Einstellung (`PUT
  // /api/settings/fassungen` fragt nie nexcrate) und kommen deshalb aus
  // `/api/config`, nicht aus dem nexcrate-Stand. `bereit` und `gruende`
  // ergänzt der Stand nur, wenn er da ist - fehlt er (Störung, noch nicht
  // geladen), zeigt die Zeile trotzdem den gespeicherten Rechte-Stand.
  const nexFassungen = (config?.fassungen ?? []).filter((fassung) => fassung.quelle === "nex");
  const rechteFassungen: NexFassungZeile[] = nexFassungen.map((fassung) => {
    const ausStand = stand?.fassungen.find((eintrag) => eintrag.kennung === fassung.kennung);
    return {
      kennung: fassung.kennung,
      media_type: fassung.media_type,
      name: fassung.name,
      klasse: fassung.klasse,
      bereit: ausStand?.bereit ?? fassung.bereit,
      gruende: ausStand?.gruende ?? [],
    };
  });
  const rechteOffen = Object.fromEntries(
    nexFassungen.map((fassung) => [fassung.kennung, fassung.offen_fuer_alle]),
  );

  return (
    <div className="mt-6 flex flex-col gap-6">
      {fehler && <ErrorBanner message={fehler} />}
      {zumUmstieg &&
        speichern.error instanceof ApiError &&
        speichern.error.code === "beschaffung_switch_needs_assistant" && (
          <button
            type="button"
            onClick={zumUmstieg}
            className="self-start text-sm text-accent-400 hover:underline"
          >
            {t("umstieg.section")}
          </button>
        )}

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
        <NexcrateVerbinden onVerbunden={auffrischen} />
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
              {/* ⚠️ Die Standprüfung gehört nach oben, nicht ans Ende: Eine
                  nexcrate, die Nexview nicht bedienen kann, ist keine
                  Randnotiz unter den Fassungen. */}
              <Standpruefung befunde={stand.pruefung ?? []} />
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

              {/* ⚠️ Die reine Anzeige (ohne Haken) gehört nur zum ARR-Betrieb:
                  Der Rechte-Kasten für den NEX-Betrieb steht unten, eigens
                  losgelöst von "erreichbar" - siehe dort. */}
              {modus !== "nex" && (
                <div>
                  <p className="font-medium text-mist-100">{t("nexcrate.versionsTitle")}</p>
                  <ul className="mt-2 flex flex-col gap-2">
                    {stand.fassungen.map((fassung) => (
                      <FassungsZeile key={fassung.kennung} fassung={fassung} />
                    ))}
                  </ul>
                </div>
              )}

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

          {/* ⚠️ Eigene Zeile, unabhängig von "erreichbar": Das Recht ist eine
              reine Nexview-Einstellung, `PUT /api/settings/fassungen` fragt
              nexcrate nie. Ein unabhängiger Prüfer fand den Kasten vorher im
              Erreichbar-Zweig - genau bei einer Störung hätte der Betreiber
              eine offene Fassung dann nicht mehr schließen können. */}
          {modus === "nex" && rechteFassungen.length > 0 && (
            <FassungsRechte
              fassungen={rechteFassungen}
              offen={rechteOffen}
              onToggle={(kennung, wert) =>
                rechteMutation.mutate({ kennung, offen_fuer_alle: wert })
              }
              titel={t("setup.nexcrateVersionsTitle")}
              text={t("nexcrate.rightsText")}
              disabled={rechteMutation.isPending}
            />
          )}
        </Section>
      )}
    </div>
  );
}
