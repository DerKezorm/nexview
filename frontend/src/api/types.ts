export type Role = 'admin' | 'approver' | 'user'
export type QuotaPeriod = 'day' | 'week' | 'month'

export type User = {
  id: number
  username: string
  role: Role
  display_name: string | null
  language: string
  /** 'dark' oder 'light' - siehe lib/theme.ts. */
  theme: string
  is_active: boolean
  auto_approve: boolean
  /** Was tatsächlich gilt – bei Administratoren immer true. */
  effective_auto_approve: boolean
  can_approve: boolean
  quota_movies_limit: number | null
  quota_series_limit: number | null
  quota_period: QuotaPeriod
  /** Leere Liste = alle Qualitätsprofile erlaubt. */
  blocked_movie_profiles: number[]
  blocked_series_profiles: number[]
  avatar_url: string | null
  created_at: string
  last_login_at: string | null
  /** Wann der Admin das Kontingent zuletzt von Hand zurückgesetzt hat. */
  quota_reset_at: string | null
  /** Verbrauch im laufenden Zeitraum – nur in der Admin-Liste gefüllt. */
  quota_movies_used: number
  quota_series_used: number
  email: string | null
  email_verified: boolean

  /**
   * E-Mail-Benachrichtigungen – jede einzeln, Standard überall aus.
   * Die Glocke in der App ist davon nicht betroffen.
   */
  mail_download_complete: boolean
  mail_request_pending: boolean
  mail_request_decided: boolean
  mail_feedback: boolean
  /** Neue Tickets (für Admins) und Antworten darauf (für den Eigentümer). */
  mail_ticket: boolean

  /** Vorbelegung der Filterleiste; null = Vorgabe des Admins. */
  discover_region: string | null

  /**
   * Altersbeschränkung. `null` heißt "nicht beschränkt" – der Normalfall.
   * Sonst das Alter des Benutzers: gezeigt wird, was höchstens ab diesem
   * Alter freigegeben ist.
   *
   * Nur der Administrator darf beides ändern; über das eigene Profil geht es
   * bewusst nicht, sonst höbe der Betroffene die Sperre selbst auf.
   */
  age: number | null
  /** Land, nach dessen Einstufung geurteilt wird; null = Vorgabe des Admins. */
  rating_region: string | null
  /** Titel ganz ohne Einstufung verbergen? Standard ja. */
  hide_unrated: boolean
}

/** Offene Einladung – ein Konto gibt es dazu noch nicht. */
export type Invitation = {
  id: number
  email: string
  role: Role
  created_at: string
  expires_at: string
}

/** Antwort nach dem Einladen – mit Auskunft über den Mailversand. */
export type InvitationCreated = Invitation & {
  mail_sent: boolean
  mail_error: string | null
  manual_link: string | null
}

export type SetupStatus = {
  needs_setup: boolean
  /** Vom Server – der Assistent läuft vor der Anmeldung, /api/config ist noch zu. */
  min_password_length: number
}

export type MediaType = 'movie' | 'tv'

export type MediaStatus =
  | 'not_requested'
  | 'pending_approval'
  | 'requested'
  | 'searching'
  | 'downloaded'
  | 'in_library'
  | 'rejected'
  | 'failed'
  | 'cancelled'
  /** Vom Administrator gesperrt: sichtbar, aber nicht anfragbar. */
  | 'blocked'

export type LogEntry = {
  time: string
  level: 'INFO' | 'WARNING' | 'ERROR'
  logger: string
  message: string
}

/** Eine Staffel, wie TMDB sie kennt. */
export type SeasonInfo = {
  season_number: number
  name: string
  episode_count: number
  air_date: string | null
  overview: string
  poster_url: string | null
  /** Wie viele Folgen davon schon in der Bibliothek liegen (aus Sonarr). */
  episodes_available: number
}

export type EpisodeInfo = {
  episode_number: number
  name: string
  overview: string
  air_date: string | null
  runtime_minutes: number | null
  still_url: string | null
  vote_average: number
  /** Liegt diese Folge schon vor? */
  available: boolean
}

export type SeasonDetail = {
  season_number: number
  name: string
  overview: string
  air_date: string | null
  episodes: EpisodeInfo[]
}

/** Etwas mit Kennung und Namen - Schlagwort oder Studio. */
export type NamedRef = {
  id: number
  name: string
}

export type Trailer = {
  key: string
  name: string
  site: string
  language: string
}

export type CastMember = {
  person_id: number
  name: string
  character: string
  photo_url: string | null
}

export type CrewMember = {
  person_id: number
  name: string
  job: string
}

export type PersonCredit = {
  media_type: MediaType
  tmdb_id: number
  title: string
  character: string
  poster_url: string | null
  release_date: string | null
  vote_average: number
  status: MediaStatus
}

export type PersonDetail = {
  person_id: number
  name: string
  biography: string
  photo_url: string | null
  birthday: string | null
  deathday: string | null
  place_of_birth: string | null
  known_for_department: string
  credits: PersonCredit[]
}

export type MediaItem = {
  media_type: MediaType
  tmdb_id: number
  tvdb_id: number | null
  title: string
  original_title: string | null
  overview: string
  poster_url: string | null
  backdrop_url: string | null
  release_date: string | null
  vote_average: number
  vote_count: number
  genres: string[]
  runtime_minutes: number | null
  certification: string | null
  original_language: string | null
  /** Nur in der Detailansicht einer Serie gefüllt. */
  seasons: SeasonInfo[]
  status: MediaStatus
}

/** Alles zu einem Titel - nur die Detailseite bekommt das. */
export type MediaDetail = MediaItem & {
  tagline: string
  homepage: string | null
  status_text: string
  original_country: string[]
  spoken_languages: string[]
  budget: number | null
  revenue: number | null
  studios: NamedRef[]
  keywords: NamedRef[]
  trailer: Trailer | null
  cast: CastMember[]
  crew: CrewMember[]
  recommendations: MediaItem[]
  /** Nur bei Serien. */
  seasons_total: number | null
  episodes_total: number | null
  series_status: string
  networks: NamedRef[]
}

export type MediaPage = {
  page: number
  total_pages: number
  total_results: number
  items: MediaItem[]
  demo: boolean
  /** Gesetzt, wenn der Abgleich mit Radarr/Sonarr nicht möglich war. */
  arr_warning: string | null
}

export type ArrOptions = {
  quality_profiles: { id: number; name: string }[]
  root_folders: { path: string; free_space: number | null }[]
  /** Welcher Zielordner gilt - und darf der Benutzer ihn ändern? */
  default_root_folder: string | null
  root_folder_choice: boolean
  /** Vorauswahl für diesen Benutzer – vom Server bestimmt. */
  default_quality_profile_id: number | null
}

/** Ein mit dem Herz markierter Titel. */
export type Favorite = {
  media_type: MediaType
  tmdb_id: number
  title: string
  poster_url: string | null
  created_at: string
}

/** Wertungen der großen Portale - nur bei Filmen, aus Radarr. */
export type MovieRatings = {
  /** Für den Link auf die IMDb-Seite. */
  imdb_id: string | null
  imdb: number | null
  imdb_votes: number | null
  rotten_tomatoes: number | null
  metacritic: number | null
}

export type Genre = {
  id: number
  name: string
}

export type AppConfig = {
  default_region: string
  default_language: string
  tmdb_configured: boolean
  radarr_configured: boolean
  sonarr_configured: boolean
  using_demo_data: boolean
  /** Kommt vom Server, damit Formular und Prüfung nicht auseinanderlaufen. */
  min_password_length: number
  /** Ohne beides sind Einladungen sinnlos – der Knopf bleibt gesperrt. */
  mail_configured: boolean
  public_url_set: boolean
}

export type AppSettings = {
  tmdb_api_key: string
  tmdb_api_key_set: boolean
  radarr_url: string
  radarr_api_key: string
  radarr_api_key_set: boolean
  sonarr_url: string
  sonarr_api_key: string
  sonarr_api_key_set: boolean
  default_region: string
  default_language: string
  poll_interval_seconds: number
  demo_mode: 'auto' | 'on' | 'off'
  using_demo_data: boolean
  default_movie_profile_id: number | null
  default_series_profile_id: number | null
  smtp_host: string
  smtp_port: number
  smtp_security: MailSecurity
  smtp_username: string
  /** Maskiert – das echte Passwort verlässt den Server nie. */
  smtp_password: string
  smtp_password_set: boolean
  smtp_from_address: string
  smtp_from_name: string
  mail_configured: boolean
  /** Adresse, unter der Nexview von außen erreichbar ist – steckt in jedem Link. */
  public_url: string
  update_check: boolean
  root_folder_choice: boolean
  default_movie_root: string
  default_series_root: string
}

export type AboutInfo = {
  version: string
  repo_url: string
  release_url: string
  license: string
  /**
   * Ob überhaupt bei GitHub nachgesehen wurde. Für alle außer Administratoren
   * bleibt das aus - sie können ohnehin nicht aktualisieren.
   */
  update_checked: boolean
  latest_version: string | null
  update_available: boolean
  checked_at: string | null
}

export type MailSecurity = 'none' | 'starttls' | 'ssl'

export type TestResult = {
  ok: boolean
  message: string
}

export type MediaRequest = {
  id: number
  media_type: MediaType
  tmdb_id: number
  title: string
  poster_path: string | null
  release_date: string | null
  status: MediaStatus
  quality_profile_id: number | null
  root_folder_path: string | null
  /** Nur bei Serien; null = ganze Serie. */
  season: number | null
  requested_at: string
  approved_at: string | null
  completed_at: string | null
  rejection_reason: string | null
  error_message: string | null
  rating: number | null
  feedback: string | null
  rated_at: string | null
  feedback_reply: string | null
  replied_at: string | null
}

export type QuotaInfo = {
  limit: number | null
  used: number
  remaining: number | null
  unlimited: boolean
  exhausted: boolean
  period: QuotaPeriod
  resets_at: string | null
}

export type MediaRequestWithUser = MediaRequest & {
  user_id: number
  username: string
  display_name: string | null
  avatar_url: string | null
}

/** Ein zuletzt fertig geladener Titel für die Startseite. */
export type RecentItem = {
  request_id: number
  media_type: MediaType
  tmdb_id: number
  title: string
  overview: string
  poster_url: string | null
  backdrop_url: string | null
  release_date: string | null
  vote_average: number
  runtime_minutes: number | null
  genres: string[]
  completed_at: string | null
  requested_by: string
  requester_avatar: string | null
}

export type UserStats = {
  user_id: number
  username: string
  display_name: string | null
  avatar_url: string | null
  role: Role
  total: number
  movies: number
  series: number
  downloaded: number
  pending: number
  rejected: number
  cancelled: number
  failed: number
  ratings: number
  average_rating: number | null
  poor_ratings: number
  /** Anteil der Anfragen, die als Download ankamen - null ohne Anfragen. */
  success_rate: number | null
  quota_movie_used: number
  quota_movie_limit: number | null
  quota_series_used: number
  quota_series_limit: number | null
}

export type Stats = {
  totals: {
    requests: number
    movies: number
    series: number
    downloaded: number
    downloaded_movies: number
    downloaded_series: number
    pending: number
    rejected: number
    cancelled: number
    failed: number
    active_users: number
    ratings: number
    average_rating: number | null
    poor_ratings: number
    unanswered_feedback: number
    rating_distribution: Record<number, number>
    last_request_at: string | null
  }
  users: UserStats[]
  history: { month: string; movies: number; series: number }[]
  most_requested: {
    media_type: MediaType
    tmdb_id: number
    title: string
    poster_path: string | null
    count: number
  }[]
}

export type AppNotification = {
  id: number
  type:
    | 'download_complete'
    | 'approved'
    | 'rejected'
    | 'cancelled'
    | 'request_pending'
    | 'feedback'
    | 'feedback_poor'
    | 'feedback_reply'
    | 'ticket_new'
    | 'ticket_reply'
  /** Übersetzungsschlüssel – der Text kommt aus der Oberfläche. */
  message_key: string
  message_title: string | null
  request_id: number | null
  ticket_id: number | null
  is_read: boolean
  created_at: string
}

export type QuotaOverview = {
  movie: QuotaInfo
  tv: QuotaInfo
  auto_approve: boolean
}
