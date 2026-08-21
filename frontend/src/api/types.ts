export type Role = "admin" | "approver" | "user";
export type QuotaPeriod = "day" | "week" | "month";

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
  quota_movies_limit: number | null;
  quota_series_limit: number | null;
  quota_period: QuotaPeriod;
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
  /** Neues Konto über den Media-Server – nur für Admins von Belang. */
  mail_user_imported: boolean;
  mail_mediaserver_reconnect: boolean;
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
};

export type MediaType = "movie" | "tv";

/** Welche der beiden Radarr-/Sonarr-Instanzen gemeint ist. */
export type QualityTier = "standard" | "uhd";

/** Wer wählt den Zielordner – eine Frage je Dienst, drei mögliche Antworten. */
export type RootFolderMode = "user" | "fixed" | "approver";

export type MediaStatus =
  | "not_requested"
  | "pending_approval"
  | "requested"
  | "searching"
  | "downloaded"
  | "in_library"
  | "rejected"
  | "failed"
  | "cancelled"
  /** War geladen, aber die Datei ist wieder aus der Bibliothek verschwunden. */
  | "deleted"
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
  level: "INFO" | "WARNING" | "ERROR";
  logger: string;
  message: string;
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
  episodes_available: number;
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
  /** Gibt es eine zweite Radarr-/Sonarr-Instanz für 4K? */
  radarr_uhd_configured: boolean;
  sonarr_uhd_configured: boolean;
  /** Ist ein Media-Server verbunden? Daran hängt der Merklisten-Bereich. */
  mediaserver_configured: boolean;
  /**
   * Speicher-Kontingente eingeschaltet? Ist der Schalter aus, verhält sich
   * Nexview wie vor deren Einbau – kein Reiter, keine Karte, keine
   * Verteilung, und gemessen wird auch nicht.
   */
  storage_enabled: boolean;
  /** Ist die Merklisten-Automatik eingeschaltet? Blendet den Herkunfts-Filter ein. */
  watchlist_enabled: boolean;
};

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
  update_check: boolean;
  /** Je Dienst getrennt – Filme und Serien haben andere Ordnerstrukturen. */
  /** Wer wählt den Zielordner: 'user' | 'fixed' | 'approver'. */
  movie_root_folder_mode: RootFolderMode;
  series_root_folder_mode: RootFolderMode;
  movie_profile_mode: RootFolderMode;
  series_profile_mode: RootFolderMode;
  default_movie_root: string;
  default_series_root: string;
  radarr_uhd_url: string;
  radarr_uhd_api_key: string;
  radarr_uhd_api_key_set: boolean;
  sonarr_uhd_url: string;
  sonarr_uhd_api_key: string;
  sonarr_uhd_api_key_set: boolean;
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
  mediaserver_auto_import: boolean;
  mediaserver_default_role: "user" | "approver";
  mediaserver_default_quota_movies: number | null;
  mediaserver_default_quota_series: number | null;
  mediaserver_default_quota_period: QuotaPeriod;
  /** Leer = keine Altersbeschränkung für neu angelegte Konten. */
  mediaserver_default_age: number | null;

  /** Dürfen Benutzer ihre Merkliste sehen und daraus anfragen? */
  watchlist_enabled: boolean;
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
export type ChannelKind = "ntfy" | "gotify" | "telegram" | "email";

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
  /** Kam die Anfrage von der Merkliste statt von einem Klick? */
  from_watchlist: boolean;
  requested_at: string;
  approved_at: string | null;
  completed_at: string | null;
  rejection_reason: string | null;
  error_message: string | null;
  rating: number | null;
  feedback: string | null;
  rated_at: string | null;
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
}

export type StorageMine = {
  used_bytes: number
  items: number
  /** Abgegeben, aber noch nicht entschieden – zählt weiter mit. */
  pending_bytes: number
  entries: StorageEntry[]
}

export type StorageShare = {
  /** null steht für den Hausbestand – der gehört niemandem. */
  user_id: number | null
  username: string | null
  display_name: string | null
  used_bytes: number
  items: number
}

export type StorageOverview = {
  total_bytes: number
  house_bytes: number
  house_items: number
  shares: StorageShare[]
}

export type MediaRequestWithUser = MediaRequest & {
  user_id: number;
  username: string;
  display_name: string | null;
  avatar_url: string | null;
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
    | "mediaserver_reconnect";
  /** Übersetzungsschlüssel – der Text kommt aus der Oberfläche. */
  message_key: string;
  message_title: string | null;
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
