/**
 * `child` ist ein Kinderkonto: gehört zu genau einem erwachsenen Konto
 * (`parent_id`) und wird von dort verwaltet. Es entsteht nur über
 * `/api/children` – niemals über eine Einladung oder eine Rollenänderung.
 */
export type Role = "admin" | "approver" | "user" | "child";
export type QuotaPeriod = "day" | "week" | "month";

/** Eine Grenze am Konto: Standardwert des Hauses, ohne Grenze, oder eine Zahl. */
export type Kontingentwert = number | "standard" | "unlimited";

export type User = {
  id: number;
  username: string;
  role: Role;
  display_name: string | null;
  language: string;
  /** 'dark' oder 'light' - siehe lib/theme.ts. */
  theme: string;
  is_active: boolean;
  auto_approve: boolean;
  /** Was tatsächlich gilt – bei Administratoren immer true. */
  effective_auto_approve: boolean;
  /** Je Medienart – wie die Kontingente. `null` heißt „nicht eigens gesetzt". */
  auto_approve_movies: boolean | null;
  auto_approve_series: boolean | null;
  effective_auto_approve_movies: boolean;
  effective_auto_approve_series: boolean;
  can_approve: boolean;
  /**
   * Die drei Grenzen am Konto. Alle drei sprechen dieselbe Sprache:
   * `"standard"` = es gilt der Standardwert des Hauses, `"unlimited"` =
   * ausdrücklich ohne Grenze, eine Zahl = genau diese (die **0 heißt „darf
   * nichts"**).
   *
   * ⚠️ Bewusst Wörter statt Zahlen-Sentinels: In der Datenbank stehen `NULL`
   * und `-1`, aber ein Feld, in dem `-1` mal „Standard" und mal „unbegrenzt"
   * bedeutet, ist genau die Verwechslung, die niemand bemerkt – und am Ende
   * stünde eine `-1` im Eingabefeld.
   *
   * Der **Zeitraum** steht nicht mehr am Konto: Er gilt haus-weit und kommt
   * aus den Einstellungen (`quota_period`).
   */
  quota_movies_limit: Kontingentwert;
  quota_series_limit: Kontingentwert;
  storage_limit_gb: Kontingentwert;
  /** Leere Liste = alle Qualitätsprofile erlaubt. */
  blocked_movie_profiles: number[];
  blocked_series_profiles: number[];
  can_request_uhd_movies: boolean;
  can_request_uhd_series: boolean;
  auto_approve_uhd: boolean;
  effective_auto_approve_uhd: boolean;
  blocked_movie_uhd_profiles: number[];
  blocked_series_uhd_profiles: number[];
  avatar_url: string | null;
  created_at: string;
  last_login_at: string | null;
  /** Wann der Admin das Kontingent zuletzt von Hand zurückgesetzt hat. */
  quota_reset_at: string | null;
  /** Verbrauch im laufenden Zeitraum – nur in der Admin-Liste gefüllt. */
  quota_movies_used: number;
  quota_series_used: number;
  email: string | null;
  email_verified: boolean;

  /**
   * E-Mail-Benachrichtigungen – jede einzeln, Standard überall aus.
   * Die Glocke in der App ist davon nicht betroffen.
   */
  mail_download_complete: boolean;
  mail_request_pending: boolean;
  mail_request_decided: boolean;
  mail_feedback: boolean;
  /** Neue Tickets (für Admins) und Antworten darauf (für den Eigentümer). */
  mail_ticket: boolean;
  mail_watch: boolean;
  /** Neues Konto über den Media-Server – nur für Admins von Belang. */
  mail_user_imported: boolean;
  mail_mediaserver_reconnect: boolean;
  mail_child_wish: boolean;
  mail_cleanup: boolean;
  mail_storage: boolean;
  /**
   * Liegt ein persönlicher Plex-Zugang vor? Nur diese Auskunft – nie das
   * Token selbst. Ohne ihn lässt sich die eigene Merkliste nicht lesen.
   */
  watchlist_connected: boolean;

  /**
   * Verknüpfung mit dem Media-Server. Die Kennung selbst liefert der Server
   * bewusst nicht aus – für die Oberfläche zählt nur, ob und mit welchem Namen.
   */
  mediaserver_provider: string | null;
  mediaserver_username: string | null;
  mediaserver_linked: boolean;
  /**
   * Alle verknüpften Medienserver-Konten, je eine Zeile.
   *
   * Die beiden Felder darüber nennen nur das zuletzt hinzugekommene – im
   * Parallelbetrieb also willkürlich eines von zweien.
   */
  mediaserver_accounts: { provider: string; username: string | null }[];
  /**
   * Die OIDC-Anmeldungen am Konto – je Anbieter eine. Zugeordnet wird über
   * die Anbieter-Adresse, nicht über ein Kürzel: Der Eintrag des
   * Administrators kann gelöscht sein, während die Verknüpfung noch steht.
   */
  oidc_links: { issuer: string; display: string | null }[];
  /**
   * Kann sich dieses Konto auch mit Passwort anmelden? Wer über den
   * Media-Server angelegt wurde, hat zunächst keines – und darf die
   * Verknüpfung dann nicht lösen, ohne sich auszusperren.
   */
  has_password: boolean

  /**
   * Hat Plex den persönlichen Merklisten-Zugang abgelehnt?
   *
   * Nicht zu verwechseln mit dem Server-Zugang des Administrators. Nur
   * wer schon einmal verbunden war, kann hier true stehen haben – wer nie
   * verbunden hat, soll keinen Hinweis auf ein abgelaufenes Token sehen.
   */
  watchlist_token_invalid: boolean;

  /** Vorbelegung der Filterleiste; null = Vorgabe des Admins. */
  discover_region: string | null;

  /**
   * Altersbeschränkung. `null` heißt "nicht beschränkt" – der Normalfall.
   * Sonst das Alter des Benutzers: gezeigt wird, was höchstens ab diesem
   * Alter freigegeben ist.
   *
   * Nur der Administrator darf beides ändern; über das eigene Profil geht es
   * bewusst nicht, sonst höbe der Betroffene die Sperre selbst auf.
   */
  age: number | null;
  /** Land, nach dessen Einstufung geurteilt wird; null = Vorgabe des Admins. */
  rating_region: string | null;
  /** Titel ganz ohne Einstufung verbergen? Standard ja. */
  hide_unrated: boolean;
  /** Bei einem Kinderkonto das Konto der Eltern, sonst `null`. */
  parent_id: number | null;
  /** Darf dieses Konto Kinderkonten anlegen? Bei Administratoren immer. */
  can_manage_children: boolean;
};

/**
 * Ein Kinderkonto, wie das Elternteil es sieht.
 *
 * Bewusst schmal: Kontingent, Mailschalter und 4K-Rechte haben bei einem
 * Kinderkonto keine Bedeutung – die Anfragen laufen später über das Konto der
 * Eltern.
 */
export type Child = {
  id: number;
  username: string;
  display_name: string | null;
  age: number | null;
  is_active: boolean;
  /** Darf dieses Kind Trailer ansehen? */
  child_trailers: boolean;
  /** Sprache der Kinderansicht – vom Elternteil gesetzt. */
  language: string;
  /** Rubriken, die dieses Kind sieht. Leere Liste heißt: alle. */
  genres: string[];
  created_at: string;
  last_login_at: string | null;
};

/** Steht der „Alles, was neu ist"-Hinweis an – und bis wohin wurde quittiert? */
export type Neuigkeiten = {
  version: string;
  offen: boolean;
  /** Bis wohin dieses Konto quittiert hat; `null` = noch nie. */
  zuletzt_gesehen: string | null;
};

/** Eine Kachel auf der Kinder-Startseite. */
export type KidsCategory = {
  rubrik: string;
  /**
   * Szenenbilder aus den Titeln dieser Rubrik. Die Oberfläche wählt eines
   * zufällig aus – serverseitig zu würfeln wäre wirkungslos, weil die
   * TMDB-Antwort drei Stunden im Zwischenspeicher liegt.
   */
  bilder: string[];
};

/**
 * Zwei Listen: was das Kind schon schauen kann, und was es sich wünschen kann.
 *
 * Getrennt statt gemischt, weil es zwei verschiedene Handlungen sind. Und
 * getrennt statt „Vorhandenes weglassen", weil damit gemessen 90 % der Seite
 * verschwanden – ausgerechnet das, was zu Hause längst liegt.
 */
export type KidsItems = {
  verfuegbar: MediaItem[];
  wuenschbar: MediaItem[];
  /** In der Eltern-Vorschau immer leer – dort wird nichts gewünscht. */
  gewuenscht: number[];
};

/**
 * Der Zustand eines Kinderwunsches – vier statt der acht aus `RequestStatus`.
 * Ein Kind will wissen, ob es den Film sehen kann, nicht wie der Ablauf heißt.
 */
export type KidsWishState = "waiting" | "coming" | "available" | "declined";

/** Ein Wunsch, wie das Kind ihn sieht. */
export type KidsWish = {
  id: number;
  media_type: "movie" | "tv";
  tmdb_id: number;
  title: string;
  poster_path: string | null;
  release_date: string | null;
  state: KidsWishState;
  /** Kurze Begründung des Elternteils – nur bei einer Absage. */
  decline_note: string | null;
  created_at: string;
};

/** Ein offener Wunsch, wie das Elternteil ihn sieht. */
export type ParentWish = {
  id: number;
  child_id: number;
  child_name: string;
  media_type: "movie" | "tv";
  tmdb_id: number;
  title: string;
  poster_path: string | null;
  release_date: string | null;
  created_at: string;
  /** Abos **des Elternteils**, in denen der Titel schon läuft. */
  in_my_subscriptions?: string[];
};

/** Offene Einladung – ein Konto gibt es dazu noch nicht. */
export type Invitation = {
  id: number;
  email: string;
  role: Role;
  created_at: string;
  expires_at: string;
};

/** Antwort nach dem Einladen – mit Auskunft über den Mailversand. */
export type InvitationCreated = Invitation & {
  mail_sent: boolean;
  mail_error: string | null;
  manual_link: string | null;
};

export type SetupStatus = {
  needs_setup: boolean;
  /** Vom Server – der Assistent läuft vor der Anmeldung, /api/config ist noch zu. */
  min_password_length: number;
  /** Ist ein Media-Server verbunden? Nur dann gibt es den zusätzlichen Knopf. */
  mediaserver_login: boolean;
  mediaserver_provider: string | null;
  /**
   * **Welche** Anbieter einen Anmeldeweg bieten – und welcher Art.
   *
   * Die beiden Felder darüber reichten nicht: Aus „irgendeiner ist verbunden"
   * wurde ein fest beschrifteter Plex-Knopf, der bei einer Jellyfin-only-
   * Installation beim Klick scheiterte.
   */
  mediaserver_login_ways: LoginWay[];
};

/** Ein Anmeldeweg auf der Anmeldeseite. */
export type LoginWay = {
  provider: string;
  label: string;
  /** `pin` öffnet das Fenster des Anbieters, `password` klappt ein Formular auf. */
  kind: "pin" | "password";
};

/**
 * Ein Anmelde-Anbieter nach OpenID Connect, wie ihn Anmeldeseite und Profil
 * sehen: Kürzel für die Adresse, Beschriftung für den Knopf – und die
 * Anbieter-Adresse, über die das Profil seine Verknüpfungen zuordnet.
 */
export type OidcAnbieter = {
  slug: string;
  label: string;
  issuer_url: string;
};

/** Derselbe Anbieter aus Sicht des Administrators – das Geheimnis bleibt drin. */
export type OidcAdminEintrag = {
  id: number;
  slug: string;
  label: string;
  issuer_url: string;
  client_id: string;
  /** `"••••"` wenn gesetzt – kein Zeichen des Geheimnisses verlässt die Datenbank. */
  client_secret_vorschau: string;
  auto_create: boolean;
  enabled: boolean;
  created_at: string;
  /** Zum Kopieren; leer, solange die öffentliche Adresse fehlt. */
  rueckkehr_adresse: string;
  /** Wie viele Konten über diesen Anbieter verknüpft sind. */
  verknuepfte: number;
};

/** Antwort des Prüf-Knopfs – strukturiert, nie als Fehler. */
export type OidcPruefErgebnis = {
  ok: boolean;
  code: string | null;
  aussteller: string | null;
};

export type MediaType = "movie" | "tv";

/** Welche der beiden Radarr-/Sonarr-Instanzen gemeint ist. */
export type QualityTier = "standard" | "uhd";

/** Wer wählt den Zielordner – eine Frage je Dienst, drei mögliche Antworten. */
export type RootFolderMode = "user" | "fixed" | "approver";

export type MediaStatus =
  | "not_requested"
  | "pending_approval"
  /**
   * Freigegeben, aber noch nicht an Radarr/Sonarr uebergeben.
   *
   * ⚠️ Fehlte hier lange, obwohl der Server ihn sehr wohl schickt: Der
   * Status-Abgleich schaltet erst nach bis zu zwei Minuten auf `searching`
   * weiter, und nach einer Zeitueberschreitung bleibt eine Anfrage
   * ausdruecklich darauf stehen. Die Etiketten fanden ihn dann in keiner
   * Farbtabelle und rendertem `undefined` in die Klassenliste.
   */
  | "approved"
  | "requested"
  | "searching"
  | "downloaded"
  // Serie mit Dateien, aber Lücken: einzelne Staffeln fehlen noch.
  | "partial"
  | "in_library"
  | "rejected"
  | "failed"
  | "cancelled"
  /** War geladen, aber die Datei ist wieder aus der Bibliothek verschwunden. */
  | "deleted"
  /**
   * Zurückgestellt: „Ja im Prinzip, nur nicht jetzt."
   *
   * Entsteht, wenn ein Konto beim Freigeben schon überzogen ist. **Blockiert
   * den Titel ausdrücklich nicht** – der Grund liegt an der Person, nicht am
   * Titel, also darf ihn jemand anders holen.
   */
  | "deferred"
  /** Vom Administrator gesperrt: sichtbar, aber nicht anfragbar. */
  | "blocked";

/** Woher ein Kalendereintrag stammt – der eigene Bestand oder der Markt. */
export type CalendarSource = "meine" | "neu";
export type CalendarOrigin = "sonarr" | "radarr" | "tmdb";
export type CalendarDateType =
  "kino" | "digital" | "physisch" | "premiere" | "tv";

/**
 * Ein Ereignis an einem Tag.
 *
 * Absichtlich kein `MediaItem`: Dort ist die TMDB-Kennung Pflicht, eine
 * Sonarr-Folge kann aber zu Recht keine haben.
 */
export type CalendarEntry = {
  key: string;
  date: string;
  source: CalendarSource;
  origin: CalendarOrigin;
  media_type: MediaType;
  tmdb_id: number | null;
  tvdb_id: number | null;
  title: string;
  poster_url: string | null;
  overview: string;
  vote_average: number;
  vote_count: number;
  genres: string[];
  runtime_minutes: number | null;
  certification: string | null;
  status: MediaStatus;
  /** Zustand in der 4K-Instanz; `null`/fehlt = keine zweite Instanz. */
  status_uhd?: MediaStatus | null;
  watched: boolean;
  season: number | null;
  /** Schon fertig formatiert: „S03E05“ oder „S03E05–06“. */
  episode_label: string | null;
  /** Nur gesetzt, wenn an dem Tag genau eine Folge läuft. */
  episode_title: string | null;
  missing_episodes: number[];
  date_type: CalendarDateType | null;
  aired: boolean;
  /** Erschienen, aber die Datei fehlt – nur bei eigenen Titeln. */
  missing: boolean;
};

export type CalendarDay = {
  date: string;
  entries: CalendarEntry[];
};

export type CalendarResult = {
  date_from: string;
  date_to: string;
  days: CalendarDay[];
  arr_warning: string | null;
  tmdb_warning: string | null;
  demo: boolean;
};

export type LogEntry = {
  time: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  logger: string;
  message: string;
  /** Vorgangsnummer der Anfrage, zu der diese Zeile gehört. */
  request_id: string | null;
  /** Wer die Anfrage gestellt hat, falls angemeldet. */
  user: string | null;
};

/** Wie ausführlich Nexview gerade mitschreibt. */
export type LogModeState = {
  mode: string;
  /** Zeitpunkt, an dem eine Diagnose-Stufe von allein endet (ISO, UTC). */
  until: string | null;
  /** Über NEXVIEW_LOG_LEVEL festgelegt - dann ist Umschalten gesperrt. */
  fixed_by_env: boolean;
  modes: string[];
  durations: number[];
};

/** Eine Staffel, wie TMDB sie kennt. */
export type SeasonInfo = {
  season_number: number;
  name: string;
  episode_count: number;
  air_date: string | null;
  overview: string;
  poster_url: string | null;
  /** Wie viele Folgen davon schon in der Bibliothek liegen (aus Sonarr). */
  episodes_available: number
  /** Dasselbe für die 4K-Instanz – `null`, solange keine eingerichtet ist. */
  episodes_available_uhd?: number | null;
  requested_uhd?: boolean | null;
  /**
   * Läuft zu dieser Staffel schon eine Anfrage – von **irgendwem**?
   *
   * Nicht „von mir": `find_active` sperrt eine laufende Anfrage für alle.
   */
  requested?: boolean;
  /**
   * Von Folgen-Paketen belegte Folgen dieser Staffel – je Stufe. Leer heißt
   * frei; die Staffel bleibt wählbar, nur diese Nummern sind vergeben.
   */
  requested_episodes?: number[];
  requested_episodes_uhd?: number[] | null;
  /** Status der deckenden Voll-Anfrage – für das ehrliche Wort daneben:
      „wartet" ist etwas anderes als „läuft" oder „schon da". */
  requested_status?: string | null;
  requested_status_uhd?: string | null;
  /** **Sonarrs** Folgenzahl der Staffel – der Maßstab für „vollständig".
      TMDB zählt gern anders (Baywatch S1: 22 gegen 21), und wer mit der
      TMDB-Zahl rechnet, zeigt einer kompletten Staffel ewig „21 von 22".
      `null` heißt: Sonarr kennt die Serie nicht – dann gilt TMDB. */
  episodes_total_arr?: number | null;
  episodes_total_arr_uhd?: number | null;
};

export type EpisodeInfo = {
  episode_number: number;
  name: string;
  overview: string;
  air_date: string | null;
  runtime_minutes: number | null;
  still_url: string | null;
  vote_average: number;
  /** Liegt diese Folge schon vor? */
  available: boolean;
  /** Läuft zu dieser Folge schon eine Anfrage – von **irgendwem**? */
  requested?: boolean;
  /** Und in welchem Zustand – für „wartet" / „läuft" / „schon da". */
  requested_status?: string | null;
  /** Dieselben zwei Fragen für die 4K-Instanz – `null` heißt „keine zweite
      Instanz", nicht „belegt". */
  available_uhd?: boolean | null;
  requested_uhd?: boolean | null;
  requested_status_uhd?: string | null;
};

export type SeasonDetail = {
  season_number: number;
  name: string;
  overview: string;
  air_date: string | null;
  episodes: EpisodeInfo[];
};

/** Etwas mit Kennung und Namen - Schlagwort oder Studio. */
export type NamedRef = {
  id: number;
  name: string;
};

export type Trailer = {
  key: string;
  name: string;
  site: string;
  language: string;
};

export type CastMember = {
  person_id: number;
  name: string;
  character: string;
  photo_url: string | null;
};

export type CrewMember = {
  person_id: number;
  name: string;
  job: string;
};

/** Art eines Filmografie-Eintrags: Film, Serie oder TV-Auftritt ("Self"). */
export type CreditKind = "movie" | "series" | "appearance";

export type PersonCredit = {
  media_type: MediaType;
  tmdb_id: number;
  kind: CreditKind;
  title: string;
  character: string;
  poster_url: string | null;
  release_date: string | null;
  vote_average: number;
  status: MediaStatus;
  /** Wie bei `MediaItem` – der Server reichert die Filmografie mit an. */
  status_uhd?: MediaStatus | null;
  /**
   * Hat *der angemeldete* Benutzer das schon gesehen? Kommt vom Media-Server.
   * Bewusst neben dem Zustand und nicht als Zustandswert: "gesehen" ist eine
   * andere Achse und gilt je Person, nicht für alle.
   */
  watched?: boolean;
};

/** Ein Fach für die Personenseite. */
export type PersonDepartment = "acting" | "directing" | "writing";

/** Eine mit dem Herz gemerkte Person. */
export type FavoritePerson = {
  person_id: number;
  name: string;
  photo_url: string | null;
  department: string;
  created_at: string;
};

/** Eine Person in der Übersicht oder in Suchergebnissen. */
export type PersonSummary = {
  person_id: number;
  name: string;
  photo_url: string | null;
  department: string;
  known_for: string;
};

export type PersonDetail = {
  person_id: number;
  name: string;
  biography: string;
  photo_url: string | null;
  birthday: string | null;
  deathday: string | null;
  place_of_birth: string | null;
  known_for_department: string;
  credits: PersonCredit[];
};

export type MediaItem = {
  media_type: MediaType;
  tmdb_id: number;
  tvdb_id: number | null;
  title: string;
  original_title: string | null;
  overview: string;
  poster_url: string | null;
  backdrop_url: string | null;
  release_date: string | null;
  vote_average: number;
  vote_count: number;
  genres: string[];
  runtime_minutes: number | null;
  certification: string | null;
  original_language: string | null;
  /** Nur in der Detailansicht einer Serie gefüllt. */
  seasons: SeasonInfo[];
  status: MediaStatus;
  /**
   * Derselbe Titel in der 4K-Instanz. `null` heißt „diese Achse gibt es hier
   * nicht" – kein zweites Radarr, oder der Benutzer darf kein 4K. Normalfall.
   */
  status_uhd?: MediaStatus | null;
  /**
   * Hat *der angemeldete* Benutzer das schon gesehen? Kommt vom Media-Server.
   * Bewusst neben dem Zustand und nicht als Zustandswert: "gesehen" ist eine
   * andere Achse und gilt je Person, nicht für alle.
   */
  watched?: boolean;
  /**
   * Wer sagt „gesehen“ – und wer widerspricht.
   *
   * **Beide Listen bleiben leer, solange nur ein Medienserver verbunden ist.**
   * Die Entscheidung darüber fällt im Backend, weil nur dort bekannt ist,
   * welche Server überhaupt verbunden sind. Sind sie gefüllt, sind sie es
   * beide – dann und nur dann gibt es etwas zu unterscheiden.
   */
  watched_on?: string[];
  watched_not_on?: string[];
  /**
   * Wo die Datei liegt – bei Filmen samt Dateiname, bei Serien der Ordner.
   *
   * **Kommt nur bei Administratoren mit.** Das entscheidet der Server, nicht
   * die Oberfläche – Ausblenden hieße, ihn trotzdem ausgeliefert zu haben.
   */
  path?: string | null;
  /**
   * Wo die **4K-Fassung** liegt, falls es sie als eigene Datei gibt.
   *
   * Zwei Felder und kein gemeinsames: 1080p und 4K sind zwei Dateien in zwei
   * Instanzen. Ebenfalls nur für Administratoren.
   */
  path_uhd?: string | null;
  /**
   * Liegt in der **Standard**-Instanz bereits eine 4K-Datei dieses Titels?
   *
   * Kein Hinderungsgrund, nur ein Hinweis vor dem Klick: Eine 4K-Anfrage
   * erzeugt dann eine *zweite* 4K-Datei.
   */
  uhd_in_standard?: boolean;
};

export type WatchProvider = {
  id: number;
  name: string;
  logo_url: string | null;
};

/** Wo ein Titel in der Region des Nutzers läuft. Quelle: JustWatch über TMDB. */
export type WatchProviders = {
  region: string;
  flatrate: WatchProvider[];
  free: WatchProvider[];
  rent: WatchProvider[];
  buy: WatchProvider[];
};

/** Alles zu einem Titel - nur die Detailseite bekommt das. */
export type MediaDetail = MediaItem & {
  tagline: string;
  homepage: string | null;
  status_text: string;
  original_country: string[];
  spoken_languages: string[];
  budget: number | null;
  revenue: number | null;
  studios: NamedRef[];
  keywords: NamedRef[];
  trailer: Trailer | null;
  watch: WatchProviders | null;
  /** Eigene Abos, in denen dieser Titel läuft. Leer = kein Hinweis. */
  in_my_subscriptions?: string[];
  /** Habe ich diesen Titel vorgemerkt („Sag mir Bescheid")? */
  watching?: boolean;
  /** Läuft zu diesem Titel eine Anfrage **von mir**? Dann kein Warten-Knopf. */
  requested_by_me?: boolean;
  /** Meine eigene Rückmeldung zur Qualität – am Titel, nicht an der Anfrage. */
  my_feedback?: {
    rating: number;
    comment: string | null;
    reply: string | null;
    outdated: boolean;
  } | null;
  /** Liegt schon ein offenes Ticket von mir zu diesem Titel? */
  open_ticket?: boolean;
  cast: CastMember[];
  crew: CrewMember[];
  recommendations: MediaItem[];
  /** Nur bei Serien. */
  seasons_total: number | null;
  episodes_total: number | null;
  series_status: string;
  networks: NamedRef[];
};

export type MediaPage = {
  page: number;
  total_pages: number;
  total_results: number;
  items: MediaItem[];
  demo: boolean;
  /** Gesetzt, wenn der Abgleich mit Radarr/Sonarr nicht möglich war. */
  arr_warning: string | null;
};

/** Ein Regal auf der Stöber-Seite - ohne die Titel darin. */
export type RegalInfo = {
  kennung: string;
  /** "reihe" = geladen anzeigen, "kachel" = nur verweisen. */
  gruppe: 'reihe' | 'kachel';
  /** "jahrzehnt" | "genre" | "persoenlich" | "" - womit gruppiert wird. */
  kategorie: string;
  persoenlich: boolean;
  /**
   * Fertiger Name statt i18n-Schlüssel.
   *
   * Die nötige Ausnahme: „Weil dir *Der Pate* gefällt" enthält einen
   * Filmtitel, und der lässt sich nicht übersetzen. Sonst leer.
   */
  titel?: string | null;
};

/** Nach dem eigenen Bestand sieben - serverseitig, nicht im Browser. */
export type Bestandsfilter = 'egal' | 'nur_vorhanden' | 'nur_neu';

export type RegalSeite = {
  kennung: string;
  items: MediaItem[];
  page: number;
  total_pages: number;
  seiten_durchsucht: number;
  /**
   * Es gibt nichts mehr nachzuladen.
   *
   * Damit die Seite ehrlich sein kann: Liefert "nur was schon da ist" bei
   * einer dünnen Bibliothek drei Titel, ist das eine endgültige Auskunft -
   * kein Grund für einen Knopf, hinter dem nichts mehr kommt.
   */
  erschoepft: boolean;
  demo: boolean;
  arr_warning: string | null;
};

export type FilterSeite = {
  items: MediaItem[];
  page: number;
  total_pages: number;
  seiten_durchsucht: number;
  erschoepft: boolean;
  /** Welche Jahrzehnte die Leiste anbieten darf - kommt vom Server. */
  jahrzehnte: number[];
  demo: boolean;
  arr_warning: string | null;
};

/** Eine Frage des Filmabend-Assistenten. */
export type FilmabendFrage = {
  kennung: string;
  antworten: string[];
  /**
   * Wann diese Frage ganz entfällt: {frühere Frage: Antworten}.
   *
   * Kommt vom Server, damit Fragebaum und Bedeutung nicht auseinanderlaufen.
   */
  entfaellt_wenn: Record<string, string[]>;
  /** Wann einzelne Antwortmöglichkeiten wegfallen: {Antwort: {Frage: Werte}}. */
  antworten_entfallen_wenn: Record<string, Record<string, string[]>>;
};

export type FilmabendStapel = {
  items: MediaItem[];
  runde: number;
  /** Welche Antworten tatsächlich gezählt haben - ohne übersprungene. */
  antworten: Record<string, string>;
  /**
   * Es gab gar nichts, woraus hätte gewählt werden können.
   *
   * Bei "lange nicht gesehen" ohne verknüpften Media-Server. Eine andere
   * Auskunft als "nichts gefunden", und die Oberfläche muss sie sagen können.
   */
  quelle_leer: boolean;
  erschoepft: boolean;
  demo: boolean;
};

export type ArrOptions = {
  quality_profiles: { id: number; name: string }[];
  root_folders: { path: string; free_space: number | null }[];
  /** Welcher Zielordner gilt - und darf der Benutzer ihn ändern? */
  default_root_folder: string | null;
  root_folder_choice: boolean;
  quality_profile_choice: boolean;
  /** Vorauswahl für diesen Benutzer – vom Server bestimmt. */
  default_quality_profile_id: number | null;
};

/** Ein mit dem Herz markierter Titel. */
export type Favorite = {
  media_type: MediaType;
  tmdb_id: number;
  title: string;
  poster_url: string | null;
  created_at: string;
};

/** Wertungen der großen Portale - nur bei Filmen, aus Radarr. */
export type MovieRatings = {
  /** Für den Link auf die IMDb-Seite. */
  imdb_id: string | null;
  imdb: number | null;
  imdb_votes: number | null;
  rotten_tomatoes: number | null;
  metacritic: number | null;
};

export type Genre = {
  id: number;
  name: string;
};

export type AppConfig = {
  default_region: string;
  default_language: string;
  tmdb_configured: boolean;
  radarr_configured: boolean;
  sonarr_configured: boolean;
  using_demo_data: boolean;
  /** Kommt vom Server, damit Formular und Prüfung nicht auseinanderlaufen. */
  min_password_length: number;
  /** Ohne beides sind Einladungen sinnlos – der Knopf bleibt gesperrt. */
  mail_configured: boolean;
  public_url_set: boolean;
  /**
   * Wählt der Entscheider Zielordner und Profil erst bei der Freigabe?
   * Dann blendet das Anfrageformular beide Felder aus - außer für alle,
   * die selbst freigeben dürfen.
   */
  /** Wählt der Entscheider Ordner und Profil erst bei der Freigabe? Je Dienst. */
  approver_picks_target_movie: boolean;
  approver_picks_target_tv: boolean;
  approver_picks_target_movie_uhd: boolean;
  approver_picks_target_tv_uhd: boolean;
  /** Gibt es eine zweite Radarr-/Sonarr-Instanz für 4K? */
  radarr_uhd_configured: boolean;
  sonarr_uhd_configured: boolean;
  /** Ist ein Media-Server verbunden? Daran hängt der Merklisten-Bereich. */
  mediaserver_configured: boolean;
  /**
   * **Welche** Server verbunden sind. Heute höchstens einer – trotzdem eine
   * Liste, weil genau hier der Parallelbetrieb ansetzt.
   */
  mediaserver_providers: string[];
  /**
   * Welche Anbieter **diese Fassung** kennt – unabhängig davon, ob einer
   * verbunden ist. Alles, was hier nicht steht, bekommt eine ausgegraute
   * Kachel: sichtbar, damit man weiß, dass es kommt, aber nicht anklickbar.
   */
  mediaserver_available: string[];
  /**
   * Welche Anbieter mit Benutzername und Passwort verbunden werden.
   *
   * Entscheidet, welches Formular die Einrichtung zeigt. Kommt vom Server,
   * damit hier keine zweite Liste steht, die davon abweichen kann.
   */
  mediaserver_password_login: string[];
  /**
   * Quellen für Merklisten: was diese Fassung kennt, und was davon verbunden
   * ist. Zwei Listen, weil eine Quelle auch dann dastehen soll, wenn sie
   * *nicht* verbunden ist – sonst verschwindet der Bereich kommentarlos.
   */
  mediaserver_watchlist_available: string[];
  mediaserver_watchlist_connected: string[];
  /** Ist die Merklisten-Automatik eingeschaltet? Blendet den Herkunfts-Filter ein. */
  watchlist_enabled: boolean;
  /** Dürfen Benutzer Folgen-Pakete anfragen? Blendet die Aufklapp-Pfeile im
      Staffel-Wähler ein. */
  episode_requests_enabled: boolean;
};

/** Wie oft Nexview von selbst sichert. */
export type BackupSchedule = 'off' | 'daily' | 'weekly' | 'monthly';

export type AppSettings = {
  tmdb_api_key: string;
  tmdb_api_key_set: boolean;
  radarr_url: string;
  radarr_api_key: string;
  radarr_api_key_set: boolean;
  sonarr_url: string;
  sonarr_api_key: string;
  sonarr_api_key_set: boolean;
  default_region: string;
  default_language: string;
  poll_interval_seconds: number;
  demo_mode: "auto" | "on" | "off";
  using_demo_data: boolean;
  default_movie_profile_id: number | null;
  default_series_profile_id: number | null;
  smtp_host: string;
  smtp_port: number;
  smtp_security: MailSecurity;
  smtp_username: string;
  /** Maskiert – das echte Passwort verlässt den Server nie. */
  smtp_password: string;
  smtp_password_set: boolean;
  smtp_from_address: string;
  smtp_from_name: string;
  mail_configured: boolean;

  /** Adresse, unter der Nexview von außen erreichbar ist – steckt in jedem Link. */
  public_url: string;
  /** Adresse aus Sicht von Radarr/Sonarr – leer heißt: die öffentliche gilt. */
  webhook_basis_url: string;
  update_check: boolean;
  /** Regelmäßige Sicherung: 'off' | 'daily' | 'weekly' | 'monthly'. */
  backup_schedule: BackupSchedule;
  /** Wie viele automatische Stände liegen bleiben. Von Hand angelegte zählen nicht mit. */
  backup_keep: number;
  /** Je Dienst getrennt – Filme und Serien haben andere Ordnerstrukturen. */
  /** Wer wählt den Zielordner: 'user' | 'fixed' | 'approver'. */
  movie_root_folder_mode: RootFolderMode;
  series_root_folder_mode: RootFolderMode;
  movie_profile_mode: RootFolderMode;
  series_profile_mode: RootFolderMode;
  /** Dieselben Regeln je 4K-Instanz - geliefert als effektive Werte. */
  movie_uhd_root_folder_mode: RootFolderMode;
  series_uhd_root_folder_mode: RootFolderMode;
  movie_uhd_profile_mode: RootFolderMode;
  series_uhd_profile_mode: RootFolderMode;
  default_movie_root: string;
  default_series_root: string;
  radarr_uhd_url: string;
  radarr_uhd_api_key: string;
  radarr_uhd_api_key_set: boolean;
  sonarr_uhd_url: string;
  sonarr_uhd_api_key: string;
  sonarr_uhd_api_key_set: boolean;
  /** Frei wählbare Anzeigenamen – leer heißt: der Dienstname gilt. */
  radarr_name: string;
  sonarr_name: string;
  radarr_uhd_name: string;
  sonarr_uhd_name: string;
  default_movie_uhd_profile_id: number | null;
  default_series_uhd_profile_id: number | null;
  default_movie_uhd_root: string;
  default_series_uhd_root: string;

  /**
   * Media-Server. Anbieter-neutral gehalten – heute Plex, später ebenso
   * Jellyfin oder Emby. Das Token liefert der Server nie aus, nur die Auskunft,
   * ob eines hinterlegt ist.
   */
  mediaserver_provider: string;
  mediaserver_machine_id: string;
  mediaserver_name: string;
  mediaserver_url: string;
  mediaserver_token_set: boolean;
  mediaserver_configured: boolean;
  /**
   * Alle Verbindungen, je eine Zeile.
   *
   * Die Einzelwerte darüber (`mediaserver_name`, `mediaserver_url`) sind immer
   * die der **ersten**. Wer eine bestimmte meint – etwa die Seite eines
   * Anbieters –, muss hier suchen, sonst zeigt sie im Parallelbetrieb die
   * Angaben des anderen Servers.
   */
  mediaserver_connections: MediaServerConnectionInfo[];
  mediaserver_auto_import: boolean;
  mediaserver_default_role: "user" | "approver";
  /** Dürfen Benutzer ihre Merkliste sehen und daraus anfragen? */
  watchlist_enabled: boolean;
  /** Dürfen Benutzer Folgen-Pakete anfragen (einzelne Folgen einer Staffel)? */
  episode_requests_enabled: boolean;
  /**
   * Die drei Standardwerte des Hauses – sie gelten für jedes Konto ohne
   * eigenen Wert, und zwar **immer alle drei zugleich**. `null` heißt
   * unbegrenzt.
   */
  quota_default_movies: number | null;
  quota_default_series: number | null;
  storage_default_limit_gb: number | null;
  /** Zeitraum der Stückzahl – haus-weit, nicht mehr je Konto. */
  quota_period: QuotaPeriod;
  /**
   * Gespeicherte Zugangsdaten, die sich mit dem aktuellen Schlüssel nicht
   * mehr entschlüsseln lassen – NEXVIEW_SECRET_KEY geändert oder
   * data/secret.key beim Container-Neubau verloren.
   */
  secrets_unreadable: boolean;
};

/** Ein gesperrtes Media-Server-Konto – entsteht beim Löschen eines Benutzers. */
export type MediaServerBlock = {
  id: number;
  provider: string;
  account_id: string;
  username: string | null;
};

/** Stand des Bibliotheks-Abgleichs mit dem Media-Server. */
export type MediaServerLibraryState = {
  count: number;
  updated_at: string | null;
};

/** Ein Server zur Auswahl bei der Einrichtung. */
export type MediaServerOption = {
  machine_id: string;
  name: string;
  url: string;
  owned: boolean;
};

/**
 * Was ein Trennen der Verbindung anrichten würde – abrufbar **vor** dem Klick.
 *
 * `gefaehrdet` sind Konten, die nur über den Medienserver hereinkommen: kein
 * eigenes Passwort, keine bestätigte Adresse. Für sie ist ein Trennen keine
 * Umstellung, sondern eine verschlossene Tür.
 */
export type MediaServerDisconnectImpact = {
  verknuepft: number;
  gefaehrdet: { id: number; username: string; display_name: string | null }[];
};

export type AboutInfo = {
  version: string;
  repo_url: string;
  release_url: string;
  license: string;
  /**
   * Ob überhaupt bei GitHub nachgesehen wurde. Für alle außer Administratoren
   * bleibt das aus - sie können ohnehin nicht aktualisieren.
   */
  update_checked: boolean;
  latest_version: string | null;
  update_available: boolean;
  checked_at: string | null;
};

export type MailSecurity = "none" | "starttls" | "ssl";

/** Wie sich Nexview bei einer ntfy-Instanz anmeldet. */
export type NtfyAuth = "none" | "basic" | "token";

/** Sprache eines serverseitigen Kanals – der hat keinen Empfänger. */
export type ChannelLanguage = "de" | "en";

/** Dringlichkeit einer einzelnen Meldung – jeder Kanal rechnet sie selbst um. */
export type ChannelLevel = "low" | "normal" | "high" | "urgent";

/** Serverseitige Kanäle, die es heute gibt. */
export type ChannelKind = "ntfy" | "gotify" | "telegram" | "discord" | "webhook" | "apprise" | "email";

/**
 * Ein eingerichtetes Ziel – eine Kachel in den Systembenachrichtigungen.
 *
 * Je Dienst darf es mehrere geben: ein Gotify-Postfach für die Entscheider,
 * eines für den Betreiber, ein ntfy-Topic für die Familie. Nicht jeder Dienst
 * nutzt jedes Feld; Geheimnisse kommen nur maskiert heraus, dazu die Auskunft,
 * ob überhaupt eines hinterlegt ist.
 */
export type ChannelTarget = {
  id: number;
  channel: ChannelKind;
  /** Frei gewählt, steht auf der Kachel. */
  name: string;
  /**
   * Zu welcher Instanz dieses Ziel gehört – `null` bei der oberen Ebene.
   *
   * Manche Dienste haben zwei Ebenen: Bei ntfy trägt die Instanz Adresse und
   * Anmeldung, das Topic darunter ist das Postfach. Bei Gotify ist die
   * Application schon beides.
   */
  parent_id: number | null;
  /** Die Topics einer ntfy-Instanz. Nur auf der oberen Ebene gefüllt. */
  children?: ChannelTarget[];
  /** In Betrieb? Stillgelegte Ziele bekommen nichts, bleiben aber erhalten. */
  enabled: boolean;
  /** Testnachricht verschickt **und** der Code daraus eingetippt? */
  verified: boolean;
  /** Meldung auf Dringlichkeit. Was fehlt, ist aus. */
  events: Record<string, ChannelLevel>;
  created_at: string;
  url: string;
  language: ChannelLanguage;
  topic?: string;
  address?: string;
  /** Fester Betreff; leer heisst: die uebliche Vorlage. */
  subject?: string;
  /** Telegram: der Chat und – bei Gruppen mit Themen – das Thema darin. */
  chat_id?: string;
  thread_id?: string;
  /** „on“ = ohne Ton zustellen. */
  silent?: string;
  auth?: NtfyAuth;
  username?: string;
  password?: string;
  password_set?: boolean;
  token?: string;
  token_set?: boolean;
  /** Discord: die Webhook-URL ist selbst das Geheimnis. */
  url_set?: boolean;
  /** Letzter endgültig gescheiterter Versand – sonst fällt ein Ausfall niemandem auf. */
  last_error: string | null;
  last_error_at: string | null;
};

export type TestResult = {
  ok: boolean;
  message: string;
  /**
   * Was die Prüfung nebenbei herausgefunden hat – etwa der Benutzername eines
   * Telegram-Bots. Die Oberfläche trägt es ins passende Feld ein.
   */
  values?: Record<string, string> | null;
};

export type MediaRequest = {
  id: number;
  media_type: MediaType;
  /** Welche Instanz – steht an der Anfrage, nicht an der Einstellung. */
  tier: QualityTier;
  tmdb_id: number;
  title: string;
  poster_path: string | null;
  release_date: string | null;
  status: MediaStatus;
  /**
   * Hat *der angemeldete* Benutzer das schon gesehen? Kommt vom Media-Server.
   * Bewusst neben dem Zustand und nicht als Zustandswert: "gesehen" ist eine
   * andere Achse und gilt je Person, nicht für alle.
   */
  watched?: boolean;
  quality_profile_id: number | null;
  root_folder_path: string | null;
  /** Nur bei Serien; null = ganze Serie. */
  season: number | null;
  /** Nur bei Folgen-Paketen: die bestellten Folgen. `null` = ganze Staffel. */
  episodes?: number[] | null;
  /** Kam die Anfrage von der Merkliste statt von einem Klick? */
  from_watchlist: boolean;
  requested_at: string;
  approved_at: string | null;
  completed_at: string | null;
  /** „Lädt gerade": Prozent aus der Warteschlange (0–100) oder null – eine
   * Momentaufnahme für die Pille, kein eigener Status. */
  laedt_fortschritt?: number | null;
  laedt_seit?: string | null;
  /** Wer freigegeben hat – leer heißt: automatisch, ohne Entscheider. */
  approved_by_name?: string | null;
  /** Wann Nexview zuletzt bei Radarr/Sonarr nachgesehen hat. */
  last_checked_at?: string | null;
  rejection_reason: string | null;
  error_message: string | null;
  /**
   * Kennung samt Werten zur gespeicherten Fehlermeldung.
   *
   * `error_message` ist der deutsche Rückfall; den angezeigten Satz baut
   * `gespeicherterFehler` daraus in der eingestellten Sprache.
   */
  error_detail?: Record<string, unknown> | null;
  rating: number | null;
  feedback: string | null;
  rated_at: string | null;
  /** Galt die Bewertung einer älteren Fassung? Radarr hat nachgeladen. */
  rating_outdated?: boolean;
  feedback_reply: string | null;
  replied_at: string | null;
};

export type QuotaInfo = {
  limit: number | null;
  used: number;
  remaining: number | null;
  unlimited: boolean;
  exhausted: boolean;
  period: QuotaPeriod;
  resets_at: string | null;
};

/** Ein belegter Titel oder eine belegte Staffel. */
export type StorageEntry = {
  id: number
  media_type: MediaType
  tier: QualityTier
  tmdb_id: number | null
  tvdb_id: number | null
  /** null = ein Film. Sonst die Staffel – feiner wird nie gerechnet. */
  season: number | null
  title: string
  size_bytes: number
  state: 'owned' | 'pending' | 'house'
  measured_at: string
  /** Nur im Hausbestand gefüllt – und nur für Administratoren. */
  path?: string
  /** Gesetzt, solange der Posten auf eine Entscheidung wartet. */
  released_at?: string | null
  /** Wunsch des Abgebenden: `delete` oder `keep`. `null` ohne Abgabe. */
  release_wish?: 'delete' | 'keep' | null
  /** „Schon gesehen?“ – nur auf der eigenen Seite, nur bei Filmen. `null` = unbekannt. */
  watched?: boolean | null
  /**
   * Führt Radarr bzw. Sonarr diesen Titel noch?
   *
   * `false` heißt: Nur der Media-Server meldet ihn. Er zählt weiter, lässt
   * sich aber **nicht mehr löschen** – Nexview löscht ausschließlich über
   * Radarr/Sonarr. Abgeben geht dann nur noch an den Hausbestand.
   */
  managed?: boolean
}

/**
 * Was ein Löschen treffen würde – **ohne dass etwas passiert**.
 *
 * Der Administrator bestätigt mit dieser Liste vor Augen und nicht mit einer
 * Zahl: Ein Fehler trifft Dateien, die jemand behalten wollte, und eine Zahl
 * verrät nicht, welche.
 */
export type StorageLoeschvorschau = {
  files: { path: string; size_bytes: number }[]
  total_bytes: number
  deletable: boolean
  /** `tier` = Sperre, `series` = noch nicht scharf, `unmanaged` = kennt Radarr nicht. */
  reason: string
}

/** Eine wartende Abgabe, aus Sicht des Administrators. */
export type StorageAbgabe = {
  entry: StorageEntry
  user_id: number | null
  username: string | null
  display_name: string | null
  released_at: string | null
}

export type StorageUserPage = {
  user_id: number
  username: string
  display_name: string | null
  used_bytes: number
  items: number
  /** null heißt unbegrenzt. */
  limit_bytes: number | null
  pending_bytes: number
  /** Wie viele Zeilen die Suche trifft – für die Seitenzahl. */
  matches: number
  /**
   * Wie viele Zeilen eine Seite fasst. Kommt vom Server – **nicht** hier
   * nachbauen: Eine zweite Konstante ginge beim nächsten Ändern auseinander,
   * und dann stimmt die Seitenzahl nicht mehr.
   */
  per_page: number
  entries: StorageEntry[]
}

export type StorageMine = {
  used_bytes: number
  items: number
  /** Wie viele Zeilen die Suche trifft – für die Seitenzahl. */
  matches: number
  /** Wie viele Zeilen eine Seite fasst – vom Server, nicht nachbauen. */
  per_page: number
  /** null heißt unbegrenzt. */
  limit_bytes: number | null
  /** Abgegeben, aber noch nicht entschieden – zählt weiter mit. */
  pending_bytes: number
  entries: StorageEntry[]
  /** Gibt es Gesehen-Daten? Daran hängt der Filter „Nur Gesehene“. */
  watched_available?: boolean
}

export type StorageHouse = {
  used_bytes: number
  items: number
  /** Wie viele Zeilen die **Suche** trifft – nicht wie viele das Haus hält. */
  matches: number
  /**
   * Freier Platz auf den Zielordnern – und auf wie vielen Trägern.
   *
   * **Keine Gesamtkapazität.** Die kennt Radarr nicht, und „belegt + frei"
   * wäre erfunden: Liegt anderes auf demselben Träger, ist die Platte größer,
   * ohne dass es jemand sähe.
   */
  free_bytes: number
  free_volumes: number
  /** Wie viele Zeilen eine Seite fasst – vom Server, nicht nachbauen. */
  per_page: number
  entries: StorageEntry[]
}

export type StorageShare = {
  /** null steht für den Hausbestand – der gehört niemandem. */
  user_id: number | null
  username: string | null
  display_name: string | null
  used_bytes: number
  items: number
  /**
   * Wie viel diese Person **darf**. `null` heißt unbegrenzt – und beim
   * Hausbestand heißt es „die Frage stellt sich nicht".
   */
  limit_bytes: number | null
}

/** Wie eine Radarr-/Sonarr-Instanz beim Löschen mit Dateien umgeht. */
export type PapierkorbInstanz = {
  media_type: string
  tier: string
  /** „Radarr", „Radarr 4K", „Sonarr", „Sonarr 4K" */
  name: string
  /**
   * ⚠️ Drei Zustände, nicht zwei: „nicht erreichbar" ist etwas anderes als
   * „kein Papierkorb". Wer beides gleich behandelt, meldet einen Fehlalarm,
   * sobald Radarr gerade neu startet.
   */
  reachable: boolean
  path: string
  cleanup_days: number | null
  protected: boolean
}

export type PapierkorbStand = {
  /**
   * Der abgeleitete Zustand: an, wenn **jede** eingerichtete Instanz einen
   * Papierkorb hat. Kommt vom Server und wird dort gerechnet, nicht
   * gespeichert – so kann er nicht von der Wirklichkeit abweichen.
   */
  enabled: boolean
  /** Konnte jede Instanz gefragt werden? Sonst heißt es „unbekannt", nicht „aus". */
  complete: boolean
  instances: PapierkorbInstanz[]
}

/**
 * Was im Papierkorb einer Instanz liegt – **nur die Ordnernamen**.
 *
 * Kein Plakat: Das ginge nur über die TMDB-Nummer im Ordnernamen, und die
 * steht dort nicht von Natur aus, sondern nur wenn jemand sein
 * Benennungsschema so eingerichtet hat. Der Ordnername steht dagegen immer da.
 */
export type PapierkorbInhalt = {
  instances: {
    name: string
    path: string
    entries: string[]
    /** Wurde die Liste gekürzt? Dann steht das dabei. */
    truncated: boolean
  }[]
}

/**
 * Wie viel Platz die Papierkörbe belegen.
 *
 * **Der Papierkorb ist keine Freigabe** – was dort liegt, ist von der Platte
 * nicht verschwunden. Wer die Belegung anschaut und ihn nicht mitzählt, sieht
 * zu wenig und wundert sich, warum trotz Aufräumens nichts frei wird.
 */
export type PapierkorbBelegung = {
  total_bytes: number
  /** Musste die Suche abgebrochen werden? Dann ist die Zahl eine Untergrenze. */
  incomplete: boolean
  instances: { name: string; path: string; bytes: number }[]
}

export type StorageOverview = {
  total_bytes: number
  house_bytes: number
  house_items: number
  shares: StorageShare[]
}

/**
 * Wo der Anfragende beim Speicher steht – für die Freigabe-Entscheidung.
 *
 * Kommt nur mit, wenn Speicher-Kontingente eingeschaltet sind. `null` heißt
 * „diese Währung gilt hier nicht" – es gilt immer nur eine.
 */
export type AnfragerSpeicher = {
  used_bytes: number;
  /** null heißt unbegrenzt. */
  limit_bytes: number | null;
  /** Liegt das Konto **schon jetzt** auf oder über der Grenze? */
  exhausted: boolean;
};

export type MediaRequestWithUser = MediaRequest & {
  user_id: number;
  username: string;
  display_name: string | null;
  avatar_url: string | null;
  storage?: AnfragerSpeicher | null;
  /**
   * Abos **des Anfragenden**, in denen dieser Titel läuft. Leer = kein Hinweis.
   * Nicht die des Entscheiders – der hat vielleicht kein Netflix, aber die
   * Frage ist, ob der Anfragende ohne den Download auskäme.
   */
  requester_subscriptions?: string[];
};

/** Ein zuletzt fertig geladener Titel für die Startseite. */
export type RecentItem = {
  request_id: number;
  media_type: MediaType;
  tmdb_id: number;
  title: string;
  overview: string;
  poster_url: string | null;
  backdrop_url: string | null;
  release_date: string | null;
  vote_average: number;
  runtime_minutes: number | null;
  genres: string[];
  completed_at: string | null;
  requested_by: string;
  requester_avatar: string | null;
  /**
   * Die Staffeln hinter dieser Kachel, aufsteigend.
   *
   * Leer heißt: ein Film, oder eine als Ganzes angefragte Serie. Dann steht
   * auf der Kachel schlicht „Film" bzw. „Serie".
   */
  seasons: number[];
};

export type UserStats = {
  user_id: number;
  username: string;
  display_name: string | null;
  avatar_url: string | null;
  role: Role;
  total: number;
  movies: number;
  series: number;
  downloaded: number;
  pending: number;
  rejected: number;
  cancelled: number;
  failed: number;
  ratings: number;
  average_rating: number | null;
  poor_ratings: number;
  /** Anteil der Anfragen, die als Download ankamen - null ohne Anfragen. */
  success_rate: number | null;
  /** Belegter Platz und Grenze in Bytes - immer gefüllt, wie die Stückzahlen. */
  storage_used_bytes: number | null;
  storage_limit_bytes: number | null;
  quota_movie_used: number;
  quota_movie_limit: number | null;
  quota_series_used: number;
  quota_series_limit: number | null;
};

export type Stats = {
  totals: {
    requests: number;
    movies: number;
    series: number;
    downloaded: number;
    downloaded_movies: number;
    downloaded_series: number;
    pending: number;
    rejected: number;
    cancelled: number;
    failed: number;
    active_users: number;
    ratings: number;
    average_rating: number | null;
    poor_ratings: number;
    unanswered_feedback: number;
    rating_distribution: Record<number, number>;
    last_request_at: string | null;
  };
  users: UserStats[];
  history: { month: string; movies: number; series: number }[];
  most_requested: {
    media_type: MediaType;
    tmdb_id: number;
    title: string;
    poster_path: string | null;
    count: number;
  }[];
};

export type AppNotification = {
  id: number;
  type:
    | "download_complete"
    | "approved"
    | "rejected"
    | "cancelled"
    | "request_pending"
    | "feedback"
    | "feedback_poor"
    | "feedback_reply"
    | "ticket_new"
    | "ticket_reply"
    | "user_imported"
    | "mediaserver_reconnect"
    | "child_wish";
  /** Übersetzungsschlüssel – der Text kommt aus der Oberfläche. */
  message_key: string;
  message_title: string | null;
  /** Bei Staffelanfragen die Staffel – sonst `null`. */
  season: number | null;
  /** Bei Folgen-Paketen die Folgen – aus demselben Grund wie die Staffel. */
  episodes?: number[] | null;
  request_id: number | null;
  ticket_id: number | null;
  is_read: boolean;
  created_at: string;
};

export type QuotaOverview = {
  movie: QuotaInfo;
  tv: QuotaInfo;
  auto_approve: boolean;
};

/** Ein verbundener Medienserver, wie ihn die Einstellungen ausliefern. */
export interface MediaServerConnectionInfo {
  provider: string;
  /** Wie der Server sich selbst nennt – „Bizzy" sagt mehr als eine Adresse. */
  name: string;
  url: string;
}

/** Ein Land für die Regionsauswahl. Kommt von TMDB, nicht aus dem Quelltext. */
export type Region = {
  code: string;
  name: string;
};

/** Der Rückkanal-Zustand einer Radarr-/Sonarr-Instanz (GET /api/settings/webhooks). */
export interface WebhookInstanzStand {
  kennung: string;
  name: string;
  media_type: "movie" | "tv";
  tier: "standard" | "uhd";
  aktiv: boolean;
  /** Steht unser Eintrag gerade drüben in Radarr/Sonarr? */
  eingetragen: boolean;
  bewiesen_am: string | null;
  zuletzt_angerufen_am: string | null;
  letztes_ereignis: string;
  geprueft_am: string | null;
  /** Hindernis-Kennung ("no_address", "too_old", …) – übersetzt im Frontend. */
  fehler: string;
  /** Roher Zusatz (Version, fehlende Fähigkeiten) – wird nicht übersetzt. */
  fehler_info: string;
}

export interface WebhookStand {
  /** Von wo aus Radarr/Sonarr anrufen – leer, wenn keine Adresse gesetzt ist. */
  basis: string;
  instanzen: WebhookInstanzStand[];
}

/** Antwort des Testen-Knopfs (POST /api/settings/webhooks/{kennung}/testen). */
export interface WebhookProbe {
  angekommen: boolean;
  dauer_ms?: number | null;
  fehler?: string | null;
  info?: string | null;
}

/** Was eine Instanz selbst als Problem meldet (GET /api/settings/instanzen/gesundheit). */
export interface GesundheitProblem {
  typ: string;
  /** Wortlaut der Instanz – bewusst unübersetzt. */
  text: string;
}

export interface GesundheitInstanz {
  kennung: string;
  name: string;
  probleme: GesundheitProblem[];
  aktualisiert_am: string | null;
}

export interface GesundheitStand {
  instanzen: GesundheitInstanz[];
}

/** Die Statusleuchte der Kacheln (GET /api/settings/instanzen/verbindung). */
export interface VerbindungInstanz {
  kennung: string;
  name: string;
  erreichbar: boolean;
  version: string;
}

export interface VerbindungStand {
  instanzen: VerbindungInstanz[];
}

/** Eine Kopie eines Qualitätsprofils auf einer Instanz. */
export interface QualitaetsprofilInstallation {
  kennung: string;
  geschrieben_am: string | null;
  trash_stand: string;
}

/**
 * Ein Profil, wie es in Nexview liegt.
 *
 * ⚠️ `rezept` sind die Antworten aus dem Assistenten, nicht das fertige
 * Radarr-Profil. Daraus wird beim Verteilen jeweils frisch gebaut.
 */
export interface Qualitaetsprofil {
  id: number;
  name: string;
  dienst: "radarr" | "sonarr";
  rezept: Record<string, unknown>;
  installationen: QualitaetsprofilInstallation[];
}

export interface VerteilenErgebnis {
  installationen: QualitaetsprofilInstallation[];
  formate_neu: number;
  formate_wiederverwendet: number;
  hinweise: string[];
}

export interface TrashQuelle {
  stand: string;
  quelle: string;
  lizenz: string;
  commit: string;
  /** Noch der mit Nexview ausgelieferte Stand? */
  mitgeliefert: boolean;
  geholt_am: string;
  /** Hat das tägliche Nachsehen schon etwas ergeben? */
  pruefung_bekannt: boolean;
  neuer_stand_da: boolean;
  neuer_stand_datum: string;
}

/** Ein einzelner Unterschied zwischen Ablage und Instanz. */
export interface QualitaetsprofilUnterschied {
  /** "qualitaeten" | "mindestpunkte" | "schlusspunkte" | "punkte" | "fremd" | "fehlt" */
  art: string;
  was: string;
  ist: string;
  soll: string;
}

/** Steht auf der Instanz noch, was Nexview geschrieben hat? */
export interface QualitaetsprofilAbgleich {
  profil_id: number;
  kennung: string;
  /** "aktuell" | "update" | "angepasst" | "konflikt" | "fehlt" | "unerreichbar" */
  stand: string;
  unterschiede: QualitaetsprofilUnterschied[];
}

/** Ein Medienserver und der Zugang, den Radarr/Sonarr dafür braucht. */
export interface MedienserverZugang {
  id: number;
  provider: string;
  name: string;
  url: string;
  /** Braucht dieser Anbieter einen API-Schlüssel vom Betreiber? */
  braucht_schluessel: boolean;
  /** Liegt er vor? Der Schlüssel selbst wird nie ausgeliefert. */
  schluessel_da: boolean;
}

/**
 * Wie Pfade umgeschrieben werden müssen.
 *
 * ⚠️ Radarr nennt dem Medienserver einen Pfad aus **seiner** Sicht. Stecken
 * beide in eigenen Containern, meint derselbe Film zwei verschiedene Pfade —
 * und ohne diese Umschreibung sucht der Medienserver ins Leere, ohne dass es
 * jemand merkt.
 */
export interface PfadZuordnung {
  /** Was Radarr sagt. Leer heißt: beide Seiten sehen denselben Pfad. */
  von: string;
  /** Was der Medienserver versteht. */
  nach: string;
  /** "" | "keine_pfade" | "keine_wurzeln" | "kein_treffer" | "unreachable" | "kein_schluessel" */
  hindernis: string;
  beispiel_arr: string;
  beispiel_server: string;
}

export interface MedienserverLuecke {
  provider: string;
  name: string;
  url: string;
  selbst_moeglich: boolean;
  hindernis: string;
  zuordnung: PfadZuordnung;
}

/** Eine bestehende Verbindung, die nicht mehr tut, was sie soll. */
export interface MedienserverWarnung {
  instanz: string;
  provider: string;
  grund: string;
}

export interface VerbindungslageInstanz {
  kennung: string;
  name: string;
  erreichbar: boolean;
  fehlend: MedienserverLuecke[];
  /**
   * Anbieter mit funktionierender Verbindung.
   *
   * ⚠️ Muss sichtbar bleiben: Sonst zeigt eine halb verbundene Instanz nur
   * ihre Lücken, und das Bestehende sieht aus, als fehlte es auch.
   */
  verbunden: string[];
}

export interface VerbindungslageGesamt {
  server: MedienserverZugang[];
  instanzen: VerbindungslageInstanz[];
  /** Leer heißt „nichts gefunden“, nicht „nicht geprüft“. */
  warnungen: MedienserverWarnung[];
}

/** Wie weit das Angleichen des Bestands ist. */
export interface UmbenennenFortschritt {
  laeuft: boolean;
  instanz: string;
  /** "pruefen" | "umbenennen" | "fertig" | "fehler" */
  schritt: string;
  erledigt: number;
  gesamt: number;
  /** Wie viele Titel wirklich einen neuen Namen bekommen. */
  betroffen: number;
  beispiele: string[];
  /**
   * Nach einem Abbruch wieder aufgenommen.
   *
   * ⚠️ Muss sichtbar sein: Ein Lauf, der nach einem Neustart von selbst
   * weiterläuft, wirkt sonst wie ein Fehler statt wie die Rettung, die er ist.
   */
  fortgesetzt: boolean;
}

/** Wie eine Instanz Dateien und Ordner benennt - und was empfohlen wird. */
export interface BenennungStand {
  kennung: string;
  name: string;
  dienst: string;
  umbenennen_an: boolean;
  datei_ist: string;
  datei_soll: string;
  ordner_ist: string;
  ordner_soll: string;
  /** Welche TRaSH-Fassung gilt - richtet sich nach dem Medienserver. */
  fassung: string;
  erreichbar: boolean;
  /** Sagt diese Instanz dem Medienserver Bescheid? */
  meldet_medienserver: boolean;
  altnamen: Altnamen;
  /**
   * Läuft gerade ein Bestandslauf auf dieser Instanz?
   *
   * ⚠️ Nötig, damit die Seite einen Lauf auch dann findet, wenn sie ihn nicht
   * selbst angestoßen hat — nach einem Neuladen oder von einem anderen Gerät.
   */
  lauf_offen: boolean;
}

/**
 * Erkennungsmuster, die noch den alten Vorsatz `NXV - ` tragen.
 *
 * ⚠️ `im_dateinamen` ist die Zahl, die zählt: So viele Muster fließen in
 * Dateinamen ein. Ein Bestandslauf in diesem Zustand schreibt `[NXV - German
 * DL]` in jede betroffene Datei — deshalb steht die Warnung direkt über dem
 * Knopf und nicht im Kleingedruckten.
 */
export interface Altnamen {
  gesamt: number;
  im_dateinamen: number;
  /**
   * Muster, die im Dateinamen landen und sich NICHT umbenennen lassen, weil
   * ein fremdes Muster den schlichten Namen hält. Diese bleiben nach dem
   * Aufräumen stehen und brauchen eine Entscheidung.
   */
  blockiert: number;
  beispiele: string[];
  blockierte_namen: string[];
}

export interface AltnamenAufgeraeumt {
  umbenannt: number;
  altnamen: Altnamen;
}

/** Wie weit das Schreiben eines Profils auf eine Instanz ist. */
export interface QualitaetsprofilFortschritt {
  laeuft: boolean;
  instanz: string;
  /** "plan" | "formate" | "profil" */
  schritt: string;
  erledigt: number;
  gesamt: number;
  instanz_nummer: number;
  von_instanzen: number;
}


/**
 * Ein Qualitätsprofil auf der Instanz — auch eines, das Nexview nicht angelegt hat.
 *
 * ⚠️ `grund` nennt, **was** daran hängt (`medien:12,sammlungen:3`). Radarr
 * selbst sagt beim Löschversuch nur „is in use“ und verschweigt, wer es
 * benutzt — genau daran scheitert das Aufräumen sonst.
 */
/**
 * Was der Import auf **einer** Instanz zu **einem** Profil gefunden hat.
 *
 * ⚠️ `weicht_ab` heisst nicht „Fehler": Das Profil wird trotzdem uebernommen,
 * nur unveraendert gelassen. In Radarr schreibt der Import nie.
 */
/**
 * Zwei oder mehr Instanzen auf derselben Bahn eines Download-Programms.
 *
 * ⚠️ Das kann nur Nexview sehen: Radarr kennt die zweite Instanz nicht und
 * warnt deshalb nie - waehrend beide sich die Downloads wegnehmen.
 */
export interface DownloadKollision {
  /** Woran sie wiedererkannt wird - traegt die Beteiligten, fuers Wegklicken. */
  schluessel: string;
  /** Lesbar, etwa "Sabnzbd auf 10.10.10.109:8080". */
  programm: string;
  kategorie: string;
  /** Keine Kategorie gesetzt - der schwerere Fall. */
  ohne_kategorie: boolean;
  instanzen: string[];
  kennungen: string[];
}

export interface DownloadKollisionStand {
  kollisionen: DownloadKollision[];
}

export interface UmzugBefund {
  name: string;
  dienst: string;
  kennung: string;
  instanz: string;
  /** "uebernehmen" | "weicht_ab" | "nicht_gefunden" | "unerreichbar" */
  lage: string;
  profil_id_extern: number | null;
  /** Wie viele Punkte oder Qualitaeten anders sind - nur bei `weicht_ab`. */
  unterschiede: number;
}

export interface UmzugErgebnis {
  /** Profile, die neu in die Ablage kommen. */
  neu: string[];
  /** Namen, die es in der Ablage schon gibt - sie werden uebersprungen. */
  schon_da: string[];
  befunde: UmzugBefund[];
}


export interface ProfilBestand {
  id: number;
  name: string;
  /** Hat Nexview dieses Profil angelegt? */
  unser: boolean;
  medien: number;
  importlisten: number;
  sammlungen: number;
  loeschbar: boolean;
  grund: string;
}

/**
 * Ein Erkennungsmuster auf der Instanz.
 *
 * ⚠️ Hier steht bewusst nicht „gehört uns“: Für Muster merkt sich Nexview
 * keine Nummer, und seit der Vorsatz entfallen ist, tragen sie keine
 * Handschrift mehr. Beantwortbar ist nur die Frage, die zählt — benutzt es
 * noch jemand?
 */
export interface MusterBestand {
  id: number;
  name: string;
  /** Profile, die diesem Muster Punkte geben. Leer heißt: niemand braucht es. */
  benutzt_von: string[];
  /**
   * Gehört zum Bauplan eines Nexview-Profils auf dieser Instanz.
   *
   * ⚠️ Solche Muster sind nicht „ungenutzt", auch wenn kein Profil ihnen
   * Punkte gibt: Ein Bauplan bringt bewusst Muster mit null Punkten mit. Sie
   * zu löschen ist vergebliche Arbeit — das nächste Verteilen legt sie wieder
   * an.
   */
  gehoert_zu_plan: boolean;
  alter_vorsatz: boolean;
  im_dateinamen: boolean;
  loeschbar: boolean;
}

export interface InstanzBestand {
  kennung: string;
  name: string;
  erreichbar: boolean;
  profile: ProfilBestand[];
  muster: MusterBestand[];
}

export interface AufraeumErgebnis {
  geloescht_profile: string[];
  geloescht_muster: string[];
  /** Was nicht ging, mit Grund — `name: grund`. */
  abgelehnt: Record<string, string>;
}


/**
 * Medien von einem Profil auf ein anderes umhängen.
 *
 * ⚠️ Dateien werden dabei **nicht** angefasst — nur die Zuordnung ändert sich.
 * Das neue Profil bewertet aber anders, und Titel darunter merkt die Instanz
 * zur Aufwertung vor.
 */
export interface UmhaengErgebnis {
  umgehaengt: number;
  /** Leer heißt: hat geklappt. */
  grund: string;
}
