import { useEffect, useRef, useState } from "react";
import { Reiterreihe } from "../../components/Reiterreihe";
import { Symbol } from "../../components/Symbol";
import type { SymbolName } from "../../components/Symbol";
import { Section } from "../../components/ui";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import type {
  AppSettings,
  ArrOptions,
  GesundheitStand,
  RootFolderMode,
  TestResult,
  VerbindungStand,
  WebhookStand,
} from "../../api/types";
import {
  Button,
  ErrorBanner,
  Field,
  PlusKachel,
  RundKnopf,
  Spinner,
} from "../../components/ui";
import { AdminFolgenSettings } from "./AdminFolgenSettings";
import { AdminMediaServerSettings } from "./AdminMediaServerSettings";
import { AdminQualitaetsBereich } from "./AdminQualitaetsBereich";
import { InstanzGesundheit } from "./InstanzGesundheit";
import { DownloadKollision } from "./DownloadKollision";
import { WebhookZeile } from "./WebhookZeile";
import { useRegionen } from "../../hooks/useRegionen";
import { useConfig } from "../../hooks/useConfig";

type TestService = "tmdb" | "radarr" | "sonarr" | "radarr_uhd" | "sonarr_uhd";

/**
 * Ein Knopf je Dienst statt einer langen Liste.
 *
 * Untereinander waren das fünf Blöcke, in denen man das Gesuchte nur noch
 * durch Scrollen fand. "Allgemein" steht voran: Region, Sprache und
 * Beispieldaten gehören zu keinem einzelnen Dienst.
 */
type UnterTab = "general" | "tmdb" | "radarr" | "sonarr" | "plex" | "qualitaet";

const UNTER_TABS: {
  value: UnterTab;
  labelKey: string;
  symbol: SymbolName;
  /**
   * Bedingung für das Erscheinen. Fehlt sie, steht der Reiter immer da.
   *
   * Qualitätsprofile brauchen etwas, worauf sie geschoben werden können -
   * ohne eine einzige Instanz wäre der Reiter eine Sackgasse.
   */
  wenn?: (stand: { arrVorhanden: boolean }) => boolean;
}[] = [
  { value: "general", labelKey: "settings.generalSection", symbol: "allgemein" },
  { value: "tmdb", labelKey: "settings.tmdbSection", symbol: "fernseher" },
  { value: "radarr", labelKey: "settings.radarrSection", symbol: "radarr" },
  { value: "sonarr", labelKey: "settings.sonarrSection", symbol: "sonarr" },
  {
    value: "qualitaet",
    labelKey: "qualityProfiles.title",
    symbol: "qualitaet",
    wenn: ({ arrVorhanden }) => arrVorhanden,
  },
  { value: "plex", labelKey: "mediaserver.adminTitle", symbol: "medienserver" },
];

type Draft = {
  tmdb_api_key: string;
  radarr_url: string;
  radarr_api_key: string;
  sonarr_url: string;
  sonarr_api_key: string;
  /** Frei wählbare Anzeigenamen – leer heißt: der Dienstname gilt. */
  radarr_name: string;
  sonarr_name: string;
  radarr_uhd_name: string;
  sonarr_uhd_name: string;
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
  /** Dieselben Regeln je 4K-Instanz - seit dem Kachel-Umbau je Instanz. */
  movie_uhd_root_folder_mode: RootFolderMode;
  series_uhd_root_folder_mode: RootFolderMode;
  movie_uhd_profile_mode: RootFolderMode;
  series_uhd_profile_mode: RootFolderMode;
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
  radarr_name: "",
  sonarr_name: "",
  radarr_uhd_name: "",
  sonarr_uhd_name: "",
  default_region: "DE",
  default_language: "de",
  demo_mode: "auto",
  default_movie_profile_id: "",
  default_series_profile_id: "",
  movie_root_folder_mode: "user",
  series_root_folder_mode: "user",
  movie_profile_mode: "user",
  series_profile_mode: "user",
  movie_uhd_root_folder_mode: "user",
  series_uhd_root_folder_mode: "user",
  movie_uhd_profile_mode: "user",
  series_uhd_profile_mode: "user",
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

/**
 * Eine Instanz als Kachel – dieselbe Optik wie die Benachrichtigungs-Ziele.
 *
 * ⚠️ Nur die Optik ist neu: Dahinter stehen weiterhin genau zwei feste
 * Plätze je Dienst (Standard + 4K, siehe QualityTier) und ein gemeinsamer
 * Speichern-Fluss. Die Kachelreihe ist zugleich die Vorbereitung auf
 * „beliebig viele Instanzen": Sie ist als Liste gebaut, und der spätere
 * Umbau tauscht nur ihre Quelle – nicht diese Bausteine.
 */
function InstanzKachel({
  titel,
  adresse,
  kennung,
  symbol,
  uhd = false,
  aktiv,
  onBearbeiten,
}: {
  titel: string;
  adresse: string;
  kennung: string;
  /** Das Dienst-Logo, blass im Hintergrund - wie bei den Meldungs-Zielen. */
  symbol: SymbolName;
  /** Traegt die Kachel das 4K-Abzeichen? */
  uhd?: boolean;
  aktiv: boolean;
  onBearbeiten: () => void;
}) {
  const { t } = useTranslation();
  // Dieselbe Abfrage wie der Warnkasten im Formular – geteilt über den
  // Query-Schlüssel, also keine zweite Netzanfrage.
  const gesundheit = useQuery({
    queryKey: ["instanz-gesundheit"],
    queryFn: () =>
      api.get<GesundheitStand>("/api/settings/instanzen/gesundheit"),
  });
  const probleme =
    gesundheit.data?.instanzen.find((zeile) => zeile.kennung === kennung)
      ?.probleme ?? [];
  // Die Statusleuchte: live gefragt und minutenweise aufgefrischt, solange
  // die Seite offen ist. Gruen beruhigt - Rot sagt ehrlich Bescheid.
  const verbindung = useQuery({
    queryKey: ["instanz-verbindung"],
    queryFn: () =>
      api.get<VerbindungStand>("/api/settings/instanzen/verbindung"),
    refetchInterval: 60_000,
  });
  const stand = verbindung.data?.instanzen.find(
    (zeile) => zeile.kennung === kennung,
  );
  // Heisst die Instanz selbst schon "4K", waere das Abzeichen ein Echo.
  const zeigeAbzeichen = uhd && titel.trim().toUpperCase() !== "4K";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onBearbeiten}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onBearbeiten();
        }
      }}
      className={
        "relative flex min-h-28 cursor-pointer flex-col justify-between overflow-hidden rounded-2xl border px-4 py-3 transition-colors " +
        (aktiv
          ? "border-accent-500/60 bg-accent-500/10"
          : "border-ink-700 bg-ink-900 hover:border-ink-600")
      }
    >
      <Symbol
        name={symbol}
        className="pointer-events-none absolute inset-0 m-auto h-20 w-20 text-mist-100 opacity-[0.14]"
      />
      <div className="relative">
        <p className="flex items-center gap-2 text-lg font-semibold text-mist-100">
          {titel}
          {zeigeAbzeichen && (
            <span className="rounded-full bg-accent-500/15 px-2 py-0.5 text-[10px] font-semibold text-accent-400 ring-1 ring-accent-500/40">
              4K
            </span>
          )}
        </p>
        <p className="mt-0.5 text-xs text-mist-600">
          {adresse || t("settings.tileNotConfigured")}
        </p>
      </div>
      <div className="relative mt-3 flex items-center justify-between gap-2">
        <span className="flex items-center gap-3">
          {stand && (
            <span
              className={
                "flex items-center gap-1.5 text-xs " +
                (stand.erreichbar ? "text-ok-500" : "text-bad-500")
              }
              title={stand.erreichbar && stand.version ? `v${stand.version}` : undefined}
            >
              <span
                className={
                  "h-2 w-2 rounded-full " +
                  (stand.erreichbar ? "bg-ok-500" : "bg-bad-500")
                }
              />
              {stand.erreichbar
                ? t("settings.tileConnected")
                : t("settings.tileUnreachable")}
            </span>
          )}
          <span className="text-xs text-warn-500">
            {probleme.length > 0 ? t("settings.tileProblems") : ""}
          </span>
        </span>
        <RundKnopf label={t("settings.tileEdit")} onClick={onBearbeiten}>
          <path
            d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </RundKnopf>
      </div>
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
 *
 * Die Umsetzung steht seit 0.22 in ``components/ui.tsx``: Sie wird auf jeder
 * Einstellungsseite gebraucht, und solange sie hier lag, baute jede andere
 * Seite ihre Bereiche selbst - mal mit Karte, mal ohne.
 */

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
  // Der Kopplungs-Hinweis ("beides wurde mitumgestellt") wohnt bewusst NICHT
  // hier: Zwei Gruppen haetten ihn doppelt gezeigt - er steht einmal in der
  // Regel-Sektion. ``partner`` bleibt fuer die Umstell-Logik der Aufrufer.
  void partner;

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

/**
 * Welcher Unterreiter hinter welchem Adress-Wort steckt.
 *
 * Bewusst nicht dieselben Woerter wie die internen Werte: `medienserver` ist
 * anbieter-neutral, waehrend der interne Wert aus historischen Gruenden noch
 * `plex` heisst. Wer einen Link setzt, soll nicht den Anbieter nennen muessen,
 * den diese Installation zufaellig benutzt.
 */
const UNTER_AUS_ADRESSE: Record<string, UnterTab> = {
  allgemein: "general",
  tmdb: "tmdb",
  radarr: "radarr",
  sonarr: "sonarr",
  medienserver: "plex",
  qualitaet: "qualitaet",
};

export function AdminServicesSettings({
  /** Startwert aus der Adresse (`?unter=sonarr`). Danach verwaltet die Seite ihn selbst. */
  startUnter,
}: {
  startUnter?: string;
} = {}) {
  const { t } = useTranslation();
  const regionen = useRegionen();
  const queryClient = useQueryClient();

  const { data: config } = useConfig();

  const [unterTab, setUnterTab] = useState<UnterTab>(
    UNTER_AUS_ADRESSE[startUnter ?? ""] ?? "general",
  );
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
    null,
  );
  const [testResults, setTestResults] = useState<
    Partial<Record<TestService, TestResult>>
  >({});
  // Der Webhook-Haken je Instanz ist ein Wunsch im Entwurf: Er wirkt erst
  // mit dem Speichern-Knopf der Instanz. Der Testen-Beweis wird je Instanz
  // mitgefuehrt - Einschalten darf erst speichern, wenn der Anruf ankam.
  const [webhookWunsch, setWebhookWunsch] = useState<Record<string, boolean>>(
    {},
  );
  const [webhookProbeOk, setWebhookProbeOk] = useState<Record<string, boolean>>(
    {},
  );
  // Welche Instanz gerade zum Entfernen ansteht - erst die Folgen, dann die Tat.
  const [loeschBestaetigung, setLoeschBestaetigung] = useState<string | null>(
    null,
  );

  const webhookStand = useQuery({
    queryKey: ["webhook-stand"],
    queryFn: () => api.get<WebhookStand>("/api/settings/webhooks"),
  });

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
      radarr_name: data.radarr_name,
      sonarr_name: data.sonarr_name,
      radarr_uhd_name: data.radarr_uhd_name,
      sonarr_uhd_name: data.sonarr_uhd_name,
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
      movie_uhd_root_folder_mode: data.movie_uhd_root_folder_mode,
      series_uhd_root_folder_mode: data.series_uhd_root_folder_mode,
      movie_uhd_profile_mode: data.movie_uhd_profile_mode,
      series_uhd_profile_mode: data.series_uhd_profile_mode,
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
    onSuccess: (data, gesendet) => {
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
      // Ab jetzt gilt das **Gesendete** als unveraendert - nur das. Wer in
      // einem anderen Bereich noch ungespeicherte Aenderungen hat, behaelt
      // dort seinen aktiven Knopf; frueher schluckte ein Teil-Speichern die
      // Marker aller anderen gleich mit.
      basis.current = {
        ...basis.current,
        ...gesendet,
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

  const hakenMutation = useMutation({
    mutationFn: ({ kennung, aktiv }: { kennung: string; aktiv: boolean }) =>
      api.patch<WebhookStand>(`/api/settings/webhooks/${kennung}`, { aktiv }),
    onSuccess: (stand, { kennung }) => {
      queryClient.setQueryData(["webhook-stand"], stand);
      setWebhookWunsch((aktuell) => {
        const neu = { ...aktuell };
        delete neu[kennung];
        return neu;
      });
      setMessage({ ok: true, text: t("settings.saved") });
    },
    onError: (error) =>
      setMessage({
        ok: false,
        text:
          error instanceof ApiError ? error.message : t("settings.saveFailed"),
      }),
  });

  const loeschMutation = useMutation({
    mutationFn: (kennung: string) =>
      api.delete<AppSettings>(`/api/settings/instanzen/${kennung}`),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings"], data);
      void queryClient.invalidateQueries({ queryKey: ["webhook-stand"] });
      void queryClient.invalidateQueries({ queryKey: ["instanz-verbindung"] });
      void queryClient.invalidateQueries({ queryKey: ["instanz-gesundheit"] });
      void queryClient.invalidateQueries({ queryKey: ["arr-options"] });
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      setLoeschBestaetigung(null);
      setOffeneInstanz(null);
      setMessage({ ok: true, text: t("settings.saved") });
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

  // Feldgruppen je Speichern-Knopf: Der grosse Knopf unten gilt nur noch
  // fuer Allgemein und TMDB; jede Instanz und der Regel-Block speichern ihre
  // eigene Gruppe. So blockiert ein halb ausgefuellter Radarr-Entwurf nicht
  // mehr das Speichern der Region - und umgekehrt.
  const ALLGEMEIN_FELDER: (keyof Draft)[] = [
    "tmdb_api_key",
    "default_region",
    "default_language",
    "demo_mode",
  ];
  const INSTANZ_FELDER: Record<Exclude<TestService, "tmdb">, (keyof Draft)[]> = {
    radarr: [
      "radarr_url", "radarr_api_key", "radarr_name",
      "movie_profile_mode", "movie_root_folder_mode",
      "default_movie_profile_id", "default_movie_root",
    ],
    radarr_uhd: [
      "radarr_uhd_url", "radarr_uhd_api_key", "radarr_uhd_name",
      "movie_uhd_profile_mode", "movie_uhd_root_folder_mode",
      "default_movie_uhd_profile_id", "default_movie_uhd_root",
    ],
    sonarr: [
      "sonarr_url", "sonarr_api_key", "sonarr_name",
      "series_profile_mode", "series_root_folder_mode",
      "default_series_profile_id", "default_series_root",
    ],
    sonarr_uhd: [
      "sonarr_uhd_url", "sonarr_uhd_api_key", "sonarr_uhd_name",
      "series_uhd_profile_mode", "series_uhd_root_folder_mode",
      "default_series_uhd_profile_id", "default_series_uhd_root",
    ],
  };
  const KENNUNGEN: Record<Exclude<TestService, "tmdb">, string> = {
    radarr: "radarr-standard",
    radarr_uhd: "radarr-uhd",
    sonarr: "sonarr-standard",
    sonarr_uhd: "sonarr-uhd",
  };
  const teil = (felder: (keyof Draft)[]): Partial<Draft> =>
    Object.fromEntries(felder.map((feld) => [feld, draft[feld]])) as Partial<Draft>;
  const geaendert = (felder: (keyof Draft)[]) =>
    felder.some((feld) => draft[feld] !== basis.current[feld]);

  // Wer eine Adresse oder einen Key aendert, muss erst pruefen, ob die
  // Verbindung ueberhaupt steht. Sonst speichert man eine tote Adresse und
  // merkt es erst, wenn die erste Anfrage ins Leere laeuft. Das gilt je
  // Knopf: unten fuer TMDB, in jedem Instanz-Formular fuer dessen Dienst.
  const ungetestet = (["tmdb"] as TestService[]).filter(
    (dienst) => verbindungGeaendert(dienst) && testResults[dienst]?.ok !== true,
  );
  const speichernGesperrt = !geaendert(ALLGEMEIN_FELDER) || ungetestet.length > 0;
  const zeigtGlobalenKnopf = unterTab === "general" || unterTab === "tmdb";

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    // Nur die Allgemein-/TMDB-Gruppe: Instanzen und Regeln haben ihre
    // eigenen Knoepfe und schicken nur ihre eigenen Felder.
    saveMutation.mutate(teil(ALLGEMEIN_FELDER));
  }

  /**
   * Der Speichern-Knopf eines Instanz-Formulars: schickt nur die Felder
   * dieser Instanz und verlangt vorher den Verbindungstest, wenn Adresse
   * oder Key sich geaendert haben.
   */
  /**
   * "Wer waehlt Profil und Zielordner" - seit dem Kachel-Umbau je Instanz.
   * Sitzt im Instanz-Formular und speichert mit dessen Knopf; die alte
   * "gilt fuer beide"-Box ist damit Geschichte.
   */
  const regelSektion = (dienst: "movie" | "tv", stufe: "standard" | "uhd") => {
    const profilFeld = (
      dienst === "movie"
        ? stufe === "uhd"
          ? "movie_uhd_profile_mode"
          : "movie_profile_mode"
        : stufe === "uhd"
          ? "series_uhd_profile_mode"
          : "series_profile_mode"
    ) as keyof Draft;
    const ordnerFeld = (
      dienst === "movie"
        ? stufe === "uhd"
          ? "movie_uhd_root_folder_mode"
          : "movie_root_folder_mode"
        : stufe === "uhd"
          ? "series_uhd_root_folder_mode"
          : "series_root_folder_mode"
    ) as keyof Draft;
    const profilVorgabe = (
      dienst === "movie"
        ? stufe === "uhd"
          ? "default_movie_uhd_profile_id"
          : "default_movie_profile_id"
        : stufe === "uhd"
          ? "default_series_uhd_profile_id"
          : "default_series_profile_id"
    ) as keyof Draft;
    const ordnerVorgabe = (
      dienst === "movie"
        ? stufe === "uhd"
          ? "default_movie_uhd_root"
          : "default_movie_root"
        : stufe === "uhd"
          ? "default_series_uhd_root"
          : "default_series_root"
    ) as keyof Draft;
    const profil = draft[profilFeld] as RootFolderMode;
    const ordner = draft[ordnerFeld] as RootFolderMode;
    const konfiguriert = Boolean(
      dienst === "movie"
        ? stufe === "uhd"
          ? settings?.radarr_uhd_api_key_set
          : settings?.radarr_api_key_set
        : stufe === "uhd"
          ? settings?.sonarr_uhd_api_key_set
          : settings?.sonarr_api_key_set,
    );

    return (
      <div className="flex flex-col gap-4 border-t border-ink-700 pt-4 lg:border-t-0 lg:pt-0">
        <div>
          <p className="text-sm font-medium text-mist-300">
            {t("settings.targetSection")}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-mist-600">
            {t("settings.targetPerInstance")}
          </p>
        </div>
        <WerWaehlt
          was="profile"
          gruppe={`${dienst}-${stufe}`}
          wert={profil}
          partner={ordner}
          onChange={(value) =>
            update(zielPaar(value, ordner, profilFeld, ordnerFeld))
          }
        />
        {profil === "fixed" && (
          <DefaultProfileField
            mediaType={dienst}
            tier={stufe === "uhd" ? "uhd" : undefined}
            value={draft[profilVorgabe] as string}
            onChange={(value) =>
              update({ [profilVorgabe]: value } as Partial<Draft>)
            }
            configured={konfiguriert}
          />
        )}
        <div className="border-t border-ink-700 pt-4">
          <WerWaehlt
            was="rootFolder"
            gruppe={`${dienst}-${stufe}`}
            wert={ordner}
            partner={profil}
            onChange={(value) =>
              update(zielPaar(value, profil, ordnerFeld, profilFeld))
            }
          />
        </div>
        {ordner === "fixed" && (
          <DefaultRootField
            mediaType={dienst}
            tier={stufe === "uhd" ? "uhd" : undefined}
            value={draft[ordnerVorgabe] as string}
            onChange={(value) =>
              update({ [ordnerVorgabe]: value } as Partial<Draft>)
            }
            configured={konfiguriert}
          />
        )}
        {(profil === "approver" || ordner === "approver") && (
          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
            {t("settings.approverRuleShort")}
          </p>
        )}
      </div>
    );
  };

  const instanzSpeichernReihe = (dienst: Exclude<TestService, "tmdb">) => {
    const felder = INSTANZ_FELDER[dienst];
    const kennung = KENNUNGEN[dienst];
    const zeile = webhookStand.data?.instanzen.find(
      (eintrag) => eintrag.kennung === kennung,
    );
    const wunsch = webhookWunsch[kennung];
    const hakenGeaendert =
      zeile !== undefined && wunsch !== undefined && wunsch !== zeile.aktiv;
    const felderGeaendert = geaendert(felder);
    const verbindungTestNoetig =
      verbindungGeaendert(dienst) && testResults[dienst]?.ok !== true;
    // Einschalten verlangt den bewiesenen Anruf - Abwaehlen nicht: Fuers
    // Aufraeumen braucht es keinen Beweis.
    const webhookTestNoetig =
      hakenGeaendert && wunsch === true && webhookProbeOk[kennung] !== true;
    const laufend = saveMutation.isPending || hakenMutation.isPending;
    const konfiguriert = Boolean(
      (settings as unknown as Record<string, unknown> | undefined)?.[
        `${dienst}_api_key_set`
      ],
    );
    const dienstName = dienst.startsWith("radarr") ? "Radarr" : "Sonarr";

    const speichern = () => {
      setMessage(null);
      const hakenAusfuehren = () => {
        if (hakenGeaendert) {
          hakenMutation.mutate({ kennung, aktiv: wunsch as boolean });
        }
      };
      if (felderGeaendert) {
        // Erst die Felder, dann der Haken - und der Haken nur, wenn das
        // Speichern der Felder nicht schiefging.
        saveMutation.mutate(teil(felder), { onSuccess: hakenAusfuehren });
      } else {
        hakenAusfuehren();
      }
    };

    return (
      <>
      <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-3">
        <Button
          type="button"
          onClick={speichern}
          loading={laufend}
          disabled={
            (!felderGeaendert && !hakenGeaendert) ||
            verbindungTestNoetig ||
            webhookTestNoetig
          }
        >
          {t("common.save")}
        </Button>
        {verbindungTestNoetig ? (
          <span className="text-xs text-warn-500">{t("settings.testFirst")}</span>
        ) : webhookTestNoetig ? (
          <span className="text-xs text-warn-500">
            {t("settings.webhookTestFirst")}
          </span>
        ) : (
          !felderGeaendert &&
          !hakenGeaendert && (
            <span className="text-xs text-mist-600">
              {t("settings.nothingChanged")}
            </span>
          )
        )}
        {konfiguriert && loeschBestaetigung !== kennung && (
          <button
            type="button"
            onClick={() => setLoeschBestaetigung(kennung)}
            className="ml-auto text-xs text-mist-600 transition-colors hover:text-bad-500"
          >
            {t("settings.removeInstance")}
          </button>
        )}
      </div>
      {loeschBestaetigung === kennung && (
        <div className="flex flex-col gap-3 rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3">
          <p className="text-xs leading-relaxed text-bad-500">
            {t("settings.removeInstanceConfirm", { dienst: dienstName })}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => loeschMutation.mutate(kennung)}
              loading={loeschMutation.isPending}
            >
              {t("settings.removeInstanceReally")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setLoeschBestaetigung(null)}
            >
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      </>
    );
  };

  // Welche Instanz-Kachel gerade aufgeklappt ist ("radarr-standard" usw.).
  // Höchstens eine – wie bei den Benachrichtigungs-Zielen: Ist ein Formular
  // offen, gehört die Aufmerksamkeit ihm.
  const [offeneInstanz, setOffeneInstanz] = useState<string | null>(null);
  const instanzUmschalten = (kennung: string) =>
    setOffeneInstanz((aktuell) => (aktuell === kennung ? null : kennung));

  const update = (patch: Partial<Draft>) =>
    setDraft((current) => ({ ...current, ...patch }));
  const settings = settingsQuery.data;

  /**
   * Welche Reiter überhaupt dastehen.
   *
   * ⚠️ Und der Rückfall dazu: Verschwindet der aktive Reiter - etwa weil
   * gerade die letzte Instanz entfernt wurde -, bliebe die Ansicht sonst auf
   * ihm stehen und zeigte nichts. Ein leerer Bereich ohne erkennbaren Grund
   * ist schlimmer als ein Sprung zurück auf "Allgemein".
   */
  const arrVorhanden = Boolean(
    config?.radarr_configured ||
      config?.sonarr_configured ||
      config?.radarr_uhd_configured ||
      config?.sonarr_uhd_configured,
  );
  const sichtbareTabs = UNTER_TABS.filter(
    (e) => !e.wenn || e.wenn({ arrVorhanden }),
  );
  useEffect(() => {
    if (!sichtbareTabs.some((e) => e.value === unterTab)) setUnterTab("general");
  }, [sichtbareTabs, unterTab]);

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

      <Reiterreihe
        unter
        className="mt-1"
        eintraege={sichtbareTabs.map((e) => ({
          value: e.value,
          label: t(e.labelKey),
          symbol: e.symbol,
        }))}
        aktiv={unterTab}
        onWechsel={setUnterTab}
      />

      {/* Der Media-Server bringt eigenes Speichern und eigene Abläufe mit -
          er steht deshalb außerhalb dieses Formulars. */}
      {unterTab === "plex" && (
        <div className="mt-6">
          <AdminMediaServerSettings />
        </div>
      )}

      {/* Profile werden nicht mit den Zugangsdaten zusammen gespeichert - sie
          haben eigene Abläufe und stehen deshalb, wie der Media-Server, außerhalb
          dieses Formulars. */}
      {unterTab === "qualitaet" && <AdminQualitaetsBereich />}

      <form
        onSubmit={handleSubmit}
        className={
          "mt-6 flex-col gap-5 " +
          (unterTab === "plex" || unterTab === "qualitaet" ? "hidden" : "flex")
        }
      >
        {/* ⚠️ Dieser Satz stand über der Unterreihe und schob sie um eine Zeile
            nach unten - auf **dieser einen** Seite saß die Reihe damit anders
            als auf allen anderen. Gelöscht ist er nicht: Hier steht er bei den
            Diensten, die wirklich einen Schlüssel führen, also dort, wo man ihn
            beim Eintragen liest statt beim Vorbeigehen. */}
        {unterTab !== "general" && (
          <p className="max-w-3xl text-sm text-mist-600">{t("settings.intro")}</p>
        )}

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
                  {(regionen.data ?? []).map((region) => (
                    <option key={region.code} value={region.code}>
                      {region.name}
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
            {/* ⚠️ In beiden Unterreitern: Eine Kollision kann Radarr und Sonarr
              zugleich betreffen - stuende sie nur bei einem, faende sie
              ausgerechnet der nicht, der beim anderen sucht. Das Bauteil zeigt
              nichts, wenn nichts anliegt. */}
            <DownloadKollision />

            {/* Die Instanzen als Kacheln - dieselbe Optik wie die
              Benachrichtigungs-Ziele. Heute bewusst auf zwei begrenzt
              (Standard + 4K); die Reihe ist trotzdem eine Liste, damit der
              spaetere Mehr-Instanzen-Umbau nur ihre Quelle tauscht. Der
              Klick oeffnet das Formular darunter; was fuer beide gilt
              (Profil, Zielordner), bleibt gemeinsam weiter unten. */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <InstanzKachel
                titel={settings?.radarr_name || "Radarr"}
                adresse={settings?.radarr_url ?? ""}
                kennung="radarr-standard"
                symbol="radarr"
                aktiv={offeneInstanz === "radarr-standard"}
                onBearbeiten={() => instanzUmschalten("radarr-standard")}
              />
              {settings?.radarr_uhd_url || settings?.radarr_uhd_api_key_set ? (
                <InstanzKachel
                  titel={settings?.radarr_uhd_name || "Radarr"}
                  adresse={settings?.radarr_uhd_url ?? ""}
                  kennung="radarr-uhd"
                  symbol="radarr"
                  uhd
                  aktiv={offeneInstanz === "radarr-uhd"}
                  onBearbeiten={() => instanzUmschalten("radarr-uhd")}
                />
              ) : (
                <PlusKachel
                  beschriftung={t("settings.tileAddUhd")}
                  aktiv={offeneInstanz === "radarr-uhd"}
                  onClick={() => instanzUmschalten("radarr-uhd")}
                />
              )}
            </div>

            {offeneInstanz === "radarr-standard" && (
              <InstanzBlock
                titel={t("settings.instanceStandard")}
                hinweis={t("settings.instanceStandardHint")}
              >
                {/* Zweispaltig, sobald der Platz reicht: links der Zugang,
                  rechts die Entscheider-Regeln - untereinander war das
                  verschenkte Breite. */}
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="flex flex-col gap-4">
                <Field
                  label={t("settings.instanceName")}
                  value={draft.radarr_name}
                  onChange={(event) =>
                    update({ radarr_name: event.target.value })
                  }
                  placeholder="Radarr"
                  hint={t("settings.instanceNameHint")}
                  autoComplete="off"
                />
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
                {settings?.radarr_api_key_set && (
                  <>
                    <InstanzGesundheit kennung="radarr-standard" />
                    <WebhookZeile
                      kennung="radarr-standard"
                      dienst="radarr"
                      wunsch={webhookWunsch["radarr-standard"]}
                      onWunsch={(aktiv) =>
                        setWebhookWunsch((aktuell) => ({
                          ...aktuell,
                          "radarr-standard": aktiv,
                        }))
                      }
                      onProbe={(angekommen) =>
                        setWebhookProbeOk((aktuell) => ({
                          ...aktuell,
                          "radarr-standard": angekommen,
                        }))
                      }
                    />
                  </>
                )}
                </div>
                <div className="lg:border-l lg:border-ink-700 lg:pl-6">
                  {regelSektion("movie", "standard")}
                </div>
                </div>
                {instanzSpeichernReihe("radarr")}
              </InstanzBlock>
            )}

            {offeneInstanz === "radarr-uhd" && (
              <InstanzBlock
                titel={t("uhd.section")}
                hinweis={t("uhd.sectionHint", { dienst: "Radarr" })}
              >
                {/* Zweispaltig, sobald der Platz reicht: links der Zugang,
                  rechts die Entscheider-Regeln - untereinander war das
                  verschenkte Breite. */}
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="flex flex-col gap-4">
                <Field
                  label={t("settings.instanceName")}
                  value={draft.radarr_uhd_name}
                  onChange={(event) =>
                    update({ radarr_uhd_name: event.target.value })
                  }
                  placeholder="Radarr 4K"
                  hint={t("settings.instanceNameHint")}
                  autoComplete="off"
                />
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
                {settings?.radarr_uhd_api_key_set && (
                  <>
                    <InstanzGesundheit kennung="radarr-uhd" />
                    <WebhookZeile
                      kennung="radarr-uhd"
                      dienst="radarr"
                      wunsch={webhookWunsch["radarr-uhd"]}
                      onWunsch={(aktiv) =>
                        setWebhookWunsch((aktuell) => ({
                          ...aktuell,
                          "radarr-uhd": aktiv,
                        }))
                      }
                      onProbe={(angekommen) =>
                        setWebhookProbeOk((aktuell) => ({
                          ...aktuell,
                          "radarr-uhd": angekommen,
                        }))
                      }
                    />
                  </>
                )}
                </div>
                <div className="lg:border-l lg:border-ink-700 lg:pl-6">
                  {regelSektion("movie", "uhd")}
                </div>
                </div>
                {instanzSpeichernReihe("radarr_uhd")}
              </InstanzBlock>
            )}

          </Section>
        )}

        {unterTab === "sonarr" && (
          <Section title={t("settings.sonarrSection")} breit>
            {/* ⚠️ In beiden Unterreitern: Eine Kollision kann Radarr und Sonarr
              zugleich betreffen - stuende sie nur bei einem, faende sie
              ausgerechnet der nicht, der beim anderen sucht. Das Bauteil zeigt
              nichts, wenn nichts anliegt. */}
            <DownloadKollision />

            {/* Kacheln wie bei Radarr - Begruendung dort. */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <InstanzKachel
                titel={settings?.sonarr_name || "Sonarr"}
                adresse={settings?.sonarr_url ?? ""}
                kennung="sonarr-standard"
                symbol="sonarr"
                aktiv={offeneInstanz === "sonarr-standard"}
                onBearbeiten={() => instanzUmschalten("sonarr-standard")}
              />
              {settings?.sonarr_uhd_url || settings?.sonarr_uhd_api_key_set ? (
                <InstanzKachel
                  titel={settings?.sonarr_uhd_name || "Sonarr"}
                  adresse={settings?.sonarr_uhd_url ?? ""}
                  kennung="sonarr-uhd"
                  symbol="sonarr"
                  uhd
                  aktiv={offeneInstanz === "sonarr-uhd"}
                  onBearbeiten={() => instanzUmschalten("sonarr-uhd")}
                />
              ) : (
                <PlusKachel
                  beschriftung={t("settings.tileAddUhd")}
                  aktiv={offeneInstanz === "sonarr-uhd"}
                  onClick={() => instanzUmschalten("sonarr-uhd")}
                />
              )}
            </div>

            {offeneInstanz === "sonarr-standard" && (
              <InstanzBlock
                titel={t("settings.instanceStandard")}
                hinweis={t("settings.instanceStandardHint")}
              >
                {/* Zweispaltig, sobald der Platz reicht: links der Zugang,
                  rechts die Entscheider-Regeln - untereinander war das
                  verschenkte Breite. */}
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="flex flex-col gap-4">
                <Field
                  label={t("settings.instanceName")}
                  value={draft.sonarr_name}
                  onChange={(event) =>
                    update({ sonarr_name: event.target.value })
                  }
                  placeholder="Sonarr"
                  hint={t("settings.instanceNameHint")}
                  autoComplete="off"
                />
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
                {settings?.sonarr_api_key_set && (
                  <>
                    <InstanzGesundheit kennung="sonarr-standard" />
                    <WebhookZeile
                      kennung="sonarr-standard"
                      dienst="sonarr"
                      wunsch={webhookWunsch["sonarr-standard"]}
                      onWunsch={(aktiv) =>
                        setWebhookWunsch((aktuell) => ({
                          ...aktuell,
                          "sonarr-standard": aktiv,
                        }))
                      }
                      onProbe={(angekommen) =>
                        setWebhookProbeOk((aktuell) => ({
                          ...aktuell,
                          "sonarr-standard": angekommen,
                        }))
                      }
                    />
                  </>
                )}
                </div>
                <div className="lg:border-l lg:border-ink-700 lg:pl-6">
                  {regelSektion("tv", "standard")}
                </div>
                </div>
                {instanzSpeichernReihe("sonarr")}
              </InstanzBlock>
            )}

            {offeneInstanz === "sonarr-uhd" && (
              <InstanzBlock
                titel={t("uhd.section")}
                hinweis={t("uhd.sectionHint", { dienst: "Sonarr" })}
              >
                {/* Zweispaltig, sobald der Platz reicht: links der Zugang,
                  rechts die Entscheider-Regeln - untereinander war das
                  verschenkte Breite. */}
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="flex flex-col gap-4">
                <Field
                  label={t("settings.instanceName")}
                  value={draft.sonarr_uhd_name}
                  onChange={(event) =>
                    update({ sonarr_uhd_name: event.target.value })
                  }
                  placeholder="Sonarr 4K"
                  hint={t("settings.instanceNameHint")}
                  autoComplete="off"
                />
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
                {settings?.sonarr_uhd_api_key_set && (
                  <>
                    <InstanzGesundheit kennung="sonarr-uhd" />
                    <WebhookZeile
                      kennung="sonarr-uhd"
                      dienst="sonarr"
                      wunsch={webhookWunsch["sonarr-uhd"]}
                      onWunsch={(aktiv) =>
                        setWebhookWunsch((aktuell) => ({
                          ...aktuell,
                          "sonarr-uhd": aktiv,
                        }))
                      }
                      onProbe={(angekommen) =>
                        setWebhookProbeOk((aktuell) => ({
                          ...aktuell,
                          "sonarr-uhd": angekommen,
                        }))
                      }
                    />
                  </>
                )}
                </div>
                <div className="lg:border-l lg:border-ink-700 lg:pl-6">
                  {regelSektion("tv", "uhd")}
                </div>
                </div>
                {instanzSpeichernReihe("sonarr_uhd")}
              </InstanzBlock>
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
          {zeigtGlobalenKnopf && (
            <>
              <Button
                type="submit"
                loading={saveMutation.isPending}
                disabled={speichernGesperrt}
              >
                {t("common.save")}
              </Button>
              {/* Warum der Knopf gesperrt ist, muss dastehen - ein grauer
                  Knopf ohne Begruendung ist die haeufigste Sackgasse in
                  Formularen. */}
              {ungetestet.length > 0 ? (
                <span className="text-xs text-warn-500">
                  {t("settings.testRequired", { dienste: ungetestet.join(", ") })}
                </span>
              ) : (
                !geaendert(ALLGEMEIN_FELDER) && (
                  <span className="text-xs text-mist-600">
                    {t("settings.nothingChanged")}
                  </span>
                )
              )}
            </>
          )}
        </div>
      </form>

      {/* Der Folgen-Paket-Schalter wohnt bei den Serien: Er bestimmt, wie
          feinkoernig bei Sonarr bestellt werden darf. Er hing mal unter der
          ganzen Diensteseite und tauchte damit auch bei Filmen auf - dort
          hat er nichts zu bestimmen. */}
      {unterTab === "sonarr" && (
        <div className="mt-6">
          <AdminFolgenSettings />
        </div>
      )}
    </div>
  );
}
