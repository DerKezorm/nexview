import { useState } from "react";
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
 * ⚠️ **Der Knopf hängt an `restorable`, nicht an `datei_da`/`im_bestand`**
 * (#job-43). Eine Datei kann weg sein (oder ihre Platte gerade nicht
 * sichtbar) – dann geht nichts. Ihr Titel kann den Bestand verlassen haben
 * und sich trotzdem wieder anlegen lassen – dann bleibt der Knopf an, nur ein
 * Hinweis sagt es vorher. Erst wenn der Weg selbst „nein“ sagt (`restorable:
 * false`), ist wirklich nichts mehr zu holen, und der Knopf ist zu. Ein
 * Knopf, der das verschweigt, verspricht etwas, das nicht eintritt.
 */
export function AdminPapierkorbNex() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  /** Kurze Erfolgsmeldung nach einem Zurückholen, das den Titel neu angelegt hat. */
  const [meldung, setMeldung] = useState<string | null>(null);

  const liste = useQuery({
    queryKey: ["beschaffung", "papierkorb"],
    queryFn: () => api.get<PapierkorbListe>("/api/beschaffung/papierkorb"),
  });

  const zurueck = useMutation({
    mutationFn: (eintrag: number) =>
      api.post<{ created: boolean }>(`/api/beschaffung/papierkorb/${eintrag}/zurueckholen`),
    onSuccess: (antwort) => {
      // ⚠️ Mit `created: true` hat nexcrate den Titel neu angelegt - unüberwacht
      // (#job-43). Das ist kein gewöhnliches Zurückholen, und die Meldung sagt
      // es, statt stillschweigend so zu tun, als wäre nichts dabei passiert.
      setMeldung(antwort.created ? t("papierkorbNex.restoredUnmonitored") : null);
      void queryClient.invalidateQueries({ queryKey: ["beschaffung", "papierkorb"] });
      // Der Bestand hat sich geändert – die Kontingente darüber rechnen neu.
      void queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
    onError: () => setMeldung(null),
  });

  const eintraege = liste.data?.eintraege ?? [];

  return (
    <Section title={t("papierkorbNex.title")}>
      <p className="max-w-3xl text-sm text-mist-400">{t("papierkorbNex.intro")}</p>

      {liste.isLoading && <Spinner />}
      {liste.error && <ErrorBanner message={liste.error.message} />}
      {zurueck.error && <ErrorBanner message={zurueck.error.message} />}
      {meldung && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {meldung}
        </p>
      )}

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
  // ⚠️ Drei Fälle, nicht mehr zwei (#job-43). „Datei weg" und „lässt sich
  // nicht wieder anlegen" sperren den Knopf; „wird wieder angelegt" ist nur
  // ein Hinweis - der Knopf bleibt an, denn genau dafür legt nexcrate den
  // Titel beim Zurückholen neu an.
  const grund = !eintrag.datei_da
    ? t("papierkorbNex.fileGone")
    : !eintrag.restorable
      ? t("papierkorbNex.gone")
      : null;
  const hinweis = eintrag.restorable && !eintrag.im_bestand ? t("papierkorbNex.willReappear") : null;

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
        {!grund && hinweis && <span className="text-mist-400">{hinweis}</span>}
        <Button
          type="button"
          variant="ghost"
          onClick={onZurueck}
          loading={laeuft}
          disabled={!eintrag.restorable}
        >
          {t("papierkorbNex.restore")}
        </Button>
      </span>
    </li>
  );
}
