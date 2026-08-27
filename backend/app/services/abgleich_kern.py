"""Der Kern des Status-Abgleichs: bewertet Befunde, ruft nie selbst an.

Der Takt-Laeufer (``status_poller``) beschafft die Befunde aus Radarr und
Sonarr und fuehrt die Folgen aus - Status setzen, Speicher verbuchen, Meldung
schicken. Dieser Baustein beantwortet nur die Fragen dazwischen: fertig?
noch da? wirklich weg? Ueberwachung kaputt?

⚠️ **Hier gehoert kein Netzaufruf und keine Datenbank hinein.** Die Trennung
ist Absicht und Vorbereitung: Spaeter sollen Sonarr und Radarr Nexview je
Ereignis anrufen (Webhook), statt nur im Takt befragt zu werden. Der Anruf
wird dann kein zweiter Wahrheits-Kanal, sondern nur ein Wecker - dieselbe
Beschaffung, derselbe Kern, dieselben Uebergaenge. Was hier landet, muss
deshalb mit jedem Zulieferer funktionieren: Der Takt reicht seine
Bibliotheks-Eintraege herein, ein Webhook-Empfaenger spaeter seinen frisch
geholten Einzel-Befund.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..models import MediaRequest, MediaType, RequestStatus, utcnow


def ist_fertig(request: MediaRequest, eintrag: Any) -> bool:
    """Ist **das Angefragte** geladen - die Staffel, nicht die Serie?

    ⚠️ ``has_file`` sagt "irgendeine Folge der ganzen Serie liegt vor". Solange
    nur ganze Serien angefragt werden konnten, war das dieselbe Aussage. Seit
    es Staffelanfragen gibt, ist es das nicht mehr: Gemeldet wurde eine Serie
    mit drei Dateien in einer einzigen Staffel, worauf **fuenf** Staffeln
    gleichzeitig als "bereits geladen" galten - und fuenf Fertig-Meldungen in
    derselben Sekunde hinausgingen, obwohl vier davon noch gar nicht gesucht
    hatten.
    """
    if request.season is None:
        return bool(getattr(eintrag, "has_file", False))
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and stand.vollstaendig


def ist_noch_da(request: MediaRequest, eintrag: Any) -> bool:
    """Liegt vom Angefragten ueberhaupt noch etwas auf der Platte?

    Bewusst schwaecher als :func:`ist_fertig`: Einer fertigen Staffel, der
    jemand eine einzelne Folge entfernt, ist nicht "geloescht". Mit derselben
    Schwelle in beide Richtungen spraenge sie zwischen "geladen" und "weg" hin
    und her, und jeder Sprung erzeugte eine Meldung.
    """
    if request.season is None:
        return bool(getattr(eintrag, "has_file", False))
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and stand.dateien > 0


# Wie lange eine frisch uebergebene Anfrage geschont wird, bevor ihr
# Verschwinden als endgueltig gilt.
#
# ⚠️ Ohne diese Frist bricht der naechste Durchgang genau die Anfrage ab, die
# gerade erst an Radarr uebergeben wurde: Die Bibliothek wird kurz
# zwischengespeichert, und ein Titel, der vor dreissig Sekunden angelegt wurde,
# steht in der alten Antwort noch nicht drin.
SCHONFRIST_MINUTEN = 15


def ist_wirklich_weg(request: MediaRequest, instanz_hat_geantwortet: bool) -> bool:
    """Ist der Titel wirklich aus der Instanz verschwunden?

    Drei Bedingungen, und alle drei sind noetig:

    * Die Instanz **hat geantwortet** - das sagt der Zulieferer, denn nur er
      weiss, ob sein leeres Ergebnis "leer" heisst oder "nicht erreichbar".
      Sonst waere jeder Ausfall ein Grund, reihenweise Anfragen abzubrechen.
    * Die Anfrage wurde bereits **uebergeben** (``approved`` oder
      ``searching``). Eine wartende Freigabe steht naturgemaess in keiner
      Bibliothek.
    * Sie liegt laenger als die Schonfrist zurueck - siehe dort.
    """
    if not instanz_hat_geantwortet:
        return False
    if request.status not in (RequestStatus.approved, RequestStatus.searching):
        return False
    seit = request.approved_at or request.requested_at
    if seit is None:
        return False
    # ⚠️ Aus der Datenbank kommen Zeiten **ohne** Zeitzone zurueck, ``utcnow``
    # liefert eine **mit** - der direkte Vergleich wirft einen TypeError.
    # Dasselbe ``.replace(tzinfo=None)`` steht an jeder anderen Stelle, die
    # gespeicherte Zeiten vergleicht (``cache``, ``tokens``, ``quota``).
    jetzt = utcnow().replace(tzinfo=None)
    if seit.tzinfo is not None:
        seit = seit.replace(tzinfo=None)
    return (jetzt - seit) > timedelta(minutes=SCHONFRIST_MINUTEN)


def heilung_noetig(request: MediaRequest, eintrag: Any) -> bool:
    """Hat Sonarr die Ueberwachung einer laufenden Staffelanfrage abgeraeumt?

    ⚠️ Sonarrs ``addOptions.monitor: "none"`` wirkt asynchron: Bei einer
    frisch angelegten Serie laedt Sonarr erst die Metadaten und raeumt
    **danach** die Ueberwachung ab - auch die Staffel, die Nexview unmittelbar
    nach dem Anlegen eingeschaltet hat. Live nachgemessen: Staffel
    freigegeben, Antwort "wird gesucht", und in Sonarr war alles aus - fuer
    immer, denn die Heilung bei der naechsten Freigabe derselben Serie setzt
    eine naechste Freigabe voraus. Deshalb prueft jeder Durchgang mit dieser
    Frage: Ist zu einer laufenden Staffelanfrage die Ueberwachung aus, stellt
    der Zulieferer sie aus Nexviews eigenen Anfragen wieder her - und stoesst
    die Suche an, die sonst nie lief.
    """
    if request.media_type != MediaType.tv or request.season is None:
        return False
    if request.status not in (RequestStatus.approved, RequestStatus.searching):
        return False
    if eintrag is None or not getattr(eintrag, "arr_id", None):
        return False
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and not stand.monitored
