import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type {
  AnfragerSpeicher,
  MediaRequestWithUser,
  MediaStatus,
  MediaType,
} from "../api/types";
import { useAuth } from "../auth/useAuth";
import { Avatar } from "../components/Avatar";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StarRating } from "../components/StarRating";
import { StatusBadge } from "../components/media/StatusBadge";
import { Button, Card, ErrorBanner, Spinner } from "../components/ui";
import { Pagination, useSeiten } from "../components/Pagination";
import { formatDate, formatSize } from "../lib/format";
import { TargetPicker, type Target } from "../components/TargetPicker";
import { useConfig } from "../hooks/useConfig";
import { anfragenStandNeuLaden } from "../lib/refresh";
import { TitelVerweis } from '../components/TitelVerweis'

// "watchlist" ist kein Zustand, sondern eine Herkunft - deshalb ein eigener
// Wert neben den Zustaenden. Der Knopf erscheint nur, wenn die Automatik
// ueberhaupt eingeschaltet ist; sonst gaebe es dort nie etwas zu sehen.
type Filter = "pending_approval" | "all" | "feedback" | "watchlist" | MediaStatus;

const FILTERS: Filter[] = [
  "pending_approval",
  // Direkt hinter den wartenden, und aus einem Grund: Zurückgestellte sind
  // **nicht erledigt**. Sie warten auf freien Speicher, und wer sie nicht
  // wiederfindet, kann sie auch nicht freigeben, wenn wieder Platz ist – dann
  // wäre das Versprechen „niemand muss neu fragen" leer.
  "deferred",
  "feedback",
  "watchlist",
  "all",
  "searching",
  "downloaded",
  "rejected",
  "cancelled",
  "deleted",
  "failed",
];

/** Adresse für den gewählten Filter. */
function urlFuer(filter: Filter): string {
  if (filter === "all") return "/api/admin/requests";
  if (filter === "feedback") return "/api/admin/requests?feedback=true";
  if (filter === "watchlist") return "/api/admin/requests?from_watchlist=true";
  return `/api/admin/requests?status=${filter}`;
}

/** Zustände, in denen ein Abbruch möglich ist (Titel liegt in Radarr/Sonarr). */
const CANCELLABLE: ReadonlySet<string> = new Set(["approved", "searching"]);

type Gruppe = {
  userId: number;
  username: string;
  displayName: string | null;
  avatar: string | null;
  /**
   * Speicherstand des Anfragenden – vom Server, einmal je Person.
   *
   * `null`/fehlt heißt, dass Speicher-Kontingente aus sind. Dann zählt die
   * Stückzahl, und eine Speicher-Zahl daneben wäre eine zweite Währung in
   * derselben Karte.
   */
  storage: AnfragerSpeicher | null;
  requests: MediaRequestWithUser[];
};

/** Ab hier (und darunter) gilt eine Rückmeldung als Beschwerde. */
const POOR_RATING = 2;

/** Rückmeldung des Anfragenden lesen und beantworten. */
function FeedbackReview({
  request,
  darfAntworten,
  onSaved,
}: {
  request: MediaRequestWithUser;
  /** Antworten ist dem Administrator vorbehalten. */
  darfAntworten: boolean;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(request.feedback_reply ?? "");
  const [offen, setOffen] = useState(false);

  const antworten = useMutation({
    mutationFn: () =>
      api.post(`/api/admin/requests/${request.id}/reply`, {
        reply: text.trim(),
      }),
    onSuccess: () => {
      setOffen(false);
      onSaved();
    },
  });

  return (
    <div className="mt-3 border-t border-ink-700 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <StarRating value={request.rating ?? 0} size="sm" />
        <span className="text-xs text-mist-600">
          {t("feedback.from", {
            name: request.display_name ?? request.username,
          })}
        </span>
      </div>
      {request.feedback && (
        <p className="mt-1 text-sm text-mist-300">{request.feedback}</p>
      )}

      {request.feedback_reply && !offen && (
        <div className="mt-2 rounded-xl border border-ink-700 bg-ink-850/60 px-3 py-2">
          <p className="text-xs font-medium text-accent-500">
            {t("feedback.replyTitle")}
          </p>
          <p className="mt-0.5 text-sm text-mist-300">
            {request.feedback_reply}
          </p>
        </div>
      )}

      {!darfAntworten ? null : offen ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={1000}
            placeholder={t("feedback.replyPlaceholder")}
            aria-label={t("feedback.replyPlaceholder")}
            className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
          <Button
            onClick={() => antworten.mutate()}
            loading={antworten.isPending}
            disabled={!text.trim()}
          >
            {t("feedback.sendReply")}
          </Button>
          <Button variant="ghost" onClick={() => setOffen(false)}>
            {t("common.cancel")}
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOffen(true)}
          className="mt-2 text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
        >
          {request.feedback_reply
            ? t("feedback.changeReply")
            : t("feedback.reply")}
        </button>
      )}

      {antworten.isError && (
        <p className="mt-1 text-xs text-bad-500">
          {antworten.error instanceof ApiError
            ? antworten.error.message
            : t("feedback.failed")}
        </p>
      )}
    </div>
  );
}

export function AdminRequestsPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  // Antworten auf Rückmeldungen ist dem Administrator vorbehalten.
  const istAdmin = user?.role === "admin";

  // Aus der Adresse vorbelegen, damit ein Klick in der Glocke direkt in der
  // richtigen Ansicht landet.
  const [suche, setSuche] = useSearchParams();
  const [filter, setFilterState] = useState<Filter>(() =>
    suche.get("filter") === "feedback" ? "feedback" : "pending_approval",
  );

  function setFilter(wert: Filter) {
    setFilterState(wert);
    // Einen offenen Ablehnen-Bereich mitschließen. Er hängt an einer
    // Anfragenummer, und die kommt in der nächsten Liste womöglich wieder vor -
    // dann klappte er ausgerechnet bei einer längst abgelehnten Anfrage auf,
    // samt Knopf "Ablehnen bestätigen".
    setRejecting(null);
    setReason("");
    setSperren(false);
    // Der Parameter hat seinen Zweck erfüllt - sonst spränge ein Neuladen
    // wieder zurück.
    if (suche.has("filter")) setSuche({}, { replace: true });
  }
  const [rejecting, setRejecting] = useState<number | null>(null);
  /** Beim Ablehnen zusätzlich sperren? Nur für Administratoren. */
  const [sperren, setSperren] = useState(false);
  const [reason, setReason] = useState("");
  const [cancelling, setCancelling] = useState<MediaRequestWithUser | null>(
    null,
  );

  const requestsQuery = useQuery({
    queryKey: ["admin-requests", filter],
    queryFn: () => api.get<MediaRequestWithUser[]>(urlFuer(filter)),
  });

  const { data: config } = useConfig();
  // Welche Freigabe wartet gerade auf die Ordnerwahl? Kennung der Anfrage
  // bzw. des Benutzers bei der Sammelfreigabe.
  const [zielFuer, setZielFuer] = useState<number | null>(null);
  const [sammelZielFuer, setSammelZielFuer] = useState<number | null>(null);
  const [ziel, setZiel] = useState<Target | null>(null);
  // Je Kombination aus Medienart und Stufe ein eigenes Ziel: Das sind bis zu
  // vier verschiedene Instanzen mit vollkommen verschiedenen Ordnern.
  const [stapelZiele, setStapelZiele] = useState<Record<string, Target | null>>(
    {},
  );

  /**
   * Muss vor der Freigabe noch ein Ordner gewählt werden?
   *
   * **Allein daran, ob der Anfrage einer fehlt** – nicht daran, ob die Regel
   * "der Entscheider wählt" gerade gilt. Altbestand hat seinen Ordner längst,
   * ist also nie betroffen.
   *
   * Die Regel mitzuprüfen war ein Fehler, und ein tückischer: Kommt eine
   * Anfrage ohne Ordner herein, weil der Entscheider wählen soll, und stellt
   * der Betreiber danach auf einen festen Ordner um, verschwand hier das
   * Auswahlfeld – während der Server weiterhin eine Wahl verlangte. Die
   * Anfrage ließ sich dann weder freigeben noch reparieren.
   *
   * Der Entscheider darf ohnehin immer wählen (siehe
   * ``requests_service.apply_target``). Fehlt der Ordner, fragen wir ihn also
   * – statt still den Standardordner zu nehmen, was genau der Fehler wäre,
   * den die Einstellung verhindern soll.
   */
  function brauchtZiel(request: {
    root_folder_path: string | null;
    media_type: MediaType;
  }): boolean {
    return request.root_folder_path === null;
  }

  function refresh() {
    anfragenStandNeuLaden(queryClient);
    void queryClient.invalidateQueries({ queryKey: ["admin-requests"] });
    void queryClient.invalidateQueries({ queryKey: ["pending-count"] });
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  }

  const approveMutation = useMutation({
    mutationFn: ({ id, target }: { id: number; target: Target | null }) =>
      api.post(`/api/admin/requests/${id}/approve`, target ?? undefined),
    onSuccess: () => {
      setZielFuer(null);
      setZiel(null);
    },
    onSettled: refresh,
  });

  const approveAllMutation = useMutation({
    mutationFn: ({
      userId,
      ziele,
    }: {
      userId: number;
      ziele: Record<string, Target | null>;
    }) =>
      api.post(
        `/api/admin/requests/approve-all/${userId}`,
        Object.values(ziele).some(Boolean) ? ziele : undefined,
      ),
    onSuccess: () => {
      setSammelZielFuer(null);
      setStapelZiele({});
    },
    onSettled: refresh,
  });

  /**
   * Die Rückfrage, wenn ein überzogenes Konto freigegeben werden soll.
   *
   * Drei Ausgänge, und der dritte ist der eigentliche Gewinn: **Abbrechen
   * lässt die Anfragen stehen.** Sie warten dann weiter, und sobald der
   * Anfragende Platz geschaffen hat, lassen sie sich freigeben – ohne dass er
   * sie neu stellen muss. Ein reines ja/nein hätte diesen Weg nicht.
   */
  const [speicherFrage, setSpeicherFrage] = useState<{
    gruppe: Gruppe;
    betroffen: number[];
    ausfuehren: () => void;
  } | null>(null);

  const deferManyMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      // Nacheinander, nicht parallel: SQLite lässt genau einen Schreiber zu.
      for (const id of ids) {
        await api.post(`/api/admin/requests/${id}/defer`, {});
      }
    },
    onSuccess: () => setSpeicherFrage(null),
    onSettled: refresh,
  });

  const rejectManyMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      // Nacheinander, nicht parallel: SQLite lässt genau einen Schreiber zu,
      // und bei zwei Anfragen ist der Unterschied ohnehin nicht messbar.
      for (const id of ids) {
        await api.post(`/api/admin/requests/${id}/reject`, {
          reason: t("adminRequests.storageRejectReason"),
          block: false,
        });
      }
    },
    onSuccess: () => setSpeicherFrage(null),
    onSettled: refresh,
  });

  /**
   * Freigeben – aber erst fragen, wenn das Konto **schon** überzogen ist.
   *
   * Steht bewusst an allen vier Freigabe-Knöpfen und nicht nur an der
   * Sammelfreigabe: Zehnmal einzeln freigeben hat dieselbe Wirkung wie einmal
   * sammeln, und eine Warnung, die man durch Einzelklicks umgeht, ist keine.
   */
  function freigeben(gruppe: Gruppe, betroffen: number[], ausfuehren: () => void) {
    if (!gruppe.storage?.exhausted) {
      ausfuehren();
      return;
    }
    setSpeicherFrage({ gruppe, betroffen, ausfuehren });
  }

  const rejectMutation = useMutation({
    mutationFn: ({
      id,
      text,
      block,
    }: {
      id: number;
      text: string;
      block: boolean;
    }) =>
      api.post(`/api/admin/requests/${id}/reject`, {
        reason: text || undefined,
        block,
      }),
    onSuccess: () => {
      setRejecting(null);
      setReason("");
      setSperren(false);
      refresh();
      // Die Sperrliste und alle Kacheln zeigen den Titel jetzt anders.
      void queryClient.invalidateQueries({ queryKey: ["blocklist"] });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/admin/requests/${id}/cancel`),
    onSuccess: () => setCancelling(null),
    onSettled: refresh,
  });

  const requests = requestsQuery.data ?? [];
  const failure =
    approveMutation.error ??
    rejectMutation.error ??
    cancelMutation.error ??
    approveAllMutation.error;

  // Erst blättern, dann gruppieren: Die Gruppen entstehen aus dem, was auf
  // dieser Seite steht. Andersherum wäre eine "Seite" mal drei und mal
  // dreißig Zeilen lang, je nachdem wie viele Titel auf einen Benutzer
  // entfallen.
  const blaettern = useSeiten(requests, filter);

  // Nach Benutzer gruppieren, damit man nicht jeden Titel einzeln freigeben muss.
  const gruppen: Gruppe[] = [];
  for (const request of blaettern.sichtbar) {
    let gruppe = gruppen.find((eintrag) => eintrag.userId === request.user_id);
    if (!gruppe) {
      gruppe = {
        userId: request.user_id,
        username: request.username,
        displayName: request.display_name,
        avatar: request.avatar_url,
        storage: request.storage ?? null,
        requests: [],
      };
      gruppen.push(gruppe);
    }
    gruppe.requests.push(request);
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {t("nav.allRequests")}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-1.5 text-mist-500">{t("adminRequests.intro")}</p>
        </div>
        <Link
          to="/admin/stats"
          className="rounded-full border border-ink-700 px-4 py-2 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100"
        >
          {t("nav.stats")}
        </Link>
      </header>

      <div className="flex flex-wrap gap-2">
        {FILTERS.filter(
          (value) => value !== "watchlist" || Boolean(config?.watchlist_enabled),
        ).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setFilter(value)}
            aria-pressed={filter === value}
            className={
              "rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors " +
              (filter === value
                ? "border-accent-500/60 bg-accent-500/15 text-accent-400"
                : "border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100")
            }
          >
            {value === "all"
              ? t("adminRequests.filterAll")
              : value === "feedback"
                ? t("adminRequests.filterFeedback")
                : value === "watchlist"
                  ? t("myRequests.fromWatchlistTab")
                  : t(`status.${value}`)}
          </button>
        ))}
      </div>

      {failure && (
        <ErrorBanner
          message={
            failure instanceof ApiError ? failure.message : t("errors.generic")
          }
        />
      )}

      {requestsQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t("common.loading")}
        </p>
      )}

      {!requestsQuery.isPending && requests.length === 0 && (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {filter === "pending_approval"
            ? t("adminRequests.noPending")
            : filter === "feedback"
              ? t("adminRequests.emptyFeedback")
              : t("adminRequests.empty")}
        </p>
      )}

      {gruppen.map((gruppe) => {
        // **Über die ganze Liste**, nicht über die sichtbare Seite: Der
        // Sammel-Knopf gibt serverseitig *alle* wartenden Anfragen dieses
        // Benutzers frei. Zählte er nur die Seite, stünde "3 freigeben" auf
        // einem Knopf, der fünfundzwanzig freigibt - und die Prüfung, ob
        // eine davon noch ein Ziel braucht, übersähe die übrigen Seiten.
        const offene = requests.filter(
          (r) =>
            r.user_id === gruppe.userId && r.status === "pending_approval",
        );
        return (
          <Card key={gruppe.username} className="flex flex-col gap-3 p-4">
            <div className="flex flex-wrap items-center gap-3 border-b border-ink-700 pb-3">
              <Avatar
                url={gruppe.avatar}
                name={gruppe.displayName ?? gruppe.username}
                className="h-10 w-10"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold">
                  {gruppe.displayName ?? gruppe.username}
                </p>
                <p className="text-xs text-mist-600">
                  {t("adminRequests.countForUser", {
                    count: gruppe.requests.length,
                  })}
                  {/* Der Speicherstand steht dort, wo entschieden wird.
                      Vorher prüfte die Freigabe **gar nichts**: Wer fünf
                      Anfragen anlegt, solange noch Platz ist, dann über seine
                      Grenze rutscht und danach sammelfreigegeben wird, landet
                      beliebig weit im Minus – und niemand sieht es dabei. */}
                  {gruppe.storage && (
                    <span
                      className={
                        "ml-2 " + (gruppe.storage.exhausted ? "text-bad-500" : "")
                      }
                    >
                      ·{" "}
                      {gruppe.storage.limit_bytes === null
                        ? formatSize(gruppe.storage.used_bytes, i18n.language)
                        : t("storage.usedOfLimit", {
                            used: formatSize(
                              gruppe.storage.used_bytes,
                              i18n.language,
                            ),
                            limit: formatSize(
                              gruppe.storage.limit_bytes,
                              i18n.language,
                            ),
                          })}
                    </span>
                  )}
                </p>
              </div>

              {offene.length > 1 && (
                <Button
                  onClick={() => {
                    // Offene Einzel-Freigaben zuklappen: Sonst stehen deren
                    // Auswahlfelder unter dem Stapel-Dialog weiter offen und
                    // man sieht nicht mehr, welche Auswahl wofür gilt.
                    setZielFuer(null);
                    setZiel(null);
                    setRejecting(null);
                    if (offene.some(brauchtZiel)) {
                      setStapelZiele({});
                      setSammelZielFuer(
                        sammelZielFuer === gruppe.userId ? null : gruppe.userId,
                      );
                      return;
                    }
                    freigeben(
                      gruppe,
                      offene.map((eintrag) => eintrag.id),
                      () =>
                        approveAllMutation.mutate({
                          userId: gruppe.userId,
                          ziele: {},
                        }),
                    );
                  }}
                  loading={
                    approveAllMutation.isPending &&
                    approveAllMutation.variables?.userId === gruppe.userId
                  }
                >
                  {t("adminRequests.approveAll", { count: offene.length })}
                </Button>
              )}
            </div>

            {/* Je Kombination aus Medienart und Stufe eine eigene Wahl: Das
                sind bis zu vier Instanzen mit vollkommen verschiedenen Ordnern
                und Profilen. Gezeigt wird nur, was im Stapel vorkommt. */}
            {sammelZielFuer === gruppe.userId && (
              <div className="flex flex-col gap-3 rounded-xl border border-ink-700 bg-ink-900/50 p-3">
                {(
                  [
                    ["movie", "standard", "common.movies"],
                    ["movie", "uhd", "common.movies"],
                    ["tv", "standard", "common.seriesPlural"],
                    ["tv", "uhd", "common.seriesPlural"],
                  ] as const
                ).map(([art, stufe, labelKey]) => {
                  const vorhanden = offene.some(
                    (r) =>
                      brauchtZiel(r) &&
                      r.media_type === art &&
                      r.tier === stufe,
                  );
                  if (!vorhanden) return null;
                  const schluessel = stufe === "uhd" ? `${art}_uhd` : art;
                  return (
                    <TargetPicker
                      key={schluessel}
                      mediaType={art}
                      tier={stufe}
                      label={t(labelKey) + (stufe === "uhd" ? " · 4K" : "")}
                      onChange={(ziel) =>
                        setStapelZiele((bisher) => ({
                          ...bisher,
                          [schluessel]: ziel,
                        }))
                      }
                    />
                  );
                })}
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() =>
                      freigeben(
                        gruppe,
                        offene.map((eintrag) => eintrag.id),
                        () =>
                          approveAllMutation.mutate({
                            userId: gruppe.userId,
                            ziele: stapelZiele,
                          }),
                      )
                    }
                    loading={approveAllMutation.isPending}
                    disabled={!Object.values(stapelZiele).some(Boolean)}
                  >
                    {t("adminRequests.confirmApprove")}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => setSammelZielFuer(null)}
                  >
                    {t("common.cancel")}
                  </Button>
                </div>
              </div>
            )}

            {gruppe.requests.map((request) => (
              <div
                key={request.id}
                className="rounded-xl border border-ink-700 bg-ink-900/50 p-3"
              >
                <div className="flex flex-wrap items-center gap-3">
                  {/* Siehe MyRequestsPage: auf dem Telefon eigene Zeile fuer
                      den Titel, damit er nicht auf ein Zeichen schrumpft. */}
                  <div className="w-full min-w-0 sm:w-auto sm:flex-1">
                    <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                      <TitelVerweis
                        mediaType={request.media_type}
                        tmdbId={request.tmdb_id}
                        titel={request.title}
                        erschienen={request.release_date}
                        className="min-w-0 font-semibold break-words"
                      />
                      {request.season !== null && (
                        <span className="shrink-0 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs font-medium text-mist-400">
                          {t("request.seasonShort", { number: request.season })}
                        </span>
                      )}
                      {/* Haengt an der Anfrage selbst, nicht an der Einstellung:
                          Nimmt der Admin die 4K-Instanz heraus, waere eine laufende
                          4K-Anfrage sonst nicht mehr als solche zu erkennen. */}
                      {request.tier === "uhd" && (
                        <span className="shrink-0 rounded-full border border-accent-500/50 bg-accent-500/10 px-2 py-0.5 text-xs font-semibold text-accent-400">
                          4K
                        </span>
                      )}
                      {/* Von der Merkliste statt von einem Klick - siehe
                          MyRequestsPage. */}
                      {request.from_watchlist && (
                        <span className="shrink-0 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs font-medium text-mist-400">
                          {t("myRequests.fromWatchlist")}
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-mist-600">
                      {t(
                        request.media_type === "movie"
                          ? "common.movies"
                          : "common.series",
                      )}{" "}
                      · {t("myRequests.requestedAt")}{" "}
                      {formatDate(
                        request.requested_at.slice(0, 10),
                        i18n.language,
                      )}
                    </p>
                    {request.error_message && (
                      <p className="mt-1 text-xs text-bad-500">
                        {request.error_message}
                      </p>
                    )}
                    {request.rejection_reason && (
                      <p className="mt-1 text-xs text-mist-500">
                        {t("adminRequests.reason")}: {request.rejection_reason}
                      </p>
                    )}
                    {/* Läuft im Abo **des Anfragenden**. Ein Hinweis für die
                        Entscheidung, keine Sperre – und ausdrücklich an seinen
                        Abos gemessen, nicht an denen des Entscheiders. */}
                    {(request.requester_subscriptions ?? []).length > 0 && (
                      <p className="mt-1 text-xs text-warn-500">
                        {t("adminRequests.inRequesterSubscription", {
                          name: gruppe.displayName ?? gruppe.username,
                          services: (request.requester_subscriptions ?? []).join(
                            ", ",
                          ),
                        })}
                        {request.media_type === "tv" &&
                          ` ${t("adminRequests.inSubscriptionSeriesNote")}`}
                      </p>
                    )}
                  </div>

                  {/* Schwache Bewertungen sollen ohne Aufklappen auffallen. */}
                  {request.rating !== null && (
                    <span
                      title={t("feedback.stars", { count: request.rating })}
                      className={
                        "rounded-full border px-2 py-1 leading-none " +
                        (request.rating <= POOR_RATING
                          ? "border-bad-500/50 bg-bad-500/10"
                          : "border-ink-700 bg-ink-850")
                      }
                    >
                      <StarRating value={request.rating} size="sm" />
                    </span>
                  )}

                  <StatusBadge status={request.status} />

                  {request.status === "pending_approval" && (
                    <div className="flex gap-2">
                      <Button
                        onClick={() => {
                          // Fehlt der Ordner, erst danach fragen - sonst
                          // sofort freigeben wie bisher.
                          if (brauchtZiel(request)) {
                            setZiel(null);
                            setZielFuer(
                              zielFuer === request.id ? null : request.id,
                            );
                            return;
                          }
                          freigeben(gruppe, [request.id], () =>
                            approveMutation.mutate({
                              id: request.id,
                              target: null,
                            }),
                          );
                        }}
                        loading={
                          approveMutation.isPending &&
                          approveMutation.variables?.id === request.id
                        }
                      >
                        {t("adminRequests.approve")}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setSperren(false);
                          setRejecting(
                            rejecting === request.id ? null : request.id,
                          );
                          setReason("");
                        }}
                      >
                        {t("adminRequests.reject")}
                      </Button>
                    </div>
                  )}

                  {CANCELLABLE.has(request.status) && (
                    <Button
                      variant="ghost"
                      onClick={() => setCancelling(request)}
                      loading={
                        cancelMutation.isPending &&
                        cancelMutation.variables === request.id
                      }
                    >
                      {t("requests.cancel")}
                    </Button>
                  )}
                </div>

                {zielFuer === request.id && (
                  <div className="mt-3 flex flex-col gap-3 border-t border-ink-700 pt-3">
                    <TargetPicker
                      mediaType={request.media_type}
                      tier={request.tier}
                      onChange={setZiel}
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        onClick={() =>
                          freigeben(gruppe, [request.id], () =>
                            approveMutation.mutate({
                              id: request.id,
                              target: ziel,
                            }),
                          )
                        }
                        loading={approveMutation.isPending}
                        disabled={ziel === null}
                      >
                        {t("adminRequests.confirmApprove")}
                      </Button>
                      <Button variant="ghost" onClick={() => setZielFuer(null)}>
                        {t("common.cancel")}
                      </Button>
                    </div>
                  </div>
                )}

                {rejecting === request.id && (
                  <div className="mt-3 flex flex-col gap-2 border-t border-ink-700 pt-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        placeholder={t("adminRequests.reasonPlaceholder")}
                        aria-label={t("adminRequests.reasonPlaceholder")}
                        className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                      />
                      <Button
                        onClick={() =>
                          rejectMutation.mutate({
                            id: request.id,
                            text: reason,
                            block: istAdmin && sperren,
                          })
                        }
                        loading={rejectMutation.isPending}
                      >
                        {t("adminRequests.confirmReject")}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => setRejecting(null)}
                      >
                        {t("common.cancel")}
                      </Button>
                    </div>

                    {/* Nur der Administrator. Ein Entscheider entscheidet über
                        diese eine Anfrage; ob ein Titel grundsätzlich nicht in
                        die Bibliothek soll, ist Sache des Betreibers. Das
                        Backend weist es zusätzlich ab. */}
                    {istAdmin && (
                      <label className="flex cursor-pointer items-start gap-2 text-sm text-mist-300">
                        <input
                          type="checkbox"
                          checked={sperren}
                          onChange={(event) => setSperren(event.target.checked)}
                          className="mt-0.5 h-4 w-4 accent-accent-500"
                        />
                        <span>
                          {t("adminRequests.alsoBlock")}
                          <span className="mt-0.5 block text-xs text-mist-600">
                            {t("adminRequests.alsoBlockHint")}
                          </span>
                        </span>
                      </label>
                    )}
                  </div>
                )}

                {request.rating !== null && (
                  <FeedbackReview
                    request={request}
                    darfAntworten={istAdmin}
                    onSaved={refresh}
                  />
                )}
              </div>
            ))}
          </Card>
        );
      })}

      <Pagination
        seite={blaettern.seite}
        seiten={blaettern.seiten}
        onSeite={blaettern.setSeite}
      />

      <ConfirmDialog
        open={cancelling !== null}
        title={t("requests.cancelTitle")}
        description={t("requests.cancelTextAdmin", {
          title: cancelling?.title ?? "",
          name: cancelling?.display_name ?? cancelling?.username ?? "",
        })}
        warning={t("requests.cancelWarning")}
        confirmLabel={t("requests.cancelConfirm")}
        loading={cancelMutation.isPending}
        onCancel={() => setCancelling(null)}
        onConfirm={() => cancelling && cancelMutation.mutate(cancelling.id)}
      />

      {/* Überzogenes Konto: drei Ausgänge, nicht zwei.
          „Abbrechen" ist hier kein Rückzieher, sondern eine eigene
          Entscheidung – die Anfragen bleiben stehen und lassen sich freigeben,
          sobald wieder Platz ist. Ohne diesen Weg müsste der Entscheider
          zwischen „jetzt trotzdem" und „endgültig nein" wählen, und die
          ehrlichste Antwort – „später" – gäbe es gar nicht. */}
      <ConfirmDialog
        open={speicherFrage !== null}
        title={t("adminRequests.storageWarnTitle")}
        description={
          speicherFrage?.gruppe.storage
            ? t("adminRequests.storageWarnText", {
                name:
                  speicherFrage.gruppe.displayName ??
                  speicherFrage.gruppe.username,
                used: formatSize(
                  speicherFrage.gruppe.storage.used_bytes,
                  i18n.language,
                ),
                limit: formatSize(
                  speicherFrage.gruppe.storage.limit_bytes ?? 0,
                  i18n.language,
                ),
                over: formatSize(
                  speicherFrage.gruppe.storage.used_bytes -
                    (speicherFrage.gruppe.storage.limit_bytes ?? 0),
                  i18n.language,
                ),
                count: speicherFrage.betroffen.length,
              })
            : ""
        }
        warning={t("adminRequests.storageWarnHint")}
        confirmLabel={t("adminRequests.storageWarnApprove")}
        weitere={[
          {
            // Der eigentlich richtige Ausgang - und deshalb der erste.
            label: t("adminRequests.storageWarnDefer"),
            onClick: () =>
              speicherFrage && deferManyMutation.mutate(speicherFrage.betroffen),
          },
          {
            label: t("adminRequests.storageWarnReject"),
            onClick: () =>
              speicherFrage && rejectManyMutation.mutate(speicherFrage.betroffen),
            gefahr: true,
          },
        ]}
        loading={rejectManyMutation.isPending || deferManyMutation.isPending}
        onCancel={() => setSpeicherFrage(null)}
        onConfirm={() => {
          speicherFrage?.ausfuehren();
          setSpeicherFrage(null);
        }}
      />
    </div>
  );
}
