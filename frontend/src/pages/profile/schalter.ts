/**
 * Die Haken der Benachrichtigungsseiten: Feld, Text und wer sie sieht.
 *
 * Eine eigene Datei, weil zwei Seiten dieselbe Liste zeigen: die Mail-Seite
 * mit den `mail_*`-Feldern und die Web-Push-Seite mit denselben Haken unter
 * `push_*`. Eine zweite Liste liefe bei der naechsten Meldungsart
 * auseinander, und der Fehler saehe aus wie ein Haken, der nichts tut.
 *
 * Nicht in NotificationSettings.tsx, weil eine Komponentendatei nichts
 * anderes exportieren soll (Fast Refresh haengt daran).
 */

export type MailFeld =
  | 'mail_download_complete'
  | 'mail_request_decided'
  | 'mail_request_pending'
  | 'mail_feedback'
  | 'mail_ticket'
  | 'mail_watch'
  | 'mail_user_imported'
  | 'mail_mediaserver_reconnect'
  | 'mail_storage'
  | 'mail_child_wish'
  | 'mail_cleanup'

export type Schalter = {
  feld: MailFeld
  labelKey: string
  hintKey: string
  /** Wer die Meldung gar nicht bekommen kann, sieht den Schalter nicht. */
  nurEntscheider?: boolean
  nurAdmin?: boolean
  /** Nur für Konten mit verknüpftem Media-Server-Konto von Belang. */
  nurVerknuepft?: boolean
  /** Nur, wenn im Haus überhaupt nach Speicherplatz gerechnet wird. */
  nurMitSpeicher?: boolean
  /** Nur für Konten, die auch wirklich ein aktives Kinderkonto führen. */
  nurMitKindern?: boolean
  /**
   * Nur für Leute ohne Freigaberecht: Wer selbst freigeben darf, wartet nie
   * auf eine Entscheidung – „freigegeben/abgelehnt" kann ihn nicht erreichen,
   * und ein Schalter ohne mögliche Meldung ist eine Einladung zur Verwirrung.
   */
  nieEntscheider?: boolean
}

/**
 * Reihenfolge nach Häufigkeit, nicht alphabetisch.
 *
 * Exportiert, weil die Web-Push-Seite dieselben Haken zeigt: dieselben Texte,
 * dieselben Sichtbarkeitsregeln, nur der Feldname wechselt die Vorsilbe. Eine
 * zweite Liste liefe bei der nächsten Meldungsart auseinander.
 */
export const SCHALTER: Schalter[] = [
  {
    feld: 'mail_download_complete',
    labelKey: 'profile.mailDownloadComplete',
    hintKey: 'profile.mailDownloadCompleteHint',
  },
  {
    // Vorgemerkte Titel. Bewusst getrennt von „deine Anfrage ist fertig": Hier
    // hat der Empfänger nichts angefragt, sondern gewartet, weil ein anderer
    // schneller war. „Ist da" und „neue Folgen" teilen sich einen Haken - die
    // Unterscheidung ist keine, die jemand getrennt abbestellen möchte.
    feld: 'mail_watch',
    labelKey: 'profile.mailWatch',
    hintKey: 'profile.mailWatchHint',
  },
  {
    feld: 'mail_request_decided',
    labelKey: 'profile.mailRequestDecided',
    hintKey: 'profile.mailRequestDecidedHint',
    nieEntscheider: true,
  },
  {
    feld: 'mail_request_pending',
    labelKey: 'profile.mailRequestPending',
    hintKey: 'profile.mailRequestPendingHint',
    nurEntscheider: true,
  },
  {
    feld: 'mail_feedback',
    labelKey: 'profile.mailFeedback',
    hintKey: 'profile.mailFeedbackHint',
    nurEntscheider: true,
  },
  {
    // Für alle sichtbar: Benutzer bekommen Antworten auf ihre Tickets,
    // Administratoren die neuen Anliegen. Ein Schalter für beides.
    feld: 'mail_ticket',
    labelKey: 'profile.mailTicket',
    hintKey: 'profile.mailTicketHint',
  },
  {
    // Nur Administratoren: neue Konten über den Media-Server entstehen sonst
    // lautlos, und niemand merkt, wer dazugekommen ist.
    feld: 'mail_user_imported',
    labelKey: 'profile.mailUserImported',
    hintKey: 'profile.mailUserImportedHint',
    nurAdmin: true,
  },
  {
    // Nur wer ein Plex-Konto verknüpft hat, kann einen abgelaufenen Zugang
    // haben - für alle anderen wäre der Schalter eine Meldung, die nie kommt.
    feld: 'mail_mediaserver_reconnect',
    labelKey: 'profile.mailMediaserverReconnect',
    hintKey: 'profile.mailMediaserverReconnectHint',
    nurVerknuepft: true,
  },
  {
    // Ein Schalter für das ganze Speicher-Thema: abgegeben, entschieden,
    // gewachsen. Was davon einen erreicht, hängt an der Rolle – der
    // Administrator bekommt die Abgaben, alle anderen die Entscheidungen über
    // ihre eigenen. Drei Haken wären drei Zeilen für einen Vorgang.
    //
    // Unsichtbar, solange nicht nach Speicherplatz gerechnet wird: Ein
    // Schalter für eine Meldung, die es nicht geben kann, ist eine Einladung
    // zur Verwirrung.
    feld: 'mail_storage',
    labelKey: 'profile.mailStorage',
    hintKey: 'profile.mailStorageHint',
    nurMitSpeicher: true,
  },
  {
    // Ein Wunsch wartet auf eine Entscheidung, und die Glocke sieht nur, wer
    // die App gerade offen hat. Sichtbar aber erst, wenn es überhaupt ein
    // aktives Kinderkonto gibt - sonst wäre es ein Schalter für eine Meldung,
    // die nicht kommen kann.
    feld: 'mail_child_wish',
    labelKey: 'profile.mailChildWish',
    hintKey: 'profile.mailChildWishHint',
    nurMitKindern: true,
  },
  {
    // ⚠️ Der einzige Schalter, hinter dem kein Ereignis steht, sondern ein
    // Termin: einmal im Monat, ob etwas passiert ist oder nicht. Deshalb ist
    // er auch für sich zu haben — wer keine Einzelmeldungen will, möchte
    // vielleicht trotzdem einmal im Monat aufräumen.
    //
    // Nur sichtbar, solange nach Speicherplatz gerechnet wird: Ohne Messung
    // gäbe es nichts zu berichten.
    feld: 'mail_cleanup',
    labelKey: 'profile.mailCleanup',
    hintKey: 'profile.mailCleanupHint',
    nurMitSpeicher: true,
  },
]
