"""Aus Seerrs Daten eine Entscheidungsvorlage machen. Geschrieben wird nichts.

⚠️ **Die drei Umrechnungen, an denen ein naiver Umzug scheitert.**

1. **Die Kontingent-Null ist umgedreht.** In Seerr heisst ``movieQuotaLimit = 0``
   "nicht zaehlen", also *unbegrenzt* (``User.getQuota`` prueft
   ``movieQuotaLimit ? count : 0``). In Nexview heisst die 0 "darf nichts";
   unbegrenzt ist ``quota.UNBEGRENZT``. Wer die Zahl uebernimmt, sperrt genau
   die Konten, die drueben keine Grenze hatten. Nexview hatte dieselbe Null bis
   0.19 andersherum und musste sie einmalig umziehen - der Fehler ist hier also
   schon einmal passiert.
2. **Serien werden anders gezaehlt.** Seerr summiert **Staffeln**
   (``COUNT(season.id)`` ueber alle Anfragen), Nexview zaehlt **Anfragen**
   (``func.count(MediaRequest.id)``). Dieselbe Zahl ist hier eine deutlich
   lockerere Grenze. Uebernommen wird sie trotzdem, aber die Vorschau sagt es
   je Konto - eine Fussnote liest niemand.
3. **Der Zustand steht an zwei Stellen.** Seerrs Anfrage kennt kein
   "heruntergeladen"; ob eine Datei wirklich liegt, steht am Werk
   (``media.status`` beziehungsweise ``media.status4k``) und bei Serien je
   Staffel. Wer nur den Anfrage-Zustand liest, behauptet fuer alles
   Freigegebene, es liege vor. An einer echten Installation gemessen war das
   jede fuenfte Anfrage.

⚠️ **Alles Vorhandene bleibt Hausbestand, und das ist eine Entscheidung.**
Der Umzug rechnet keinem Konto rueckwirkend Gigabyte zu. Der Grund ist nicht
Bequemlichkeit: Eine Zurechnung waere eine *Behauptung*, die niemand pruefen
kann - aus einer Anfrage von 2022 zu schliessen, wem eine Datei heute gehoert.
Und der Seerr-Betreiber hatte nie ein Speicherkontingent, es geht also nichts
verloren, das jemand hatte.

Damit die Entscheidung haelt, muessen uebernommene Anfragen mit dem **wahren**
Zustand hereinkommen. Der Grund ist ``status_poller``: Er beobachtet
``approved`` und ``searching``, und wenn eine solche Anfrage fertig wird, ruft
er ``storage.verbuchen`` - und das nimmt sich herrenlose Hausposten und
schreibt sie dem Besteller zu. Laengst erledigte Historie als ``approved``
einzuspielen wuerde die Hausbestands-Entscheidung also ueber Stunden hinweg
still rueckgaengig machen. Mit dem wahren Zustand passiert das nicht: Nur was
wirklich noch laeuft, geht durch den Poller, und dort ist die Zurechnung
richtig - das ist eine Anschaffung nach dem Umzug.

⚠️ **Was hier nicht ins Protokoll darf.** Seerrs Antworten tragen mehr mit sich,
als man erwartet: ``settings/main`` liefert Seerrs eigenen API-Schluessel im
Klartext, ``settings/radarr`` und ``settings/sonarr`` liefern die Schluessel
dieser Dienste, und an jeder Anfrage haengen die internen Adressen des fremden
Netzes. Protokolliert werden deshalb **Zaehlungen und Kennungen, niemals
Antwortkoerper**. Nexviews Protokoll liegt als Datei, geht zusaetzlich auf
stderr, wird ausdruecklich nicht mitgesichert und landet erfahrungsgemaess im
Fehlerbericht - also bei Fremden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...models import RequestStatus, Role, User
from ..quota import UNBEGRENZT

logger = logging.getLogger("nexview.seerr")

# --------------------------------------------------------------------------
# Seerrs Zahlenwerte, ausgeschrieben
# --------------------------------------------------------------------------

#: ``server/constants/media.ts`` in Seerr 3.4.1.
ANFRAGE_OFFEN = 1
ANFRAGE_FREIGEGEBEN = 2
ANFRAGE_ABGELEHNT = 3
ANFRAGE_FEHLGESCHLAGEN = 4
ANFRAGE_ABGESCHLOSSEN = 5

#: Zustand des **Werks**, nicht der Anfrage.
WERK_TEILWEISE_DA = 4
WERK_VERFUEGBAR = 5

#: ``server/constants/user.ts``.
KONTO_PLEX = 1
KONTO_LOKAL = 2
KONTO_JELLYFIN = 3
KONTO_EMBY = 4

#: ``server/lib/permissions.ts``. Nur die drei, aus denen in Nexview eine
#: Rolle wird; die uebrigen 28 Bits haben hier kein Gegenstueck.
RECHT_ADMIN = 2
RECHT_EINSTELLUNGEN = 4
RECHT_KONTEN = 8
RECHT_ANFRAGEN_VERWALTEN = 16

#: Was ein neu angelegtes Konto im **laufenden Betrieb** bekommt.
#:
#: ⚠️ Dieselbe Festlegung wie in ``nutzer_import.Vorgaben``: "Ein Import legt
#: gewoehnliche Nutzer an, nie Entscheider und nie Administratoren." Wer
#: dreissig Konten auf einmal anlegt, soll dabei nicht dreissig Leuten Rechte
#: geben koennen, die er einzeln sorgfaeltig vergeben wuerde.
ROLLE_FUER_NEUE = Role.user

#: ⚠️ **Und warum die Regel bei einer frischen Installation nicht gilt.**
#:
#: Im laufenden Betrieb gibt es bereits Administratoren; weitere im Stapel
#: anzulegen ist der Fehler, den ``nutzer_import`` verhindert. Bei einer
#: **frischen** Installation ist die Lage umgekehrt: Der Betreiber richtet
#: gerade erst ein, und die Rollen, die er drueben ueber Jahre vergeben hat,
#: von Hand nachzubauen ist genau die Arbeit, die dieser Umzug abnehmen soll.
#: Wer zwei Administratoren hatte, will hier zwei haben.
#:
#: Zwei Grenzen bleiben trotzdem, und beide sind hart:
#:
#: 1. **Der Betreiber-Haken wird nie uebernommen.** Er sagt "dieser
#:    Installation gehoert diesem Menschen" und gehoert dem, der die
#:    Einrichtung durchlaeuft. ``setup.py`` setzt ihn beim allerersten Konto;
#:    ein Umzug, der ihn mitbraechte, gaebe die Installation an jemanden
#:    weiter, der gerade nicht davorsitzt. Der Haken ist ohnehin keine Rolle,
#:    sondern eine Aussage darueber, was **andere** mit diesem Konto nicht tun
#:    duerfen (siehe ``services/betreiber``).
#: 2. **Vorausgewaehlt ist nichts.** Eine Rolle ueber Nutzer hinaus muss ein
#:    Mensch anhaken, Zeile fuer Zeile. Der Unterschied zwischen "darf" und
#:    "wird automatisch" ist hier der ganze Punkt.
ROLLEN_BEI_FRISCHER_INSTALLATION = (Role.admin, Role.approver, Role.user)

#: Der aelteste Schema-Stand, gegen den dieser Umzug geprueft ist.
#:
#: ⚠️ **Der 14.02.2026 ist keine willkuerliche Grenze.** An diesem Tag hat
#: Seerr die Sperrlisten-Tabelle von ``blacklist`` auf ``blocklist``
#: umbenannt. Aeltere Installationen kennen den neuen Namen nicht, und ein
#: Umzug, der ihn sucht, meldete faelschlich "keine Sperrliste" statt "diesen
#: Stand kenne ich nicht". Ueber die Schnittstelle faellt das nicht auf, weil
#: sie den alten Pfad als Zweitnamen behalten hat - genau deshalb muss die
#: Grenze hier ausgeschrieben stehen und nicht dem Zufall ueberlassen werden.
MINDESTFASSUNG = (3, 0, 0)

#: Bis hierher geprueft. Alles Neuere fuehrt zur Weigerung, nicht zur Warnung.
GEPRUEFT_BIS = (3, 4, 1)


def _fassung(roh: str) -> tuple[int, ...]:
    """``"3.4.1"`` zu ``(3, 4, 1)``. Unbrauchbares wird zu ``()``."""
    teile: list[int] = []
    for stueck in (roh or "").split("-")[0].split("."):
        if not stueck.isdigit():
            break
        teile.append(int(stueck))
    return tuple(teile)


# --------------------------------------------------------------------------
# Was der Betreiber zu sehen bekommt
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Kontozeile:
    """Ein Seerr-Konto, so wie es zur Entscheidung vorgelegt wird."""

    seerr_id: int
    anzeigename: str
    email: str | None
    #: ``plex``, ``jellyfin``, ``emby`` oder ``lokal``.
    herkunft: str
    #: Die Kennung beim Medienserver, falls es eine gibt.
    anbieter_kennung: str | None
    #: Nur wenn Nexview sich **sicher** ist: dieselbe Kennung, dieselbe Quelle.
    #:
    #: ⚠️ Ein Vorschlag ueber aehnliche Namen steht hier bewusst nicht. Er
    #: taeuschte Sicherheit vor, und ein falsch zugeordnetes Konto laesst sich
    #: in Nexview nicht mehr trennen - es gibt kein Zusammenfuehren.
    treffer_user_id: int | None
    treffer_grund: str | None
    #: Was das Konto in Seerr war, ausgedrueckt in Nexviews Rollen.
    #:
    #: ⚠️ **Eine Auskunft ueber die Quelle, keine Ankuendigung.** Was der Umzug
    #: anlegt, steht in :attr:`rolle_neu`, und das ist etwas anderes.
    rolle_seerr: str
    #: Was ein **neu angelegtes** Konto bekaeme. Immer ``user``.
    #:
    #: ⚠️ **Die Rolle steht nicht zur Wahl, genau wie beim Medienserver-Import**
    #: (``nutzer_import.Vorgaben``): "Ein Import legt gewoehnliche Nutzer an,
    #: nie Entscheider und nie Administratoren." Wer dreissig Konten auf einmal
    #: anlegt, soll dabei nicht dreissig Leuten Rechte geben koennen, die er
    #: einzeln sorgfaeltig vergeben wuerde.
    #:
    #: Der erste Entwurf hat hier die Seerr-Rolle durchgereicht und damit fuer
    #: einen Seerr-Administrator ohne Medienserver-Konto ein frisches
    #: Administrator-Konto angekuendigt. Das ist genau der Fehler, den die
    #: Hausregel verhindert.
    rolle_neu: str
    rolle_verlust: str | None
    kontingent_filme: int | None
    kontingent_serien: int | None
    kontingent_hinweise: tuple[str, ...]
    anfragen: int
    #: Seerrs Profilbild - **immer eine Adresse**, nie eine Datei.
    #:
    #: ⚠️ **Anzeigen ist etwas anderes als uebernehmen.** Bei einem Plex-Konto
    #: zeigt sie auf plex.tv, bei einem lokalen auf Gravatar. Nexview kennt nur
    #: hochgeladene Dateien; "uebernehmen" hiesse also herunterladen, und beim
    #: Gravatar-Bild sagt schon der Abruf einem Dritten, dass jemand dieses
    #: Konto ansieht. Deshalb steht hier nur die Adresse: Die Oberflaeche zeigt
    #: das Bild, damit der Betreiber die Person wiedererkennt. Was beim
    #: Anlegen daraus wird, ist eine eigene Entscheidung.
    bild: str | None


@dataclass(frozen=True)
class Anfragezeile:
    """Eine Anfrage, abgebildet auf Nexviews Zustaende."""

    seerr_id: int
    titel_tmdb: int | None
    titel_tvdb: int | None
    art: str
    staffel: int | None
    ziel_status: str
    seerr_status: int
    werk_status: int | None
    besteller_seerr_id: int
    uhd: bool
    instanz: int | None
    #: Gesetzt heisst: kommt **nicht** mit, und hier steht warum.
    uebersprungen: str | None = None


@dataclass
class Vorschau:
    """Das Ergebnis eines Leselaufs. Nichts davon ist geschrieben."""

    fassung: str
    fassung_geprueft: bool
    fassung_hinweis: str | None
    medienserver: str | None
    konten: list[Kontozeile] = field(default_factory=list)
    anfragen: list[Anfragezeile] = field(default_factory=list)
    sperrliste: int = 0
    meldungen: int = 0
    #: Was Nexview nicht mitnehmen kann, mit Grund - je Sache ein Satz.
    kommt_nicht_mit: dict[str, str] = field(default_factory=dict)
    #: Radarr- und Sonarr-Instanzen, die Seerr kennt.
    instanzen: list[dict[str, Any]] = field(default_factory=list)
    #: Richtet gerade jemand ein, oder laeuft die Anlage schon?
    #:
    #: Entscheidet, ob eine Rolle ueber "Nutzer" hinaus ueberhaupt zur Wahl
    #: steht. Die Oberflaeche muss es anzeigen, sonst wundert sich jemand,
    #: warum dieselbe Liste zweimal etwas anderes vorschlaegt.
    frische_installation: bool = False

    @property
    def anfragen_uebernehmbar(self) -> int:
        return sum(1 for a in self.anfragen if a.uebersprungen is None)

    @property
    def konten_verknuepft(self) -> int:
        """Konten, die auf ein vorhandenes Nexview-Konto treffen."""
        return sum(1 for k in self.konten if k.treffer_user_id is not None)

    @property
    def konten_neu(self) -> int:
        """Konten, für die ein **neues** Nexview-Konto entstünde.

        ⚠️ **Das ist die Zahl, an der ein Duplikat auffällt, und sonst nichts.**
        Zwei Seerr-Konten koennen derselbe Mensch sein - ein Plex-Konto und
        daneben ein lokales, das der Betreiber irgendwann von Hand angelegt
        hat. Nexview kann das nicht wissen: Ueber Namen oder Adresse zu raten
        waere genau der Abgleich, den ``nutzer_import`` ausdruecklich ablehnt,
        weil er Sicherheit vortaeuscht.

        Was Nexview stattdessen tun kann, ist die Zahl hinstellen. Wer weiss,
        dass er sechs Leute im Haus hat, und hier "7 neue Konten" liest,
        stutzt. Wer nur eine Liste sieht, in der jede Zeile plausibel aussieht,
        stutzt nicht.
        """
        return sum(1 for k in self.konten if k.treffer_user_id is None)


# --------------------------------------------------------------------------
# Die Abbildungen
# --------------------------------------------------------------------------


def rolle_aus_rechten(rechte: int) -> tuple[Role, str | None]:
    """Was war dieses Konto in Seerr, in Nexviews Rollen ausgedrueckt?

    ⚠️ **Das Ergebnis ist eine Auskunft, keine Zuweisung.** Was der Umzug
    tatsaechlich anlegt, ist immer ``user`` (:data:`ROLLE_FUER_NEUE`). Diese
    Funktion beantwortet die andere Frage: Was durfte die Person drueben? Der
    Betreiber braucht das, um zu entscheiden, wem er hier von Hand mehr gibt.

    ⚠️ **Es wird nach unten gerundet, immer.** Die gefaehrliche Richtung ist
    die andere: Wer in Seerr ``MANAGE_SETTINGS`` hatte, aber nicht ``ADMIN``,
    duerfte dort Einstellungen aendern und sonst nichts. In Nexview gibt es
    dafuer keine Stufe. Ihn zum Administrator zu erklaeren gaebe ihm mehr, als
    er hatte, und das darf auch eine blosse Auskunft nicht.

    Deshalb wird nur das oberste Bit gelesen, und alles Feinere wird als
    Verlust gemeldet statt stillschweigend hochgestuft.
    """
    if rechte & RECHT_ADMIN:
        return Role.admin, None
    if rechte & RECHT_ANFRAGEN_VERWALTEN:
        verlust = None
        if rechte & (RECHT_EINSTELLUNGEN | RECHT_KONTEN):
            verlust = (
                "Durfte in Seerr auch Einstellungen oder Konten verwalten. "
                "Dafür gibt es in Nexview nur die Administrator-Rolle, und die "
                "vergibst du besser selbst."
            )
        return Role.approver, verlust
    verlust = None
    if rechte & (RECHT_EINSTELLUNGEN | RECHT_KONTEN):
        verlust = (
            "Durfte in Seerr Einstellungen oder Konten verwalten, aber keine "
            "Anfragen entscheiden. Diese Kombination kennt Nexview nicht."
        )
    return Role.user, verlust


def kontingent_aus_seerr(grenze: int | None, tage: int | None, art: str) -> tuple[int | None, list[str]]:
    """Seerrs Stueckzahl auf Nexviews Grenze - samt der Warnungen dazu.

    Rueckgabe ist der **Datenbankwert**: ``None`` heisst Hausvorgabe,
    ``UNBEGRENZT`` heisst ausdruecklich ohne Grenze, eine Zahl genau diese.
    """
    hinweise: list[str] = []
    if grenze is None:
        return None, hinweise
    if grenze == 0:
        hinweise.append(
            "In Seerr stand 0, und das heißt dort „nicht zählen“, also ohne "
            "Grenze. Hier hieße die 0 „darf nichts“, deshalb wird daraus "
            "ausdrücklich „ohne Grenze“."
        )
        return UNBEGRENZT, hinweise
    if art == "serien":
        hinweise.append(
            f"Seerr zählte {grenze} Staffeln, Nexview zählt {grenze} Anfragen. "
            "Eine Anfrage kann mehrere Staffeln umfassen, die Grenze ist hier "
            "also lockerer als drüben."
        )
    if tage:
        hinweise.append(
            f"Seerr rechnete {tage} Tage rückwärts ab jetzt, Nexview rechnet am "
            "Kalender. Der Zeitraum ist nicht derselbe."
        )
    return int(grenze), hinweise


def zustand_aus_seerr(
    anfrage_status: int, werk_status: int | None
) -> tuple[RequestStatus | None, str | None]:
    """Beide Seerr-Quellen zusammen auf einen Nexview-Zustand.

    ``werk_status`` ist ``media.status4k`` bei einer 4K-Anfrage und sonst
    ``media.status`` - bei Staffelanfragen der Zustand der Staffel. Wer hier
    die falsche Spalte nimmt, behauptet fuer 4K-Anfragen die Verfuegbarkeit
    der normalen Fassung.

    Gibt ``(None, Grund)`` zurueck, wenn es kein ehrliches Gegenstueck gibt.
    """
    if anfrage_status == ANFRAGE_OFFEN:
        return RequestStatus.pending_approval, None
    if anfrage_status == ANFRAGE_ABGELEHNT:
        return RequestStatus.rejected, None
    if anfrage_status == ANFRAGE_FEHLGESCHLAGEN:
        # ⚠️ Nexview kennt ``failed``, aber es bedeutet etwas anderes: Dort ist
        # es ein Fehler beim Uebergeben an Radarr oder Sonarr, mit einem Text
        # daran. Seerrs "fehlgeschlagen" traegt keinen. Das als ``failed``
        # einzuspielen erzeugte eine Anfrage, die einen Grund verspricht und
        # keinen hat.
        return None, (
            "In Seerr fehlgeschlagen. Nexview kennt dafür keinen Zustand, der "
            "dasselbe bedeutet."
        )
    if anfrage_status in (ANFRAGE_FREIGEGEBEN, ANFRAGE_ABGESCHLOSSEN):
        if werk_status in (WERK_VERFUEGBAR, WERK_TEILWEISE_DA):
            return RequestStatus.downloaded, None
        # Laeuft wirklich noch. Der Poller nimmt sie auf, und wenn sie fertig
        # wird, ist die Zurechnung des Speichers richtig - das ist dann eine
        # Anschaffung nach dem Umzug.
        return RequestStatus.approved, None
    return None, f"Unbekannter Zustand in Seerr: {anfrage_status}"


def _herkunft(konto: dict[str, Any]) -> tuple[str, str | None]:
    """Woran haengt dieses Seerr-Konto, und unter welcher Kennung?"""
    art = konto.get("userType")
    if art == KONTO_PLEX and konto.get("plexId") is not None:
        return "plex", str(konto["plexId"])
    if art in (KONTO_JELLYFIN, KONTO_EMBY) and konto.get("jellyfinUserId"):
        return ("jellyfin" if art == KONTO_JELLYFIN else "emby"), str(konto["jellyfinUserId"])
    return "lokal", None


def _treffer(
    herkunft: str, kennung: str | None, vorhanden: dict[tuple[str, str], User]
) -> tuple[int | None, str | None]:
    """Gibt es dieses Konto in Nexview schon - sicher, nicht vermutet?

    ⚠️ **Nur ueber die Medienserver-Kennung, und nur bei Plex ohne Vorbehalt.**
    Seerrs ``plexId`` stammt aus ``plex.tv/users/account.json``, und Nexviews
    Verknuepfung stammt aus derselben Quelle (``mediaserver/plextv.kontenliste``).
    Derselbe Wert bedeutet denselben Menschen.

    Bei Jellyfin und Emby ist die Kennung serverbezogen, und Seerr notiert am
    Konto **nicht**, welcher Server gemeint war. Ein Treffer heisst dort also
    nur "gleiche Zeichenkette" - deshalb der ausgeschriebene Vorbehalt, den die
    Oberflaeche anzeigen muss.

    Ueber die Adresse wird **nicht** zugeordnet. Beide Seiten koennen sie
    unabhaengig aendern, und ein Treffer gaebe demjenigen, der beim Anbieter
    eine fremde Adresse eintraegt, den Weg in ein fremdes Konto.
    """
    if kennung is None:
        return None, None
    konto = vorhanden.get((herkunft, kennung))
    if konto is None:
        return None, None
    if herkunft == "plex":
        return konto.id, "Dieselbe Plex-Kennung, aus derselben Quelle."
    return konto.id, (
        "Gleiche Kennung. Ob beide Installationen denselben Server meinen, "
        "kann Nexview nicht feststellen - Seerr schreibt es nicht auf."
    )


def _werk_status(anfrage: dict[str, Any], staffel: int | None) -> int | None:
    """Der Verfuegbarkeitszustand, der zu **dieser** Anfrage gehoert."""
    werk = anfrage.get("media") or {}
    uhd = bool(anfrage.get("is4k"))
    if staffel is not None:
        for zeile in anfrage.get("seasons") or []:
            if zeile.get("seasonNumber") == staffel:
                return zeile.get("status4k") if uhd else zeile.get("status")
    return werk.get("status4k") if uhd else werk.get("status")


# --------------------------------------------------------------------------
# Der Zusammenbau
# --------------------------------------------------------------------------

#: Was Nexview nicht aufnehmen kann, und warum. Steht hier und nicht in der
#: Oberflaeche, damit die Liste an einer Stelle vollstaendig ist.
KOMMT_NICHT_MIT = {
    "watchlist": (
        "Merklisten. Nexview führt keine eigene, es zeigt die deines "
        "Medienservers live an. Es gibt hier keinen Ort dafür."
    ),
    "notification_targets": (
        "Persönliche Meldeadressen (Discord, Telegram, Pushover). In Seerr "
        "hängen sie am Konto, in Nexview gehören die Kanäle dem Haus."
    ),
    "override_rules": (
        "Seerrs Zielregeln. Sie steuern Server, Profil und Ordner. Nexviews "
        "Regeln entscheiden über freigeben oder ablehnen - gleicher Name, "
        "anderer Zweck."
    ),
    "discover_sliders": (
        "Die Reihen auf Seerrs Startseite. Nexviews Regale stehen fest."
    ),
    "passwords": (
        "Passwörter. Über die Schnittstelle gibt Seerr sie nicht heraus - im "
        "Konto-Datensatz gibt es kein Passwortfeld."
    ),
}


def vorschau_bauen(
    *,
    status: dict[str, Any],
    einstellungen: dict[str, Any],
    konten: list[dict[str, Any]],
    anfragen: list[dict[str, Any]],
    sperrliste: list[dict[str, Any]],
    meldungen: list[dict[str, Any]],
    radarr: list[dict[str, Any]],
    sonarr: list[dict[str, Any]],
    nexview_konten: list[User],
    frische_installation: bool = False,
) -> Vorschau:
    """Alles Gelesene zu einer Entscheidungsvorlage verrechnen.

    Reine Rechnung: keine Datenbank, kein Netz, keine Nebenwirkung. Genau
    deshalb laesst sie sich gegen erfundene Antworten pruefen, und genau das
    ist noetig - an einer echten kleinen Installation kommen die
    interessanten Faelle (mehrere Konten, gesetzte Kontingente, 4K, alte
    Zeitstempel) gar nicht vor.

    ``frische_installation`` entscheidet ueber die Rolle neuer Konten, und die
    Frage stellt nicht der Betreiber, sondern der Zustand der Anlage: Nexview
    weiss selbst, ob es schon Konten hat (``deps.has_any_user``, dieselbe
    Pruefung wie in ``routers/setup``). Wer daraus einen Schalter macht, den
    ein Mensch umlegen kann, baut die Moeglichkeit ein, ihn falsch zu stellen.
    Siehe :data:`ROLLEN_BEI_FRISCHER_INSTALLATION`.
    """
    roh = str(status.get("version") or "")
    fassung = _fassung(roh)
    geprueft = bool(fassung) and MINDESTFASSUNG <= fassung <= GEPRUEFT_BIS
    hinweis: str | None = None
    if not fassung:
        hinweis = "Diese Installation nennt keine brauchbare Fassung."
    elif fassung < MINDESTFASSUNG:
        hinweis = (
            f"Seerr {roh} ist älter als die älteste geprüfte Fassung "
            f"{'.'.join(map(str, MINDESTFASSUNG))}."
        )
    elif fassung > GEPRUEFT_BIS:
        hinweis = (
            f"Seerr {roh} ist neuer als alles, wogegen dieser Umzug geprüft "
            f"wurde ({'.'.join(map(str, GEPRUEFT_BIS))})."
        )

    # Was Nexview schon kennt, einmal nachschlagbar gemacht.
    vorhanden: dict[tuple[str, str], User] = {}
    for konto in nexview_konten:
        for zeile in konto.mediaserver_accounts:
            vorhanden[(zeile.provider, zeile.account_id)] = konto

    bekannte_konten = {int(k["id"]) for k in konten}
    anfragezeilen: list[Anfragezeile] = []
    for eintrag in anfragen:
        werk = eintrag.get("media") or {}
        besteller = (eintrag.get("requestedBy") or {}).get("id")
        staffeln = [
            zeile.get("seasonNumber")
            for zeile in (eintrag.get("seasons") or [])
            if zeile.get("seasonNumber") is not None
        ]
        art = str(eintrag.get("type") or "")
        # Eine Seerr-Anfrage ueber mehrere Staffeln wird zu mehreren Zeilen -
        # Nexview fuehrt Staffeln einzeln.
        for staffel in staffeln or [None]:
            werkstand = _werk_status(eintrag, staffel)
            ziel, warum = zustand_aus_seerr(int(eintrag.get("status") or 0), werkstand)
            uebersprungen = warum
            if uebersprungen is None and besteller not in bekannte_konten:
                uebersprungen = "Das Konto dazu gibt es in Seerr nicht mehr."
            if uebersprungen is None and art == "tv" and not werk.get("tvdbId"):
                uebersprungen = (
                    "Seerr kennt zu dieser Serie keine TVDB-Nummer. Ohne sie "
                    "findet Nexview sie in Sonarr nicht wieder."
                )
            if uebersprungen is None and not werk.get("tmdbId"):
                uebersprungen = "Seerr kennt zu diesem Titel keine TMDB-Nummer."
            anfragezeilen.append(
                Anfragezeile(
                    seerr_id=int(eintrag["id"]),
                    titel_tmdb=werk.get("tmdbId"),
                    titel_tvdb=werk.get("tvdbId"),
                    art=art,
                    staffel=staffel,
                    ziel_status=(ziel or RequestStatus.pending_approval).value,
                    seerr_status=int(eintrag.get("status") or 0),
                    werk_status=werkstand,
                    besteller_seerr_id=int(besteller or 0),
                    uhd=bool(eintrag.get("is4k")),
                    instanz=eintrag.get("serverId"),
                    uebersprungen=uebersprungen,
                )
            )

    # ⚠️ **Je Konto wird in Nexview-Zeilen gezaehlt, nicht in Seerr-Anfragen.**
    # Eine Serienanfrage ueber drei Staffeln ist drueben *eine* Anfrage und hier
    # *drei*, weil Nexview Staffeln einzeln fuehrt. Wer beides nebeneinander
    # stellt, ohne es zu sagen, zeigt zwei richtige Zahlen, die sich zu
    # widersprechen scheinen - an einer echten Installation gemessen 81 gegen 97.
    #
    # Gezaehlt wird nur, was auch mitkaeme: Die Summe ueber alle Konten ist
    # damit genau ``anfragen_uebernehmbar``.
    anfragen_je_konto: dict[int, int] = {}
    for zeile in anfragezeilen:
        if zeile.uebersprungen is not None:
            continue
        anfragen_je_konto[zeile.besteller_seerr_id] = (
            anfragen_je_konto.get(zeile.besteller_seerr_id, 0) + 1
        )

    zeilen: list[Kontozeile] = []
    for konto in konten:
        herkunft, kennung = _herkunft(konto)
        treffer_id, grund = _treffer(herkunft, kennung, vorhanden)
        rolle_seerr, verlust = rolle_aus_rechten(int(konto.get("permissions") or 0))
        filme, h1 = kontingent_aus_seerr(
            konto.get("movieQuotaLimit"), konto.get("movieQuotaDays"), "filme"
        )
        serien, h2 = kontingent_aus_seerr(
            konto.get("tvQuotaLimit"), konto.get("tvQuotaDays"), "serien"
        )
        zeilen.append(
            Kontozeile(
                seerr_id=int(konto["id"]),
                anzeigename=str(konto.get("displayName") or konto.get("username") or ""),
                email=konto.get("email"),
                herkunft=herkunft,
                anbieter_kennung=kennung,
                treffer_user_id=treffer_id,
                treffer_grund=grund,
                rolle_seerr=rolle_seerr.value,
                rolle_neu=(
                    rolle_seerr.value
                    if frische_installation
                    else ROLLE_FUER_NEUE.value
                ),
                rolle_verlust=verlust,
                kontingent_filme=filme,
                kontingent_serien=serien,
                kontingent_hinweise=tuple(h1 + h2),
                anfragen=anfragen_je_konto.get(int(konto["id"]), 0),
                bild=str(konto.get("avatar") or "") or None,
            )
        )

    medienserver = {1: "plex", 2: "jellyfin", 3: "emby"}.get(
        einstellungen.get("mediaServerType")
    )

    instanzen = [
        {
            "art": art,
            "seerr_id": eintrag.get("id"),
            "name": eintrag.get("name"),
            "uhd": bool(eintrag.get("is4k")),
            "ordner": eintrag.get("activeDirectory"),
            "profil": eintrag.get("activeProfileName"),
        }
        for art, liste in (("radarr", radarr), ("sonarr", sonarr))
        for eintrag in liste
    ]

    # ⚠️ Zaehlungen, keine Inhalte. Siehe Kopf der Datei.
    logger.info(
        "Seerr preview built: version=%s vetted=%s accounts=%d requests=%d skipped=%d",
        roh or "?",
        geprueft,
        len(zeilen),
        len(anfragezeilen),
        sum(1 for a in anfragezeilen if a.uebersprungen),
    )

    return Vorschau(
        frische_installation=frische_installation,
        fassung=roh,
        fassung_geprueft=geprueft,
        fassung_hinweis=hinweis,
        medienserver=medienserver,
        konten=zeilen,
        anfragen=anfragezeilen,
        sperrliste=len(sperrliste),
        meldungen=len(meldungen),
        kommt_nicht_mit=dict(KOMMT_NICHT_MIT),
        instanzen=instanzen,
    )


__all__ = [
    "ROLLEN_BEI_FRISCHER_INSTALLATION",
    "ROLLE_FUER_NEUE",
    "Anfragezeile",
    "Kontozeile",
    "Vorschau",
    "kontingent_aus_seerr",
    "rolle_aus_rechten",
    "vorschau_bauen",
    "zustand_aus_seerr",
]
