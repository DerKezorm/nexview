"""Wohin ein Klick auf eine Meldung fuehrt - **eine** Liste, nicht zwei.

⚠️ **Warum es dieses Modul gibt.** Die Antwort stand an zwei Stellen, und sie
waren verschieden:

* ``channel_outbox.LINKS`` baute die Links in Discord-, Telegram- und
  E-Mail-Nachrichten. Die Liste kannte die Speicher-Meldungen und die
  Instanz-Warnung.
* ``NotificationBell.zielFuer`` in der Oberflaeche baute den Klick in der
  Glocke. Die Liste kannte acht Faelle; alles andere fiel auf die eigene
  Anfrageliste durch.

Dieselbe Meldung fuehrte damit in Discord an die richtige Stelle und in der
Glocke auf die private Anfrageliste - und das traf ausgerechnet den Betreiber:
"Jemand hat einen Titel abgegeben" und "Instanz meldet ein Problem" landeten
beide bei seinen eigenen Anfragen, wo weder das eine noch das andere steht.

Jetzt sagt der Server das Ziel, und beide Wege folgen ihm. Auseinanderlaufen
kann es damit nicht mehr.

⚠️ **Die Liste ist vollstaendig, nicht ein Rueckfall mit Ausnahmen.** Beide
alten Listen hatten einen Rueckfall ("alles andere nach ..."), und genau darin
verschwanden die Faelle, an die niemand gedacht hatte. Hier traegt **jede**
Meldungsart einen Eintrag, und ``test_meldungsziele.py`` besteht darauf. Wer
eine neue Art hinzufuegt, muss sich entscheiden - er kann es nicht vergessen.
"""

from __future__ import annotations

from ..models import Notification, NotificationType

#: Die eigene Anfrageliste. Alles, was den Menschen ueber **seine** Anfrage
#: informiert, gehoert dorthin - dort steht sie mit ihrem Zustand.
EIGENE = "/requests"

#: Der Speicher-Reiter im Profil. Der Reiter wird immer angeboten
#: (``ProfilePage``), der Link kann also nicht ins Leere zeigen.
MEIN_SPEICHER = "/profil?reiter=speicher"

ZIELE: dict[NotificationType, str] = {
    # --- Was die eigene Anfrage betrifft -----------------------------------
    NotificationType.download_complete: EIGENE,
    NotificationType.approved: EIGENE,
    NotificationType.rejected: EIGENE,
    NotificationType.cancelled: EIGENE,
    NotificationType.request_deferred: EIGENE,
    NotificationType.request_fulfilled: EIGENE,
    NotificationType.feedback_reply: EIGENE,
    NotificationType.rating_outdated: EIGENE,
    # "Sag mir Bescheid": Der vorgemerkte Titel ist da. Es gibt keine Anfrage
    # dahinter, aber die Anfrageliste ist die Stelle, an der man ihn jetzt
    # bestellt - und sie war schon bisher das Ziel.
    NotificationType.watch_ready: EIGENE,
    NotificationType.watch_episodes: EIGENE,
    # --- Was der Entscheider tun soll --------------------------------------
    NotificationType.request_pending: "/admin/requests",
    # Direkt in den Filter: Sonst landet man auf "wartet auf Freigabe" und
    # muss erst suchen, worum es ging.
    NotificationType.feedback: "/admin/requests?filter=feedback",
    NotificationType.feedback_poor: "/admin/requests?filter=feedback",
    # --- Was der Betreiber tun soll ----------------------------------------
    NotificationType.user_imported: "/admin/settings",
    # ⚠️ Korrigiert: Ueber die Glocke landete das bisher bei den eigenen
    # Anfragen. Entschieden wird eine Abgabe aber in den Einstellungen.
    NotificationType.storage_release_requested: "/admin/settings",
    # ⚠️ Ebenfalls korrigiert - und das ist der Fall, in dem es am meisten
    # weh tut: Bei einem Radarr-Ausfall will man am schnellsten irgendwohin.
    NotificationType.instanz_gesundheit: "/admin/settings",
    # --- Was den eigenen Speicher betrifft ---------------------------------
    NotificationType.storage_released: MEIN_SPEICHER,
    NotificationType.storage_kept: MEIN_SPEICHER,
    NotificationType.storage_deleted: MEIN_SPEICHER,
    NotificationType.storage_grew: MEIN_SPEICHER,
    NotificationType.storage_scheduled: MEIN_SPEICHER,
    NotificationType.storage_unscheduled: MEIN_SPEICHER,
    # --- Der Rest -----------------------------------------------------------
    # Der abgelaufene Zugang wird auf der Profilseite erneuert - dort sitzt die
    # einmalige Media-Server-Anmeldung.
    NotificationType.mediaserver_reconnect: "/profil",
    # Ein Kinderwunsch wird unter "Kinder" entschieden, nicht in den eigenen
    # Anfragen. Dort laege er erst, wenn er freigegeben ist.
    NotificationType.child_wish: "/profil?reiter=kinder",
    # Tickets bekommen unten die Vorgangsnummer angehaengt - die Liste allein
    # hilft nicht weiter, wenn mehrere offen sind.
    NotificationType.ticket_new: "/tickets",
    NotificationType.ticket_reply: "/tickets",
}

#: Bei diesen Arten wird die Vorgangsnummer angehaengt, sofern sie dasteht.
MIT_TICKET = (NotificationType.ticket_new, NotificationType.ticket_reply)


def ziel_fuer(eintrag: Notification) -> str:
    """Der Pfad, auf dem der Sachverhalt dieser Meldung wirklich steht.

    Immer ohne Adresse und ohne Unterpfad - wer daraus einen vollstaendigen
    Link braucht (die Kanaele), haengt ``settings.link(...)`` davor; die
    Oberflaeche haengt ihren eigenen Grundpfad davor.
    """
    pfad = ZIELE.get(eintrag.type)
    if pfad is None:
        # Kann nur passieren, wenn jemand eine Meldungsart ergaenzt und den
        # Eintrag vergisst - ``test_meldungsziele.py`` faengt das vorher ab.
        # Fuer den Fall, dass es doch einmal live passiert: die Startseite ist
        # der einzige Ort, den es fuer jede Rolle gibt.
        return "/"

    if eintrag.type in MIT_TICKET and eintrag.ticket_id:
        return f"/tickets/{eintrag.ticket_id}"
    return pfad
