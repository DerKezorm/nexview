"""Was gerade nicht stimmt - an **einer** Stelle, fuer drei Ansichten.

Ein Betreiber hat 105 admin-eigene Pfade auf 30 Einstellungsseiten. Was ihm
fehlt, ist nicht noch eine Seite, sondern der eine Ort, der sagt: *drei
Anfragen warten seit Tagen, Sonarr meldet ein Problem, die letzte Sicherung
ist ueberfaellig.* Dieses Modul ist die Quelle dafuer.

**Warum ein Register und nicht drei Endpunkte.** Das Admin-Dashboard zeigt die
dringendsten Befunde, die Analyse-Seite alle eines Bereichs, die Kachel nur
ihre Anzahl. Das ist dieselbe Antwort in drei Verdichtungen - drei getrennte
Rechnungen liefen unweigerlich auseinander. Genau diesen Fehler haben
``Reiterreihe`` und ``MediaCard`` im Frontend schon einmal geheilt.

Drei Regeln, an denen sich jede neue Pruefung messen lassen muss:

1. **Der Text steht nicht hier.** Geliefert werden Kennung und Werte; die
   Oberflaeche uebersetzt (``befund.<kennung>.titel`` und
   ``befund.<kennung>.folge``). Sonst waere jeder Befund einsprachig.
   **Ausnahme** ist ``wortlaut``: Was Radarr oder Sonarr selbst meldet, bleibt
   im Original stehen - es ist ihre Aussage, nicht unsere (dieselbe
   Begruendung wie in ``pages/settings/InstanzGesundheit.tsx``).

2. **Eine Pruefung rechnet nicht nach draussen.** Sie liest Datenbank und
   Zwischenspeicher, nie das Netz. Was gemessen werden muss, misst der
   Rundgang und legt es ab. Andernfalls kostet ein Aufruf des Dashboards
   zwanzig Radarr-Anfragen - und zwar bei jedem Neuladen.

3. **Jeder Befund sagt, was folgt und wohin.** Nicht "Indexer 3/5 aktiv",
   sondern "zwei Indexer antworten nicht, deshalb bleiben Serienanfragen
   liegen" - mit einem Ziel, das man anklicken kann. Ein Befund ohne Ausweg
   ist eine Sorge, keine Hilfe.

Eine neue Pruefung ist eine Funktion, ein Eintrag in ``PRUEFUNGEN`` und zwei
Schluessel in **beiden** Sprachdateien.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    ArrWebhook,
    BefundGesehen,
    SpeicherVerlauf,
    MediaRequest,
    Notification,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
    utcnow,
)
from . import (
    abgleich,
    instanz_gesundheit,
    instanz_stand,
    logs,
    mail_outbox,
    sicherung,
    updates,
)
from .settings_service import AppSettings

logger = logging.getLogger("nexview.befunde")


class Schwere(str, enum.Enum):
    """Wie dringend - und damit auch: in welcher Reihenfolge.

    ``fehler`` heisst "etwas funktioniert nicht", ``warnung`` "es geht noch
    gut, aber nicht mehr lange", ``hinweis`` "koennte man mal aufraeumen".
    Die Trennung traegt nur, solange sie streng bleibt: Sobald Aufraeum-Ideen
    als Warnung erscheinen, gewoehnt man sich das Wegsehen an - und uebersieht
    die eine Warnung, auf die es ankommt.
    """

    fehler = "fehler"
    warnung = "warnung"
    hinweis = "hinweis"


#: Sortierreihenfolge. Steht hier und nicht im Frontend, damit Dashboard,
#: Analyse und Kachel dieselbe "die drei dringendsten" meinen.
RANG = {Schwere.fehler: 0, Schwere.warnung: 1, Schwere.hinweis: 2}


class Bereich(str, enum.Enum):
    """Wohin ein Befund auf der Analyse-Seite gehoert."""

    dienste = "dienste"
    platz = "platz"
    nachschub = "nachschub"
    bibliothek = "bibliothek"
    #: Wo die Quellen sich widersprechen - Radarr/Sonarr gegen Medienserver.
    #: Ein eigener Bereich und kein Teil von "bibliothek": Dort geht es um den
    #: Bestand selbst, hier darum, ob die Buecher uebereinstimmen.
    abgleich = "abgleich"
    betrieb = "betrieb"


@dataclass
class Befund:
    """Ein einzelner Befund."""

    #: Stabil, traegt den i18n-Schluessel. Wer sie umbenennt, macht die
    #: Uebersetzung stumm - deshalb aendert man sie nicht "nur schoener".
    kennung: str
    schwere: Schwere
    bereich: Bereich
    #: Zahlen und Namen fuer die Textbausteine (anzahl, instanz, tage, ...).
    werte: dict = field(default_factory=dict)
    #: Wohin der Knopf fuehrt. ``None`` heisst: es gibt nichts anzuklicken.
    ziel: str | None = None
    #: Fremder Wortlaut, unuebersetzt - nur fuer Meldungen von Radarr/Sonarr.
    wortlaut: str | None = None
    #: Macht den Schluessel eindeutig, wenn dieselbe Pruefung mehrfach
    #: anschlaegt (je Instanz, je Datentraeger). Reine Technik, nie angezeigt.
    zusatz: str = ""

    @property
    def schluessel(self) -> str:
        return f"{self.kennung}|{self.zusatz}" if self.zusatz else self.kennung


# ---------------------------------------------------------------------------
# Schwellen
# ---------------------------------------------------------------------------
#
# WICHTIG: **Bewusst fest verdrahtet, nicht einstellbar.** Es gibt schon
# dreissig Einstellungsseiten; eine einunddreissigste fuer Zahlen, die kaum
# jemand anfasst, waere genau das Problem, das dieses Vorhaben loesen soll.
# Wer sie aendern will, aendert sie hier - mit einer Begruendung daneben.

#: Ab wann eine Anfrage im Zustand "wird gesucht" als haengend gilt.
#:
#: Vierzehn Tage, weil ein Titel vor dem Kinostart voellig zu Recht wochenlang
#: sucht - kuerzer angesetzt waere die Haelfte aller Treffer falsch. Wer
#: dagegen seit zwei Wochen an einem *veroeffentlichten* Titel sucht, hat in
#: aller Regel einen kaputten Indexer, und genau das faellt heute erst auf,
#: wenn sich jemand beschwert.
HAENGT_TAGE = 14

#: Ab wann eine wartende Freigabe unangenehm wird. Drei Tage: Wer laenger
#: wartet, fragt nach - und dann ist es eine Beschwerde statt eines Hinweises.
FREIGABE_TAGE = 3

#: Fehlgeschlagene Anfragen im Zeitfenster, ab denen es ein Muster ist.
#: Einzelne Fehlschlaege sind normal (Datei weg, Umbenennung, Handarbeit);
#: vier in einer Woche sind es nicht.
FEHLGESCHLAGEN_TAGE = 7
FEHLGESCHLAGEN_AB = 4

#: Wie lange eine aufgegebene Mail noch gemeldet wird. Laenger zurueck ist es
#: Geschichte und keine Aufgabe mehr.
MAIL_TAGE = 7

#: Ab welchem Fuellstand eine Platte gemeldet wird.
#:
#: Neunzig Prozent, weil unterhalb davon nichts passiert, was der Betreiber
#: nicht ohnehin weiss - und weil oberhalb die Zeit knapp wird: Bei einer
#: 80-TB-Platte sind die letzten zehn Prozent acht Terabyte, also wenige
#: Wochen. Fuenfundneunzig ist die Grenze, ab der der naechste grosse Download
#: schlicht nicht mehr passt; das ist kein Ausblick mehr, sondern ein Fehler.
PLATZ_WARNUNG = 0.90
PLATZ_FEHLER = 0.95

#: Ab wann eine stumme Instanz gemeldet wird.
#:
#: Nicht sofort: Ein Neustart des Containers, ein Update, ein kurzer
#: Netzhaenger - all das erzeugt eine oder zwei Runden ohne Antwort, und wer
#: dafuer eine Meldung bekommt, schaltet sie ab. Nach einer Viertelstunde ist
#: es kein Neustart mehr.
STUMM_MINUTEN = 15

#: Ab wann eine Wachstums-Vorhersage gemeldet wird.
#:
#: Sechs Wochen, weil das die Zeit ist, in der man noch etwas tun **kann**:
#: Platten bestellen, aufraeumen, umziehen. Wer erst bei einer Woche gewarnt
#: wird, hat nur noch die Wahl zwischen Loeschen und Ueberlaufen.
VOLL_IN_WOCHEN = 6

#: So viele Tage Verlauf muessen vorliegen, bevor gerechnet wird.
#:
#: ⚠️ **Der wichtigste Wert hier.** Aus zwei Punkten laesst sich jede beliebige
#: Zukunft herauslesen: Ein einziger grosser Download am Vortag ergaebe
#: hochgerechnet "in drei Tagen voll". Eine Woche glaettet das so weit, dass
#: die Aussage etwas taugt - und solange sie fehlt, wird lieber geschwiegen
#: als geraten.
VERLAUF_MINDESTTAGE = 7

#: Ab wie vielen Fehlerzeilen in 24 Stunden das Protokoll auffaellt.
#: Einzelne Fehler gibt es in jedem Betrieb (eine Zeitueberschreitung, ein
#: abgelehnter Aufruf); zwanzig an einem Tag sind ein Muster.
PROTOKOLL_FEHLER_AB = 20

# --- Abgleich der Quellen ---------------------------------------------------
#
# ⚠️ **Alle mit Schwelle, und zwar aus einem Grund.** Zwischen zwei Messungen
# liegt eine Stunde; ein Titel, der gerade geladen wurde, ist in Radarr schon
# da und im Medienserver noch nicht. Einzelne Abweichungen sind also der
# normale Betrieb und kein Fund - erst eine Haeufung heisst, dass etwas
# strukturell nicht stimmt.

#: Dateien in Radarr/Sonarr, die kein Medienserver kennt.
ARR_OHNE_SERVER_AB = 10

#: Titel, denen der Medienserver keine einzige Kennung zuordnen konnte.
#: Fuer Nexview sind sie unsichtbar und werden ein zweites Mal bestellt.
NICHT_ERKANNT_AB = 5

#: Titel, die ein Anbieter kennt und ein anderer nicht.
ANBIETER_LUECKE_AB = 25

#: Zugestandene Verspaetung fuer die automatische Sicherung. Der Waechter sieht
#: stuendlich nach (``sicherung.NACHSEHEN_SEKUNDEN``), ein Tag Luft deckt also
#: auch einen laengeren Stillstand des Containers ab, ohne falschen Alarm.
SICHERUNG_TOLERANZ_TAGE = 1


def _jetzt() -> datetime:
    """Ohne Zeitzone - so liegen die Zeitstempel in der Datenbank."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Dienste
# ---------------------------------------------------------------------------


def _dienst_ziel(instanz) -> str:
    """Der Weg zu genau dieser Instanz - Reiter und Unterreiter.

    ⚠️ **Die Adress-Woerter sind eine Zusage der Oberflaeche**, nachzulesen in
    ``SettingsPage.REITER_AUS_ADRESSE`` und
    ``AdminServicesSettings.UNTER_AUS_ADRESSE``. Wer sie dort umbenennt, macht
    diese Ziele stumm - ohne dass irgendetwas rot wird.

    Welcher Unterreiter, entscheidet die Art der Instanz und nicht ihr Name:
    Ein Haus kann seine Instanzen nennen, wie es will ("Filme", "Anime"), und
    ein Ziel, das auf einen Namen baut, waere anderswo falsch.
    """
    return (
        "/admin/settings?reiter=dienste&unter="
        + ("radarr" if instanz.media_type == "movie" else "sonarr")
    )


def _dienst_meldet_problem(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Was Radarr oder Sonarr selbst unter ``/health`` fuehrt.

    Der Rundgang holt das ohnehin jede Runde und legt es in ``arr_gesundheit``
    ab; hier wird es nur gelesen. Bisher war es allein als Warnkasten tief in
    den Einstellungen zu sehen - und in der Glocke, einmal, beim ersten Mal.
    """
    ergebnis: list[Befund] = []
    staende = instanz_stand.alle(db)
    for instanz in settings.arr_instanzen():
        # ⚠️ **Eine stumme Instanz meldet hier gar nichts.** Was in
        # ``arr_gesundheit`` steht, ist der zuletzt *gesehene* Stand; ob er noch
        # gilt, weiss in dem Moment niemand - ``instanz_gesundheit`` laesst ihn
        # bei Nichterreichbarkeit ausdruecklich unangetastet, statt ihn zu
        # loeschen. Ihn trotzdem als Befund zu zeigen hiesse: zwei Zeilen fuer
        # eine Instanz, von denen die zweite geraten ist. Die eine Zeile, die
        # zaehlt, ist "antwortet nicht".
        gemessen = staende.get(instanz.kennung)
        if gemessen is not None and not gemessen.erreichbar:
            continue
        zeile = instanz_gesundheit.eintrag(db, instanz.kennung)
        for problem in (zeile.stand if zeile else None) or []:
            typ = str(problem.get("typ") or "warning").lower()
            ergebnis.append(
                Befund(
                    kennung="dienst.meldet_problem",
                    # Radarr kennt "error" und "warning"; alles andere wird
                    # nachsichtig als Warnung gelesen statt verworfen.
                    schwere=Schwere.fehler if typ == "error" else Schwere.warnung,
                    bereich=Bereich.dienste,
                    werte={"instanz": instanz.name},
                    wortlaut=str(problem.get("text") or ""),
                    ziel=_dienst_ziel(instanz),
                    zusatz=f"{instanz.kennung}|{problem.get('schluessel') or ''}",
                )
            )
    return ergebnis


def _dienst_rueckkanal_gestoert(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Der Rueckkanal ist eingeschaltet, kommt aber nicht zustande.

    WICHTIG: **Gemeldet wird der bekannte Fehler, nicht das Schweigen.**
    Naheliegend waere "seit sieben Tagen kein Anruf" - aber ein Haushalt, in
    dem eine Woche lang niemand etwas anfragt, bekaeme dann eine Warnung fuer
    voellig richtiges Verhalten. ``ArrWebhook.fehler`` dagegen setzt die Pflege
    nur, wenn sie selbst festgestellt hat, dass es klemmt.
    """
    ergebnis: list[Befund] = []
    for instanz in settings.arr_instanzen():
        zeile = db.scalar(
            select(ArrWebhook).where(ArrWebhook.kennung == instanz.kennung)
        )
        if zeile is None or not zeile.aktiv or not zeile.fehler:
            continue
        ergebnis.append(
            Befund(
                kennung="dienst.rueckkanal_gestoert",
                schwere=Schwere.warnung,
                bereich=Bereich.dienste,
                werte={"instanz": instanz.name, "grund": zeile.fehler},
                wortlaut=zeile.fehler_info or None,
                ziel=_dienst_ziel(instanz),
                zusatz=instanz.kennung,
            )
        )
    return ergebnis


def _dienst_nicht_erreichbar(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Eine Instanz antwortet nicht mehr.

    ⚠️ **Der wichtigste Teil ist das "seit".** Ob Radarr gerade neu startet
    oder seit gestern Abend weg ist, sind zwei voellig verschiedene
    Nachrichten - und die Diensteseite kann das bis heute nicht sagen, weil
    sie nur live fragt und nichts merkt. Der Zeitpunkt kommt aus
    ``InstanzStand.erreichbar_seit``, das nur beim *Wechsel* gesetzt wird.

    Erst nach ``STUMM_MINUTEN``: Ein Update dauert laenger als eine
    Poller-Runde, und eine Meldung dafuer waere Laerm.
    """
    ergebnis: list[Befund] = []
    staende = instanz_stand.alle(db)
    for instanz in settings.arr_instanzen():
        zeile = staende.get(instanz.kennung)
        if zeile is None or zeile.erreichbar:
            continue
        seit = zeile.erreichbar_seit
        minuten = int((jetzt - seit).total_seconds() // 60) if seit else 0
        if seit is not None and minuten < STUMM_MINUTEN:
            continue
        ergebnis.append(
            Befund(
                kennung="dienst.nicht_erreichbar",
                schwere=Schwere.fehler,
                bereich=Bereich.dienste,
                werte={"instanz": instanz.name, "minuten": minuten},
                ziel=_dienst_ziel(instanz),
                zusatz=instanz.kennung,
            )
        )
    return ergebnis


def _dienst_version_alt(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Fuer eine Instanz steht eine neuere Fassung bereit.

    Nur ein Hinweis: Ein ausstehendes Update ist keine Stoerung, und wer
    bewusst auf einer Fassung bleibt, soll dafuer keine Warnung bekommen.
    """
    ergebnis: list[Befund] = []
    staende = instanz_stand.alle(db)
    for instanz in settings.arr_instanzen():
        zeile = staende.get(instanz.kennung)
        neuer = (zeile.messwerte or {}).get("aktualisierung") if zeile else None
        if not isinstance(neuer, dict) or not neuer.get("version"):
            continue
        ergebnis.append(
            Befund(
                kennung="dienst.version_alt",
                schwere=Schwere.hinweis,
                bereich=Bereich.dienste,
                werte={
                    "instanz": instanz.name,
                    "jetzt": zeile.version if zeile else "",
                    "neu": str(neuer["version"]),
                },
                ziel=_dienst_ziel(instanz),
                zusatz=instanz.kennung,
            )
        )
    return ergebnis


# ---------------------------------------------------------------------------
# Platz
# ---------------------------------------------------------------------------


def _platz_knapp(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Eine Platte laeuft voll.

    ⚠️ **Je Datentraeger, nicht je Instanz.** Filme und Serien liegen fast
    immer auf derselben Platte; je Instanz zu melden hiesse, dieselbe Platte
    drei- oder viermal als Befund zu zeigen. ``storage.traeger`` entdoppelt
    schon beim Messen ueber die Gesamtgroesse - hier wird nur noch gelesen,
    und zwar aus **einer** Instanz-Zeile: Der Wert ist haus-weit derselbe.
    """
    for zeile in instanz_stand.alle(db).values():
        traeger = (zeile.messwerte or {}).get("traeger")
        if not isinstance(traeger, list):
            continue
        ergebnis: list[Befund] = []
        for platte in traeger:
            if not isinstance(platte, dict):
                continue
            anteil = platte.get("belegt_anteil")
            if not isinstance(anteil, (int, float)) or anteil < PLATZ_WARNUNG:
                continue
            ordner = [str(o) for o in (platte.get("ordner") or [])]
            ergebnis.append(
                Befund(
                    kennung="platz.knapp",
                    schwere=(
                        Schwere.fehler if anteil >= PLATZ_FEHLER else Schwere.warnung
                    ),
                    bereich=Bereich.platz,
                    werte={
                        "prozent": round(anteil * 100),
                        "bytes": int(platte.get("frei") or 0),
                        # Der Ordner sagt einem Betreiber, welche Platte
                        # gemeint ist - eine Byte-Zahl tut das nicht.
                        "ordner": ", ".join(ordner) or "?",
                    },
                    ziel="/admin/settings?reiter=kontingente",
                    zusatz=str(platte.get("gesamt") or ""),
                )
            )
        return ergebnis
    return []


def _platz_waechst_schnell(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Bei diesem Tempo ist die Platte bald voll.

    ⚠️ **Die Vorhersage ist der eigentliche Wert - und die Falle.** "Zu 91
    Prozent belegt" sagt nicht, ob man heute Abend Platten bestellt oder es
    notiert. Genau das beantwortet die Steigung. Sie beantwortet es aber nur,
    wenn genug Verlauf da ist: Aus zwei Punkten liesse sich jede beliebige
    Zukunft herauslesen, und ein einziger grosser Download am Vortag ergaebe
    "in drei Tagen voll".

    Deshalb: erst ab ``VERLAUF_MINDESTTAGE``, und **nur bei Wachstum**. Wer
    aufgeraeumt hat, bekommt keine Warnung, die aus einer negativen Steigung
    entstanden ist.
    """
    punkte = list(
        db.scalars(
            select(SpeicherVerlauf).order_by(SpeicherVerlauf.tag.desc()).limit(60)
        )
    )
    if len(punkte) < VERLAUF_MINDESTTAGE:
        return []

    neuester, aeltester = punkte[0], punkte[-1]
    try:
        tage = (
            datetime.strptime(neuester.tag, "%Y-%m-%d")
            - datetime.strptime(aeltester.tag, "%Y-%m-%d")
        ).days
    except ValueError:
        return []
    if tage < VERLAUF_MINDESTTAGE:
        return []

    zuwachs = neuester.belegt_bytes - aeltester.belegt_bytes
    if zuwachs <= 0 or neuester.frei_bytes <= 0:
        return []

    pro_tag = zuwachs / tage
    wochen = (neuester.frei_bytes / pro_tag) / 7
    if wochen > VOLL_IN_WOCHEN:
        return []

    return [
        Befund(
            kennung="platz.waechst_schnell",
            schwere=Schwere.hinweis,
            bereich=Bereich.platz,
            werte={
                # ⚠️ Heisst ``anzahl`` und nicht ``wochen``, obwohl es Wochen
                # sind: Die Oberflaeche macht genau aus diesem Feld i18nexts
                # ``count`` und kann den Text dann beugen. Als ``wochen`` stand
                # dort "in etwa 1 Wochen voll".
                "anzahl": max(1, round(wochen)),
                "bytes": int(pro_tag * 7),
                "tage": tage,
            },
            ziel="/admin/settings?reiter=kontingente",
        )
    ]


# ---------------------------------------------------------------------------
# Nachschub
# ---------------------------------------------------------------------------


def _nachschub_haengt(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Anfragen, die seit Wochen suchen - der stille Totalausfall.

    Faellt heute erst auf, wenn sich jemand beschwert: In der Oberflaeche sieht
    "wird gesucht" nach Arbeit aus, nicht nach Stillstand. Gerechnet wird ab
    der Freigabe, nicht ab der Bestellung - vorher hat ja niemand gesucht.

    ⚠️ **Und ab dem Erscheinen, nicht nur ab der Freigabe.** Man kann Titel
    anfragen, die erst in Monaten herauskommen; die suchen voellig zu Recht
    wochenlang, und nichts daran ist kaputt. Die Schwelle allein hat das nie
    geprueft, sie hat es nur *angenommen* - vierzehn Tage galten als genug
    Puffer. Bei einem Film mit Start im naechsten Halbjahr stimmt die Annahme
    nicht, und der Befund meldete einen Ausfall, den es nicht gab. Genau so
    ist es gemeldet worden.

    Deshalb zaehlt hier der **spaetere** der beiden Zeitpunkte: freigegeben
    *und* erschienen, beides mindestens ``HAENGT_TAGE`` her.

    Ein Titel **ohne** Erscheinungsdatum zaehlt mit. Das ist die unangenehmere
    Wahl, aber die richtige: Ohne Datum laesst sich nicht sagen, dass er noch
    nicht heraus ist - und ein echter Indexer-Ausfall an einem alten Titel
    bliebe sonst stumm.

    Der Titel des aeltesten Falls steht im Befund. Ohne ihn nannte die Kachel
    nur eine Zahl, und der einzige Weg weiter fuehrte in eine Liste *aller*
    suchenden Anfragen - man wusste nicht einmal, wonach man dort sucht.
    """
    grenze = jetzt - timedelta(days=HAENGT_TAGE)
    seit = func.coalesce(MediaRequest.approved_at, MediaRequest.requested_at)
    # ``release_date`` steht als "JJJJ-MM-TT" in der Zeile - der Vergleich mit
    # demselben Format ist damit ein gewoehnlicher Zeichenvergleich.
    erschienen_bis = (jetzt - timedelta(days=HAENGT_TAGE)).date().isoformat()
    treffer = list(
        db.execute(
            select(MediaRequest.title)
            .where(
                MediaRequest.status == RequestStatus.searching,
                seit < grenze,
                or_(
                    MediaRequest.release_date.is_(None),
                    MediaRequest.release_date == "",
                    MediaRequest.release_date <= erschienen_bis,
                ),
            )
            # Der aelteste zuerst - sein Titel steht nachher in der Kachel.
            .order_by(seit)
        ).scalars()
    )
    if not treffer:
        return []
    return [
        Befund(
            kennung="nachschub.haengt",
            schwere=Schwere.fehler,
            bereich=Bereich.nachschub,
            werte={"anzahl": len(treffer), "tage": HAENGT_TAGE, "titel": treffer[0]},
            ziel="/admin/requests?filter=searching",
        )
    ]


def _nachschub_freigabe_wartet(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Freigaben, die zu lange liegen.

    Die blosse Anzahl wartender Anfragen ist **kein** Befund - sie steht als
    Handlungszahl auf dem Dashboard. Ein Befund wird daraus erst, wenn sie
    liegen bleiben: Dauerhaft ein "Problem" anzuzeigen, das keins ist, stumpft
    gegen die ab, die zaehlen.
    """
    grenze = jetzt - timedelta(days=FREIGABE_TAGE)
    anzahl = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.status == RequestStatus.pending_approval,
                MediaRequest.requested_at < grenze,
            )
        )
        or 0
    )
    if not anzahl:
        return []
    return [
        Befund(
            kennung="nachschub.freigabe_wartet",
            schwere=Schwere.warnung,
            bereich=Bereich.nachschub,
            werte={"anzahl": anzahl, "tage": FREIGABE_TAGE},
            ziel="/admin/requests?filter=pending_approval",
        )
    ]


def _nachschub_fehlgeschlagen(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Haeufen sich die Fehlschlaege?"""
    grenze = jetzt - timedelta(days=FEHLGESCHLAGEN_TAGE)
    anzahl = (
        db.scalar(
            select(func.count(MediaRequest.id)).where(
                MediaRequest.status == RequestStatus.failed,
                MediaRequest.requested_at >= grenze,
            )
        )
        or 0
    )
    if anzahl < FEHLGESCHLAGEN_AB:
        return []
    return [
        Befund(
            kennung="nachschub.fehlgeschlagen",
            schwere=Schwere.warnung,
            bereich=Bereich.nachschub,
            werte={"anzahl": anzahl, "tage": FEHLGESCHLAGEN_TAGE},
            ziel="/admin/requests?filter=failed",
        )
    ]


def _nachschub_eingriff_noetig(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Downloads, die ohne Handarbeit nicht weitergehen.

    Der Import haengt, die Datei liegt fertig da und niemand merkt es - fuer
    den Besteller sieht es aus wie "laedt noch". Gezaehlt wird je Instanz und
    hier zusammengefasst: Wer drei Instanzen betreibt, will eine Zahl sehen
    und nicht drei Zeilen.
    """
    anzahl = 0
    for zeile in instanz_stand.alle(db).values():
        warteschlange = (zeile.messwerte or {}).get("warteschlange")
        if isinstance(warteschlange, dict):
            wert = warteschlange.get("eingriff")
            if isinstance(wert, int):
                anzahl += wert
    if not anzahl:
        return []
    return [
        Befund(
            kennung="nachschub.eingriff_noetig",
            schwere=Schwere.fehler,
            bereich=Bereich.nachschub,
            werte={"anzahl": anzahl},
            ziel="/admin/requests?filter=searching",
        )
    ]


# ---------------------------------------------------------------------------
# Bibliothek
# ---------------------------------------------------------------------------


def _bibliothek_geisterposten(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Belastet, aber von Radarr/Sonarr nicht mehr gefuehrt.

    Entsteht bei einem verbreiteten Ablauf: laden, bis die Qualitaet stimmt,
    dann den Eintrag aus Radarr werfen und die Datei behalten. Der Posten
    zaehlt weiter gegen das Kontingent seines Besitzers, ist aber nicht mehr
    loeschbar - Nexview loescht ausschliesslich ueber Radarr/Sonarr.

    WICHTIG: **Das ist das schleichende Leck.** Bisher erfaehrt davon nur der
    Admin, und nur wenn er den Loeschversuch macht. Wer Speichergrenzen als
    grosszuegiges Sicherheitsnetz gesetzt hat, merkt es erst, wenn jemand ohne
    eigenes Zutun gesperrt ist.
    """
    zeile = db.execute(
        select(func.count(StorageEntry.id), func.sum(StorageEntry.size_bytes)).where(
            StorageEntry.arr_managed.is_(False),
            StorageEntry.state == StorageState.owned,
        )
    ).one()
    anzahl = zeile[0] or 0
    if not anzahl:
        return []
    return [
        Befund(
            kennung="bibliothek.geisterposten",
            schwere=Schwere.warnung,
            bereich=Bereich.bibliothek,
            werte={"anzahl": anzahl, "bytes": int(zeile[1] or 0)},
            ziel="/admin/settings?reiter=kontingente",
        )
    ]


# ---------------------------------------------------------------------------
# Abgleich der Quellen
# ---------------------------------------------------------------------------
#
# ⚠️ **Alle lesen denselben, stuendlich gemessenen Stand.** Der Vergleich
# selbst laeuft ueber tausende Zeilen und braucht die Bibliothek aus dem Netz -
# er gehoert in den Rundgang, nicht in eine Pruefung.


def _abgleich_arr_ohne_server(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Datei liegt in Radarr/Sonarr, der Medienserver kennt sie nicht.

    Der haeufigste Fall dahinter: Der Server hat nicht eingelesen, oder die
    Pfadzuordnung stimmt nicht. Fuer jeden Benutzer sieht es aus, als gaebe es
    den Titel nicht - und er bestellt ihn ein zweites Mal.
    """
    stand = abgleich.lesen(db)
    if not stand.moeglich or stand.arr_ohne_server < ARR_OHNE_SERVER_AB:
        return []
    return [
        Befund(
            kennung="abgleich.arr_ohne_server",
            schwere=Schwere.warnung,
            bereich=Bereich.abgleich,
            werte={"anzahl": stand.arr_ohne_server},
            wortlaut=", ".join(stand.beispiele.get("arr_ohne_server", [])) or None,
            ziel="/admin/settings?reiter=dienste&unter=medienserver",
        )
    ]


def _abgleich_nicht_erkannt(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Der Medienserver konnte diesen Titeln nichts zuordnen.

    Ohne Kennung ist ein Titel fuer Nexview nicht vorhanden - er taucht in
    keiner Zustandsanzeige auf und wird munter noch einmal bestellt.
    """
    stand = abgleich.lesen(db)
    if not stand.moeglich or stand.nicht_erkannt < NICHT_ERKANNT_AB:
        return []
    return [
        Befund(
            kennung="abgleich.nicht_erkannt",
            schwere=Schwere.hinweis,
            bereich=Bereich.abgleich,
            werte={"anzahl": stand.nicht_erkannt},
            ziel="/admin/settings?reiter=dienste&unter=medienserver",
        )
    ]


def _abgleich_jahr_widerspruch(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Zwei Anbieter, dieselbe Nummer, verschiedene Jahre.

    ⚠️ **Ein Verdacht, keine Feststellung.** Entweder hat einer den falschen
    Titel erwischt - oder beide benutzen verschiedene Erscheinungsdaten
    (Festival gegen Kinostart). Der Text laesst deshalb beides offen; wer hier
    "falsch zugeordnet" behauptet, schickt jemanden auf die Suche nach einem
    Fehler, den es vielleicht nicht gibt.

    Warum es trotzdem zaehlt: ``mediaserver_library._jahre_passen`` verwirft
    einen Treffer, dessen Jahre nicht zusammenpassen. Steht im Medienserver
    ein falsches Jahr, meldet Nexview "nicht vorhanden" fuer einen Titel, der
    daliegt.

    Schon **einer** genuegt - das ist selten genug, um jedes Mal hinzusehen.
    """
    stand = abgleich.lesen(db)
    if not stand.moeglich or stand.jahr_widerspruch < 1:
        return []
    return [
        Befund(
            kennung="abgleich.jahr_widerspruch",
            schwere=Schwere.warnung,
            bereich=Bereich.abgleich,
            werte={"anzahl": stand.jahr_widerspruch},
            wortlaut=", ".join(stand.beispiele.get("jahr_widerspruch", [])) or None,
            ziel="/admin/settings?reiter=dienste&unter=medienserver",
        )
    ]


def _abgleich_anbieter_uneinig(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Mehrere Medienserver kennen unterschiedliche Bestaende.

    ⚠️ **Erscheint nur bei mehr als einem verbundenen Server.** Wer genau
    einen hat - und das sind die meisten - sieht diesen Befund nie. Eine
    Pruefung, die nicht zutrifft, schweigt; sie sagt nicht "du koenntest noch
    einen verbinden".
    """
    stand = abgleich.lesen(db)
    if not stand.moeglich or len(stand.je_anbieter) < 2:
        return []
    if stand.anbieter_luecke < ANBIETER_LUECKE_AB:
        return []
    return [
        Befund(
            kennung="abgleich.anbieter_uneinig",
            schwere=Schwere.hinweis,
            bereich=Bereich.abgleich,
            werte={
                "anzahl": stand.anbieter_luecke,
                "server": len(stand.je_anbieter),
            },
            ziel="/admin/settings?reiter=dienste&unter=medienserver",
        )
    ]


# ---------------------------------------------------------------------------
# Betrieb
# ---------------------------------------------------------------------------


def _betrieb_sicherung_alt(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Die automatische Sicherung ist ueberfaellig.

    WICHTIG: **"Faellig" allein ist kein Befund.** ``sicherung.faellig()`` wird
    im normalen Betrieb staendig kurz wahr und ist eine Stunde spaeter wieder
    falsch - das zu melden hiesse, jeden Tag einmal grundlos zu warnen.
    Gemeldet wird erst, was den Takt **plus** einen Tag Luft ueberschreitet.

    Der Zeitpunkt kommt aus den Dateien, nicht aus der Datenbank: Ein Wert in
    der Datenbank stammte nach einer Wiederherstellung aus dem Stand von
    damals und waere schlicht falsch.
    """
    takt = settings.backup_schedule
    if takt not in sicherung.TAKTE:
        # "off" ist eine bewusste Entscheidung des Betreibers, kein Versaeumnis.
        return []

    erlaubt = sicherung.TAKTE[takt] + SICHERUNG_TOLERANZ_TAGE
    try:
        automatisch = [e for e in sicherung.liste() if e.art == sicherung.AUTOMATISCH]
    except OSError:
        # Kein Zugriff auf den Ordner - daraus "keine Sicherung" zu folgern
        # waere geraten. Lieber nichts sagen als etwas Falsches.
        return []

    if not automatisch:
        # ⚠️ **"Noch keine" ist erst nach einer Weile ein Befund.** Der
        # Waechter legt binnen einer Stunde nach dem ersten Start eine an;
        # sofort zu warnen hiesse, jede frische Installation zu beschimpfen.
        #
        # Als Alter der Installation dient das aelteste Konto - der
        # Administrator entsteht bei der Einrichtung, also genau dann. Das
        # spart eine eigene Spalte, die dasselbe noch einmal sagen wuerde.
        seit = db.scalar(select(func.min(User.created_at)))
        if seit is None or (jetzt - seit).days < erlaubt:
            return []
        return [
            Befund(
                kennung="betrieb.sicherung_fehlt",
                schwere=Schwere.warnung,
                bereich=Bereich.betrieb,
                werte={"tage": (jetzt - seit).days},
                ziel="/admin/settings?reiter=sicherungen",
            )
        ]

    try:
        letzte = datetime.fromisoformat(automatisch[0].erstellt)
    except ValueError:
        return []
    if letzte.tzinfo is not None:
        letzte = letzte.astimezone(timezone.utc).replace(tzinfo=None)

    tage = (jetzt - letzte).days
    if tage < erlaubt:
        return []
    return [
        Befund(
            kennung="betrieb.sicherung_alt",
            schwere=Schwere.warnung,
            bereich=Bereich.betrieb,
            werte={"tage": tage},
            ziel="/admin/settings?reiter=sicherungen",
        )
    ]


def _betrieb_mail_haengt(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Mails, die der Ausgang endgueltig aufgegeben hat.

    Aufgegeben heisst: nicht mehr in der Warteschlange (``mail_pending`` aus)
    und trotzdem nie versendet, nach ``MAX_ATTEMPTS`` Versuchen. Ohne diesen
    Befund faellt ein stiller SMTP-Server ueberhaupt nicht auf - die Glocke
    funktioniert ja weiter, nur die Post kommt nie an.
    """
    grenze = jetzt - timedelta(days=MAIL_TAGE)
    anzahl = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.mail_pending.is_(False),
                Notification.mail_sent_at.is_(None),
                Notification.mail_attempts >= mail_outbox.MAX_ATTEMPTS,
                Notification.created_at >= grenze,
            )
        )
        or 0
    )
    if not anzahl:
        return []
    return [
        Befund(
            kennung="betrieb.mail_haengt",
            schwere=Schwere.warnung,
            bereich=Bereich.betrieb,
            werte={"anzahl": anzahl, "tage": MAIL_TAGE},
            ziel="/admin/settings?reiter=mail",
        )
    ]


def _betrieb_protokoll_fehler(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Haeufen sich Fehlerzeilen im Protokoll?

    ⚠️ **Nur die letzten 24 Stunden.** Das Protokoll wird gedreht und
    aufbewahrt; ohne Zeitfenster stuende hier die Summe seit dem letzten
    Neustart, und die waechst nur - ein Zaehler, der nie kleiner wird, ist
    keine Auskunft, sondern eine Anzeigetafel.
    """
    grenze = (jetzt - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        zeilen = logs.read(limit=2000, level="ERROR")
    except OSError:
        return []
    anzahl = sum(1 for z in zeilen if z.time >= grenze)
    if anzahl < PROTOKOLL_FEHLER_AB:
        return []
    return [
        Befund(
            kennung="betrieb.protokoll_fehler",
            schwere=Schwere.hinweis,
            bereich=Bereich.betrieb,
            werte={"anzahl": anzahl},
            ziel="/admin/settings?reiter=protokoll",
        )
    ]


def _betrieb_diagnose_an(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Eine ausfuehrliche Protokollstufe laeuft noch.

    Sie schaltet sich selbst wieder ab - aber "bis zum Neustart" ist eine der
    erlaubten Dauern, und ein Container startet manchmal wochenlang nicht neu.
    Dann schreibt Nexview die ganze Zeit mit, und niemand weiss davon.
    """
    try:
        stand = logs.state()
    except OSError:
        return []
    if stand.mode not in logs.DEEP_MODES:
        return []
    return [
        Befund(
            kennung="betrieb.diagnose_an",
            schwere=Schwere.hinweis,
            bereich=Bereich.betrieb,
            werte={"stufe": stand.mode},
            ziel="/admin/settings?reiter=protokoll",
        )
    ]


def _betrieb_aktualisierung(
    db: Session, settings: AppSettings, jetzt: datetime
) -> list[Befund]:
    """Fuer Nexview selbst gibt es eine neuere Fassung.

    Gelesen wird der gemerkte Stand, nicht frisch nachgefragt - eine Pruefung
    greift nicht nach draussen. Der Rundgang haelt ihn warm; direkt nach dem
    Start ist er leer, und dann schweigt der Befund.
    """
    stand = updates.gemerkt()
    if stand is None or not stand.update_available or not stand.latest:
        return []
    return [
        Befund(
            kennung="betrieb.aktualisierung",
            schwere=Schwere.hinweis,
            bereich=Bereich.betrieb,
            werte={"jetzt": stand.current, "neu": stand.latest},
            ziel="/ueber",
        )
    ]


# ---------------------------------------------------------------------------
# Das Register
# ---------------------------------------------------------------------------

#: **Die Liste steht hier und nicht im Test** - so sieht man an einer Stelle,
#: was geprueft wird, und ein neuer Eintrag bleibt eine bewusste Handlung.
PRUEFUNGEN = (
    _dienst_nicht_erreichbar,
    _dienst_meldet_problem,
    _dienst_rueckkanal_gestoert,
    _dienst_version_alt,
    _platz_knapp,
    _platz_waechst_schnell,
    _nachschub_haengt,
    _nachschub_eingriff_noetig,
    _nachschub_freigabe_wartet,
    _nachschub_fehlgeschlagen,
    _bibliothek_geisterposten,
    _abgleich_arr_ohne_server,
    _abgleich_jahr_widerspruch,
    _abgleich_nicht_erkannt,
    _abgleich_anbieter_uneinig,
    _betrieb_sicherung_alt,
    _betrieb_mail_haengt,
    _betrieb_protokoll_fehler,
    _betrieb_diagnose_an,
    _betrieb_aktualisierung,
)


def sammeln(
    db: Session,
    settings: AppSettings,
    *,
    bereich: Bereich | None = None,
    jetzt: datetime | None = None,
) -> list[Befund]:
    """Alle Befunde, die dringendsten zuerst.

    WICHTIG: **Eine kaputte Pruefung darf das Dashboard nicht mitnehmen.**
    Sonst kostet ein Fehler in der unwichtigsten Pruefung genau die Seite, auf
    der man nachsehen wollte, was kaputt ist. Sie faellt aus, wird
    protokolliert, und die uebrigen stehen weiter da.
    """
    jetzt = jetzt or _jetzt()
    gefunden: list[Befund] = []
    for pruefung in PRUEFUNGEN:
        try:
            gefunden.extend(pruefung(db, settings, jetzt))
        except Exception:  # noqa: BLE001 - siehe Docstring
            logger.exception("Check %s failed", pruefung.__name__)

    if bereich is not None:
        gefunden = [b for b in gefunden if b.bereich == bereich]

    # Zweitschluessel ist die Kennung, damit die Reihenfolge bei gleicher
    # Schwere nicht zwischen zwei Aufrufen springt.
    gefunden.sort(key=lambda b: (RANG[b.schwere], b.kennung, b.zusatz))
    return gefunden


def zaehlen(befunde: list[Befund]) -> dict[str, int]:
    """Wie viele je Schwere - die Zahl, die Kachel und Menue-Abzeichen zeigen."""
    return {
        schwere.value: sum(1 for b in befunde if b.schwere is schwere)
        for schwere in Schwere
    }


# --- Ungesehenes ------------------------------------------------------------
#
# ⚠️ **Das Abzeichen am Menue zaehlt Ungelesenes, nicht Probleme.** Befunde sind
# Zustaende: "sucht seit ueber 14 Tagen" ist morgen genauso wahr. Der Zaehler
# stand deshalb dauerhaft auf derselben Zahl, auch nachdem jemand nachgesehen
# hatte - und ein Abzeichen, das immer leuchtet, sieht bald niemand mehr an.
#
# Die Befunde selbst bleiben stehen, solange sie zutreffen. Nur das Abzeichen
# geht auf null, sobald das Dashboard geoeffnet war.


def _anzahl_von(befund: Befund) -> int | None:
    """Wie viele Faelle dieser Befund umfasst - falls er es sagt.

    ⚠️ **Nur ``anzahl``, nicht die uebrigen Werte.** Manche Befunde tragen
    Zahlen, die von selbst wachsen ("seit X Tagen", "X Minuten", "X Bytes").
    Wer daran haengt, baut ein Abzeichen, das sich jeden Tag selbst wieder
    einschaltet.
    """
    wert = befund.werte.get("anzahl")
    return wert if isinstance(wert, int) else None


def ungesehen(db: Session, user_id: int, befunde: list[Befund]) -> list[Befund]:
    """Welche dieser Befunde der Betreiber noch nicht gesehen hat.

    Ungesehen ist ein Befund, wenn sein Schluessel gar nicht vermerkt ist -
    oder wenn er **groesser** geworden ist, seit jemand hinsah. Haengt ein
    zweiter Titel, ist das eine neue Nachricht, auch wenn der Befund derselbe
    bleibt; ohne diese Zeile versteckte ein einziges Hinsehen jede kuenftige
    Verschlechterung mit.
    """
    if not befunde:
        return []
    gesehen = {
        zeile.schluessel: zeile.anzahl
        for zeile in db.scalars(
            select(BefundGesehen).where(BefundGesehen.user_id == user_id)
        )
    }
    offen = []
    for befund in befunde:
        if befund.schluessel not in gesehen:
            offen.append(befund)
            continue
        vorher = gesehen[befund.schluessel]
        jetzt = _anzahl_von(befund)
        if vorher is not None and jetzt is not None and jetzt > vorher:
            offen.append(befund)
    return offen


def als_gesehen(db: Session, user_id: int, befunde: list[Befund]) -> int:
    """Alles, was gerade zutrifft, als gesehen vermerken.

    Gibt zurueck, wie viele Eintraege danach stehen. Aufgerufen, wenn jemand
    das Dashboard **oeffnet** - nicht, wenn das Menue seinen Zaehler abfragt:
    Das tut es alle 60 Sekunden, und das Abzeichen waere nie zu sehen.

    ⚠️ **Was nicht mehr zutrifft, wird vergessen.** Sonst waechst die Tabelle
    mit jedem Befund, den es je gab, und ein Problem, das nach Monaten
    wiederkehrt, kaeme stumm zurueck - vermerkt ist es ja noch.
    """
    aktuell = {b.schluessel: _anzahl_von(b) for b in befunde}
    vorhanden = {
        zeile.schluessel: zeile
        for zeile in db.scalars(
            select(BefundGesehen).where(BefundGesehen.user_id == user_id)
        )
    }

    for schluessel, zeile in vorhanden.items():
        if schluessel not in aktuell:
            db.delete(zeile)

    for schluessel, anzahl in aktuell.items():
        zeile = vorhanden.get(schluessel)
        if zeile is None:
            db.add(
                BefundGesehen(user_id=user_id, schluessel=schluessel, anzahl=anzahl)
            )
        else:
            zeile.anzahl = anzahl
            zeile.gesehen_am = utcnow()

    db.commit()
    return len(aktuell)
