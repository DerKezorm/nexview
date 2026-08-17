export type Role = 'admin' | 'approver' | 'user'
export type QuotaPeriod = 'day' | 'week' | 'month'

export type User = {
  id: number
  username: string
  role: Role
  display_name: string | null
  language: string
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

export type LogEntry = {
  time: string
  level: 'INFO' | 'WARNING' | 'ERROR'
  logger: string
  message: string
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
  status: MediaStatus
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
  /** Vorauswahl für diesen Benutzer – vom Server bestimmt. */
  default_quality_profile_id: number | null
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
  /** Übersetzungsschlüssel – der Text kommt aus der Oberfläche. */
  message_key: string
  message_title: string | null
  request_id: number | null
  is_read: boolean
  created_at: string
}

export type QuotaOverview = {
  movie: QuotaInfo
  tv: QuotaInfo
  auto_approve: boolean
}
