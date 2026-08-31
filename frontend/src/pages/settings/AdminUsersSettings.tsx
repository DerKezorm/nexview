import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import type {
  AppSettings,
  ArrOptions,
  HausordnungVerwaltung,
  Invitation,
  InvitationCreated,
  Kontingentwert,
  QuotaPeriod,
  Role,
  User,
} from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { MediaServerLogo } from "../../components/MediaServerLogo";
import { providerName } from "../../lib/mediaserver";
import { Avatar } from "../../components/Avatar";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { AdminKontoAufloesung } from "./AdminKontoAufloesung";
import { Button, Card, ErrorBanner, Field, Spinner } from "../../components/ui";
import { useConfig } from "../../hooks/useConfig";
import { formatDate } from "../../lib/format";

/**
 * Die drei Grenzen eines Kontos - in der Reihenfolge, in der sie dastehen.
 *
 * ⚠️ **Alle drei gelten immer.** Bis 0.19 war es ein haus-weites
 * Entweder-oder (Stueckzahl *oder* Speicher); entsprechend blendete diese
 * Seite je nach Betriebsart die eine oder die andere Haelfte aus. Beides
 * nebeneinander ist keine doppelte Wahrheit mehr, sondern die Regel: Eine
 * Anfrage geht nur durch, wenn beide noch Luft haben.
 */
const GRENZEN = [
  {
    feld: "quota_movies_limit",
    label: "adminUsers.quotaMovies",
    vorgabe: "quota_default_movies",
    gb: false,
  },
  {
    feld: "quota_series_limit",
    label: "adminUsers.quotaSeries",
    vorgabe: "quota_default_series",
    gb: false,
  },
  {
    feld: "storage_limit_gb",
    label: "adminUsers.storageLimit",
    vorgabe: "storage_default_limit_gb",
    gb: true,
  },
] as const;

type Grenzfeld = (typeof GRENZEN)[number]["feld"];

/**
 * Was im Auswahlfeld steht. Der Entwurf haelt genau diese drei Zustaende -
 * "zahl" traegt die Ziffern getrennt daneben, damit ein Wechsel auf
 * "Standard" und zurueck die eingetippte Zahl nicht verliert.
 */
type Grenzentwurf = { modus: "standard" | "unlimited" | "zahl"; zahl: string };

/**
 * Die Liste nach Rolle gegliedert - die mit den meisten Rechten zuerst.
 *
 * In einer flachen Liste steht die Rolle nur klein in der Zusammenfassung;
 * wer wissen will, wer alles freigeben darf, muss jede Zeile einzeln lesen.
 */
const GRUPPEN = [
  { rolle: "admin" as const, titel: "adminUsers.groupAdmins" },
  { rolle: "approver" as const, titel: "adminUsers.groupApprovers" },
  { rolle: "user" as const, titel: "adminUsers.groupUsers" },
  // Kinderkonten stehen zuletzt und werden hier nur *angezeigt*. Verwaltet
  // werden sie vom Elternteil in seinem Profil - haette der Administrator hier
  // dieselben Felder, koennte er die Rolle wegstellen und das Konto waere
  // elternlos: niemand koennte ihm noch ein Passwort geben. Sichtbar muessen
  // sie trotzdem sein, sonst waeren es Konten, von denen der Betreiber nichts
  // weiss.
  { rolle: "child" as const, titel: "adminUsers.groupChildren" },
];

function periodLabel(period: QuotaPeriod): string {
  return `adminUsers.period${period.charAt(0).toUpperCase()}${period.slice(1)}`;
}

/** Backend-Wert -> Entwurf. */
function alsEntwurf(wert: Kontingentwert): Grenzentwurf {
  return typeof wert === "number"
    ? { modus: "zahl", zahl: String(wert) }
    : { modus: wert, zahl: "" };
}

/** Entwurf -> Backend. ``undefined`` heisst "keine gueltige Zahl". */
function alsWert(entwurf: Grenzentwurf): Kontingentwert | undefined {
  if (entwurf.modus !== "zahl") return entwurf.modus;
  const zahl = Number(entwurf.zahl);
  if (entwurf.zahl.trim() === "" || !Number.isInteger(zahl) || zahl < 0)
    return undefined;
  return zahl;
}

/**
 * "2/3" bei einer eigenen Zahl, sonst nur die verbrauchte Anzahl.
 *
 * Steht am Konto "Standard", kennt diese Seite die tatsächliche Grenze nicht -
 * sie liegt beim Haus. Lieber die nackte Zahl als ein erfundener Nenner.
 */
function verbrauchText(used: number, limit: Kontingentwert): string {
  return typeof limit === "number" ? `${used}/${limit}` : String(used);
}

export function AdminUsersSettings() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { user: me } = useAuth();
  const { data: config } = useConfig();
  // ⚠️ **Nur wenn es überhaupt eine gibt.** Sonst stünde in jeder Zeile
  // dauerhaft „Hausordnung: offen" – eine Spalte voller Striche, deren Grund
  // niemand sieht.
  const { data: hausordnung } = useQuery({
    queryKey: ["hausordnung-verwaltung"],
    queryFn: () => api.get<HausordnungVerwaltung>("/api/hausordnung/verwaltung"),
  });
  const minPassword = config?.min_password_length ?? 4;
  /**
   * Einladen geht nur mit beidem: ohne öffentliche Adresse zeigt der Link ins
   * Leere, ohne Mailserver kommt er nicht an. Das Backend weist es ebenfalls
   * ab - hier wird es nur früh und verständlich sichtbar.
   */
  const kannEinladen =
    (config?.mail_configured ?? false) && (config?.public_url_set ?? false);

  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState<number | null>(null);
  const [newPassword, setNewPassword] = useState("");
  /** Vorgemerkte Grenzen je Benutzer und Feld - erst beim Speichern geschrieben. */
  const [grenzDrafts, setGrenzDrafts] = useState<
    Record<number, Partial<Record<Grenzfeld, Grenzentwurf>>>
  >({});
  /** Welcher Benutzer ist gerade aufgeklappt? Nur einer zur Zeit. */
  const [editing, setEditing] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);
  /** Wessen Kontingent soll zurückgesetzt werden? Steuert die Rückfrage. */
  const [quotaReset, setQuotaReset] = useState<User | null>(null);
  /** Wessen Speicher soll ins Haus? Steuert die zweite Rückfrage. */
  const [speicherReset, setSpeicherReset] = useState<User | null>(null);
  /**
   * Ungespeicherte Änderungen je Benutzer.
   *
   * Vorher schrieb jedes Häkchen sofort in die Datenbank. Ein Fehlklick war
   * damit nicht zurücknehmbar, und es passte zu keiner anderen Seite -
   * überall sonst sammelt Nexview erst und speichert auf Knopfdruck.
   */
  const [drafts, setDrafts] = useState<Record<number, Partial<User>>>({});

  const [invite, setInvite] = useState({ email: "", role: "user" as Role });
  /** Link zum Weitergeben, falls der Mailversand nicht geklappt hat. */
  const [manualLink, setManualLink] = useState<{
    link: string;
    grund: string;
  } | null>(null);

  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<User[]>("/api/users"),
  });

  const invitationsQuery = useQuery({
    queryKey: ["invitations"],
    queryFn: () => api.get<Invitation[]>("/api/users/invitations"),
  });

  // Nur wegen der Frage, wer das Qualitätsprofil wählt. Die öffentliche
  // Konfiguration fasst Ordner und Profil zu einem Kennzeichen zusammen; hier
  // wird beides getrennt gebraucht.
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/api/settings"),
  });
  /**
   * Warum greift die Auto-Freigabe hier nicht?
   *
   * Vorher stand dort immer „Zielordner“ - auch wenn der Ordner frei wählbar
   * war und nur das Profil beim Entscheider lag. Der Hinweis muss benennen,
   * was tatsächlich zutrifft, sonst sucht man an der falschen Stelle.
   */
  function grundText(media: "movie" | "tv"): string {
    const daten = settingsQuery.data;
    const ordner =
      (media === "movie"
        ? daten?.movie_root_folder_mode
        : daten?.series_root_folder_mode) === "approver";
    const profil =
      (media === "movie"
        ? daten?.movie_profile_mode
        : daten?.series_profile_mode) === "approver";
    if (ordner && profil) return t("adminUsers.autoApproveTargetLaterBoth");
    if (profil) return t("adminUsers.autoApproveTargetLaterProfile");
    return t("adminUsers.autoApproveTargetLaterFolder");
  }

  /** Darf der Benutzer das Profil selbst wählen? Sonst ist eine Sperrliste sinnlos. */
  function profilFreiWaehlbar(media: "movie" | "tv"): boolean {
    const modus =
      media === "movie"
        ? settingsQuery.data?.movie_profile_mode
        : settingsQuery.data?.series_profile_mode;
    return (modus ?? "user") === "user";
  }

  // Profile aus Radarr/Sonarr - nur verfügbar, wenn die Dienste eingerichtet sind.
  const movieProfiles = useQuery({
    queryKey: ["arr-options", "movie"],
    queryFn: () => api.get<ArrOptions>("/api/arr/movie/options"),
    enabled: config?.radarr_configured ?? false,
    retry: false,
  });
  const seriesProfiles = useQuery({
    queryKey: ["arr-options", "tv"],
    queryFn: () => api.get<ArrOptions>("/api/arr/tv/options"),
    enabled: config?.sonarr_configured ?? false,
    retry: false,
  });

  // Die Profile der 4K-Instanz sind andere - eigene Abfrage, eigener Schluessel.
  const movieUhdProfiles = useQuery({
    queryKey: ["arr-options", "movie", "uhd"],
    queryFn: () => api.get<ArrOptions>("/api/arr/movie/options?tier=uhd"),
    enabled: config?.radarr_uhd_configured ?? false,
    retry: false,
  });
  const seriesUhdProfiles = useQuery({
    queryKey: ["arr-options", "tv", "uhd"],
    queryFn: () => api.get<ArrOptions>("/api/arr/tv/options?tier=uhd"),
    enabled: config?.sonarr_uhd_configured ?? false,
    retry: false,
  });

  function toggleProfile(
    user: User,
    media: "movie" | "tv",
    tier: "standard" | "uhd",
    id: number,
    checked: boolean,
  ) {
    const field =
      tier === "uhd"
        ? media === "movie"
          ? "blocked_movie_uhd_profiles"
          : "blocked_series_uhd_profiles"
        : media === "movie"
          ? "blocked_movie_profiles"
          : "blocked_series_profiles";
    const current = feld(user, field);
    const next = checked
      ? [...current, id]
      : current.filter((entry) => entry !== id);
    setzen(user, field, next);
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["users"] });
    void queryClient.invalidateQueries({ queryKey: ["invitations"] });
    void queryClient.invalidateQueries({ queryKey: ["quota"] });
  }

  function fail(caught: unknown, fallback: string) {
    setMessage(null);
    setError(caught instanceof ApiError ? caught.message : fallback);
  }

  const inviteMutation = useMutation({
    mutationFn: () =>
      api.post<InvitationCreated>("/api/users/invitations", {
        email: invite.email.trim(),
        role: invite.role,
      }),
    onSuccess: (angelegt) => {
      setInvite({ email: "", role: "user" });
      setError(null);
      // Klappt der Versand nicht, bekommt der Admin den Link zum Weitergeben -
      // sonst blockiert ein kaputter Mailserver die ganze Verwaltung.
      if (angelegt.mail_sent) {
        setMessage(t("adminUsers.inviteSent", { email: angelegt.email }));
      } else if (angelegt.manual_link) {
        setManualLink({
          link: angelegt.manual_link,
          grund: angelegt.mail_error ?? t("adminUsers.mailFailed"),
        });
      }
      refresh();
    },
    onMutate: () => {
      resetMessages();
      setManualLink(null);
    },
    onError: (caught) => fail(caught, t("errors.generic")),
  });

  const withdrawMutation = useMutation({
    mutationFn: (id: number) =>
      api.delete<void>(`/api/users/invitations/${id}`),
    onSuccess: () => {
      setError(null);
      setMessage(t("adminUsers.inviteWithdrawn"));
      refresh();
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t("errors.generic")),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<User> }) =>
      api.patch<User>(`/api/users/${id}`, patch),
    onSuccess: () => {
      setError(null);
      setMessage(t("adminUsers.saved"));
      refresh();
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t("errors.generic")),
  });

  const passwordMutation = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      api.post<void>(`/api/users/${id}/password`, { password }),
    onSuccess: () => {
      setResetting(null);
      setNewPassword("");
      setError(null);
      setMessage(t("adminUsers.passwordReset"));
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t("errors.generic")),
  });

  /**
   * Die Speicher-Belegung eines Kontos ins Haus - der Ausweg aus dem
   * **Geisterposten**.
   *
   * ⚠️ Wer einen über Nexview angefragten Titel aus Radarr wirft und die Datei
   * behält, bleibt dafür belastet: Nexview löscht ausschließlich über
   * Radarr/Sonarr und kommt an diese Datei nicht mehr heran. Ohne diesen Knopf
   * sitzt der Betroffene auf einer Belastung, die er selbst nicht loswird.
   */
  const resetSpeicherMutation = useMutation({
    mutationFn: (id: number) => api.post<User>(`/api/users/${id}/storage/reset`),
    onSuccess: () => {
      setSpeicherReset(null);
      setError(null);
      setMessage(t("adminUsers.storageResetDone"));
      refresh();
      // Der Betroffene sieht seinen Stand sofort neu, wenn er die Seite hat.
      void queryClient.invalidateQueries({ queryKey: ["storage-mine"] });
      void queryClient.invalidateQueries({ queryKey: ["storage-overview"] });
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t("errors.generic")),
  });

  const resetQuotaMutation = useMutation({
    mutationFn: (id: number) => api.post<User>(`/api/users/${id}/quota/reset`),
    onSuccess: () => {
      setQuotaReset(null);
      setError(null);
      setMessage(t("adminUsers.quotaResetDone"));
      refresh();
      // Der Betroffene sieht sein Kontingent sofort neu, wenn er die Seite hat.
      void queryClient.invalidateQueries({ queryKey: ["quota"] });
    },
    onMutate: resetMessages,
    onError: (caught) => fail(caught, t("errors.generic")),
  });

  // Das Loeschen selbst wohnt im Aufloesungs-Dialog - er entscheidet vorher
  // ueber den hinterlassenen Bestand und storniert laufende Bestellungen.

  /**
   * Vor jeder Aktion beide Meldungen leeren.
   *
   * Sonst bleibt eine alte grüne "Gespeichert"-Meldung stehen, während gerade
   * etwas fehlschlägt - und behauptet das Gegenteil.
   */
  function resetMessages() {
    setError(null);
    setMessage(null);
  }

  function handleInvite(event: FormEvent) {
    event.preventDefault();
    resetMessages();
    inviteMutation.mutate();
  }

  /** Kontingent-Eingabe: leer = unbegrenzt (null ans Backend). */
  /** Der anzuzeigende Wert: Entwurf, sonst das Gespeicherte. */
  function feld<K extends keyof User>(user: User, key: K): User[K] {
    const entwurf = drafts[user.id];
    return entwurf && key in entwurf ? (entwurf[key] as User[K]) : user[key];
  }

  /**
   * Wie `feld`, aber für die drei Freigabe-Haken.
   *
   * Deren Wirkung errechnet der Server (`effective_*`) - ein leeres Feld erbt
   * von der alten Sammel-Einstellung. Solange nichts geändert wurde, gilt
   * genau dieser errechnete Wert; danach das, was gerade angeklickt wurde.
   */
  function entwurfOder(
    user: User,
    key: "auto_approve_movies" | "auto_approve_series" | "auto_approve_uhd",
    errechnet: boolean,
  ): boolean {
    const entwurf = drafts[user.id];
    return entwurf && key in entwurf ? Boolean(entwurf[key]) : errechnet;
  }

  /** Eine Änderung vormerken - geschrieben wird erst beim Speichern. */
  function setzen<K extends keyof User>(user: User, key: K, wert: User[K]) {
    resetMessages();
    setDrafts((alt) => ({
      ...alt,
      [user.id]: { ...alt[user.id], [key]: wert },
    }));
  }

  /** Was gerade im Auswahlfeld steht - Entwurf schlaegt gespeicherten Wert. */
  function grenze(user: User, grenzfeld: Grenzfeld): Grenzentwurf {
    return (
      grenzDrafts[user.id]?.[grenzfeld] ??
      alsEntwurf(user[grenzfeld] as Kontingentwert)
    );
  }

  function setGrenze(user: User, grenzfeld: Grenzfeld, wert: Grenzentwurf) {
    resetMessages();
    setGrenzDrafts((alt) => ({
      ...alt,
      [user.id]: { ...alt[user.id], [grenzfeld]: wert },
    }));
  }


  /** Gibt es für diesen Benutzer ungespeicherte Änderungen? */
  function geaendert(user: User): boolean {
    const entwurf = drafts[user.id];
    if (
      entwurf &&
      Object.keys(entwurf).some((key) => {
        const links = entwurf[key as keyof User];
        const rechts = user[key as keyof User];
        return Array.isArray(links) && Array.isArray(rechts)
          ? links.length !== rechts.length ||
              links.some((wert, i) => wert !== rechts[i])
          : links !== rechts;
      })
    ) {
      return true;
    }
    return GRENZEN.some(
      ({ feld: grenzfeld }) =>
        grenzDrafts[user.id]?.[grenzfeld] !== undefined &&
        alsWert(grenze(user, grenzfeld)) !== user[grenzfeld],
    );
  }

  /**
   * Alles auf einmal speichern.
   *
   * Ein Knopf für die ganze Karte - wie auf jeder anderen Einstellungsseite.
   * Ungültige Zahlen brechen ab, *bevor* etwas gesendet wird; sonst ginge die
   * halbe Karte durch und die andere Hälfte nicht.
   */
  function speichern(user: User) {
    const patch: Partial<User> = { ...drafts[user.id] };

    for (const { feld: grenzfeld } of GRENZEN) {
      if (grenzDrafts[user.id]?.[grenzfeld] === undefined) continue;
      const wert = alsWert(grenze(user, grenzfeld));
      if (wert === undefined) {
        setError(t("adminUsers.quotaInvalid"));
        return;
      }
      patch[grenzfeld] = wert;
    }

    updateMutation.mutate(
      { id: user.id, patch },
      {
        onSuccess: () => {
          setDrafts(({ [user.id]: _weg, ...rest }) => rest);
          setGrenzDrafts(({ [user.id]: _auch, ...rest }) => rest);
        },
      },
    );
  }

  /** Beim Zuklappen verwerfen - sonst hängt eine unsichtbare Änderung fest. */
  function verwerfen(user: User) {
    setDrafts(({ [user.id]: _weg, ...rest }) => rest);
    setGrenzDrafts(({ [user.id]: _auch, ...rest }) => rest);
  }

  function summary(user: User): string {
    const rollen: Record<Role, string> = {
      admin: "adminUsers.roleAdmin",
      approver: "adminUsers.roleApprover",
      user: "adminUsers.roleUser",
      child: "adminUsers.roleChild",
    };
    const teile = [t(rollen[user.role])];

    teile.push(
      t(
        user.effective_auto_approve
          ? "adminUsers.summaryAuto"
          : "adminUsers.summaryApproval",
      ),
    );

    // Wer die Hausordnung noch nicht abgehakt hat - der Betreiber soll sehen,
    // wen er noch erinnern muss.
    //
    // ⚠️ **Kinderkonten und Administratoren stehen nicht dabei.** Die einen
    // bekommen die Hausordnung nie zu sehen, die anderen schreiben sie. Eine
    // Zeile, die bei ihnen dauerhaft „offen" sagt, wäre kein Hinweis, sondern
    // Lärm. Dieselbe Grenze wie in `routers/hausordnung.UNBETEILIGT`.
    if (hausordnung?.veroeffentlicht && user.role !== "child" && user.role !== "admin") {
      teile.push(
        user.hausordnung_gelesen_am
          ? t("adminUsers.hausordnungGelesen", {
              date: formatDate(user.hausordnung_gelesen_am.slice(0, 10), i18n.language),
            })
          : t("adminUsers.hausordnungOffen"),
      );
    }

    // Nur die Zahlen, die dieses Konto selbst trägt. "Standard" steht nicht
    // dabei: Die tatsächliche Grenze liegt dann beim Haus, und eine Zahl
    // dorthin zu kopieren wäre eine zweite Wahrheit, die beim nächsten
    // Ändern auseinanderläuft.
    const zeitraum = zeitraumText;
    const eigene = [
      { wert: user.quota_movies_limit, key: "common.movies", gb: false },
      { wert: user.quota_series_limit, key: "common.series", gb: false },
      { wert: user.storage_limit_gb, key: "adminUsers.storageLimit", gb: true },
    ]
      .filter((zeile) => typeof zeile.wert === "number")
      .map((zeile) =>
        zeile.gb
          ? `${t(zeile.key)} ${zeile.wert} GB`
          : `${t(zeile.key)} ${zeile.wert} ${zeitraum}`,
      );
    teile.push(
      eigene.length > 0 ? eigene.join(", ") : t("adminUsers.summaryDefaults"),
    );

    const gesperrt =
      user.blocked_movie_profiles.length + user.blocked_series_profiles.length;
    if (gesperrt > 0)
      teile.push(t("adminUsers.summaryBlocked", { count: gesperrt }));

    return teile.join(" · ");
  }

  /**
   * „pro Woche" - der Zeitraum, der im Kontingent-Manager eingestellt ist.
   *
   * Er steht an den Beschriftungen, weil „Filme 3" ohne ihn nichts aussagt.
   * Bis 0.19 stand hier ein eigenes Auswahlfeld je Konto; seit der Zeitraum
   * haus-weit gilt, ist er hier eine **Auskunft** und keine Einstellung.
   */
  const zeitraumText = t(periodLabel(settingsQuery.data?.quota_period ?? "week"));

  /**
   * Was „Standard" für dieses Feld konkret heißt - **als Zahl, nicht als Wort**.
   *
   * „Standard" allein beantwortet die Frage nicht, die man an dieser Stelle
   * hat: *wie viel* denn. Wer es wissen will, müsste sonst in die Kontingente
   * wechseln und wieder zurück.
   */
  function vorgabeText(schluessel: (typeof GRENZEN)[number]["vorgabe"]): string {
    const wert = settingsQuery.data?.[schluessel] ?? null;
    if (wert === null) return t("adminUsers.quotaUnlimited");
    return schluessel === "storage_default_limit_gb"
      ? `${wert} GB`
      : `${wert} ${zeitraumText}`;
  }

  const users = usersQuery.data ?? [];
  const invitations = invitationsQuery.data ?? [];

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <p className="text-sm text-mist-500">{t("adminUsers.intro")}</p>

      {error && <ErrorBanner message={error} />}
      {message && !error && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {message}
        </p>
      )}

      {/* Konten entstehen nur über eine Einladung: der Eingeladene wählt
          Benutzername, Namen und Passwort selbst. So kennt niemand sonst sein
          Passwort - und der Administrator muss keines weitergeben. */}
      <Card>
        <h2 className="text-lg font-semibold">{t("adminUsers.inviteTitle")}</h2>
        <p className="mt-1 text-sm text-mist-500">
          {t("adminUsers.inviteIntro")}
        </p>

        {!kannEinladen && (
          <p className="mt-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
            {t("adminUsers.inviteBlocked")}
          </p>
        )}

        <form
          onSubmit={handleInvite}
          className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2"
        >
          <Field
            label={t("adminUsers.email")}
            type="email"
            value={invite.email}
            onChange={(event) =>
              setInvite({ ...invite, email: event.target.value })
            }
            placeholder="name@beispiel.de"
            autoComplete="off"
            required
          />
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-mist-300">
              {t("adminUsers.role")}
            </span>
            <select
              value={invite.role}
              onChange={(event) =>
                setInvite({ ...invite, role: event.target.value as Role })
              }
              className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              <option value="user">{t("adminUsers.roleUser")}</option>
              <option value="approver">{t("adminUsers.roleApprover")}</option>
              <option value="admin">{t("adminUsers.roleAdmin")}</option>
            </select>
          </label>

          <div className="sm:col-span-2">
            <Button
              type="submit"
              loading={inviteMutation.isPending}
              disabled={!kannEinladen}
            >
              {t("adminUsers.sendInvite")}
            </Button>
            <p className="mt-2 text-xs text-mist-600">
              {t("adminUsers.inviteHint")}
            </p>
          </div>

          {manualLink && (
            <div className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 sm:col-span-2">
              <p className="text-sm font-medium text-warn-500">
                {t("adminUsers.mailFailedTitle")}
              </p>
              <p className="mt-1 text-xs text-mist-400">{manualLink.grund}</p>
              <p className="mt-2 text-xs text-mist-500">
                {t("adminUsers.manualLinkHint")}
              </p>
              <code className="mt-1 block break-all rounded-lg bg-ink-900 px-3 py-2 text-xs text-mist-300">
                {manualLink.link}
              </code>
              <Button
                variant="ghost"
                className="mt-2"
                onClick={() =>
                  void navigator.clipboard?.writeText(manualLink.link)
                }
              >
                {t("adminUsers.copyLink")}
              </Button>
            </div>
          )}
        </form>
      </Card>

      {invitations.length > 0 && (
        <Card className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">
            {t("adminUsers.openInvites", { count: invitations.length })}
          </h2>
          {invitations.map((eintrag) => (
            <div
              key={eintrag.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 p-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{eintrag.email}</p>
                <p className="text-xs text-mist-600">
                  {t(
                    `adminUsers.role${eintrag.role === "admin" ? "Admin" : eintrag.role === "approver" ? "Approver" : "User"}`,
                  )}
                  {" · "}
                  {t("adminUsers.inviteExpires", {
                    date: formatDate(
                      eintrag.expires_at.slice(0, 10),
                      i18n.language,
                    ),
                  })}
                </p>
              </div>
              <Button
                variant="ghost"
                onClick={() => withdrawMutation.mutate(eintrag.id)}
                loading={
                  withdrawMutation.isPending &&
                  withdrawMutation.variables === eintrag.id
                }
              >
                {t("adminUsers.withdrawInvite")}
              </Button>
            </div>
          ))}
        </Card>
      )}

      {usersQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t("common.loading")}
        </p>
      )}

      {GRUPPEN.map(({ rolle, titel }) => {
        const gruppe = users.filter((user) => user.role === rolle);
        // Leere Überschriften wären nur Lärm - eine Rolle ohne Konten
        // erscheint gar nicht.
        if (gruppe.length === 0) return null;
        return (
          <div key={rolle} className="flex flex-col gap-3">
            <h3 className="flex items-center gap-2 text-xs font-medium tracking-wide text-mist-600 uppercase">
              {t(titel)}
              <span className="rounded-full bg-ink-900 px-2 py-0.5 text-[11px] ring-1 ring-ink-700">
                {gruppe.length}
              </span>
            </h3>
            {rolle === "child"
              ? gruppe.map((kind) => {
                  const elternteil = users.find((u) => u.id === kind.parent_id);
                  return (
                    <Card
                      key={kind.id}
                      className="flex flex-wrap items-center gap-3"
                    >
                      <Avatar
                        url={null}
                        name={kind.display_name ?? kind.username}
                        className="h-10 w-10"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold">
                          {kind.display_name ?? kind.username}
                          <span className="ml-2 text-sm font-normal text-mist-600">
                            @{kind.username}
                          </span>
                        </p>
                        <p className="text-xs text-mist-600">
                          {elternteil
                            ? t("adminUsers.childOf", {
                                name:
                                  elternteil.display_name ??
                                  elternteil.username,
                              })
                            : t("adminUsers.childOfUnknown")}
                          {kind.age !== null &&
                            ` · ${t("adminUsers.childAge", { age: kind.age })}`}
                        </p>
                      </div>
                      {!kind.is_active && (
                        <span className="rounded-full bg-ink-900 px-2.5 py-1 text-xs text-mist-500 ring-1 ring-ink-700">
                          {t("adminUsers.inactive")}
                        </span>
                      )}
                    </Card>
                  );
                })
              : gruppe.map((user) => {
              const isMe = user.id === me?.id;
              /**
               * Das Betreiberkonto – aus Sicht eines **anderen** Administrators.
               *
               * ⚠️ Nicht anklickbar und dann eine Fehlermeldung, sondern gar
               * nicht erst anklickbar. Ein Knopf, der immer scheitert, ist eine
               * Falle; ein ausgegrauter Knopf mit einem Satz daneben ist eine
               * Auskunft.
               *
               * Der Betreiber selbst sieht hier nichts Ausgegrautes – der Haken
               * nimmt ihm nichts weg. Er sagt nur, was andere nicht dürfen.
               */
              const geschuetzt = user.is_betreiber && !isMe;
              const offen = editing === user.id;
              return (
                <Card key={user.id} className="flex flex-col gap-4">
                  {/* Zusammenfassung in einer Zeile - Details erst auf Klick. */}
                  <div className="flex flex-wrap items-center gap-3">
                    <Avatar
                      url={user.avatar_url}
                      name={user.display_name ?? user.username}
                      className="h-10 w-10"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold">
                        {user.display_name ?? user.username}
                        <span className="ml-2 text-sm font-normal text-mist-600">
                          @{user.username}
                        </span>
                      </p>
                      <p className="text-xs text-mist-600">{summary(user)}</p>
                    </div>

                    {/* Ein Zeichen je **verbundenem** Server. Für Server, die
                        gar nicht verbunden sind, wäre eine Aussage sinnlos –
                        deshalb kommt die Liste vom Backend und nicht aus einer
                        festen Aufzählung hier.

                        Grün heißt verknüpft, gedämpft heißt nicht verknüpft.
                        Bewusst **kein Rot**: Ein Konto ohne Verknüpfung ist
                        völlig in Ordnung, es ist nur eben ein lokales. Rot
                        stünde hier an jeder zweiten Zeile und nähme dem
                        Warnabzeichen daneben die Wirkung, auf das es ankommt. */}
                    {user.role !== "child" && (config?.mediaserver_providers ?? []).length > 0 && (
                      <span className="flex shrink-0 items-center gap-1.5">
                        {(config?.mediaserver_providers ?? []).map((anbieter) => {
                          // ⚠️ Aus der Liste, nicht aus `user.mediaserver_provider`.
                          // Die Einzelspalte nennt nur die **zuletzt**
                          // verknüpfte Identität - wer Plex und Jellyfin hat,
                          // bekam damit ein grünes und ein graues Zeichen,
                          // obwohl beide verknüpft sind.
                          const konto = (user.mediaserver_accounts ?? []).find(
                            (k) => k.provider === anbieter,
                          );
                          return (
                            <MediaServerLogo
                              key={anbieter}
                              provider={anbieter}
                              className={
                                "h-4 w-4 " +
                                (konto ? "text-ok-500" : "text-mist-700")
                              }
                              title={
                                konto
                                  ? t("adminUsers.linkedAs", {
                                      server: providerName(anbieter),
                                      name: konto.username ?? "",
                                    })
                                  : t("adminUsers.notLinkedTo", {
                                      server: providerName(anbieter),
                                    })
                              }
                            />
                          );
                        })}
                      </span>
                    )}

                    {/* Genau ein Konto trägt es. Es ist bewusst ruhig
                        gehalten und nicht als Warnung gefärbt: Hier ist nichts
                        kaputt, hier ist nur etwas geschützt. */}
                    {user.is_betreiber && (
                      <span
                        className="rounded-full bg-accent-500/10 px-2.5 py-1 text-xs text-accent-500 ring-1 ring-accent-500/30"
                        title={t("adminUsers.betreiberHint")}
                      >
                        {t("adminUsers.betreiber")}
                      </span>
                    )}

                    {!user.is_active && (
                      <span className="rounded-full bg-ink-900 px-2.5 py-1 text-xs text-mist-500 ring-1 ring-ink-700">
                        {t("adminUsers.inactive")}
                      </span>
                    )}

                    {/* Nur wenn es klemmt. Ein Abzeichen, das an jeder zweiten
                        Karte hinge, würde niemand mehr lesen - und dieses hier
                        ist das einzige, das vor einer verschlossenen Tür warnt:
                        Wer kein Passwort hat, kommt allein über den Medienserver
                        herein. Fällt der weg, ist Schluss. */}
                    {!user.has_password && user.role !== "child" && (
                      <span
                        className="rounded-full bg-warn-500/10 px-2.5 py-1 text-xs text-warn-500 ring-1 ring-warn-500/30"
                        title={t("adminUsers.noPasswordHint")}
                      >
                        {t("adminUsers.noPassword")}
                      </span>
                    )}

                    {geaendert(user) && (
                      <span className="text-xs text-warn-500">
                        {t("adminUsers.unsaved")}
                      </span>
                    )}

                    <Button
                      variant="ghost"
                      onClick={() => {
                        // Zuklappen wirft den Entwurf weg. Ihn im Verborgenen
                        // liegen zu lassen wäre schlimmer: Beim nächsten Öffnen
                        // stünden dort Werte, an die sich niemand erinnert.
                        if (offen) verwerfen(user);
                        setEditing(offen ? null : user.id);
                      }}
                      aria-expanded={offen}
                    >
                      {t(offen ? "adminUsers.close" : "adminUsers.edit")}
                    </Button>
                  </div>

                  {offen && (
                    <>
                      <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
                        <p className="flex-1 text-xs text-mist-600">
                          {t("adminUsers.lastLogin")}:{" "}
                          {user.last_login_at
                            ? formatDate(
                                user.last_login_at.slice(0, 10),
                                i18n.language,
                              )
                            : t("adminUsers.never")}
                        </p>

                        <select
                          value={feld(user, "role")}
                          disabled={isMe || geschuetzt}
                          title={geschuetzt ? t("adminUsers.betreiberGesperrt") : undefined}
                          onChange={(event) =>
                            setzen(user, "role", event.target.value as Role)
                          }
                          className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 disabled:opacity-50"
                        >
                          <option value="user">
                            {t("adminUsers.roleUser")}
                          </option>
                          <option value="approver">
                            {t("adminUsers.roleApprover")}
                          </option>
                          <option value="admin">
                            {t("adminUsers.roleAdmin")}
                          </option>
                        </select>

                        <label className="flex cursor-pointer items-center gap-2 text-sm text-mist-300">
                          <input
                            type="checkbox"
                            checked={feld(user, "is_active")}
                            disabled={isMe || geschuetzt}
                            onChange={(event) =>
                              setzen(user, "is_active", event.target.checked)
                            }
                            className="h-4 w-4 accent-accent-500"
                          />
                          {user.is_active
                            ? t("adminUsers.active")
                            : t("adminUsers.inactive")}
                        </label>

                        {/* Kinderkonten sind echte Konten auf dieser
                            Installation - wer welche anlegen darf, entscheidet
                            der Betreiber. Administratoren dürfen es ohnehin
                            immer, deshalb steht der Haken bei ihnen fest. */}
                        <label
                          className="flex cursor-pointer items-center gap-2 text-sm text-mist-300"
                          title={t("adminUsers.mayManageChildrenHint")}
                        >
                          <input
                            type="checkbox"
                            checked={
                              user.role === "admin" ||
                              feld(user, "can_manage_children")
                            }
                            disabled={user.role === "admin" || geschuetzt}
                            onChange={(event) =>
                              setzen(
                                user,
                                "can_manage_children",
                                event.target.checked,
                              )
                            }
                            className="h-4 w-4 accent-accent-500 disabled:opacity-50"
                          />
                          {t("adminUsers.mayManageChildren")}
                        </label>
                      </div>

                      <div className="grid grid-cols-1 gap-3 border-t border-ink-700 pt-4 sm:grid-cols-4">
                        {/* Auto-Freigabe je Medienart - wie die Kontingente.
                        Liegt der Zielordner oder das Profil eines Dienstes beim
                        Entscheider, kann es dort keine Auto-Freigabe geben: Die
                        Anfrage waere unvollstaendig. Der Haken wird dann
                        gesperrt und sagt auch, warum. */}
                        {(() => {
                          const filmeSpaeter = Boolean(
                            config?.approver_picks_target_movie,
                          );
                          const serienSpaeter = Boolean(
                            config?.approver_picks_target_tv,
                          );
                          const zeilen = [
                            {
                              schluessel: "auto_approve_movies" as const,
                              gilt: entwurfOder(
                                user,
                                "auto_approve_movies",
                                user.effective_auto_approve_movies,
                              ),
                              label: "adminUsers.autoApproveMovies",
                              gesperrt: user.can_approve || filmeSpaeter,
                              grund: filmeSpaeter,
                              grundWort: "movie" as const,
                            },
                            {
                              schluessel: "auto_approve_series" as const,
                              gilt: entwurfOder(
                                user,
                                "auto_approve_series",
                                user.effective_auto_approve_series,
                              ),
                              label: "adminUsers.autoApproveSeries",
                              gesperrt: user.can_approve || serienSpaeter,
                              grund: serienSpaeter,
                              grundWort: "tv" as const,
                            },
                          ];
                          return (
                            <div className="flex flex-wrap gap-x-6 gap-y-2 sm:col-span-4">
                              {zeilen.map(
                                ({
                                  schluessel,
                                  gilt,
                                  label,
                                  gesperrt,
                                  grund,
                                  grundWort,
                                }) => (
                                  <label
                                    key={schluessel}
                                    className={
                                      "flex items-center gap-2 text-sm " +
                                      (gesperrt
                                        ? "text-mist-600"
                                        : "text-mist-300")
                                    }
                                  >
                                    <input
                                      type="checkbox"
                                      checked={
                                        gilt && (user.can_approve || !grund)
                                      }
                                      disabled={gesperrt}
                                      onChange={(event) =>
                                        setzen(
                                          user,
                                          schluessel,
                                          event.target.checked,
                                        )
                                      }
                                      className="h-4 w-4 accent-accent-500 disabled:opacity-60"
                                    />
                                    {t(label)}
                                    {user.can_approve && (
                                      <span className="text-xs text-mist-600">
                                        ({t("adminUsers.autoApproveAdmin")})
                                      </span>
                                    )}
                                    {!user.can_approve && grund && (
                                      <span className="text-xs text-mist-600">
                                        ({grundText(grundWort)})
                                      </span>
                                    )}
                                  </label>
                                ),
                              )}
                            </div>
                          );
                        })()}

                        {/* 4K - nur wenn es ueberhaupt eine zweite Instanz gibt.
                        Ohne sie waeren das drei Haken ohne jede Wirkung. */}
                        {(config?.radarr_uhd_configured ||
                          config?.sonarr_uhd_configured) && (
                          <div className="flex flex-wrap gap-4 sm:col-span-4">
                            {(
                              [
                                [
                                  "can_request_uhd_movies",
                                  "uhd.canRequestMovies",
                                  config?.radarr_uhd_configured,
                                ],
                                [
                                  "can_request_uhd_series",
                                  "uhd.canRequestSeries",
                                  config?.sonarr_uhd_configured,
                                ],
                                ["auto_approve_uhd", "uhd.autoApprove", true],
                              ] as const
                            ).map(([schluessel, labelKey, sichtbar]) => {
                              if (!sichtbar) return null;
                              // Die 4K-Freigabe ist nur sinnvoll, solange fuer
                              // mindestens eine Medienart, die dieser Benutzer in
                              // 4K anfragen darf, nicht ohnehin der Entscheider
                              // waehlt. Sonst wartet dort jede Anfrage.
                              const nochSinnvoll =
                                (Boolean(
                                  feld(user, "can_request_uhd_movies"),
                                ) &&
                                  Boolean(config?.radarr_uhd_configured) &&
                                  !config?.approver_picks_target_movie_uhd) ||
                                (Boolean(
                                  feld(user, "can_request_uhd_series"),
                                ) &&
                                  Boolean(config?.sonarr_uhd_configured) &&
                                  !config?.approver_picks_target_tv_uhd);
                              const istFreigabe =
                                schluessel === "auto_approve_uhd";
                              // Wer freigeben darf, darf 4K anfragen *und* gibt es
                              // sich selbst frei - beides ist keine Einstellung,
                              // sondern folgt aus der Rolle. Ein anklickbares
                              // Kaestchen daneben waere eine Einladung zu einer
                              // Aenderung, die gar nichts bewirkt.
                              const gesperrt =
                                user.can_approve ||
                                (istFreigabe && !nochSinnvoll);
                              return (
                                <label
                                  key={schluessel}
                                  className={
                                    "flex items-center gap-2 text-sm " +
                                    (gesperrt
                                      ? "text-mist-600"
                                      : "text-mist-300")
                                  }
                                >
                                  <input
                                    type="checkbox"
                                    checked={
                                      user.can_approve
                                        ? true
                                        : istFreigabe
                                          ? entwurfOder(
                                              user,
                                              "auto_approve_uhd",
                                              user.effective_auto_approve_uhd,
                                            ) && nochSinnvoll
                                          : Boolean(feld(user, schluessel))
                                    }
                                    disabled={gesperrt}
                                    onChange={(event) =>
                                      setzen(
                                        user,
                                        schluessel,
                                        event.target.checked,
                                      )
                                    }
                                    className="h-4 w-4 accent-accent-500 disabled:opacity-60"
                                  />
                                  {t(labelKey)}
                                  {user.can_approve && (
                                    <span className="text-xs text-mist-600">
                                      ({t("adminUsers.autoApproveAdmin")})
                                    </span>
                                  )}
                                  {istFreigabe &&
                                    !user.can_approve &&
                                    !nochSinnvoll && (
                                      <span className="text-xs text-mist-600">
                                        (
                                        {grundText(
                                          feld(user, "can_request_uhd_movies")
                                            ? "movie"
                                            : "tv",
                                        )}
                                        )
                                      </span>
                                    )}
                                </label>
                              );
                            })}
                          </div>
                        )}

                        {/* Eine eigene Überschrift, weil hier ein anderer
                            Gedanke anfängt: Darüber steht, **was** jemand darf
                            (freigeben, 4K), hier **wie viel**. Ohne Trennung
                            liest sich die Karte wie eine einzige lange Reihe
                            von Feldern. */}
                        <h3 className="border-t border-ink-700 pt-4 text-sm font-semibold sm:col-span-4">
                          {t("adminUsers.limitsTitle")}
                        </h3>

                        {/* ⚠️ **Alle drei Grenzen stehen immer da.** Vorher
                            blendete die Betriebsart des Hauses die eine oder
                            die andere Haelfte aus - jetzt gelten sie zusammen,
                            und eine Anfrage muss durch beide.

                            Drei Zustaende, deshalb ein Auswahlfeld und nicht
                            nur eine Zahl: "Standard" faellt auf den Wert des
                            Hauses zurueck, "unbegrenzt" hebt ihn ausdruecklich
                            auf, und eine Zahl gilt genau so - die **0 heisst
                            "darf nichts"**. Als leeres Feld waere der
                            Unterschied zwischen den ersten beiden unsichtbar
                            gewesen. */}
                        {GRENZEN.map(({ feld: grenzfeld, label, vorgabe, gb }) => {
                          // Admins haben immer unbegrenzt - genau wie bei
                          // Sperrliste, Freigabe und 4K. Sie setzen die Grenzen
                          // und koennten die eigene jederzeit heraufsetzen; ein
                          // Feld dafuer waere eine Eingabe ohne Wirkung.
                          const immerFrei = feld(user, "role") === "admin";
                          const entwurf = grenze(user, grenzfeld);
                          return (
                            <div key={grenzfeld} className="flex flex-col gap-1.5">
                              <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                                {t(label, { period: zeitraumText })}
                              </span>
                              <select
                                value={immerFrei ? "unlimited" : entwurf.modus}
                                disabled={immerFrei}
                                aria-label={t(label, { period: zeitraumText })}
                                onChange={(event) =>
                                  setGrenze(user, grenzfeld, {
                                    ...entwurf,
                                    modus: event.target
                                      .value as Grenzentwurf["modus"],
                                  })
                                }
                                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50"
                              >
                                <option value="standard">
                                  {t("adminUsers.limitDefault")}
                                </option>
                                <option value="unlimited">
                                  {t("adminUsers.quotaUnlimited")}
                                </option>
                                <option value="zahl">
                                  {t("adminUsers.limitOwn")}
                                </option>
                              </select>
                              {!immerFrei && entwurf.modus === "zahl" && (
                                <div className="flex items-center gap-2">
                                  <input
                                    type="number"
                                    min={0}
                                    value={entwurf.zahl}
                                    aria-label={t(label, { period: zeitraumText })}
                                    onChange={(event) =>
                                      setGrenze(user, grenzfeld, {
                                        modus: "zahl",
                                        zahl: event.target.value,
                                      })
                                    }
                                    className="w-full rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                                  />
                                  {gb && (
                                    <span className="text-sm text-mist-500">
                                      GB
                                    </span>
                                  )}
                                </div>
                              )}
                              <span className="text-xs text-mist-600">
                                {immerFrei
                                  ? t("adminUsers.quotaAdmin")
                                  : t("adminUsers.limitHint", {
                                      standard: vorgabeText(vorgabe),
                                    })}
                              </span>
                            </div>
                          );
                        })}

                        {/* Verbrauch im laufenden Zeitraum - und der Weg, ihn wieder
                    freizugeben, ohne die Anfragen zu löschen. */}
                        <div className="flex flex-wrap items-center gap-3 sm:col-span-4">
                          <span className="text-sm text-mist-500">
                            {t("adminUsers.usedNow", {
                              movies: verbrauchText(
                                user.quota_movies_used,
                                user.quota_movies_limit,
                              ),
                              series: verbrauchText(
                                user.quota_series_used,
                                user.quota_series_limit,
                              ),
                            })}
                          </span>
                          {/* Immer angeboten: Ob eine Grenze greift, hängt seit
                      den Standardwerten nicht mehr allein am Konto - und wer
                      hier nachsieht, will den Zähler zurücksetzen können. */}
                          {
                            <Button
                              variant="ghost"
                              onClick={() => setQuotaReset(user)}
                              disabled={
                                geschuetzt ||
                                (user.quota_movies_used === 0 &&
                                  user.quota_series_used === 0)
                              }
                              title={
                                geschuetzt
                                  ? t("adminUsers.betreiberGesperrt")
                                  : undefined
                              }
                            >
                              {t("adminUsers.resetQuota")}
                            </Button>
                          }
                          {/* Das Gegenstück: Der Zähler oben erneuert sich von
                      selbst, der belegte Platz nie. Gegen das eine hilft
                      warten, gegen das andere nur dieser Knopf. */}
                          {feld(user, "role") !== "admin" && (
                            <Button
                              variant="ghost"
                              onClick={() => setSpeicherReset(user)}
                              disabled={geschuetzt}
                              title={
                                geschuetzt
                                  ? t("adminUsers.betreiberGesperrt")
                                  : undefined
                              }
                            >
                              {t("adminUsers.resetStorage")}
                            </Button>
                          )}
                          {user.quota_reset_at && (
                            <span className="text-xs text-mist-600">
                              {t("adminUsers.lastReset", {
                                date: formatDate(
                                  user.quota_reset_at.slice(0, 10),
                                  i18n.language,
                                ),
                              })}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* ⚠️ Die Altersbeschränkung stand früher hier.
                          Sie ist entfallen: Wer ein vollwertiges Konto hat,
                          gilt als volljährig, und Kinder bekommen ein
                          Kinderkonto, dessen Alter das Elternteil unter
                          „Kinder" pflegt. Zwei Wege zu derselben Sperre wären
                          zwei Stellen, an denen sie auseinanderläuft. */}

                      {/* Kein Haken = alle Profile erlaubt. So muss man nur dort etwas
                  einstellen, wo wirklich begrenzt werden soll.

                  Für Admins und Entscheider entfällt der Block ganz: Sie geben
                  sich selbst frei, könnten die Sperre also jederzeit aufheben -
                  sie einzustellen sähe nach einer Grenze aus, die keine ist.

                  Wählt der Entscheider das Profil erst bei der Freigabe, sucht
                  der Benutzer gar keines aus. Dann bleibt die Liste sichtbar,
                  aber gesperrt - mit Begründung, statt kommentarlos zu
                  verschwinden. */}
                      {!user.can_approve &&
                        (
                          [
                            [
                              "movie",
                              "standard",
                              movieProfiles.data,
                              "adminUsers.allowedMovieProfiles",
                            ],
                            [
                              "tv",
                              "standard",
                              seriesProfiles.data,
                              "adminUsers.allowedSeriesProfiles",
                            ],
                            [
                              "movie",
                              "uhd",
                              movieUhdProfiles.data,
                              "uhd.blockedProfiles",
                            ],
                            [
                              "tv",
                              "uhd",
                              seriesUhdProfiles.data,
                              "uhd.blockedProfiles",
                            ],
                          ] as const
                        ).map(([media, tier, daten, labelKey]) => {
                          const frei = profilFreiWaehlbar(media);
                          return daten && daten.quality_profiles.length > 0 ? (
                            <div
                              key={`${media}-${tier}`}
                              className="border-t border-ink-700 pt-4"
                            >
                              <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                                {t(labelKey)}
                                {tier === "uhd" &&
                                  ` · ${t(media === "movie" ? "common.movies" : "common.seriesPlural")}`}
                              </p>
                              <div className="mt-2 flex flex-wrap gap-2">
                                {daten.quality_profiles.map((profil) => {
                                  const gesperrteListe =
                                    tier === "uhd"
                                      ? media === "movie"
                                        ? feld(
                                            user,
                                            "blocked_movie_uhd_profiles",
                                          )
                                        : feld(
                                            user,
                                            "blocked_series_uhd_profiles",
                                          )
                                      : media === "movie"
                                        ? feld(user, "blocked_movie_profiles")
                                        : feld(user, "blocked_series_profiles");
                                  const gewaehlt = gesperrteListe.includes(
                                    profil.id,
                                  );
                                  return (
                                    <label
                                      key={profil.id}
                                      className={
                                        "flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors " +
                                        (frei
                                          ? "cursor-pointer "
                                          : "cursor-not-allowed opacity-50 ") +
                                        (gewaehlt
                                          ? "border-accent-500/60 bg-accent-500/15 text-accent-400"
                                          : "border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100")
                                      }
                                    >
                                      <input
                                        type="checkbox"
                                        checked={gewaehlt}
                                        disabled={!frei}
                                        onChange={(event) =>
                                          toggleProfile(
                                            user,
                                            media,
                                            tier,
                                            profil.id,
                                            event.target.checked,
                                          )
                                        }
                                        className="h-3.5 w-3.5 accent-accent-500"
                                      />
                                      {profil.name}
                                    </label>
                                  );
                                })}
                              </div>
                              <p className="mt-1.5 text-xs text-mist-600">
                                {!frei
                                  ? t("adminUsers.profilesByApprover")
                                  : (tier === "uhd"
                                        ? media === "movie"
                                          ? feld(
                                              user,
                                              "blocked_movie_uhd_profiles",
                                            )
                                          : feld(
                                              user,
                                              "blocked_series_uhd_profiles",
                                            )
                                        : media === "movie"
                                          ? feld(user, "blocked_movie_profiles")
                                          : feld(
                                              user,
                                              "blocked_series_profiles",
                                            )
                                      ).length === 0
                                    ? t("adminUsers.noProfilesBlocked")
                                    : t("adminUsers.someProfilesBlocked")}
                              </p>
                            </div>
                          ) : null;
                        })}

                      {/* Ein Knopf für die ganze Karte - wie auf jeder anderen
                  Einstellungsseite. Vorher schrieb jedes Häkchen sofort. */}
                      <div className="flex flex-wrap items-center gap-3 border-t border-ink-700 pt-4">
                        <Button
                          onClick={() => speichern(user)}
                          loading={updateMutation.isPending}
                          disabled={!geaendert(user)}
                        >
                          {t("common.save")}
                        </Button>
                        {geaendert(user) && (
                          <>
                            <Button
                              variant="ghost"
                              onClick={() => verwerfen(user)}
                            >
                              {t("common.cancel")}
                            </Button>
                            <span className="text-xs text-warn-500">
                              {t("adminUsers.unsaved")}
                            </span>
                          </>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center gap-2 border-t border-ink-700 pt-4">
                        <Button
                          variant="ghost"
                          disabled={geschuetzt}
                          title={
                            geschuetzt
                              ? t("adminUsers.betreiberGesperrt")
                              : undefined
                          }
                          onClick={() => {
                            setResetting(
                              resetting === user.id ? null : user.id,
                            );
                            setNewPassword("");
                          }}
                        >
                          {t("adminUsers.resetPassword")}
                        </Button>
                        {!isMe && (
                          <Button
                            variant="ghost"
                            disabled={geschuetzt}
                            title={
                              geschuetzt
                                ? t("adminUsers.betreiberGesperrt")
                                : undefined
                            }
                            onClick={() => setDeleting(user)}
                          >
                            {t("adminUsers.delete")}
                          </Button>
                        )}

                        {/* ⚠️ Der Satz gehört hierher, nicht in einen
                            Tooltip allein. Wer graue Knöpfe sieht und nicht
                            erfährt, warum, hält es für einen Fehler und
                            probiert es beim nächsten Mal wieder. */}
                        {geschuetzt && (
                          <p className="w-full text-xs text-mist-600">
                            {t("adminUsers.betreiberGesperrtLang")}
                          </p>
                        )}

                        {resetting === user.id && (
                          <div className="flex w-full flex-wrap items-center gap-2 pt-2">
                            <input
                              type="password"
                              value={newPassword}
                              onChange={(event) =>
                                setNewPassword(event.target.value)
                              }
                              placeholder={t("adminUsers.newPassword")}
                              aria-label={t("adminUsers.newPassword")}
                              minLength={minPassword}
                              className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                            />
                            <Button
                              onClick={() =>
                                passwordMutation.mutate({
                                  id: user.id,
                                  password: newPassword,
                                })
                              }
                              loading={passwordMutation.isPending}
                              disabled={newPassword.length < minPassword}
                            >
                              {t("common.save")}
                            </Button>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </Card>
              );
            })}
          </div>
        );
      })}

      {deleting && (
        <AdminKontoAufloesung
          benutzer={deleting}
          onSchliessen={() => setDeleting(null)}
          onGeloescht={() => {
            setDeleting(null);
            setError(null);
            setMessage(t("adminUsers.saved"));
            refresh();
          }}
        />
      )}

      <ConfirmDialog
        open={quotaReset !== null}
        title={t("adminUsers.resetQuotaTitle")}
        description={t("adminUsers.resetQuotaText", {
          name: quotaReset?.username ?? "",
        })}
        warning={t("adminUsers.resetQuotaHint")}
        confirmLabel={t("adminUsers.resetQuota")}
        loading={resetQuotaMutation.isPending}
        onCancel={() => setQuotaReset(null)}
        onConfirm={() => quotaReset && resetQuotaMutation.mutate(quotaReset.id)}
      />

      <ConfirmDialog
        open={speicherReset !== null}
        title={t("adminUsers.resetStorageTitle")}
        description={t("adminUsers.resetStorageText", {
          name: speicherReset?.username ?? "",
        })}
        warning={t("adminUsers.resetStorageHint")}
        confirmLabel={t("adminUsers.resetStorage")}
        loading={resetSpeicherMutation.isPending}
        onCancel={() => setSpeicherReset(null)}
        onConfirm={() =>
          speicherReset && resetSpeicherMutation.mutate(speicherReset.id)
        }
      />
    </div>
  );
}
