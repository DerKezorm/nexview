"""SQLite-Verbindung und Session-Verwaltung."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from enum import Enum
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import __version__
from .config import get_settings
from .models import Base, NotificationType, utcnow

logger = logging.getLogger("nexview.db")

# Wie viele Sicherungen aufgehoben werden. Sie entstehen nur bei einer
# tatsaechlichen Aenderung an der Datenbank, also praktisch nur nach Updates.
BACKUPS_TO_KEEP = 5

_settings = get_settings()

engine = create_engine(
    f"sqlite:///{_settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """WAL fuer parallele Lese-/Schreibzugriffe, Fremdschluessel aktivieren."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@event.listens_for(engine, "checkout")
def _fremdschluessel_sicherstellen(dbapi_connection, _connection_record, _proxy) -> None:
    """Bei **jeder** Entnahme aus dem Vorrat: Fremdschluessel wieder an.

    ⚠️ **Das Ereignis ``connect`` darueber genuegt nicht.** Es feuert nur,
    wenn SQLite wirklich eine neue Verbindung aufmacht. Danach reicht der
    Verbindungsvorrat (QueuePool, fuenf Plaetze) dieselbe Verbindung beliebig
    oft weiter, und ``PRAGMA foreign_keys`` gilt **je Verbindung**, nicht je
    Sitzung. Wer es einmal abschaltet und die Verbindung zurueckgibt, gibt sie
    abgeschaltet an den naechsten Aufrufer weiter, und nichts protokolliert das.

    Gemessen: frisch aus dem Vorrat ``foreign_keys=1``, nach einem einzigen
    ``PRAGMA foreign_keys=OFF`` samt Rueckgabe ``foreign_keys=0``. Betroffen
    sind dann 37 der 40 Fremdschluessel in ``models.py``, naemlich alle mit
    ``ON DELETE CASCADE`` (23) oder ``ON DELETE SET NULL`` (14): Das Loeschen
    eines Kontos liesse seine Tickets, Anfragen und Wuensche stehen.

    Das ist keine reine Testsorge. Auch im Betrieb legt vielleicht einmal
    jemand ein PRAGMA um, und dann traegt es der Vorrat weiter, bis der
    Prozess neu startet. Aufgefallen ist es in der Testreihe, weil dort
    ``tests/test_child_wishes.py`` genau das tat und eine Datei spaeter das
    Loeschen eines Kontos seine Tickets nicht mehr mitnahm.

    Kosten, gemessen, damit es niemand aus Sorge wieder herausnimmt. Eine
    Entnahme kostet mit diesem Horcher 14,8 statt 7,96 Mikrosekunden, also
    6,8 mehr (je 100.000 Entnahmen, bester von drei Durchlaeufen). Das ist
    fast das Doppelte, aber vom Falschen: Eine Entnahme ist nichts gegen das,
    was danach auf ihr passiert. In Zahlen, die zaehlen: Ein Test nimmt
    gemessen 19 mal (Waechter-Ausschnitt, 933 Entnahmen auf 50 Tests) bis 46
    mal (datenbanklastiger Ausschnitt, 2.970 auf 64 Tests) aus dem Vorrat. Auf
    die rund 2.400 Tests der Reihe sind das 45.000 bis 110.000 Entnahmen und
    damit 0,3 bis 0,8 Sekunden auf einen Lauf von 28 Minuten.

    Erst nachsehen und nur bei Bedarf setzen waere uebrigens **teurer**, nicht
    billiger: Das Abholen der Antwort kostet mehr als das blosse Setzen (5,0
    gegen 4,3 Mikrosekunden im Cursor gemessen).

    ``journal_mode`` steht bewusst nicht hier: WAL haengt an der Datei, nicht
    an der Verbindung, und ueberlebt die Rueckgabe ohnehin.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


#: Schritte in ``init_db``, die **genau einmal** laufen duerfen.
#:
#: Sie deuten Bestandsdaten um. Ein zweiter Lauf verdoppelt Zeilen, scheitert
#: am Eindeutigkeitsschluessel oder - im schlimmsten Fall - deutet eine
#: Eingabe um, die inzwischen jemand bewusst gemacht hat. Genau das ist bei
#: ``_kontingente_dreiwertig_machen`` passiert, siehe dort.
#:
#: Wer hier etwas eintraegt, sagt damit: Der Schritt ist ab jetzt durch das
#: Wanderungsbuch gesperrt und laeuft in dieser Datenbank nie wieder.
EINMAL_SCHRITTE = (
    "_verbindung_in_die_tabelle",
    "_bewertungen_in_die_tabelle",
    "_verknuepfungen_in_die_tabelle",
    "_kontingente_dreiwertig_machen",
)

#: Schritte in ``init_db``, die **bei jedem Start** laufen - und laufen muessen.
#:
#: ⚠️ **Das ist die Voreinstellung, nicht der Rest.** Mehrere davon sehen wie
#: Wanderungen aus und sind keine: ``_altersgrenzen_aufraeumen`` faengt jedes
#: Kinderkonto ab, das spaeter zum vollwertigen Konto hochgestuft wird;
#: ``_gesehen_herkunft_nachtragen`` bekommt vom laufenden Abgleich immer neue
#: Marker ohne Herkunft; ``_verwaiste_meldungsarten_aufraeumen`` bekommt sie
#: bei jeder kuenftigen Umbenennung einer Meldungsart. Wer einen von ihnen ins
#: Buch traegt, schaltet eine Reparatur ab, die noch gebraucht wird - und
#: nichts protokolliert das.
PFLEGE_SCHRITTE = (
    "_ankunftsbefund",
    "_wanderungsbuch_nachtragen",
    "_pending_changes",
    "_leere_installation",
    "_backup_database",
    "create_all",
    "_add_missing_columns",
    "_add_missing_indexes",
    "_altersgrenzen_aufraeumen",
    "_verwaiste_meldungsarten_aufraeumen",
    "_verwaiste_kinderwuensche_aufraeumen",
    "_gesehen_herkunft_nachtragen",
    "_betreiber_bestimmen",
)

#: Die Tabelle, in der das Wanderungsbuch steht - siehe ``models.Wanderung``.
WANDERUNGSBUCH = "wanderungen"

#: Dieser Lauf hat den Schritt gemacht.
AUSGEFUEHRT = "ausgefuehrt"
#: Der Ankunftsbefund hat ihn in einer bestehenden Datenbank wiedererkannt.
VORGEFUNDEN = "vorgefunden"


def init_db() -> None:
    """Datenbank auf den Stand der laufenden Version bringen.

    Wird bei jedem Start aufgerufen. Nach einem Update fehlen der bestehenden
    Datenbank die Tabellen, Spalten und Indizes, die inzwischen dazugekommen
    sind - die werden hier ergaenzt. Vorher wird gesichert, damit ein
    fehlgeschlagener Schritt nie Daten kostet.

    ⚠️ **Der Ankunftsbefund muss vor jeder Schema-Aenderung stehen.** Er
    beantwortet fuer jeden Einmal-Schritt die Frage "in dieser Datenbank schon
    gelaufen?", und die laesst sich nur am Schema *vor* dem Update beantworten.
    ``create_all`` und ``_add_missing_columns`` weiter unten ergaenzen genau
    die Tabellen und Spalten, an denen man das Alter einer Datenbank noch
    erkennen konnte - danach sieht jede Installation aus wie die neueste, und
    das Buch bekaeme fuer alle dieselbe, falsche Antwort.

    Er steht **hinter** ``_pending_changes``, nicht davor: Der Befund oeffnet
    die Datenbank, und SQLite legt die Datei dabei an. Davor gestellt naehme er
    der Schema-Pruefung ihren Kurzweg "die Datei gibt es noch gar nicht".
    Lesen aendert am Schema nichts, die Reihenfolge der beiden ist also frei.
    """
    ausstehend = _pending_changes()
    befund = _ankunftsbefund()
    # ⚠️ **Bei einer brandneuen Installation wird nicht gesichert.**
    # Die Schema-Pruefung meldet dort zwangslaeufig "alles fehlt", und Nexview
    # legte gehorsam eine Kopie einer leeren Datenbank an: Sie schuetzt nichts,
    # verbraucht aber einen der fuenf Plaetze - und wer nach dem ersten Start
    # in die Liste sieht, fragt sich zu Recht, wovor die schuetzen soll.
    if ausstehend and not _leere_installation():
        logger.info("Database schema update: %s", ", ".join(ausstehend))
        _backup_database()

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _add_missing_indexes()
    # Erst jetzt steht die Buchtabelle sicher. Was der Befund oben als "lief
    # schon" erkannt hat, wird hier nachgetragen; zurueck kommt das Buch als
    # Ganzes, damit die Schritte darunter es nicht einzeln abfragen muessen.
    gelaufen = _wanderungsbuch_nachtragen(befund)
    _altersgrenzen_aufraeumen()
    _verwaiste_meldungsarten_aufraeumen()
    _verwaiste_kinderwuensche_aufraeumen()
    # Muss **vor** dem Nachtragen der Herkunft laufen - das liest den
    # Anbieternamen bevorzugt aus dieser Tabelle.
    _einmal(_verbindung_in_die_tabelle, gelaufen)
    _einmal(_bewertungen_in_die_tabelle, gelaufen)
    _gesehen_herkunft_nachtragen()
    _einmal(_verknuepfungen_in_die_tabelle, gelaufen)
    _einmal(_kontingente_dreiwertig_machen, gelaufen)
    _betreiber_bestimmen()


def _leere_installation(ziel: Engine | None = None) -> bool:
    """Gibt es ueberhaupt schon Tabellen?

    Nur beim allerersten Start ist die Antwort nein - danach steht das Schema,
    auch wenn noch niemand ein Konto angelegt hat.

    ``ziel`` ist fuer Tests: Ohne den Parameter muesste ein Test die
    Datenbank des ganzen Laufs austauschen, um den Erstfall zu pruefen - und
    haenge damit an der Reihenfolge der Tests.
    """
    with (ziel or engine).connect() as verbindung:
        vorhanden = verbindung.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).scalar()
    return not vorhanden


def _ankunftsbefund() -> dict[str, bool]:
    """Je Einmal-Schritt: Ist er in dieser Datenbank schon gelaufen?

    Gelesen wird die Datenbank, **wie sie ankommt** - vor ``create_all`` und
    vor ``_add_missing_columns``. Danach waere die Frage nicht mehr zu
    beantworten, siehe ``init_db``.

    ⚠️ **Nichts davon wird zwischengespeichert.** ``sicherung.wiederherstellen``
    tauscht die Datenbankdatei im laufenden Prozess aus und ruft danach
    ``init_db()`` erneut. Ein gemerkter Befund gehoerte dann noch zur alten
    Datei, und eine eingespielte Sicherung aus 0.18 wuerde als fertig gewandert
    eingetragen - ihre Nullen blieben stehen und hiessen ab sofort das
    Gegenteil.

    Fehlende Tabellen und fehlende Spalten sind kein Fehler, sondern die
    Antwort "nein, noch nicht gelaufen".

    ⚠️ **Eine Luecke bleibt, und sie wird hier benannt statt verschwiegen.**
    Eine Installation, deren Start seinerzeit *zwischen* ``_add_missing_columns``
    und den Wanderungen abgestuerzt ist und die danach direkt auf diese Fassung
    springt, traegt die neuen Spalten, ohne dass ihre Wanderungen je liefen.
    Sie gilt hier als erledigt. Der Fall ist schmal: So ein Behaelter kam gar
    nicht erst hoch, das faellt auf.
    """
    befund: dict[str, bool] = dict.fromkeys(EINMAL_SCHRITTE, False)

    with engine.connect() as verbindung:
        tabellen = {
            zeile[0]
            for zeile in verbindung.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not tabellen:
            # Frische Installation: Es gibt nichts zu wandern, also gilt alles
            # als erledigt. Ohne diese Regel bliebe das Buch bis zum ersten
            # Datensatz offen, und der erste angelegte Server oder die erste
            # Bewertung wuerde eine Wanderung ausloesen, die nie gemeint war.
            return dict.fromkeys(EINMAL_SCHRITTE, True)

        def zeilen(tabelle: str) -> bool:
            if tabelle not in tabellen:
                return False
            return bool(
                verbindung.exec_driver_sql(
                    f"SELECT EXISTS(SELECT 1 FROM {tabelle})"  # noqa: S608 - feste Namen
                ).scalar()
            )

        def einstellung_leer(*schluessel: str) -> bool:
            """Steht zu **keinem** dieser Schluessel noch ein Wert da?"""
            if "settings" not in tabellen:
                return True
            frage = ", ".join("?" for _ in schluessel)
            return not verbindung.exec_driver_sql(
                f"SELECT EXISTS(SELECT 1 FROM settings "  # noqa: S608 - feste Namen
                f"WHERE key IN ({frage}) AND value IS NOT NULL AND value != '')",
                schluessel,
            ).scalar()

        # Der Server aus den Einstellungen in seine Tabelle. Steht dort eine
        # Zeile, ist die Sache klar. Ist sie leer, entscheidet die zweite
        # Haelfte: Der Schritt setzt die flachen Werte am Ende auf '', ein
        # leeres Paar heisst also "schon gewandert oder nie verbunden" - und
        # beides fuehrt zu demselben Ergebnis, naemlich nichts zu tun.
        befund["_verbindung_in_die_tabelle"] = "media_server_connections" in tabellen and (
            zeilen("media_server_connections")
            or einstellung_leer("mediaserver_provider", "mediaserver_machine_id")
        )

        # Dieselbe Ueberlegung eine Ebene tiefer. Hier bleiben die Spalten am
        # Konto zwar stehen, aber ``mediaserver_accounts.link`` raeumt sie beim
        # Loesen der letzten Verknuepfung ab: "Zieltabelle leer und trotzdem
        # Spalten gefuellt" kann deshalb nur "nie gewandert" heissen.
        spalten_konto = _existing_columns(verbindung, "users") if "users" in tabellen else set()
        noch_am_konto = False
        if {"mediaserver_provider", "mediaserver_account_id"} <= spalten_konto:
            noch_am_konto = bool(
                verbindung.exec_driver_sql(
                    "SELECT EXISTS(SELECT 1 FROM users "
                    "WHERE mediaserver_provider IS NOT NULL AND mediaserver_provider != '' "
                    "AND mediaserver_account_id IS NOT NULL AND mediaserver_account_id != '')"
                ).scalar()
            )
        befund["_verknuepfungen_in_die_tabelle"] = (
            "user_media_server_accounts" in tabellen
            and (zeilen("user_media_server_accounts") or not noch_am_konto)
        )

        # ⚠️ Hier bleibt ein Rest unentscheidbar: Ist ``title_ratings`` leer
        # und stehen an den Anfragen noch Bewertungen, laesst sich "nie
        # gewandert" nicht von "gewandert und danach alles geloescht"
        # unterscheiden. Dann gilt "nicht gelaufen", der Schritt laeuft also -
        # genau wie heute schon, und ab dem ersten Start dieser Fassung
        # schliesst das Buch dahinter zu.
        spalten_anfrage = (
            _existing_columns(verbindung, "media_requests")
            if "media_requests" in tabellen
            else set()
        )
        noch_an_der_anfrage = False
        if "rating" in spalten_anfrage:
            noch_an_der_anfrage = bool(
                verbindung.exec_driver_sql(
                    "SELECT EXISTS(SELECT 1 FROM media_requests WHERE rating IS NOT NULL)"
                ).scalar()
            )
        befund["_bewertungen_in_die_tabelle"] = "title_ratings" in tabellen and (
            zeilen("title_ratings") or not noch_an_der_anfrage
        )

        # ⚠️ **Der Schritt ohne eigene Spur.** Er schreibt ``-1``, wo eine ``0``
        # steht, und eine frisch gesetzte ``0`` sieht aus wie eine alte. Die
        # Frage muss deshalb ueber Bande beantwortet werden:
        #
        # * ``storage_entries.arr_managed`` kam in **derselben** Fassung wie
        #   dieser Schritt (0.19.0, ein Commit). Wer die Spalte hat, hat
        #   mindestens einen 0.19er Start hinter sich - und auf dem lief er.
        # * Die Zeile ``storage_enabled`` loescht der Schritt am Ende. Sie
        #   allein waere aber **kein** Beweis: Sie entsteht nur, wenn ein
        #   Administrator die Speicherseite einmal ausdruecklich gespeichert
        #   hat. Eine 0.18er Installation, die den Schalter nie anfasste, sieht
        #   ohne diese Zeile genauso aus wie eine fertig gewanderte - und ihre
        #   bewusst gesetzten Nullen wuerden nie umziehen.
        #
        # Also beides zusammen: ``arr_managed`` entscheidet die Richtung,
        # ``storage_enabled`` faengt zusaetzlich einen halb gewanderten Stand ab.
        spalten_speicher = (
            _existing_columns(verbindung, "storage_entries")
            if "storage_entries" in tabellen
            else set()
        )
        befund["_kontingente_dreiwertig_machen"] = (
            "arr_managed" in spalten_speicher and einstellung_leer("storage_enabled")
        )

    return befund


def _wanderungsbuch_nachtragen(befund: dict[str, bool]) -> set[str]:
    """Was der Ankunftsbefund wiedererkannt hat, kommt als ``vorgefunden`` ins Buch.

    Zurueck kommt das Buch als Ganzes, also alle Namen darin - die Schritte
    darunter fragen es damit nicht einzeln ab. Das ist kein Geiz: ``init_db``
    laeuft in der Testreihe rund 2400 mal, und jede Abfrage wird dort genauso
    oft bezahlt.
    """
    with engine.connect() as verbindung:
        gelaufen = {
            zeile[0]
            for zeile in verbindung.exec_driver_sql(
                f"SELECT wanderung_name FROM {WANDERUNGSBUCH}"  # noqa: S608 - fester Name
            )
        }

    nachzutragen = [
        name for name in EINMAL_SCHRITTE if name not in gelaufen and befund.get(name)
    ]
    # Lesen genuegt fast immer: Ab dem zweiten Start steht alles schon im Buch.
    # Eine Schreib-Transaktion nur fuers Nachsehen kostet einen Commit, und den
    # bezahlt die Testreihe rund 2400 mal.
    if not nachzutragen:
        return gelaufen

    with engine.begin() as verbindung:
        for name in nachzutragen:
            _eintragen(verbindung, name, VORGEFUNDEN)
            gelaufen.add(name)
            logger.info(
                "Migration %r recorded as already done (found in an existing database)", name
            )
            if name == "_kontingente_dreiwertig_machen":
                _umgedeutete_nullen_melden(verbindung)
    return gelaufen


def _umgedeutete_nullen_melden(verbindung) -> None:
    """Einmalig sagen, dass frueher gesetzte Nullen umgedeutet worden sind.

    ⚠️ **Der Schaden ist schon eingetreten und nicht mehr rueckgaengig zu
    machen.** Auf jeder Installation ab 0.19 schlug
    ``_kontingente_dreiwertig_machen`` bei **jedem** Start wieder zu. Wer einem
    Konto ausdruecklich "darf nichts anfragen" gab, fand nach dem naechsten
    Neustart "unbegrenzt" vor. Nexview kann das nicht selbst reparieren: In der
    Datenbank steht danach dieselbe ``-1`` wie bei einem Konto, dem jemand
    absichtlich "unbegrenzt" gegeben hat, und der alte Schritt hat sich keine
    Kennungen gemerkt.

    Deshalb wird **nichts** geaendert, nur gesagt.

    ⚠️ **Und sie schweigt, wo nichts passiert sein kann.** Die Meldung haengt
    an zwei Bedingungen: Der Schritt wird gerade als *vorgefunden* eingetragen
    (nur solche Datenbanken hatten den alten Schritt ueberhaupt laufen), und es
    gibt ueberhaupt ein Konto mit ``-1``. Eine Warnung, die auch dort erscheint,
    wo es nichts zu sehen gibt, liest beim dritten Mal niemand mehr.
    """
    betroffen = verbindung.exec_driver_sql(
        "SELECT COUNT(*) FROM users WHERE storage_limit_gb = -1"
    ).scalar()
    if not betroffen:
        return
    logger.warning(
        "Storage limits: until this version, a stored 0 was rewritten to -1 on every "
        "start, not just once. A 0 set on purpose ('may not request anything') was "
        "therefore turned into 'unlimited' again after the next restart. %d account(s) "
        "currently hold -1 and may be affected. Nexview cannot repair this by itself: a "
        "rewritten 0 is indistinguishable from an 'unlimited' set on purpose, so nothing "
        "has been changed. Please check the storage limits of these accounts",
        betroffen,
    )


def _eintragen(verbindung, name: str, herkunft: str) -> None:
    """Eine Zeile ins Wanderungsbuch schreiben.

    ``INSERT OR IGNORE``, weil ein Schritt sich auch **selbst** eintragen darf:
    ``_kontingente_dreiwertig_machen`` tut das in derselben Transaktion wie
    seine Aenderung, und ``_einmal`` kaeme danach ein zweites Mal vorbei. Ein
    Fehler waere das nicht, nur laut.
    """
    verbindung.exec_driver_sql(
        f"INSERT OR IGNORE INTO {WANDERUNGSBUCH} "  # noqa: S608 - fester Name
        "(wanderung_name, wanderung_am, wanderung_herkunft, wanderung_version) "
        "VALUES (?, ?, ?, ?)",
        (name, str(utcnow().replace(tzinfo=None)), herkunft, __version__),
    )


def _einmal(schritt: Callable[[], None], gelaufen: set[str]) -> None:
    """Einen Einmal-Schritt nur laufen lassen, wenn er nicht im Buch steht.

    ⚠️ **Die Zustandssperren in den Schritten selbst bleiben trotzdem
    stehen.** Zwei Schloesser sind hier billiger als eines: Das Buch schuetzt
    gegen ein spaeter geleertes Ziel (die Zieltabelle ist irgendwann leer, der
    Schritt wuerde wieder losziehen), die alte Sperre gegen einen Absturz
    zwischen Schritt und Bucheintrag.
    """
    name = schritt.__name__
    if name in gelaufen:
        return
    schritt()
    with engine.begin() as verbindung:
        _eintragen(verbindung, name, AUSGEFUEHRT)
    gelaufen.add(name)
    logger.info("Migration %r ran", name)


def _kontingente_dreiwertig_machen() -> None:
    """Die Speichergrenze auf die neue Schreibweise bringen - **einmalig**.

    ⚠️ **Dieselbe Zahl hat ihre Bedeutung gewechselt.** Bis 0.19 hiess eine
    ``0`` bei ``storage_limit_gb`` "fuer dieses Konto unbegrenzt". Seit die
    Kontingente dreiwertig sind, heisst sie "darf nichts anfragen" - und
    "unbegrenzt" ist die ``-1``. Ohne diesen Schritt waere jedes so gesetzte
    Konto ueber Nacht **still gesperrt** worden, ohne dass jemand etwas
    geaendert haette.

    ``NULL`` bleibt ``NULL``: Es hiess vorher "es gilt die Standardgrenze" und
    heisst es weiter.

    ⚠️ **Bis 0.26 lief er nicht einmalig, sondern bei jedem Start.** Die drei
    anderen Wanderungen erkennen an ihren eigenen Daten, dass sie fertig sind
    ("steht schon eine Zeile in der Zieltabelle?"). Dieser hier kann das nicht:
    Eine frisch gesetzte ``0`` sieht aus wie eine alte. Ein Betreiber, der einem
    Konto ausdruecklich "darf nichts anfragen" gab, fand nach dem naechsten
    Neustart "unbegrenzt" vor - und niemand konnte sagen, warum. Seitdem haelt
    das Wanderungsbuch fest, dass er gelaufen ist.

    Ausserdem zieht der Kontingent-Zeitraum von den Konten in die
    Einstellungen um. Er gilt jetzt haus-weit; gab es unter den Konten genau
    einen abweichenden Wert, wird er uebernommen, damit sich fuer eine
    bestehende Installation nichts aendert. Waren mehrere verschiedene im
    Umlauf, gewinnt der haeufigste - eine Entscheidung muss fallen, und die
    Mehrheit trifft am wenigsten Leute.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        ergebnis = connection.exec_driver_sql(
            "UPDATE users SET storage_limit_gb = -1 WHERE storage_limit_gb = 0"
        )
        if ergebnis.rowcount:
            logger.info(
                "Storage limit of %d account(s) migrated: 0 meant 'unlimited' and now "
                "means 'nothing allowed' - they were set to -1 (unlimited)",
                ergebnis.rowcount,
            )

        # ⚠️ **Der Umstellungs-Pardon.** War der alte Hauptschalter *aus*,
        # galten die gespeicherten GB-Grenzen bis eben gar nicht - weder die
        # des Hauses noch die der Konten. Ab jetzt gelten sie. Ohne diesen
        # Schritt waeren Leute nach dem Update schlagartig gesperrt, wegen
        # einer Zahl, die seit Monaten wirkungslos herumlag, und wegen einer
        # Zurechnung, von der sie nicht wussten, dass sie einmal zaehlen wuerde.
        #
        # Also dieselbe Regel wie beim frueheren Umschalten: alles ins Haus,
        # jedes Konto startet bei null. Keine Datei wird angefasst, gespeicherte
        # Grenzen bleiben stehen - sie greifen nur ab jetzt statt rueckwirkend.
        war_aus = connection.exec_driver_sql(
            "SELECT value FROM settings WHERE key = 'storage_enabled'"
        ).scalar()
        if war_aus is not None and (war_aus or "").strip().lower() not in {
            "on",
            "true",
            "1",
            "yes",
            "ja",
        }:
            ergebnis = connection.exec_driver_sql(
                "UPDATE storage_entries SET user_id = NULL, state = 'house', "
                "released_at = NULL, release_wish = NULL WHERE user_id IS NOT NULL"
            )
            if ergebnis.rowcount:
                logger.warning(
                    "Quotas merged: storage limits were inert until now, so %d item(s) "
                    "were moved to the household - every account starts at zero",
                    ergebnis.rowcount,
                )

        # Der alte Hauptschalter wird nicht mehr gelesen. Die Zeile stehen zu
        # lassen waere eine Einstellung, die es nicht mehr gibt - und beim
        # naechsten Blick in die Datenbank eine falsche Faehrte.
        connection.exec_driver_sql("DELETE FROM settings WHERE key = 'storage_enabled'")

        # ⚠️ **Der Bucheintrag gehoert hier hinein, in dieselbe Transaktion.**
        # Dieser Schritt ist der einzige der vier ohne eigene Zustandssperre;
        # ein Absturz zwischen der Aenderung und dem Eintrag liesse ihn beim
        # naechsten Start noch einmal ueber die Konten laufen. So faellt
        # entweder beides oder nichts. Die beiden Rueckspruenge weiter unten
        # liegen dahinter und tragen den Eintrag deshalb mit.
        _eintragen(connection, "_kontingente_dreiwertig_machen", AUSGEFUEHRT)

        # Der Zeitraum nur, solange niemand ihn haus-weit gesetzt hat: Ein
        # zweiter Lauf duerfte eine bewusste Wahl nicht ueberschreiben.
        schon_da = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM settings WHERE key = 'quota_period'"
        ).scalar()
        if schon_da:
            return
        zeile = connection.exec_driver_sql(
            """
            SELECT quota_period, COUNT(*) AS anzahl
            FROM users
            WHERE quota_period IS NOT NULL AND quota_period != 'week'
            GROUP BY quota_period
            ORDER BY anzahl DESC
            LIMIT 1
            """
        ).first()
        if zeile is None:
            return
        # ⚠️ ``is_secret`` und ``updated_at`` sind Pflichtspalten - beim
        # rohen SQL springt kein Standardwert aus dem Modell ein. Ohne sie
        # scheitert das INSERT beim **Start**, also bei jeder Installation,
        # die gerade aktualisiert.
        connection.exec_driver_sql(
            "INSERT INTO settings (key, value, is_secret, updated_at) "
            "VALUES ('quota_period', ?, 0, ?)",
            (zeile[0], str(utcnow().replace(tzinfo=None))),
        )
        logger.info(
            "Quota period is now house-wide; adopted %r from %d account(s)",
            zeile[0],
            zeile[1],
        )


def _verknuepfungen_in_die_tabelle() -> None:
    """Die eine Medienserver-Identitaet je Benutzer in ihre Tabelle.

    Dasselbe Muster wie ``_verbindung_in_die_tabelle``, eine Ebene tiefer: Dort
    wanderte der *Server* aus flachen Werten in eine Tabelle, hier die
    *Identitaeten der Menschen*. Der Grund ist derselbe - bis 0.18.0 konnte es
    nur eine geben, im Parallelbetrieb sind es zwei.

    Der Schritt laeuft **genau einmal**: Steht schon eine Zeile da, passiert
    nichts.

    ⚠️ **Anders als beim Server bleiben die alten Spalten stehen und behalten
    ihren Wert.** Beim Server wurden sie geleert, damit es nicht zwei
    Wahrheiten gibt. Hier waere genau das falsch: Die Spalten am Benutzer
    fuehren weiterhin die *zuletzt* verknuepfte Identitaet, und ein gutes
    Dutzend Stellen liest sie - Profil, Anmeldung, Wiedererkennen. Sie zu
    leeren hiesse, jeden Benutzer bei laufendem Betrieb als "nicht verbunden"
    dastehen zu lassen. Gepflegt werden sie ab jetzt von
    ``mediaserver_accounts.link``.

    Das Token wandert verschluesselt und ungeoeffnet - wie beim Server auch.
    """
    with engine.begin() as connection:
        schon_da = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM user_media_server_accounts"
        ).scalar()
        if schon_da:
            return

        zeilen = connection.exec_driver_sql(
            "SELECT id, mediaserver_provider, mediaserver_account_id, "
            "mediaserver_username, mediaserver_email, mediaserver_thumb, "
            "mediaserver_linked_at, watchlist_token, watchlist_connected_at, "
            "watchlist_token_invalid_at FROM users "
            "WHERE mediaserver_provider IS NOT NULL "
            "AND mediaserver_provider != '' "
            "AND mediaserver_account_id IS NOT NULL "
            "AND mediaserver_account_id != ''"
        ).fetchall()
        if not zeilen:
            return

        jetzt = str(utcnow().replace(microsecond=0))
        for zeile in zeilen:
            connection.exec_driver_sql(
                "INSERT INTO user_media_server_accounts "
                "(user_id, provider, account_id, username, email, thumb, "
                "linked_at, token, token_connected_at, token_invalid_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    zeile[0],
                    zeile[1],
                    zeile[2],
                    zeile[3],
                    zeile[4],
                    zeile[5],
                    zeile[6] or jetzt,
                    zeile[7],
                    zeile[8],
                    zeile[9],
                ),
            )
        logger.info(
            "Personal media server links moved into their own table: %d account(s)",
            len(zeilen),
        )


def _existing_columns(connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()
    }


def _existing_indexes(connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.exec_driver_sql(f"PRAGMA index_list('{table_name}')").fetchall()
    }


def _pending_changes() -> list[str]:
    """Was muesste an der bestehenden Datenbank geaendert werden?

    Dient nur als Ausloeser fuer die Sicherung. Die Pruefung nutzt bewusst
    dieselben PRAGMA-Abfragen wie die Aenderungsschritte weiter unten: waeren
    es zwei unterschiedliche Verfahren, koennte diese Funktion dauerhaft eine
    Aenderung melden, die dort schon als erledigt gilt - und Nexview wuerde bei
    jedem Start eine neue Sicherung anlegen.
    """
    if not _settings.db_path.exists():
        return []  # frische Installation, es gibt nichts zu sichern

    offen: list[str] = []
    with engine.connect() as connection:
        tabellen = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        for table in Base.metadata.sorted_tables:
            if table.name not in tabellen:
                offen.append(f"Tabelle {table.name}")
                continue

            spalten = _existing_columns(connection, table.name)
            offen.extend(
                f"Spalte {table.name}.{column.name}"
                for column in table.columns
                if column.name not in spalten
            )

            indizes = _existing_indexes(connection, table.name)
            offen.extend(
                f"Index {index.name}" for index in table.indexes if index.name not in indizes
            )

    return offen


def _backup_database() -> None:
    """Kopie der Datenbank anlegen, bevor sie veraendert wird.

    Die eigentliche Arbeit macht ``services.sicherung`` - dort liegt auch das
    Auflisten und das Ausliefern als verschluesseltes Archiv, und beides muss
    denselben Ordner und dieselben Namen sehen wie das hier.

    Scheitert die Sicherung, laeuft der Start trotzdem weiter: Ein Container,
    der wegen einer nicht schreibbaren Sicherung gar nicht erst hochkommt,
    waere schlimmer als eine fehlende Sicherung.
    """
    # Erst hier importiert - ``sicherung`` greift auf ``engine`` aus diesem
    # Modul zu, ein Import oben waere ein Ring.
    from .services import sicherung

    try:
        sicherung.anlegen(art=sicherung.AUTOMATISCH)
    except Exception as fehler:  # noqa: BLE001 - Start darf daran nicht scheitern
        logger.warning("Database backup failed: %s", fehler)


def _prune_backups(ordner: Path, behalten: int = BACKUPS_TO_KEEP) -> None:
    """Alte automatische Staende wegraeumen.

    Bleibt als Einstiegspunkt bestehen, weil Tests und aeltere Aufrufe ihn
    kennen; die Regel selbst - von Hand angelegte Sicherungen bleiben liegen -
    steht in ``services.sicherung``.
    """
    from .services import sicherung

    sicherung.aufraeumen(behalten=behalten, ordner_=ordner)


def _sql_literal(wert: object) -> str | None:
    """Standardwert einer Spalte als SQL-Literal - oder ``None``, wenn er sich
    nicht ausdruecken laesst.

    Die Reihenfolge der Pruefungen ist hier entscheidend: Auswahllisten (Rolle,
    Status, Kontingent-Zeitraum) erben in Nexview von ``str``, und ``bool``
    erbt von ``int``. Wer zuerst auf ``str`` bzw. ``int`` prueft, schreibt
    ``DEFAULT <QuotaPeriod.week: 'week'>`` in das ALTER TABLE - und die
    Datenbank eines aktualisierten Nexview laesst sich nicht mehr oeffnen.
    """
    if isinstance(wert, Enum):
        wert = wert.value
    if wert is None:
        return "NULL"
    if isinstance(wert, bool):
        return "1" if wert else "0"
    if isinstance(wert, (int, float)):
        return str(wert)
    if isinstance(wert, datetime):
        # Genau das Format, in dem SQLAlchemy Zeitstempel in SQLite ablegt.
        return "'" + str(wert) + "'"
    if isinstance(wert, str):
        return "'" + wert.replace("'", "''") + "'"
    return None


# Unterscheidet "kein Standardwert vorhanden" von "Standardwert ist NULL".
# Ohne diese Trennung wuerde eine Pflichtspalte ohne Standardwert stillschweigend
# ein ``DEFAULT NULL`` bekommen statt den beabsichtigten deutlichen Fehler.
_KEIN_WERT = object()


def _default_value(column) -> object:
    """Standardwert einer Spalte ermitteln - auch wenn er berechnet wird.

    Zeitstempel wie ``created_at`` haben eine Funktion als Standardwert. In ein
    ALTER TABLE laesst sich keine Funktion schreiben, also wird sie hier einmal
    ausgefuehrt: die bereits vorhandenen Zeilen bekommen damit den Zeitpunkt des
    Updates. Neue Zeilen holen ihren Wert weiterhin wie gewohnt aus dem Modell.
    """
    default = column.default
    if default is None:
        return _KEIN_WERT
    if default.is_scalar:
        return default.arg
    if default.is_callable:
        try:
            # SQLAlchemy verpackt parameterlose Funktionen so, dass sie einen
            # Kontext entgegennehmen; andere Fassungen kommen ohne aus.
            return default.arg(None)
        except TypeError:
            try:
                return default.arg()
            except Exception:  # noqa: BLE001
                return _KEIN_WERT
        except Exception:  # noqa: BLE001
            return _KEIN_WERT
    return _KEIN_WERT


def _add_missing_columns() -> None:
    """Spalten nachruesten, die in neueren Nexview-Versionen dazugekommen sind.

    ``create_all`` legt nur fehlende *Tabellen* an. Ohne diesen Schritt wuerde
    eine bestehende Installation nach einem Update auf fehlende Spalten laufen.
    SQLite kann Spalten problemlos anhaengen, solange sie einen Standardwert
    haben oder NULL erlauben.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            vorhanden = _existing_columns(connection, table.name)
            if not vorhanden:
                continue  # Tabelle gibt es (noch) nicht

            for column in table.columns:
                if column.name in vorhanden:
                    continue

                typ = column.type.compile(dialect=engine.dialect)
                klausel = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {typ}'

                wert = _default_value(column)
                literal = None if wert is _KEIN_WERT else _sql_literal(wert)

                if literal is not None:
                    klausel += f" DEFAULT {literal}"
                elif not column.nullable:
                    # Ohne Standardwert kann eine Pflichtspalte nicht ergaenzt
                    # werden - dann lieber sichtbar scheitern als still.
                    raise RuntimeError(
                        f"Spalte {table.name}.{column.name} fehlt und hat keinen Standardwert."
                    )

                logger.info("Database extended: %s.%s", table.name, column.name)
                connection.exec_driver_sql(klausel)


def _add_missing_indexes() -> None:
    """Indizes nachruesten, die zu neu ergaenzten Spalten gehoeren.

    ``create_all`` legt Indizes nur zusammen mit einer *neuen* Tabelle an. Bei
    einer bestehenden Installation bliebe die Eindeutigkeit der E-Mail-Adresse
    sonst unerzwungen - und Doppelkonten faenden ihren Weg hinein.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            vorhanden = _existing_indexes(connection, table.name)
            for index in table.indexes:
                if index.name in vorhanden:
                    continue
                logger.info("Database extended: index %s", index.name)
                index.create(bind=connection)


def get_db() -> Iterator[Session]:
    """FastAPI-Dependency: eine Datenbank-Session pro Anfrage."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _altersgrenzen_aufraeumen() -> None:
    """Altersgrenzen an vollwertigen Konten entfernen.

    Frueher konnte der Administrator jedem Konto ein Alter geben. Seit es
    Kinderkonten gibt, ist das der falsche Weg: Wer ein vollwertiges Konto hat,
    gilt als volljaehrig, und Kinder werden von ihren Eltern gepflegt. Die
    Einstellung ist deshalb aus der Benutzerverwaltung verschwunden - und ohne
    diesen Schritt bliebe ein frueher beschraenktes Konto fuer immer
    beschraenkt, weil es keine Stelle mehr gaebe, an der man es aufhebt.

    Bei Kinderkonten bleibt das Alter selbstverstaendlich stehen; dort ist es
    genau das Feld, an dem die Sperre haengt.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        ergebnis = connection.exec_driver_sql(
            "UPDATE users SET age = NULL WHERE age IS NOT NULL AND role != 'child'"
        )
        if ergebnis.rowcount:
            logger.info(
                "Age limit removed from %d adult account(s), it now applies to children only",
                ergebnis.rowcount,
            )


def _bewertungen_in_die_tabelle() -> None:
    """Bewertungen von den Anfragen an die Titel umhaengen.

    Bis 0.19 hing eine Bewertung an ``MediaRequest``. Damit durfte nur der
    Besteller urteilen - wer denselben Film spaeter sah und merkte, dass die
    Tonspur fehlt, hatte keine Moeglichkeit, es zu sagen. Seit 0.19 haengt sie
    am Titel, und die bestehenden muessen mit.

    ⚠️ **Laeuft genau einmal**, erkennbar an der leeren Zieltabelle - wie bei
    den Medienserver-Verbindungen. Ein zweiter Lauf wuerde vorhandene
    Bewertungen verdoppeln oder am Eindeutigkeitsschluessel scheitern.

    Die Spalten an der Anfrage bleiben vorerst stehen und werden nicht mehr
    gelesen. Sie zu loeschen hiesse, eine Tabelle in SQLite neu zu bauen - und
    solange sie niemanden stoeren, ist der stille Rueckweg mehr wert als die
    aufgeraeumte Spalte.
    """
    with engine.begin() as connection:
        schon_da = connection.exec_driver_sql("SELECT COUNT(*) FROM title_ratings").scalar()
        if schon_da:
            return

        # Dieselbe Person kann denselben Titel zweimal angefragt haben (etwa in
        # 1080p und 4K) und beide Male bewertet. Am Titel gibt es aber nur ein
        # Urteil - genommen wird das juengste.
        connection.exec_driver_sql(
            """
            INSERT INTO title_ratings (
                user_id, media_type, tmdb_id, season, rating, comment, title,
                reply, replied_at, file_size_bytes, outdated, created_at, updated_at
            )
            SELECT
                r.user_id, r.media_type, r.tmdb_id, r.season, r.rating, r.feedback,
                r.title, r.feedback_reply, r.replied_at,
                COALESCE(r.file_size_bytes, 0), COALESCE(r.rating_outdated, 0),
                COALESCE(r.rated_at, r.requested_at), COALESCE(r.rated_at, r.requested_at)
            FROM media_requests r
            WHERE r.rating IS NOT NULL
              AND r.id = (
                  SELECT r2.id FROM media_requests r2
                  WHERE r2.user_id = r.user_id
                    AND r2.media_type = r.media_type
                    AND r2.tmdb_id = r.tmdb_id
                    AND (r2.season IS r.season)
                    AND r2.rating IS NOT NULL
                  ORDER BY r2.rated_at DESC, r2.id DESC
                  LIMIT 1
              )
            """
        )


def _verbindung_in_die_tabelle() -> None:
    """Die eine Medienserver-Verbindung aus den Einstellungen in ihre Tabelle.

    Bis zum Parallelbetrieb lag sie in fuenf flachen Werten - Anbieter,
    Kennung, Name, Adresse, Token. Das ging, solange es nur eine geben konnte.

    Der Schritt laeuft **genau einmal**: Steht schon eine Zeile in der Tabelle,
    passiert nichts. Danach werden die alten Werte geleert, damit es nicht zwei
    Wahrheiten gibt - die zweite waere sonst genau die Art Altlast, die einem
    ein halbes Jahr spaeter als "warum steht da noch Plex" begegnet.

    ⚠️ **Das Token wandert verschluesselt und ungeoeffnet.** Es steht in beiden
    Tabellen als derselbe Text; entschluesselt wird es erst beim Benutzen. So
    braucht dieser Schritt weder den Schluessel noch den Einstellungsdienst -
    und ein Schluesselwechsel macht die Wanderung nicht kaputt, sondern
    hoechstens das Token, und das war vorher genauso.

    Die Client-Kennung bleibt, wo sie ist: Sie gehoert zur Installation, nicht
    zum Server.
    """
    with engine.begin() as connection:
        schon_da = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM media_server_connections"
        ).scalar()
        if schon_da:
            return

        werte = dict(
            connection.exec_driver_sql(
                "SELECT key, value FROM settings WHERE key IN "
                "('mediaserver_provider', 'mediaserver_machine_id', "
                "'mediaserver_name', 'mediaserver_url', 'mediaserver_token')"
            ).all()
        )
        anbieter = (werte.get("mediaserver_provider") or "").strip()
        kennung = (werte.get("mediaserver_machine_id") or "").strip()
        if not anbieter or not kennung:
            # Nie verbunden gewesen, oder eine halbe Verbindung ohne Kennung -
            # daraus laesst sich keine Zeile machen, die den Zugriff pruefen
            # koennte. Dann lieber nichts.
            return

        # ⚠️ **Jede Spalte der Tabelle gehoert in diese Aufzaehlung.**
        #
        # Rohes SQL nennt die Spalten selbst, und Standardwerte aus dem Modell
        # gelten hier nicht - die kennt nur SQLAlchemy, nicht SQLite. Eine
        # vergessene Pflichtspalte laesst diesen Schritt scheitern, und mit ihm
        # den ganzen Start. Genau so passiert, als ``account_id`` dazukam.
        connection.exec_driver_sql(
            "INSERT INTO media_server_connections "
            "(provider, machine_id, name, url, token, account_id, connected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                anbieter,
                kennung,
                werte.get("mediaserver_name") or "",
                werte.get("mediaserver_url") or "",
                werte.get("mediaserver_token") or "",
                # Leer: Eine Verbindung aus der Zeit davor kennt die
                # Kontonummer nicht. Der Adapter fragt dann wie bisher beim
                # Server nach - bei Plex und Jellyfin geht das.
                "",
                str(utcnow().replace(microsecond=0)),
            ),
        )
        connection.exec_driver_sql(
            "UPDATE settings SET value = '' WHERE key IN "
            "('mediaserver_provider', 'mediaserver_machine_id', "
            "'mediaserver_name', 'mediaserver_url', 'mediaserver_token')"
        )
        logger.info(
            "Media server connection moved into its own table: %r on %r",
            anbieter,
            werte.get("mediaserver_name") or kennung,
        )


# Tabellen mit einer Spalte ``providers`` - siehe ``UserWatched.providers``.
GESEHEN_TABELLEN = ("user_watched", "user_watched_seasons")


def _gesehen_herkunft_nachtragen() -> None:
    """Bestehenden Gesehen-Markern den Anbieter eintragen, von dem sie stammen.

    Vor dieser Spalte gab es genau einen Medienserver, also ist die Antwort
    eindeutig: Alles, was dasteht, kam von dem, der gerade verbunden ist.

    ⚠️ **Ohne diesen Schritt waeren die Marker herrenlos** - und der Abgleich
    kann eine herrenlose Zeile nicht zuordnen. Beim ersten Lauf eines zweiten
    Anbieters wuesste er nicht, ob "der andere Server sagt gesehen" gilt oder
    ob die Zeile einfach nur alt ist.

    Bewusst ueber rohes SQL statt ueber die Dienste: Diese Datei laeuft ganz am
    Anfang des Starts, und der Anbietername ist kein Geheimnis. Sie einzubinden
    brauchte ein Sitzungsobjekt und haette hier nichts zu suchen.

    Gelesen wird aus **beiden** Quellen: aus ``media_server_connections``,
    sobald es sie gibt, sonst aus den Einstellungen. Solange die Wanderung dahin
    noch nicht angeschaltet ist, steht der Anbieter naemlich weiterhin dort -
    und dieser Schritt soll von jener Umstellung unabhaengig richtig sein.

    Sind **mehrere** Server verbunden, passiert nichts: Dann liesse sich nicht
    mehr sagen, von welchem ein alter Marker stammt, und Raten waere schlimmer
    als eine leere Angabe.

    Ist gar kein Server verbunden, passiert ebenfalls nichts.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        anbieter_liste = [
            zeile[0]
            for zeile in connection.exec_driver_sql(
                "SELECT provider FROM media_server_connections"
            )
            if zeile[0] and str(zeile[0]).strip()
        ]
        if not anbieter_liste:
            aus_einstellungen = connection.exec_driver_sql(
                "SELECT value FROM settings WHERE key = 'mediaserver_provider'"
            ).scalar()
            if aus_einstellungen and str(aus_einstellungen).strip():
                anbieter_liste = [str(aus_einstellungen).strip()]
        if len(anbieter_liste) != 1:
            return
        anbieter = anbieter_liste[0]

        for tabelle in GESEHEN_TABELLEN:
            ergebnis = connection.exec_driver_sql(
                f"UPDATE {tabelle} SET providers = ? "  # noqa: S608 - feste Namen
                "WHERE providers IS NULL OR providers = ''",
                (str(anbieter).strip(),),
            )
            if ergebnis.rowcount:
                logger.info(
                    "Watched: %d marker(s) in %s attributed to the connected server %r",
                    ergebnis.rowcount,
                    tabelle,
                    str(anbieter).strip(),
                )


# Tabellen, deren Spalte ``type`` eine ``NotificationType`` fuehrt.
MELDUNGSTABELLEN = ("notifications", "channel_outbox")


def _verwaiste_kinderwuensche_aufraeumen() -> None:
    """Kinderwuensche wegraeumen, deren Kind oder Elternteil es nicht mehr gibt.

    ⚠️ **Eine einzige solche Zeile legt die Wunschliste eines Elternteils
    lahm.** ``_wunsch_zeile`` in ``routers/children`` liest
    ``wunsch.child.display_name``; fehlt das Kind, ist das kein leerer Name,
    sondern ein ``AttributeError`` - und damit ein 500er fuer die ganze Liste,
    nicht nur fuer die eine Zeile.

    Wie solche Zeilen entstehen: ``ChildWish.child_id`` traegt keine
    ``ON DELETE``-Regel, weil nachgetragene Spalten in SQLite keine tragen
    koennen. Abgeraeumt wird deshalb ausdruecklich im Dienst - und genau ein
    Weg vergass es bis 0.27: ``DELETE /api/users/{id}`` auf ein Konto, das
    selbst ein Kind ist. Der Weg ist inzwischen dicht, aber Datenbanken, die
    ihn genommen haben, tragen die Zeilen weiter. Dasselbe gilt fuer
    eingespielte Staende aus einer anderen Installation, in der die Konten
    andere Nummern hatten.

    Auf **WARNING**, nicht INFO: Hier verschwinden Wuensche, die jemand einmal
    geaeussert hat. Dass sie ohnehin niemandem mehr zuzuordnen sind, macht das
    Loeschen richtig, aber nicht unsichtbar.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    with engine.begin() as connection:
        offen = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM child_wishes "
            "WHERE child_id NOT IN (SELECT id FROM users) "
            "OR parent_id NOT IN (SELECT id FROM users)"
        ).scalar()
        if not offen:
            return

        connection.exec_driver_sql(
            "DELETE FROM child_wishes "
            "WHERE child_id NOT IN (SELECT id FROM users) "
            "OR parent_id NOT IN (SELECT id FROM users)"
        )
        logger.warning(
            "Removed %d child wish(es) whose child or parent account no longer exists. "
            "They could not be shown or decided any more.",
            offen,
        )


def _verwaiste_meldungsarten_aufraeumen() -> None:
    """Meldungen wegraeumen, deren Art es nicht mehr gibt.

    ⚠️ **Eine einzige unbekannte Zeile legt die ganze Glocke eines Kontos
    lahm.** ``Notification.type`` ist eine strikte Aufzaehlung; findet
    SQLAlchemy beim Auspacken einen Wert, den ``NotificationType`` nicht kennt,
    wirft es ``LookupError`` - und zwar nicht fuer die eine Zeile, sondern fuer
    die ganze Abfrage. Der Endpunkt antwortet mit 500, die Oberflaeche zeigt
    "Nichts Neues", und **der Zaehler daneben laeuft weiter**, weil er ueber
    ``func.count`` geht und nie eine Zeile auspackt. Genau diese Kombination
    macht den Fehler so tueckisch: Er sieht nicht aus wie ein Fehler, sondern
    wie ein Zaehler, der spinnt.

    Gemessen in einer echten Datenbank: vier Zeilen der Art
    ``watchlist_imported`` - ein Name, den es im Code laengst nicht mehr gibt -
    haben die Glocke eines Kontos dauerhaft blockiert und dabei eine **echte,
    ungelesene** Rueckmeldung mit verdeckt.

    Solche Zeilen entstehen, sobald eine Meldungsart umbenannt oder entfernt
    wird: ``init_db`` ergaenzt Tabellen, Spalten und Indizes, aber es fasst
    **niemals Zeileninhalte** an. Die alten Werte bleiben also liegen.

    Wer hier eine Art umbenennt, muss deshalb wissen: Ohne diesen Schritt ist
    das kein kosmetischer Eingriff, sondern eine kaputte Glocke bei jedem
    Konto, das eine solche Meldung hatte.

    Geloescht statt umgeschrieben, weil sich eine verschwundene Art nicht
    sinnvoll auf eine andere abbilden laesst - der Text stuende dann unter
    einer Ueberschrift, die nicht dazu gehoert. Auf **WARNING**, nicht INFO:
    Hier gehen Daten verloren, und das soll jemand sehen.

    Laeuft bei jedem Start und trifft nach dem ersten Mal nichts mehr.
    """
    bekannt = sorted(art.value for art in NotificationType)
    frage = ", ".join("?" for _ in bekannt)

    with engine.begin() as connection:
        for tabelle in MELDUNGSTABELLEN:
            # Erst nachsehen, was da ist - die Namen sind der eigentliche
            # Hinweis fuer den Betreiber. Eine reine Anzahl sagt ihm nicht,
            # welche Umbenennung ihm das eingebrockt hat.
            fremd = [
                zeile[0]
                for zeile in connection.exec_driver_sql(
                    f"SELECT DISTINCT type FROM {tabelle} "  # noqa: S608 - feste Namen
                    f"WHERE type NOT IN ({frage})",
                    tuple(bekannt),
                )
            ]
            if not fremd:
                continue

            ergebnis = connection.exec_driver_sql(
                f"DELETE FROM {tabelle} WHERE type NOT IN ({frage})",  # noqa: S608
                tuple(bekannt),
            )
            logger.warning(
                "Removed %d row(s) from %s with unknown notification type(s): %s - "
                "these were unreadable and blocked the whole list for the affected users",
                ergebnis.rowcount,
                tabelle,
                ", ".join(sorted(fremd)),
            )


def _betreiber_bestimmen() -> None:
    """Wer traegt den Betreiber-Haken? - beim Hochfahren, einmal.

    Bei einer **bestehenden** Installation hat ihn nach dem Update niemand.
    Sie bekommt den aeltesten aktiven Administrator; das ist in aller Regel das
    Konto aus dem Einrichtungsassistenten. Ist ``NEXVIEW_BETREIBER`` gesetzt,
    gewinnt die Variable. Die Regeln stehen in ``services/betreiber.py``, hier
    haengt nur der Startweg daran.

    ⚠️ **Nicht in ``_add_missing_columns`` aufgehoben.** Das Nachtragen der
    Spalte ist eine Schema-Aenderung, das Vergeben des Hakens eine
    Entscheidung ueber Daten - und die muss auch bei einer Datenbank laufen,
    der die Spalte schon gehoert (etwa nach einer eingespielten Sicherung aus
    einer aelteren Fassung).
    """
    from .services import betreiber

    with SessionLocal() as db:
        betreiber.beim_start(db)

