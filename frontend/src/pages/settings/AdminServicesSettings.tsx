import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import type {
  AppSettings,
  ArrOptions,
  RootFolderMode,
  TestResult,
} from "../../api/types";
import { REGION_OPTIONS } from "../../components/media/optionen";
import { Button, Card, ErrorBanner, Field, Spinner } from "../../components/ui";
import { AdminMediaServerSettings } from "./AdminMediaServerSettings";

type TestService = "tmdb" | "radarr" | "sonarr" | "radarr_uhd" | "sonarr_uhd";

/**
 * Ein Knopf je Dienst statt einer langen Liste.
 *
 * Untereinander waren das fünf Blöcke, in denen man das Gesuchte nur noch
 * durch Scrollen fand. "Allgemein" steht voran: Region, Sprache und
 * Beispieldaten gehören zu keinem einzelnen Dienst.
 */
type UnterTab = "general" | "tmdb" | "radarr" | "sonarr" | "plex";

const UNTER_TABS: { value: UnterTab; labelKey: string }[] = [
  { value: "general", labelKey: "settings.generalSection" },
  { value: "tmdb", labelKey: "settings.tmdbSection" },
  { value: "radarr", labelKey: "settings.radarrSection" },
  { value: "sonarr", labelKey: "settings.sonarrSection" },
  { value: "plex", labelKey: "mediaserver.adminTitle" },
];

type Draft = {
  tmdb_api_key: string;
  radarr_url: string;
  radarr_api_key: string;
  sonarr_url: string;
  sonarr_api_key: string;
  default_region: string;
  default_language: string;
  demo_mode: "auto" | "on" | "off";
  /** Leerer String = kein Standardprofil. */
  default_movie_profile_id: string;
  default_series_profile_id: string;
  /**
   * Dürfen Benutzer den Zielordner selbst wählen? Je Dienst getrennt – Filme
   * und Serien haben unterschiedliche Ordnerstrukturen, und wer bei Serien
   * feste Pfade will, muss das nicht auch bei Filmen wollen.
   */
  movie_root_folder_mode: RootFolderMode;
  series_root_folder_mode: RootFolderMode;
  movie_profile_mode: RootFolderMode;
  series_profile_mode: RootFolderMode;
  default_movie_root: string;
  default_series_root: string;
  /** Zweite Instanz für 4K – leer heißt: gibt es nicht. */
  radarr_uhd_url: string;
  radarr_uhd_api_key: string;
  sonarr_uhd_url: string;
  sonarr_uhd_api_key: string;
  default_movie_uhd_profile_id: string;
  default_series_uhd_profile_id: string;
  default_movie_uhd_root: string;
  default_series_uhd_root: string;
};

const EMPTY_DRAFT: Draft = {
  tmdb_api_key: "",
  radarr_url: "",
  radarr_api_key: "",
  sonarr_url: "",
  sonarr_api_key: "",
  default_region: "DE",
  default_language: "de",
  demo_mode: "auto",
  default_movie_profile_id: "",
  default_series_profile_id: "",
  movie_root_folder_mode: "user",
  series_root_folder_mode: "user",
  movie_profile_mode: "user",
  series_profile_mode: "user",
  default_movie_root: "",
  default_series_root: "",
  radarr_uhd_url: "",
  radarr_uhd_api_key: "",
  sonarr_uhd_url: "",
  sonarr_uhd_api_key: "",
  default_movie_uhd_profile_id: "",
  default_series_uhd_profile_id: "",
  default_movie_uhd_root: "",
  default_series_uhd_root: "",
};

/**
 * Eine Instanz als eigener Block - Standard oder 4K.
 *
 * Nebeneinander, sobald der Platz reicht: So sieht man auf einen Blick, dass
 * es zwei getrennte Server sind. Vorher war 4K ein Aufklapper *innerhalb* des
 * Standard-Blocks, was die Rangfolge falsch darstellte.
 */
function InstanzBlock({
  titel,
  hinweis,
  children,
}: {
  titel: string;
  hinweis?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900/40 p-4">
      <div>
        <p className="text-sm font-medium text-mist-300">{titel}</p>
        {hinweis && (
          <p className="mt-0.5 text-xs leading-relaxed text-mist-600">
            {hinweis}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}

function ZielordnerBlock({
  zweiInstanzen,
  children,
}: {
  zweiInstanzen: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900/40 p-4">
      <div>
        <p className="text-sm font-medium text-mist-300">
          {t("settings.targetSection")}
        </p>
        {zweiInstanzen && (
          <p className="mt-0.5 text-xs leading-relaxed text-mist-600">
            {t("settings.targetSectionHint")}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}

/**
 * Eine Karte je Themenblock.
 *
 * Die Karte selbst ist immer gleich breit - alles andere saehe zerrissen aus.
 * Begrenzt wird der *Inhalt*: Einzelne Eingabefelder ueber die volle
 * Bildschirmbreite liest niemand gern. Nur die Dienste brauchen den Platz,
 * weil dort zwei Instanzen nebeneinanderstehen.
 */
function Section({
  title,
  breit = false,
  children,
}: {
  title: string;
  breit?: boolean;
  children: ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className={"flex flex-col gap-4" + (breit ? "" : " max-w-3xl")}>
        {children}
      </div>
    </Card>
  );
}

/**
 * Darf der Benutzer den Zielordner selbst wählen?
 *
 * Der Zielordner ist die einzige Auswahl beim Anfragen, die etwas über die
 * Ablage auf dem Server verrät. Wer das nicht jedem zumuten will, schaltet sie
 * ab; dann erscheint direkt darunter der feste Ordner.
 *
 * Steht bewusst bei Radarr bzw. Sonarr und nicht mehr im Allgemein-Block:
 * Filme und Serien haben unterschiedliche Ordnerstrukturen.
 */
/**
 * Wer waehlt den Zielordner? Eine Frage je Dienst, drei Antworten.
 *
 * Vorher waren das zwei Ja/Nein-Schalter an zwei Stellen - einer hier, einer
 * allgemein -, die beide dasselbe Feld steuerten und einander widersprechen
 * konnten. Wer "der Entscheider waehlt" gesetzt hatte, sah daneben weiter das
 * Haekchen "Benutzer duerfen waehlen", das dann wirkungslos war.
 */
function WerWaehlt({
  was,
  gruppe,
  wert,
  partner,
  onChange,
}: {
  /** "rootFolder" oder "profile" - bestimmt nur die Texte. */
  was: "rootFolder" | "profile";
  /** Eindeutig je Dienst, sonst teilen sich die Radios eine Gruppe. */
  gruppe: string;
  wert: RootFolderMode;
  /** Der Stand des jeweils anderen Feldes - beide hängen zusammen. */
  partner: RootFolderMode;
  onChange: (wert: RootFolderMode) => void;
}) {
  const { t } = useTranslation();
  // Beide stehen gemeinsam auf „Entscheider"? Dann ist das erklärungsbedürftig:
  // Wer nur eines davon gesetzt hat, findet hier das andere mit umgestellt.
  const gekoppelt = wert === "approver" && partner === "approver";

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-sm font-medium text-mist-300">
        {t(`settings.${was}Mode`)}
      </legend>
      {(["user", "fixed", "approver"] as const).map((modus) => (
        <label key={modus} className="flex cursor-pointer items-start gap-3">
          <input
            type="radio"
            name={`${was}-mode-${gruppe}`}
            checked={wert === modus}
            onChange={() => onChange(modus)}
            className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
          />
          <span>
            <span className="text-sm text-mist-300">
              {t(`settings.${was}Mode_${modus}`)}
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
              {t(`settings.${was}ModeHint_${modus}`)}
            </span>
          </span>
        </label>
      ))}
      {gekoppelt && (
        <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
          {t("settings.targetPairCoupled")}
        </p>
      )}
    </fieldset>
  );
}

/**
 * Zielordner und Qualitaetsprofil gemeinsam umstellen.
 *
 * Die beiden haengen zusammen: Sobald **eines** von beiden der Entscheider
 * setzt, wartet die ganze Anfrage auf ihn, und er setzt dann auch das andere.
 * Das andere auf "der Benutzer waehlt" stehen zu lassen waere eine Einstellung
 * ohne jede Wirkung - genau das ist in einer echten Installation passiert.
 *
 * Beide ziehen deshalb gemeinsam um, **in beide Richtungen**: Zoege nur die
 * Hinrichtung, kaeme man aus "Entscheider" nie wieder heraus, weil jeder
 * Versuch vom Gegenstueck sofort wieder eingefangen wuerde. Der Server prueft
 * dasselbe noch einmal - siehe routers/settings.py.
 */
function zielPaar(
  neuerWert: RootFolderMode,
  bisherB: RootFolderMode,
  schluesselA: string,
  schluesselB: string,
): Record<string, RootFolderMode> {
  const aenderung: Record<string, RootFolderMode> = {
    [schluesselA]: neuerWert,
  };
  if (neuerWert === "approver") {
    aenderung[schluesselB] = "approver";
  } else if (bisherB === "approver") {
    aenderung[schluesselB] = neuerWert;
  }
  return aenderung;
}

function DefaultProfileField({
  mediaType,
  value,
  onChange,
  configured,
  tier = "standard",
  zusatz,
}: {
  mediaType: "movie" | "tv";
  value: string;
  onChange: (value: string) => void;
  configured: boolean;
  /** Welche Instanz? Die Profil-Kennungen beider Stufen kollidieren. */
  tier?: "standard" | "uhd";
  /** Steht hinter der Beschriftung - nennt die Instanz beim Namen. */
  zusatz?: string;
}) {
  const { t } = useTranslation();

  const optionsQuery = useQuery({
    queryKey: ["arr-options", mediaType, tier],
    queryFn: () =>
      api.get<ArrOptions>(`/api/arr/${mediaType}/options?tier=${tier}`),
    enabled: configured,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const profile = optionsQuery.data?.quality_profiles ?? [];

  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {t("settings.defaultProfile")}
        {zusatz && <span className="text-mist-600"> · {zusatz}</span>}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={!configured || optionsQuery.isPending || profile.length === 0}
        className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-60"
      >
        <option value="">{t("settings.defaultProfileNone")}</option>
        {profile.map((eintrag) => (
          <option key={eintrag.id} value={String(eintrag.id)}>
            {eintrag.name}
          </option>
        ))}
      </select>
      <span className="text-xs text-mist-600">
        {configured
          ? t("settings.defaultProfileHint")
          : t("settings.defaultProfileMissing")}
      </span>
    </label>
  );
}

/**
 * Standard-Zielordner - nur sichtbar, wenn die Auswahl abgeschaltet ist.
 *
 * Denn nur dann muss überhaupt jemand entscheiden, wohin geladen wird. Dürfen
 * die Benutzer selbst wählen, wäre das Feld nur eine Vorauswahl mehr, die man
 * erklären müsste.
 */
function DefaultRootField({
  mediaType,
  value,
  onChange,
  configured,
  tier = "standard",
  zusatz,
}: {
  mediaType: "movie" | "tv";
  value: string;
  onChange: (value: string) => void;
  configured: boolean;
  /** Welche Instanz? Die Profil-Kennungen beider Stufen kollidieren. */
  tier?: "standard" | "uhd";
  /** Steht hinter der Beschriftung - nennt die Instanz beim Namen. */
  zusatz?: string;
}) {
  const { t } = useTranslation();

  const optionsQuery = useQuery({
    queryKey: ["arr-options", mediaType, tier],
    queryFn: () =>
      api.get<ArrOptions>(`/api/arr/${mediaType}/options?tier=${tier}`),
    enabled: configured,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const ordner = optionsQuery.data?.root_folders ?? [];

  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {t("settings.defaultRoot")}
        {zusatz && <span className="text-mist-600"> · {zusatz}</span>}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={!configured || optionsQuery.isPending || ordner.length === 0}
        className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-60"
      >
        {/* Leer heißt: der erste Ordner aus Radarr/Sonarr. */}
        <option value="">{t("settings.defaultRootFirst")}</option>
        {ordner.map((eintrag) => (
          <option key={eintrag.path} value={eintrag.path}>
            {eintrag.path}
          </option>
        ))}
      </select>
      <span className="text-xs text-mist-600">
        {configured
          ? t("settings.defaultRootHint")
          : t("settings.defaultProfileMissing")}
      </span>
    </label>
  );
}

export function AdminServicesSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [unterTab, setUnterTab] = useState<UnterTab>("general");
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [testResults, setTestResults] = useState<
    Partial<Record<TestService, TestResult>>
  >({});

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/api/settings"),
  });

  /**
   * Nur die nicht-geheimen Werte vorbelegen - Key-Felder bleiben leer, damit
   * ein versehentliches Speichern den Key nicht überschreibt.
   *
   * Und nur **einmal**: die Abfragedaten kommen bei jedem Hintergrund-Abgleich
   * als neues Objekt zurück, sonst wird mitten im Tippen zurückgesetzt.
   */
  const vorbelegt = useRef(false);
  // Der Stand, wie er zuletzt vom Server kam. Daran haengt die Frage, ob
  // ueberhaupt etwas geaendert wurde - und ob eine Verbindung neu zu pruefen
  // ist, bevor gespeichert werden darf.
  const basis = useRef<Draft>(EMPTY_DRAFT);

  useEffect(() => {
    const data = settingsQuery.data;
    if (!data || vorbelegt.current) return;
    vorbelegt.current = true;
    const vorbelegung: Draft = {
      ...EMPTY_DRAFT,
      radarr_url: data.radarr_url,
      sonarr_url: data.sonarr_url,
      default_region: data.default_region,
      default_language: data.default_language,
      demo_mode: data.demo_mode,
      default_movie_profile_id: data.default_movie_profile_id?.toString() ?? "",
      default_series_profile_id:
        data.default_series_profile_id?.toString() ?? "",
      movie_root_folder_mode: data.movie_root_folder_mode,
      series_root_folder_mode: data.series_root_folder_mode,
      movie_profile_mode: data.movie_profile_mode,
      series_profile_mode: data.series_profile_mode,
      default_movie_root: data.default_movie_root,
      default_series_root: data.default_series_root,
      radarr_uhd_url: data.radarr_uhd_url,
      sonarr_uhd_url: data.sonarr_uhd_url,
      default_movie_uhd_profile_id:
        data.default_movie_uhd_profile_id === null
          ? ""
          : String(data.default_movie_uhd_profile_id),
      default_series_uhd_profile_id:
        data.default_series_uhd_profile_id === null
          ? ""
          : String(data.default_series_uhd_profile_id),
      default_movie_uhd_root: data.default_movie_uhd_root,
      default_series_uhd_root: data.default_series_uhd_root,
    };
    basis.current = vorbelegung;
    setDraft(vorbelegung);
  }, [settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: Partial<Draft>) =>
      api.put<AppSettings>("/api/settings", payload),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings"], data);
      // Startseite und Konfiguration neu holen: Region oder Demo-Modus
      // können sich geändert haben.
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void queryClient.invalidateQueries({ queryKey: ["discover"] });
      void queryClient.invalidateQueries({ queryKey: ["genres"] });
      // Qualitätsprofile und Zielordner kommen aus Radarr/Sonarr. Wer gerade
      // eine andere Adresse eingetragen hat, muss sofort die Listen der neuen
      // Instanz sehen - sonst stehen dort noch fünf Minuten lang die alten,
      // und man wählt ahnungslos ein Profil, das es dort gar nicht gibt.
      void queryClient.invalidateQueries({ queryKey: ["arr-options"] });
      setMessage({ ok: true, text: t("settings.saved") });
      setTestResults({});
      // Ab jetzt gilt das Gespeicherte als unveraendert - sonst bliebe der
      // Knopf aktiv, obwohl es nichts mehr zu speichern gibt.
      basis.current = {
        ...draft,
        tmdb_api_key: "",
        radarr_api_key: "",
        sonarr_api_key: "",
        radarr_uhd_api_key: "",
        sonarr_uhd_api_key: "",
      };
      setDraft((aktuell) => ({
        ...aktuell,
        tmdb_api_key: "",
        radarr_api_key: "",
        sonarr_api_key: "",
        radarr_uhd_api_key: "",
        sonarr_uhd_api_key: "",
      }));
    },
    onError: (error) =>
      setMessage({
        ok: false,
        text:
          error instanceof ApiError ? error.message : t("settings.saveFailed"),
      }),
  });

  const removeKeyMutation = useMutation({
    mutationFn: (name: `${TestService}_api_key`) =>
      api.delete<AppSettings>(`/api/settings/secret/${name}`),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings"], data);
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void queryClient.invalidateQueries({ queryKey: ["discover"] });
      void queryClient.invalidateQueries({ queryKey: ["genres"] });
      // Qualitätsprofile und Zielordner kommen aus Radarr/Sonarr. Wer gerade
      // eine andere Adresse eingetragen hat, muss sofort die Listen der neuen
      // Instanz sehen - sonst stehen dort noch fünf Minuten lang die alten,
      // und man wählt ahnungslos ein Profil, das es dort gar nicht gibt.
      void queryClient.invalidateQueries({ queryKey: ["arr-options"] });
      setTestResults({});
      setMessage({ ok: true, text: t("settings.keyRemoved") });
    },
  });

  const testMutation = useMutation({
    mutationFn: (service: TestService) =>
      api.post<TestResult>(`/api/settings/test/${service}`, {
        api_key: draft[`${service}_api_key`] || undefined,
        url:
          service === "tmdb" ? undefined : draft[`${service}_url`] || undefined,
      }),
    onSuccess: (result, service) =>
      setTestResults((current) => ({ ...current, [service]: result })),
    onError: (error, service) =>
      setTestResults((current) => ({
        ...current,
        [service]: {
          ok: false,
          message:
            error instanceof ApiError ? error.message : t("errors.generic"),
        },
      })),
  });

  /**
   * Wurde die Verbindung dieses Dienstes angefasst?
   *
   * Entweder steht eine andere Adresse da, oder es wurde ein neuer Key
   * getippt (das Feld ist sonst leer - ein gespeicherter Key wird nie
   * zurueckgeschickt).
   */
  function verbindungGeaendert(dienst: TestService): boolean {
    const urlFeld = `${dienst}_url` as keyof Draft;
    const keyFeld = `${dienst}_api_key` as keyof Draft;
    const neueAdresse =
      urlFeld in draft && draft[urlFeld] !== basis.current[urlFeld];
    return Boolean(neueAdresse || String(draft[keyFeld] ?? "").trim());
  }

  const ALLE_DIENSTE: TestService[] = [
    "tmdb",
    "radarr",
    "radarr_uhd",
    "sonarr",
    "sonarr_uhd",
  ];
  // Wer eine Adresse oder einen Key aendert, muss erst pruefen, ob die
  // Verbindung ueberhaupt steht. Sonst speichert man eine tote Adresse und
  // merkt es erst, wenn die erste Anfrage ins Leere laeuft.
  const ungetestet = ALLE_DIENSTE.filter(
    (dienst) => verbindungGeaendert(dienst) && testResults[dienst]?.ok !== true,
  );
  const etwasGeaendert =
    JSON.stringify(draft) !== JSON.stringify(basis.current);
  const speichernGesperrt = !etwasGeaendert || ungetestet.length > 0;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    saveMutation.mutate(draft);
  }

  const update = (patch: Partial<Draft>) =>
    setDraft((current) => ({ ...current, ...patch }));
  const settings = settingsQuery.data;

  /**
   * Zeile mit "Verbindung testen" und - falls ein Key hinterlegt ist -
   * "Key entfernen". Bewusst eine Funktion und keine eigene Komponente:
   * sonst würde React die Knöpfe bei jedem Tastendruck neu einhängen.
   */
  const testRow = (service: TestService) => {
    const result = testResults[service];
    const pending =
      testMutation.isPending && testMutation.variables === service;

    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          variant="ghost"
          onClick={() => testMutation.mutate(service)}
          loading={pending}
        >
          {t("settings.test")}
        </Button>
        {settings?.[`${service}_api_key_set`] && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => removeKeyMutation.mutate(`${service}_api_key`)}
            loading={removeKeyMutation.isPending}
          >
            {t("settings.removeKey")}
          </Button>
        )}
        {result && (
          <span
            className={
              "text-sm " + (result.ok ? "text-ok-500" : "text-bad-500")
            }
            role="status"
          >
            {result.message}
          </span>
        )}
      </div>
    );
  };

  if (settingsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t("common.loading")}
      </p>
    );
  }

  return (
    /* Volle Breite statt max-w-3xl: Die Dienste haben jetzt zwei Instanzen
       nebeneinander, und in einer schmalen Spalte stapeln die sich zu einer
       endlosen Liste. Die Lesbarkeit der einzelnen Felder regelt weiter das
       Raster darin, nicht die Seitenbreite. */
    <div>
      <p className="max-w-3xl text-sm text-mist-500">{t("settings.intro")}</p>

      {/* Zweite Reihe: ein Knopf je Dienst.
          
          ⚠️ Hier stand einmal „bewusst optisch leichter als die Reiter
          darüber" – und dazu ein eckigeres `rounded-lg`. Die Absicht war
          nachvollziehbar, nur hielt sich keine andere zweite Reihe daran:
          Benachrichtigungen, Protokoll und die Merklisten-Quelle sind alle
          rund. Damit war nicht mehr die Ebene erkennbar, sondern nur diese
          eine Seite anders. Die Ebenen trennen jetzt Größe und Farbe – die
          Form ist überall dieselbe. */}
      {/* Der Schlüssel passt nicht mehr zu den gespeicherten Zugangsdaten -
          die Folgen ("Verbindung weg", Demo-Daten) sehen aus wie viele andere
          Fehler, deshalb steht die Ursache hier deutlich und ganz oben. */}
      {settings?.secrets_unreadable && (
        <p className="mt-5 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
          {t('settings.secretsUnreadable')}
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-2" role="tablist">
        {UNTER_TABS.map((eintrag) => (
          <button
            key={eintrag.value}
            type="button"
            role="tab"
            aria-selected={unterTab === eintrag.value}
            onClick={() => setUnterTab(eintrag.value)}
            className={
              "rounded-full border px-3.5 py-1.5 text-sm transition " +
              (unterTab === eintrag.value
                ? "border-accent-500 bg-accent-500/10 text-accent-400"
                : "border-ink-700 text-mist-500 hover:border-ink-600 hover:text-mist-300")
            }
          >
            {t(eintrag.labelKey)}
          </button>
        ))}
      </div>

      {/* Der Media-Server bringt eigenes Speichern und eigene Abläufe mit -
          er steht deshalb außerhalb dieses Formulars. */}
      {unterTab === "plex" && (
        <div className="mt-6">
          <AdminMediaServerSettings />
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className={
          "mt-6 flex-col gap-5 " + (unterTab === "plex" ? "hidden" : "flex")
        }
      >
        {unterTab === "tmdb" && (
          <Section title={t("settings.tmdbSection")}>
            <Field
              label={t("settings.tmdbKey")}
              type="password"
              value={draft.tmdb_api_key}
              onChange={(event) => update({ tmdb_api_key: event.target.value })}
              placeholder={
                settings?.tmdb_api_key_set ? settings.tmdb_api_key : ""
              }
              autoComplete="off"
              hint={
                settings?.tmdb_api_key_set
                  ? t("settings.keySetHint")
                  : t("settings.tmdbHint")
              }
            />

            {testRow("tmdb")}
          </Section>
        )}

        {unterTab === "general" && (
          <Section title={t("settings.generalSection")}>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-mist-300">
                  {t("settings.region")}
                </span>
                <select
                  value={draft.default_region}
                  onChange={(event) =>
                    update({ default_region: event.target.value })
                  }
                  className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
                >
                  {REGION_OPTIONS.map((region) => (
                    <option key={region} value={region}>
                      {region}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-mist-300">
                  {t("settings.language")}
                </span>
                <select
                  value={draft.default_language}
                  onChange={(event) =>
                    update({ default_language: event.target.value })
                  }
                  className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
                >
                  <option value="de">Deutsch</option>
                  <option value="en">English</option>
                </select>
                <span className="text-xs leading-relaxed text-mist-600">
                  {t("settings.languageHint")}
                </span>
              </label>
            </div>

            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-mist-300">
                {t("settings.demoMode")}
              </span>
              <select
                value={draft.demo_mode}
                onChange={(event) =>
                  update({
                    demo_mode: event.target.value as Draft["demo_mode"],
                  })
                }
                className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                <option value="auto">{t("settings.demoAuto")}</option>
                <option value="on">{t("settings.demoOn")}</option>
                <option value="off">{t("settings.demoOff")}</option>
              </select>
            </label>
          </Section>
        )}

        {unterTab === "radarr" && (
          <Section title={t("settings.radarrSection")} breit>
            {/* Oben die Instanzen als eigene Bloecke, nebeneinander sobald Platz
              ist. Darunter, was fuer beide gilt. Vorher stand die gemeinsame
              Regel mitten im Standard-Block und sah aus, als betraefe sie nur
              die eine Instanz. */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <InstanzBlock
                titel={t("settings.instanceStandard")}
                hinweis={t("settings.instanceStandardHint")}
              >
                <Field
                  label={t("settings.url")}
                  value={draft.radarr_url}
                  onChange={(event) =>
                    update({ radarr_url: event.target.value })
                  }
                  placeholder="http://192.168.1.10:7878"
                  autoComplete="off"
                />
                <Field
                  label={t("settings.apiKey")}
                  type="password"
                  value={draft.radarr_api_key}
                  onChange={(event) =>
                    update({ radarr_api_key: event.target.value })
                  }
                  placeholder={
                    settings?.radarr_api_key_set ? settings.radarr_api_key : ""
                  }
                  hint={
                    settings?.radarr_api_key_set
                      ? t("settings.keySetHint")
                      : t("settings.arrHint")
                  }
                  autoComplete="off"
                />
                {testRow("radarr")}
              </InstanzBlock>

              <InstanzBlock
                titel={t("uhd.section")}
                hinweis={t("uhd.sectionHint")}
              >
                <Field
                  label={t("settings.url")}
                  value={draft.radarr_uhd_url}
                  onChange={(event) =>
                    update({ radarr_uhd_url: event.target.value })
                  }
                  placeholder="http://192.168.1.10:7879"
                  autoComplete="off"
                />
                <Field
                  label={t("settings.apiKey")}
                  type="password"
                  value={draft.radarr_uhd_api_key}
                  onChange={(event) =>
                    update({ radarr_uhd_api_key: event.target.value })
                  }
                  placeholder={
                    settings?.radarr_uhd_api_key_set
                      ? settings.radarr_uhd_api_key
                      : ""
                  }
                  hint={
                    settings?.radarr_uhd_api_key_set
                      ? t("settings.keySetHint")
                      : undefined
                  }
                  autoComplete="off"
                />
                {testRow("radarr_uhd")}
              </InstanzBlock>
            </div>

            {/* Erst sinnvoll, wenn ueberhaupt eine Instanz steht: Profile und
              Ordner kommen ja von dort. */}
            {(settings?.radarr_api_key_set ||
              settings?.radarr_uhd_api_key_set) && (
              <ZielordnerBlock
                zweiInstanzen={Boolean(settings?.radarr_uhd_api_key_set)}
              >
                <WerWaehlt
                  was="profile"
                  gruppe="movie"
                  wert={draft.movie_profile_mode}
                  partner={draft.movie_root_folder_mode}
                  onChange={(value) =>
                    update(
                      zielPaar(
                        value,
                        draft.movie_root_folder_mode,
                        "movie_profile_mode",
                        "movie_root_folder_mode",
                      ),
                    )
                  }
                />
                {draft.movie_profile_mode === "fixed" && (
                  <DefaultProfileField
                    mediaType="movie"
                    zusatz={
                      settings?.radarr_uhd_api_key_set
                        ? t("settings.instanceStandard")
                        : undefined
                    }
                    value={draft.default_movie_profile_id}
                    onChange={(value) =>
                      update({ default_movie_profile_id: value })
                    }
                    configured={settings?.radarr_api_key_set ?? false}
                  />
                )}
                {draft.movie_profile_mode === "fixed" &&
                  settings?.radarr_uhd_api_key_set && (
                    <DefaultProfileField
                      mediaType="movie"
                      tier="uhd"
                      zusatz={t("uhd.tierUhd")}
                      value={draft.default_movie_uhd_profile_id}
                      onChange={(value) =>
                        update({ default_movie_uhd_profile_id: value })
                      }
                      configured
                    />
                  )}

                <div className="border-t border-ink-700 pt-4">
                  <WerWaehlt
                    was="rootFolder"
                    gruppe="movie"
                    wert={draft.movie_root_folder_mode}
                    partner={draft.movie_profile_mode}
                    onChange={(value) =>
                      update(
                        zielPaar(
                          value,
                          draft.movie_profile_mode,
                          "movie_root_folder_mode",
                          "movie_profile_mode",
                        ),
                      )
                    }
                  />
                </div>
                {draft.movie_root_folder_mode === "fixed" && (
                  <DefaultRootField
                    mediaType="movie"
                    zusatz={
                      settings?.radarr_uhd_api_key_set
                        ? t("settings.instanceStandard")
                        : undefined
                    }
                    value={draft.default_movie_root}
                    onChange={(value) => update({ default_movie_root: value })}
                    configured={settings?.radarr_api_key_set ?? false}
                  />
                )}
                {draft.movie_root_folder_mode === "fixed" &&
                  settings?.radarr_uhd_api_key_set && (
                    <DefaultRootField
                      mediaType="movie"
                      tier="uhd"
                      zusatz={t("uhd.tierUhd")}
                      value={draft.default_movie_uhd_root}
                      onChange={(value) =>
                        update({ default_movie_uhd_root: value })
                      }
                      configured
                    />
                  )}

                {/* Sobald eines von beidem beim Entscheider liegt, kann keine
                  Anfrage mehr automatisch durchgehen - sie waere unvollstaendig.
                  Das muss dastehen, sonst sucht der Administrator den Fehler
                  spaeter in der Benutzerverwaltung. */}
                {(draft.movie_profile_mode === "approver" ||
                  draft.movie_root_folder_mode === "approver") && (
                  <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
                    {t("settings.approverEndsAutoApprove")}
                  </p>
                )}
              </ZielordnerBlock>
            )}
          </Section>
        )}

        {unterTab === "sonarr" && (
          <Section title={t("settings.sonarrSection")} breit>
            {/* Oben die Instanzen als eigene Bloecke, nebeneinander sobald Platz
              ist. Darunter, was fuer beide gilt. Vorher stand die gemeinsame
              Regel mitten im Standard-Block und sah aus, als betraefe sie nur
              die eine Instanz. */}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <InstanzBlock
                titel={t("settings.instanceStandard")}
                hinweis={t("settings.instanceStandardHint")}
              >
                <Field
                  label={t("settings.url")}
                  value={draft.sonarr_url}
                  onChange={(event) =>
                    update({ sonarr_url: event.target.value })
                  }
                  placeholder="http://192.168.1.10:8989"
                  autoComplete="off"
                />
                <Field
                  label={t("settings.apiKey")}
                  type="password"
                  value={draft.sonarr_api_key}
                  onChange={(event) =>
                    update({ sonarr_api_key: event.target.value })
                  }
                  placeholder={
                    settings?.sonarr_api_key_set ? settings.sonarr_api_key : ""
                  }
                  hint={
                    settings?.sonarr_api_key_set
                      ? t("settings.keySetHint")
                      : t("settings.arrHint")
                  }
                  autoComplete="off"
                />
                {testRow("sonarr")}
              </InstanzBlock>

              <InstanzBlock
                titel={t("uhd.section")}
                hinweis={t("uhd.sectionHint")}
              >
                <Field
                  label={t("settings.url")}
                  value={draft.sonarr_uhd_url}
                  onChange={(event) =>
                    update({ sonarr_uhd_url: event.target.value })
                  }
                  placeholder="http://192.168.1.10:8990"
                  autoComplete="off"
                />
                <Field
                  label={t("settings.apiKey")}
                  type="password"
                  value={draft.sonarr_uhd_api_key}
                  onChange={(event) =>
                    update({ sonarr_uhd_api_key: event.target.value })
                  }
                  placeholder={
                    settings?.sonarr_uhd_api_key_set
                      ? settings.sonarr_uhd_api_key
                      : ""
                  }
                  hint={
                    settings?.sonarr_uhd_api_key_set
                      ? t("settings.keySetHint")
                      : undefined
                  }
                  autoComplete="off"
                />
                {testRow("sonarr_uhd")}
              </InstanzBlock>
            </div>

            {/* Erst sinnvoll, wenn ueberhaupt eine Instanz steht: Profile und
              Ordner kommen ja von dort. */}
            {(settings?.sonarr_api_key_set ||
              settings?.sonarr_uhd_api_key_set) && (
              <ZielordnerBlock
                zweiInstanzen={Boolean(settings?.sonarr_uhd_api_key_set)}
              >
                <WerWaehlt
                  was="profile"
                  gruppe="series"
                  wert={draft.series_profile_mode}
                  partner={draft.series_root_folder_mode}
                  onChange={(value) =>
                    update(
                      zielPaar(
                        value,
                        draft.series_root_folder_mode,
                        "series_profile_mode",
                        "series_root_folder_mode",
                      ),
                    )
                  }
                />
                {draft.series_profile_mode === "fixed" && (
                  <DefaultProfileField
                    mediaType="tv"
                    zusatz={
                      settings?.sonarr_uhd_api_key_set
                        ? t("settings.instanceStandard")
                        : undefined
                    }
                    value={draft.default_series_profile_id}
                    onChange={(value) =>
                      update({ default_series_profile_id: value })
                    }
                    configured={settings?.sonarr_api_key_set ?? false}
                  />
                )}
                {draft.series_profile_mode === "fixed" &&
                  settings?.sonarr_uhd_api_key_set && (
                    <DefaultProfileField
                      mediaType="tv"
                      tier="uhd"
                      zusatz={t("uhd.tierUhd")}
                      value={draft.default_series_uhd_profile_id}
                      onChange={(value) =>
                        update({ default_series_uhd_profile_id: value })
                      }
                      configured
                    />
                  )}

                <div className="border-t border-ink-700 pt-4">
                  <WerWaehlt
                    was="rootFolder"
                    gruppe="series"
                    wert={draft.series_root_folder_mode}
                    partner={draft.series_profile_mode}
                    onChange={(value) =>
                      update(
                        zielPaar(
                          value,
                          draft.series_profile_mode,
                          "series_root_folder_mode",
                          "series_profile_mode",
                        ),
                      )
                    }
                  />
                </div>
                {draft.series_root_folder_mode === "fixed" && (
                  <DefaultRootField
                    mediaType="tv"
                    zusatz={
                      settings?.sonarr_uhd_api_key_set
                        ? t("settings.instanceStandard")
                        : undefined
                    }
                    value={draft.default_series_root}
                    onChange={(value) => update({ default_series_root: value })}
                    configured={settings?.sonarr_api_key_set ?? false}
                  />
                )}
                {draft.series_root_folder_mode === "fixed" &&
                  settings?.sonarr_uhd_api_key_set && (
                    <DefaultRootField
                      mediaType="tv"
                      tier="uhd"
                      zusatz={t("uhd.tierUhd")}
                      value={draft.default_series_uhd_root}
                      onChange={(value) =>
                        update({ default_series_uhd_root: value })
                      }
                      configured
                    />
                  )}

                {/* Sobald eines von beidem beim Entscheider liegt, kann keine
                  Anfrage mehr automatisch durchgehen - sie waere unvollstaendig.
                  Das muss dastehen, sonst sucht der Administrator den Fehler
                  spaeter in der Benutzerverwaltung. */}
                {(draft.series_profile_mode === "approver" ||
                  draft.series_root_folder_mode === "approver") && (
                  <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
                    {t("settings.approverEndsAutoApprove")}
                  </p>
                )}
              </ZielordnerBlock>
            )}
          </Section>
        )}

        {message && !message.ok && <ErrorBanner message={message.text} />}
        {message?.ok && (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {message.text}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            loading={saveMutation.isPending}
            disabled={speichernGesperrt}
          >
            {t("common.save")}
          </Button>
          {/* Warum der Knopf gesperrt ist, muss dastehen - ein grauer Knopf
              ohne Begruendung ist die haeufigste Sackgasse in Formularen. */}
          {ungetestet.length > 0 ? (
            <span className="text-xs text-warn-500">
              {t("settings.testRequired", { dienste: ungetestet.join(", ") })}
            </span>
          ) : (
            !etwasGeaendert && (
              <span className="text-xs text-mist-600">
                {t("settings.nothingChanged")}
              </span>
            )
          )}
        </div>
      </form>
    </div>
  );
}
