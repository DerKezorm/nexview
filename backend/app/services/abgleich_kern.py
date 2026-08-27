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


def ist_fertig(request: MediaRequest, eintrag: Any, folgen: dict | None = None) -> bool:
    """Ist **das Angefragte** geladen - das Paket bzw. die Staffel, nicht die Serie?

    ⚠️ ``has_file`` sagt "irgendeine Folge der ganzen Serie liegt vor". Solange
    nur ganze Serien angefragt werden konnten, war das dieselbe Aussage. Seit
    es Staffelanfragen gibt, ist es das nicht mehr: Gemeldet wurde eine Serie
    mit drei Dateien in einer einzigen Staffel, worauf **fuenf** Staffeln
    gleichzeitig als "bereits geladen" galten - und fuenf Fertig-Meldungen in
    derselben Sekunde hinausgingen, obwohl vier davon noch gar nicht gesucht
    hatten.

    Fuer Folgen-Pakete zaehlt der folgengenaue Befund (``folgen``, je Staffel
    die Folgen nach Nummer): fertig, wenn **jede** bestellte Folge eine Datei
    hat. Ohne Befund gibt es keine Aussage - die Anfrage bleibt dann stehen.
    """
    if request.episodes:
        staffel = (folgen or {}).get(request.season) or {}
        if not staffel:
            return False
        return all(
            (folge := staffel.get(nummer)) is not None and folge.has_file
            for nummer in request.episodes
        )
    if request.season is None:
        return bool(getattr(eintrag, "has_file", False))
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and stand.vollstaendig


def ist_noch_da(request: MediaRequest, eintrag: Any, folgen: dict | None = None) -> bool:
    """Liegt vom Angefragten ueberhaupt noch etwas auf der Platte?

    Bewusst schwaecher als :func:`ist_fertig`: Einer fertigen Staffel, der
    jemand eine einzelne Folge entfernt, ist nicht "geloescht". Mit derselben
    Schwelle in beide Richtungen spraenge sie zwischen "geladen" und "weg" hin
    und her, und jeder Sprung erzeugte eine Meldung.

    Fuer Folgen-Pakete gilt dieselbe Asymmetrie je Folge: Solange **eine**
    bestellte Folge liegt, ist nichts "geloescht". Und ohne Befund gibt es
    keine Aussage - im Zweifel gilt "noch da", denn nur was nachweislich weg
    ist, gilt als weg.
    """
    if request.episodes:
        if folgen is None:
            return True
        staffel = folgen.get(request.season) or {}
        return any(
            (folge := staffel.get(nummer)) is not None and folge.has_file
            for nummer in request.episodes
        )
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


def laedt_fortschritt(
    request: MediaRequest, eintrag: Any, warteschlange: list
) -> int | None:
    """Zu wie viel Prozent liegt das **Angefragte** in der Warteschlange?

    ``None`` heisst: Davon laedt gerade nichts - auch dann, wenn andere Teile
    derselben Serie laden. Die Zuordnung ist bewusst vorsichtig: Ein Eintrag
    ohne Staffel- oder Folgenangabe zaehlt bei Staffel- und Paket-Anfragen
    **nicht** mit - lieber keine Anzeige als eine falsche (Sonarrs
    Warteschlange war beim Messen leer, die Feldnamen sind unbelegt).

    Der Fortschritt ist eine Momentaufnahme fuer die Pille, keine Wahrheit
    ueber den Ausgang: Ein Download kann scheitern und neu anlaufen. Genau
    deshalb ist das ein Anzeige-Feld und kein Status.
    """
    arr_id = getattr(eintrag, "arr_id", None) if eintrag is not None else None
    if not arr_id:
        return None
    passend = [
        zeile
        for zeile in warteschlange
        if zeile.arr_id == arr_id and _in_der_anfrage(request, zeile)
    ]
    if not passend:
        return None
    gesamt = sum(zeile.size for zeile in passend)
    geladen = sum(max(0, zeile.size - zeile.sizeleft) for zeile in passend)
    if gesamt <= 0:
        return 0
    return max(0, min(100, round(geladen * 100 / gesamt)))


def _in_der_anfrage(request: MediaRequest, zeile: Any) -> bool:
    if request.media_type != MediaType.tv or request.season is None:
        return True
    if zeile.season != request.season:
        return False
    if request.episodes:
        return zeile.episode is not None and zeile.episode in request.episodes
    return True


def heilung_noetig(
    request: MediaRequest, eintrag: Any, folgen: dict | None = None
) -> bool:
    """Hat Sonarr die Ueberwachung einer laufenden Anfrage abgeraeumt?

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
    if request.episodes:
        if not getattr(eintrag, "monitored", True):
            # Die Serien-Flagge selbst ist abgeraeumt - ohne sie laedt Sonarr
            # auch ueberwachte Folgen nicht.
            return True
        if folgen is None:
            return False
        staffel = folgen.get(request.season) or {}
        if not staffel:
            # Folgen noch nicht bekannt (frisch angelegte Serie): das
            # Einschalten aus der Uebergabe nachholen.
            return True
        return any(
            (folge := staffel.get(nummer)) is not None and not folge.monitored
            for nummer in request.episodes
        )
    stand = (getattr(eintrag, "staffeln", None) or {}).get(request.season)
    return stand is not None and not stand.monitored
