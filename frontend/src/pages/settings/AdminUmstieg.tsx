import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import type {
  UmstiegAbbildung,
  UmstiegBericht,
  UmstiegNachreichen,
  UmstiegProbe,
  UmstiegSicherung,
  UmstiegVorab,
} from "../../api/types";
import {
  NexcrateVerbinden,
  Standpruefung,
} from "../../components/NexcrateVerbinden";
import { Button, ErrorBanner, Section, Spinner } from "../../components/ui";

/** Die sieben Schritte in der Reihenfolge des Bauplans (7.3). */
const SCHRITTE = [
  "vorab",
  "verbinden",
  "abbildung",
  "probe",
  "sicherung",
  "umschalten",
  "danach",
] as const;
type Schritt = (typeof SCHRITTE)[number];

/**
 * Der Umstiegsassistent: von Radarr und Sonarr auf nexcrate (Bauplan 7.3).
 *
 * ⚠️ **Es gibt keinen Rückweg außer der Sicherung.** Sie steht deshalb als
 * eigener Schritt vor dem Umschalten, und der Server lässt das Umschalten erst
 * zu, wenn die Datei wirklich liegt - eine Zusage der Oberfläche zählt dort
 * nicht.
 *
 * ⚠️ **Jeder Schritt hängt am vorigen.** Die Abbildung braucht die Fassungen
 * aus nexcrate, die Probe braucht die Abbildung, das Umschalten braucht die
 * Probe. Wer hier einen Schritt überspringbar macht, macht den nächsten
 * falsch.
 */
export function AdminUmstieg() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [schritt, setSchritt] = useState<Schritt>("vorab");
  const [abbildung, setAbbildung] = useState<Record<string, string | null>>({});
  const [ergebnis, setErgebnis] = useState<UmstiegProbe | null>(null);
  const [trotzdem, setTrotzdem] = useState(false);
  const [sicherung, setSicherung] = useState<UmstiegSicherung | null>(null);
  const [bericht, setBericht] = useState<UmstiegBericht | null>(null);
  const [nachgereicht, setNachgereicht] = useState(0);
  const [fehler, setFehler] = useState<string | null>(null);

  const vorabQuery = useQuery({
    queryKey: ["umstieg", "vorab"],
    queryFn: () => api.get<UmstiegVorab>("/api/umstieg/vorab"),
    enabled: schritt === "vorab",
  });

  const abbildungQuery = useQuery({
    queryKey: ["umstieg", "abbildung"],
    queryFn: () => api.get<UmstiegAbbildung>("/api/umstieg/abbildung"),
    enabled: schritt === "verbinden" || schritt === "abbildung",
  });
  const vorlage = abbildungQuery.data;

  // Der Vorschlag füllt die Auswahl einmal; danach gehört sie dem Betreiber.
  useEffect(() => {
    if (!vorlage || Object.keys(abbildung).length > 0) return;
    setAbbildung(vorlage.vorschlag);
  }, [vorlage, abbildung]);

  const probe = useMutation({
    mutationFn: () => api.post<UmstiegProbe>("/api/umstieg/probe", { abbildung }),
    onSuccess: (antwort) => {
      setErgebnis(antwort);
      setFehler(null);
      if (antwort.fehler.length === 0) setSchritt("probe");
    },
    onError: (error: Error) => setFehler(error.message),
  });

  const sichern = useMutation({
    mutationFn: () => api.post<UmstiegSicherung>("/api/umstieg/sicherung"),
    onSuccess: (antwort) => {
      setSicherung(antwort);
      setFehler(null);
    },
    onError: (error: Error) => setFehler(error.message),
  });

  const umschalten = useMutation({
    mutationFn: () =>
      api.post<UmstiegBericht>("/api/umstieg/umschalten", {
        abbildung,
        sicherung: sicherung?.name,
        posten_ohne_gegenstueck_behalten: trotzdem,
      }),
    onSuccess: (antwort) => {
      setBericht(antwort);
      setFehler(null);
      setSchritt("danach");
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error: Error) => setFehler(error.message),
  });

  const nachreichen = useMutation({
    mutationFn: () => api.post<UmstiegNachreichen>("/api/umstieg/nachreichen"),
    onSuccess: (antwort) => {
      setNachgereicht((alt) => alt + antwort.gereicht);
      setFehler(null);
    },
    onError: (error: Error) => setFehler(error.message),
  });

  const sperrt = Boolean(vorlage?.sperrt);
  const nummer = SCHRITTE.indexOf(schritt) + 1;

  return (
    <div className="mt-6 flex flex-col gap-6">
      {fehler && <ErrorBanner message={fehler} />}

      <p className="text-xs uppercase tracking-wide text-mist-500">
        {t("umstieg.stepOf", { nummer, gesamt: SCHRITTE.length })}
      </p>

      {schritt === "vorab" && (
        <Section title={t("umstieg.vorabTitle")}>
          {/* ⚠️ Der Satz steht vor den Zahlen, nicht darunter. */}
          <p className="max-w-3xl rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
            {t("umstieg.noWayBack")}
          </p>
          {vorabQuery.isLoading && <Spinner />}
          {vorabQuery.data && (
            <ul className="flex flex-col gap-1 text-sm text-mist-300">
              <li>
                {t("umstieg.running", {
                  count: vorabQuery.data.downloads_laufend,
                })}
              </li>
              <li>
                {t("umstieg.openRequests", {
                  count: vorabQuery.data.anfragen_offen,
                })}
              </li>
              <li>{t("umstieg.entries", { count: vorabQuery.data.posten })}</li>
              <li>
                {t("umstieg.instances", {
                  namen: vorabQuery.data.instanzen.join(", "),
                })}
              </li>
            </ul>
          )}
          <Button type="button" onClick={() => setSchritt("verbinden")}>
            {t("common.next")}
          </Button>
        </Section>
      )}

      {schritt === "verbinden" && (
        <Section title={t("umstieg.connectTitle")}>
          <p className="max-w-3xl text-sm text-mist-400">
            {t("umstieg.connectText")}
          </p>
          <NexcrateVerbinden
            onVerbunden={() => void abbildungQuery.refetch()}
          />
          {abbildungQuery.isFetching && <Spinner />}
          {/* ⚠️ Ohne das zeigt ein Fehler hier gar nichts an - nur einen
              Knopf, der nie aufgeht, und niemand weiß warum. */}
          {abbildungQuery.error && <ErrorBanner message={abbildungQuery.error.message} />}
          {vorlage && <Standpruefung befunde={vorlage.pruefung} />}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => setSchritt("abbildung")}
              disabled={
                !vorlage || sperrt || vorlage.nex_fassungen.length === 0
              }
            >
              {t("common.next")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setSchritt("vorab")}
            >
              {t("common.back")}
            </Button>
          </div>
        </Section>
      )}

      {schritt === "abbildung" && vorlage && (
        <Section title={t("umstieg.mappingTitle")}>
          <p className="max-w-3xl text-sm text-mist-400">
            {t("umstieg.mappingText")}
          </p>
          {/* ⚠️ **Ein leerer Vorschlag ist kein Fehler, sieht aber wie einer
              aus.** Vorgeschlagen wird nach Klasse; eine nexcrate, deren
              Profile keine Auflösung festlegen, nennt keine (`tier: null`).
              Dann stünden vier leere Auswahllisten da, und niemand wüsste
              warum. Gefunden im Durchlauf gegen eine echte nexcrate. */}
          {Object.values(vorlage.vorschlag).every((ziel) => !ziel) && (
            <p className="max-w-3xl rounded-xl border border-ink-700 px-4 py-3 text-sm text-mist-300">
              {t("umstieg.mappingNoSuggestion")}
            </p>
          )}
          <ul className="flex flex-col gap-3">
            {vorlage.arr_fassungen.map((arr) => (
              <li
                key={arr.kennung}
                className="flex flex-wrap items-center gap-3 text-sm"
              >
                <label
                  htmlFor={`ziel-${arr.kennung}`}
                  className="min-w-48 text-mist-200"
                >
                  {arr.name}
                </label>
                <select
                  id={`ziel-${arr.kennung}`}
                  className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-mist-100"
                  value={abbildung[arr.kennung] ?? ""}
                  onChange={(event) =>
                    setAbbildung((alt) => ({
                      ...alt,
                      [arr.kennung]: event.target.value || null,
                    }))
                  }
                >
                  <option value="">{t("umstieg.mappingNone")}</option>
                  {vorlage.nex_fassungen
                    .filter((nex) => nex.media_type === arr.media_type)
                    .map((nex) => (
                      <option key={nex.kennung} value={nex.kennung}>
                        {nex.name}
                      </option>
                    ))}
                </select>
              </li>
            ))}
          </ul>
          {ergebnis?.fehler.map((code) => (
            <p key={code} className="text-sm text-bad-500">
              {t(`umstieg.error.${code}`, { defaultValue: code })}
            </p>
          ))}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => probe.mutate()}
              loading={probe.isPending}
            >
              {t("umstieg.check")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setSchritt("verbinden")}
            >
              {t("common.back")}
            </Button>
          </div>
        </Section>
      )}

      {schritt === "probe" && ergebnis && (
        <Section title={t("umstieg.probeTitle")}>
          <ul className="flex flex-col gap-1 text-sm text-mist-300">
            <li>{t("umstieg.probeKnown", { count: ergebnis.bekannt })}</li>
            <li>
              {t("umstieg.probeWithoutVersion", {
                count: ergebnis.ohne_fassung,
              })}
            </li>
            <li>{t("umstieg.probeUnknown", { count: ergebnis.unbekannt })}</li>
            {ergebnis.anime_offen > 0 && (
              <li>
                {t("umstieg.probeAnime", { count: ergebnis.anime_offen })}
              </li>
            )}
          </ul>
          <p className="max-w-3xl text-sm text-mist-400">
            {t("umstieg.probeOpenRequests")}
          </p>

          {ergebnis.zu_entscheiden.length > 0 && (
            <div className="rounded-xl border border-ink-700 p-4">
              <p className="font-medium text-mist-100">
                {t("umstieg.decideTitle")}
              </p>
              <p className="mt-1 max-w-3xl text-sm text-mist-400">
                {t("umstieg.decideText")}
              </p>
              <ul className="mt-3 flex flex-col gap-1 text-sm text-mist-300">
                {ergebnis.zu_entscheiden.slice(0, 50).map((zeile) => (
                  <li key={`${zeile.media_type}:${zeile.tmdb_id}`}>
                    {zeile.titel || zeile.tmdb_id}
                    {zeile.ohne_uebersetzung &&
                      ` — ${t("umstieg.decideNoTranslation")}`}
                  </li>
                ))}
              </ul>
              <label className="mt-3 flex items-center gap-2 text-sm text-mist-200">
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0 accent-accent-500"
                  checked={trotzdem}
                  onChange={(event) => setTrotzdem(event.target.checked)}
                />
                {t("umstieg.decideKeep")}
              </label>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => setSchritt("sicherung")}
              disabled={ergebnis.zu_entscheiden.length > 0 && !trotzdem}
            >
              {t("common.next")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setSchritt("abbildung")}
            >
              {t("common.back")}
            </Button>
          </div>
        </Section>
      )}

      {schritt === "sicherung" && (
        <Section title={t("umstieg.backupTitle")}>
          <p className="max-w-3xl text-sm text-mist-400">
            {t("umstieg.backupText")}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => sichern.mutate()}
              loading={sichern.isPending}
            >
              {t("umstieg.backupCreate")}
            </Button>
            {sicherung && (
              <span className="text-sm text-green-400">
                {t("umstieg.backupDone", { name: sicherung.name })}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => setSchritt("umschalten")}
              disabled={!sicherung}
            >
              {t("common.next")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setSchritt("probe")}
            >
              {t("common.back")}
            </Button>
          </div>
        </Section>
      )}

      {schritt === "umschalten" && (
        <Section title={t("umstieg.switchTitle")}>
          <p className="max-w-3xl rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm text-bad-500">
            {t("umstieg.switchWarning")}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => umschalten.mutate()}
              loading={umschalten.isPending}
            >
              {t("umstieg.switchNow")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setSchritt("sicherung")}
            >
              {t("common.back")}
            </Button>
          </div>
        </Section>
      )}

      {schritt === "danach" && bericht && (
        <Section title={t("umstieg.doneTitle")}>
          <ul className="flex flex-col gap-1 text-sm text-mist-300">
            <li>{t("umstieg.doneVersions", { count: bericht.fassungen })}</li>
            <li>{t("umstieg.doneRequests", { count: bericht.anfragen })}</li>
            <li>{t("umstieg.doneEntries", { count: bericht.posten })}</li>
            <li>{t("umstieg.doneRights", { count: bericht.rechte })}</li>
            <li>{t("umstieg.doneRules", { count: bericht.regeln })}</li>
            {bericht.posten_ohne_uebersetzung > 0 && (
              <li className="text-amber-300">
                {t("umstieg.doneWithoutTranslation", {
                  count: bericht.posten_ohne_uebersetzung,
                })}
              </li>
            )}
          </ul>
          {bericht.verlassen.length > 0 && (
            <ul className="flex flex-col gap-1 text-sm text-mist-400">
              {bericht.verlassen.map((zeile) => (
                <li key={zeile}>{zeile}</li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => nachreichen.mutate()}
              loading={nachreichen.isPending}
            >
              {t("umstieg.handOver")}
            </Button>
            <span className="text-sm text-mist-300">
              {t("umstieg.handedOver", { count: nachgereicht })}
            </span>
          </div>
          {nachreichen.data?.weiter && (
            <p className="text-sm text-mist-400">{t("umstieg.handOverMore")}</p>
          )}
        </Section>
      )}
    </div>
  );
}
