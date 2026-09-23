import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type { PapierkorbEintrag, PapierkorbListe } from "../../api/types";
import { Button, ErrorBanner, Section, Spinner } from "../../components/ui";

/** Bytes als Zeile, die ein Mensch liest. */
function groesse(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

/**
 * Der Papierkorb des Beschaffungswegs – eine Liste, kein Ordner.
 *
 * ⚠️ **Nicht dasselbe wie `AdminPapierkorb`.** Radarr und Sonarr haben einen
 * Papierkorb-**Ordner**, den der Betreiber einstellt und den Nexview nur
 * durchsucht; daraus holt niemand etwas zurück. Hier führt der Weg eine Liste
 * und nimmt eine Datei auf Wunsch wieder an.
 *
 * ⚠️ **Zwei verschiedene Nein.** Eine Datei kann weg sein (oder ihre Platte
 * gerade nicht sichtbar), oder ihr Titel hat den Bestand verlassen – dann
 * führt Zurückholen zu nichts. Beide Fälle stehen an der Zeile, und der Knopf
 * ist zu. Ein Knopf, der das verschweigt, verspricht etwas, das nicht eintritt.
 */
export function AdminPapierkorbNex() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const liste = useQuery({
    queryKey: ["beschaffung", "papierkorb"],
    queryFn: () => api.get<PapierkorbListe>("/api/beschaffung/papierkorb"),
  });

  const zurueck = useMutation({
    mutationFn: (eintrag: number) =>
      api.post(`/api/beschaffung/papierkorb/${eintrag}/zurueckholen`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["beschaffung", "papierkorb"] });
      // Der Bestand hat sich geändert – die Kontingente darüber rechnen neu.
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
  });

  const eintraege = liste.data?.eintraege ?? [];

  return (
    <Section title={t("papierkorbNex.title")}>
      <p className="max-w-3xl text-sm text-mist-400">{t("papierkorbNex.intro")}</p>

      {liste.isLoading && <Spinner />}
      {liste.error && <ErrorBanner message={liste.error.message} />}
      {zurueck.error && <ErrorBanner message={zurueck.error.message} />}

      {liste.isSuccess && eintraege.length === 0 && (
        <p className="text-sm text-mist-500">{t("papierkorbNex.empty")}</p>
      )}

      {eintraege.length > 0 && (
        <ul className="flex flex-col gap-2">
          {eintraege.map((eintrag) => (
            <Zeile
              key={eintrag.eintrag_id}
              eintrag={eintrag}
              laeuft={zurueck.isPending && zurueck.variables === eintrag.eintrag_id}
              onZurueck={() => zurueck.mutate(eintrag.eintrag_id)}
            />
          ))}
        </ul>
      )}

      {liste.data?.sprung && (
        <a
          className="text-sm text-accent-400 hover:underline"
          href={liste.data.sprung}
          target="_blank"
          rel="noreferrer"
        >
          {t("papierkorbNex.open")}
        </a>
      )}
    </Section>
  );
}

function Zeile({
  eintrag,
  laeuft,
  onZurueck,
}: {
  eintrag: PapierkorbEintrag;
  laeuft: boolean;
  onZurueck: () => void;
}) {
  const { t } = useTranslation();
  // Die Reihenfolge ist Absicht: „Titel weg" wiegt schwerer als „Datei weg",
  // denn daran kann auch ein Betreiber nichts ändern, ohne ihn neu anzulegen.
  const grund = !eintrag.im_bestand
    ? t("papierkorbNex.gone")
    : !eintrag.datei_da
      ? t("papierkorbNex.fileGone")
      : null;

  return (
    <li className="flex flex-wrap items-center gap-3 rounded-lg border border-ink-700 px-3 py-2 text-sm">
      <span className="font-medium text-mist-100">
        {eintrag.name ?? eintrag.dateiname}
        {eintrag.jahr ? ` (${eintrag.jahr})` : ""}
      </span>
      {eintrag.staffel !== null && (
        <span className="text-mist-400">
          {t("papierkorbNex.season", {
            staffel: eintrag.staffel,
            folgen: eintrag.folgen.join(", "),
          })}
        </span>
      )}
      <span className="text-mist-500">{groesse(eintrag.size_bytes)}</span>
      <span className="text-mist-500">
        {new Date(eintrag.geloescht_am).toLocaleDateString()}
      </span>
      <span className="text-mist-500">
        {eintrag.geloescht_von_name ?? t(`papierkorbNex.by.${eintrag.geloescht_von}`, {
          defaultValue: eintrag.geloescht_von,
        })}
      </span>
      <span className="ml-auto flex items-center gap-2">
        {grund && <span className="text-amber-300">{grund}</span>}
        <Button
          type="button"
          variant="ghost"
          onClick={onZurueck}
          loading={laeuft}
          disabled={!eintrag.datei_da || !eintrag.im_bestand}
        >
          {t("papierkorbNex.restore")}
        </Button>
      </span>
    </li>
  );
}
