import type { MediaType } from './types'

export type TicketStatus = 'open' | 'in_progress' | 'closed'

export const TICKET_STATUSES: TicketStatus[] = ['open', 'in_progress', 'closed']

export type TicketMessage = {
  id: number
  body: string
  created_at: string
  /** Gesetzt, sobald jemand nachgebessert hat - null heißt unverändert. */
  edited_at: string | null
  /** null, wenn das Konto gelöscht wurde. Der Verlauf bleibt trotzdem lesbar. */
  user_id: number | null
  username: string | null
  display_name: string | null
  avatar_url: string | null
  /** Kommt die Nachricht von jemand anderem als dem Eigentümer des Tickets? */
  from_staff: boolean
}

export type Ticket = {
  id: number
  subject: string
  status: TicketStatus
  created_at: string
  updated_at: string
  closed_at: string | null
  last_reply_at: string | null
  last_reply_by: number | null

  /** Optionaler Bezug zu einem Titel - aus „Problem melden". */
  media_type: MediaType | null
  tmdb_id: number | null
  media_title: string | null

  user_id: number
  username: string
  display_name: string | null
  avatar_url: string | null
  message_count: number
  /**
   * Wer das Ticket eröffnet hat. Schreibt der Administrator jemanden an,
   * ist das *nicht* der Eigentümer.
   */
  opened_by: number | null
  opened_by_name: string | null
}

export type TicketDetail = Ticket & {
  messages: TicketMessage[]
  /**
   * Ist das der Antrag „bitte Kinderkonten freischalten", und steht die
   * Freigabe noch aus? Dann bekommt der Administrator hier einen Knopf, statt
   * in die Benutzerverwaltung wechseln zu müssen.
   */
  kinderkonten_offen: boolean
}
