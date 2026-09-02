"""Das Wanderungsbuch: welcher Einmal-Schritt lief in dieser Datenbank schon?

⚠️ **Warum es diese Datei gibt.** ``init_db`` fuehrt zwei ganz verschiedene
Arten von Schritten aus, und man sieht ihnen das nicht an. Die einen sind
Pflege und muessen bei jedem Start laufen. Die anderen deuten Bestandsdaten um
und duerfen danach nie wieder laufen.

Bei ``_kontingente_dreiwertig_machen`` ging das schief. Er schreibt ``-1``, wo
eine ``0`` steht - "unbegrenzt" statt "darf nichts anfragen" -, und er hatte
keine Moeglichkeit zu erkennen, dass er das schon getan hat: Eine frisch
gesetzte ``0`` sieht aus wie eine alte. Er lief also bei **jedem** Start
wieder. Wer einem Konto ausdruecklich "darf nichts anfragen" gab, fand nach
dem naechsten Neustart "unbegrenzt" vor, und niemand konnte sagen, warum.

Geprueft wird von beiden Seiten, denn eine Haelfte allein waere die jeweils
andere Katastrophe:

* Eine **gesetzte** ``0`` muss den Neustart ueberleben.
* Eine **alte** ``0`` aus der Zeit vor 0.19 muss trotzdem noch umziehen -
  sonst waere ein Konto ueber Nacht still gesperrt.

Dazu der Waechter ueber die Klassifikation und drei Proben, die die
Pflegeschritte in der Pflege festhalten.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import db as db_modul
from app.models import Base, Role, User, UserWatched, Wanderung

from .conftest import create_user

# ---------------------------------------------------------------------------
# Eine Datenbank fuer sich
# ---------------------------------------------------------------------------


@pytest.fixture
def eigene_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Engine]:
    """``init_db()`` auf eine eigene Datenbank in einem eigenen Verzeichnis richten.

    Nach dem Muster von ``test_migration.alte_installation``, nur baubar: Die
    zurueckgegebene Funktion legt das Verzeichnis an und richtet ``db_modul``
    darauf.

    ⚠️ **Das gemeinsame ``clean_db`` aus ``conftest`` wird hier nicht
    ausgehebelt.** Es laeuft vorher und ordentlich auf der Testdatenbank des
    Laufs; erst danach zeigt ``db_modul.engine`` hierher. Zwei Vorbereitungen,
    die sich nicht sehen - genau das ist gewollt, weil die Tests weiter unten
    ``admin_client`` brauchen und der ohne ``clean_db`` nicht funktioniert.
    """
    motoren: list[Engine] = []

    def einrichten(vollstaendig: bool = True, ohne_arr_managed: bool = False) -> Engine:
        datenverzeichnis = tmp_path / "data"
        datenverzeichnis.mkdir(exist_ok=True)
        motor = create_engine(
            f"sqlite:///{datenverzeichnis / 'nexview.db'}",
            connect_args={"check_same_thread": False},
        )
        if vollstaendig:
            Base.metadata.create_all(bind=motor)
        if ohne_arr_managed:
            # ⚠️ **Nachgebaut wird das Kennzeichen, nicht die ganze alte
            # Datei.** ``storage_entries.arr_managed`` kam in derselben
            # Fassung wie der Kontingent-Schritt (0.19.0, ein Commit); daran
            # und nur daran erkennt der Ankunftsbefund das Alter. Eine von
            # Hand geschriebene CREATE-TABLE-Kette waere in einem halben Jahr
            # veraltet und wuerde dann etwas anderes pruefen, als hier steht.
            with motor.begin() as verbindung:
                verbindung.exec_driver_sql(
                    "ALTER TABLE storage_entries DROP COLUMN arr_managed"
                )
        monkeypatch.setattr(db_modul, "engine", motor)
        monkeypatch.setattr(db_modul._settings, "data_dir", datenverzeichnis)
        motoren.append(motor)
        return motor

    yield einrichten

    for motor in motoren:
        motor.dispose()


def _konto_anlegen(motor: Engine, grenze: int | None) -> None:
    """Ein Konto mit genau dieser Speichergrenze.

    Ueber eine eigene Sitzung an *diesem* Motor, nicht ueber ``SessionLocal``:
    das haengt seit dem Import an der Testdatenbank des Laufs und wuerde in
    die falsche Datei schreiben.
    """
    with Session(motor) as sitzung:
        sitzung.add(
            User(
                username="altbenutzer",
                email="altbenutzer@beispiel.de",
                password_hash="egal",
                role=Role.user,
                display_name="Alter Hase",
                storage_limit_gb=grenze,
            )
        )
        sitzung.commit()


def _grenze(motor: Engine) -> int | None:
    with motor.connect() as verbindung:
        return verbindung.exec_driver_sql("SELECT storage_limit_gb FROM users").scalar()


def _buch(motor: Engine) -> dict[str, str]:
    """Das Wanderungsbuch als ``{Name: Herkunft}``."""
    with motor.connect() as verbindung:
        return dict(
            verbindung.exec_driver_sql(
                "SELECT wanderung_name, wanderung_herkunft FROM wanderungen"
            ).all()
        )


# ---------------------------------------------------------------------------
# Die beiden Richtungen der 0
# ---------------------------------------------------------------------------


def test_eine_gesetzte_null_ueberlebt_den_neustart(admin_client: TestClient) -> None:
    """⚠️ **Der eigentliche Fehler, und der Grund fuer diese Datei.**

    Ein Betreiber setzt "darf nichts anfragen". Beim naechsten Start stand
    dort "unbegrenzt" - aus einer Sperre wurde ein Freibrief, und zwar
    lautlos.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    antwort = admin_client.patch(f"/api/users/{konto['id']}", json={"storage_limit_gb": 0})
    assert antwort.status_code == 200, antwort.text

    db_modul.init_db()  # der Neustart

    with db_modul.engine.connect() as verbindung:
        gespeichert = verbindung.exec_driver_sql(
            "SELECT storage_limit_gb FROM users WHERE username = 'kim'"
        ).scalar()
    assert gespeichert == 0, "die gesetzte 0 wurde beim Start umgedeutet"

    # Und die Oberflaeche muss sie auch so zu sehen bekommen: In der Datenbank
    # steht die Zahl, nach aussen sind es Woerter - eine ``-1`` waere hier als
    # "unlimited" herausgekommen, und niemand haette den Zusammenhang gesehen.
    liste = admin_client.get("/api/users").json()
    kim = next(eintrag for eintrag in liste if eintrag["username"] == "kim")
    assert kim["storage_limit_gb"] == 0


def test_eine_alte_null_zieht_noch_um(eigene_installation) -> None:
    """Die Gegenrichtung: Wer von 0.18 kommt, muss den Umzug noch bekommen.

    Bis 0.19 hiess die ``0`` beim Speicher "fuer dieses Konto unbegrenzt".
    Bliebe sie stehen, waere dasselbe Konto ab dem Update still gesperrt -
    derselbe Schaden, nur in die andere Richtung.
    """
    motor = eigene_installation(ohne_arr_managed=True)
    _konto_anlegen(motor, 0)

    db_modul.init_db()

    assert _grenze(motor) == -1, "die alte 0 haette nach 'unbegrenzt' umziehen muessen"
    assert _buch(motor)["_kontingente_dreiwertig_machen"] == "ausgefuehrt"


def test_nach_dem_umzug_bleibt_die_naechste_null_stehen(eigene_installation) -> None:
    """Das Buch faellt hinter der Wanderung wirklich zu.

    Ohne diese Probe koennte der Schritt auch nur den *ersten* Start
    ueberspringen - und beim zweiten wieder zuschlagen.
    """
    motor = eigene_installation(ohne_arr_managed=True)
    _konto_anlegen(motor, 0)

    db_modul.init_db()
    assert _grenze(motor) == -1

    with motor.begin() as verbindung:
        verbindung.exec_driver_sql("UPDATE users SET storage_limit_gb = 0")

    db_modul.init_db()
    assert _grenze(motor) == 0, "nach dem Umzug wurde die naechste 0 wieder umgedeutet"


def test_bestandsdatenbank_wird_als_erledigt_vorgefunden(eigene_installation) -> None:
    """Eine Datenbank, die den Schritt hinter sich hat, bekommt ihn nicht noch einmal.

    Erkannt wird das am Schema, nicht an den Zeilen: ``arr_managed`` kam mit
    demselben Commit wie der Schritt. Ohne diese Ableitung wuesste eine
    bestehende Installation nach dem Update nicht, was sie schon hinter sich
    hat - und die 0 daneben waere wieder faellig.
    """
    motor = eigene_installation()
    _konto_anlegen(motor, 0)

    db_modul.init_db()

    assert _buch(motor)["_kontingente_dreiwertig_machen"] == "vorgefunden"
    assert _grenze(motor) == 0


def test_der_halb_gewanderte_stand_gilt_nicht_als_erledigt(eigene_installation) -> None:
    """Die zweite Haelfte des Ankunftsbefunds: ``storage_enabled`` zaehlt mit.

    ``arr_managed`` allein genuegt nicht. Eine Installation, deren
    Kontingent-Schritt seinerzeit zwischen Spalte und Aufraeumen abgebrochen
    ist, traegt die Spalte **und** noch die alte ``storage_enabled``-Zeile.
    Wer nur auf die Spalte sieht, haelt sie fuer fertig gewandert - ihre alten
    Nullen blieben stehen und hiessen ab sofort "darf nichts anfragen".
    """
    motor = eigene_installation()
    _konto_anlegen(motor, 0)
    with motor.begin() as verbindung:
        verbindung.exec_driver_sql(
            "INSERT INTO settings (key, value, is_secret, updated_at)"
            " VALUES ('storage_enabled', 'on', 0, '2026-01-01 12:00:00')"
        )

    db_modul.init_db()

    assert _grenze(motor) == -1, "der halb gewanderte Stand galt faelschlich als erledigt"
    assert _buch(motor)["_kontingente_dreiwertig_machen"] == "ausgefuehrt"


def test_ein_abgebrochener_erster_start_versiegelt_die_wanderung_nicht(
    eigene_installation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ **Der Abbruch mitten im Update darf die alte 0 nicht einsperren.**

    Der erste Start einer 0.18er Datenbank kommt bis hinter
    ``_add_missing_columns`` - die Spalte ``arr_managed`` steht damit schon da -
    und bricht dann ab, bevor die Kontingent-Wanderung an der Reihe war. Der
    zweite Start laeuft heil durch. Laese er sein Urteil jetzt aus dem
    Schema, hielte er die Wanderung fuer erledigt: Die alte 0 bliebe stehen
    und hiesse ab sofort "darf nichts anfragen" - still, dauerhaft, und ohne
    dass die Meldung ueber umgedeutete Nullen anschlaegt.

    Deshalb schreibt ``init_db`` den Befund ins Buch, **bevor** irgendeine
    Schema-Aenderung laeuft: Der Fehlstart hinterlaesst ``offen``, und der
    zweite Start holt den Schritt nach.
    """
    motor = eigene_installation(ohne_arr_managed=True)
    _konto_anlegen(motor, 0)

    echte_pflege = db_modul._gesehen_herkunft_nachtragen
    kaputt = True

    def stromausfall() -> None:
        # Einer der Pflegeschritte zwischen Schema-Aenderung und Wanderung -
        # genau das Fenster, in dem der Abbruch frueher zur Versiegelung wurde.
        if kaputt:
            raise RuntimeError("abgebrochener erster Start")
        echte_pflege()

    monkeypatch.setattr(db_modul, "_gesehen_herkunft_nachtragen", stromausfall)
    with pytest.raises(RuntimeError, match="abgebrochener erster Start"):
        db_modul.init_db()

    # Der Fehlstart hat das Schema schon vorgerueckt - aber das Buch weiss es
    # besser: Die Wanderung steht als offen darin, nicht als erledigt.
    assert _buch(motor)["_kontingente_dreiwertig_machen"] == "offen"
    assert _grenze(motor) == 0

    kaputt = False
    db_modul.init_db()

    assert _grenze(motor) == -1, (
        "die alte 0 haette der zweite Start noch umziehen muessen - "
        "der Fehlstart hat die Wanderung versiegelt"
    )
    assert _buch(motor)["_kontingente_dreiwertig_machen"] == "ausgefuehrt"


def test_frische_installation_traegt_alle_wanderungen_ein(eigene_installation) -> None:
    """Beim allerersten Start gibt es nichts zu wandern - und das kommt ins Buch.

    Ohne diese Regel bliebe das Buch offen, bis der erste Datensatz da ist.
    Die erste angelegte Medienserver-Verbindung wuerde dann eine Wanderung
    ausloesen, die nie gemeint war.
    """
    motor = eigene_installation(vollstaendig=False)

    db_modul.init_db()

    buch = _buch(motor)
    assert set(buch) == set(db_modul.EINMAL_SCHRITTE)
    assert set(buch.values()) == {"vorgefunden"}


# ---------------------------------------------------------------------------
# Was der Ankunftsbefund gesehen hat, muss nachlesbar sein
# ---------------------------------------------------------------------------
#
# ⚠️ **Die Entscheidung faellt je Datenbank genau einmal.** Danach liest
# ``init_db`` nur noch das Buch; nachstellen laesst sie sich nicht. War sie
# falsch - ein Konto steht auf "unbegrenzt", das nie so gemeint war -, ist das
# Protokoll das Einzige, woran sich das nachlesen laesst. Ein blosses "gilt als
# erledigt" beantwortet die Frage, die man dann stellt, gerade nicht: Woran
# wurde das erkannt?


def _wanderungszeilen(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        satz.getMessage()
        for satz in caplog.records
        if satz.getMessage().startswith("Migration ")
    ]


def test_ein_faelliger_schritt_wird_vor_dem_umzug_angekuendigt(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """**Vor** dem Umzug, nicht danach.

    Ein Eintrag nach getaner Arbeit erzaehlt nur von den Faellen, die
    durchgekommen sind. Bricht der Schritt mittendrin ab, ist diese Zeile der
    einzige Beleg dafuer, dass er ueberhaupt losgezogen ist - und die Probe
    daneben sagt, warum er losdurfte.
    """
    motor = eigene_installation(ohne_arr_managed=True)
    _konto_anlegen(motor, 0)

    with caplog.at_level(logging.INFO, logger="nexview.db"):
        db_modul.init_db()

    faellig = [
        zeile
        for zeile in _wanderungszeilen(caplog)
        if "_kontingente_dreiwertig_machen" in zeile and "will run now" in zeile
    ]
    assert len(faellig) == 1, _wanderungszeilen(caplog)
    # Und zwar mit der Probe, aus der das Urteil kam: Genau an der fehlenden
    # Spalte erkennt der Befund eine Datenbank von vor 0.19.
    assert "arr_managed=missing" in faellig[0]


def test_ein_vorgefundener_schritt_nennt_seine_probe(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """Die haeufigere Haelfte: Der Schritt laeuft **nicht**, und das ist die Aussage.

    Wer spaeter fragt, warum die alte 0 seines Kontos nie umgezogen ist, muss
    hier lesen koennen, dass Nexview die Datenbank fuer fertig gewandert
    hielt - und woran.
    """
    motor = eigene_installation()
    _konto_anlegen(motor, 0)

    with caplog.at_level(logging.INFO, logger="nexview.db"):
        db_modul.init_db()

    vorgefunden = [
        zeile
        for zeile in _wanderungszeilen(caplog)
        if "_kontingente_dreiwertig_machen" in zeile and "already done" in zeile
    ]
    assert len(vorgefunden) == 1, _wanderungszeilen(caplog)
    assert "arr_managed=present" in vorgefunden[0]
    assert "storage_enabled=gone" in vorgefunden[0]


def test_die_frische_installation_sagt_dass_sie_leer_war(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """⚠️ Sonst stuende im Protokoll einer brandneuen Installation viermal,
    ihre Wanderungen seien in einer **bestehenden** Datenbank wiedererkannt
    worden.

    Das ist die eine Auskunft, die hier nicht stimmt, und sie stand vorher da.
    Wer beim ersten Start hineinsieht, haelt seine leere Datenbank fuer eine
    geerbte - und traut dem Buch beim naechsten Raetsel nicht mehr.
    """
    eigene_installation(vollstaendig=False)

    with caplog.at_level(logging.INFO, logger="nexview.db"):
        db_modul.init_db()

    zeilen = _wanderungszeilen(caplog)
    assert len(zeilen) == len(db_modul.EINMAL_SCHRITTE), zeilen
    assert all("database was empty on arrival" in zeile for zeile in zeilen)


def test_beim_zweiten_start_schweigt_das_buch(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """⚠️ **Das ist die Haelfte, die das Mass haelt.**

    Der Befund entscheidet einmal, also wird er einmal aufgeschrieben. Eine
    Zeile, die bei jedem Start wiederkehrt und nie etwas bedeutet, macht die
    wichtigen unsichtbar - und dieselbe Meldung viermal je Neustart waere
    genau das.
    """
    eigene_installation()
    db_modul.init_db()

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="nexview.db"):
        db_modul.init_db()

    assert _wanderungszeilen(caplog) == []


# ---------------------------------------------------------------------------
# Die Meldung ueber die Nullen, die schon gekippt sind
# ---------------------------------------------------------------------------


def test_umgedeutete_nullen_werden_einmal_gemeldet(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """Der Schaden ist nicht mehr zu reparieren, also wird er wenigstens gesagt.

    ⚠️ **Nexview darf hier nichts von selbst geradebiegen.** Eine umgedeutete
    ``0`` steht in der Datenbank als dieselbe ``-1`` wie ein absichtlich
    gesetztes "unbegrenzt", und der alte Schritt hat sich keine Kennungen
    gemerkt. Wer das automatisch zuruecksetzt, sperrt Konten, die niemand
    sperren wollte.
    """
    motor = eigene_installation()
    _konto_anlegen(motor, -1)

    with caplog.at_level(logging.WARNING, logger="nexview.db"):
        db_modul.init_db()
    meldungen = [satz.getMessage() for satz in caplog.records if satz.levelno >= logging.WARNING]
    passend = [satz for satz in meldungen if "Storage limits" in satz]
    assert len(passend) == 1, meldungen
    assert "1 account(s)" in passend[0]

    # Und nur einmal: Beim naechsten Start steht der Schritt schon im Buch.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nexview.db"):
        db_modul.init_db()
    assert not [satz for satz in caplog.records if "Storage limits" in satz.getMessage()]


def test_ohne_betroffenes_konto_schweigt_die_meldung(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """⚠️ Eine Warnung, die auch dort erscheint, wo nichts passiert sein kann,
    liest beim dritten Mal niemand mehr.

    Steht nirgends eine ``-1``, kann auch nichts umgedeutet worden sein.
    """
    motor = eigene_installation()
    _konto_anlegen(motor, None)

    with caplog.at_level(logging.WARNING, logger="nexview.db"):
        db_modul.init_db()

    assert not [satz for satz in caplog.records if "Storage limits" in satz.getMessage()]


def test_beim_echten_umzug_gibt_es_keine_meldung(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """Wer gerade erst umzieht, hat nichts verloren - hier ist alles richtig gelaufen.

    Die Meldung haengt deshalb am Eintrag *vorgefunden*: Nur solche Datenbanken
    hatten den alten, bei jedem Start wiederkehrenden Schritt ueberhaupt.
    """
    motor = eigene_installation(ohne_arr_managed=True)
    _konto_anlegen(motor, 0)

    with caplog.at_level(logging.WARNING, logger="nexview.db"):
        db_modul.init_db()

    assert _grenze(motor) == -1
    assert not [satz for satz in caplog.records if "Storage limits" in satz.getMessage()]


# ---------------------------------------------------------------------------
# Der Waechter ueber die Klassifikation
# ---------------------------------------------------------------------------


def _init_db_rumpf() -> ast.FunctionDef:
    quelle = Path(db_modul.__file__).read_text(encoding="utf-8")
    for knoten in ast.parse(quelle).body:
        if isinstance(knoten, ast.FunctionDef) and knoten.name == "init_db":
            return knoten
    raise AssertionError("init_db nicht gefunden - wurde sie umbenannt?")


def _wurzel(knoten: ast.expr) -> ast.expr:
    """Der Anfang einer Kette wie ``Base.metadata.create_all``."""
    while isinstance(knoten, ast.Attribute):
        knoten = knoten.value
    return knoten


def _schritte_in_init_db() -> tuple[set[str], set[str]]:
    """Alle Schritte im Rumpf von ``init_db`` - und die davon durchs Buch gesperrten.

    Uebersprungen wird zweierlei, beides mit Grund: Aufrufe an ``logger`` sind
    Protokollzeilen und keine Schritte, und ein Aufruf an einer Zeichenkette
    (``", ".join(...)``) baut den Text fuer eben diese Zeile.

    ``_einmal(_x, gelaufen)`` zaehlt als Aufruf von ``_x`` - der Wickel ist ja
    gerade die Sperre, um die es hier geht.
    """
    alle: set[str] = set()
    durch_das_buch: set[str] = set()

    for knoten in ast.walk(_init_db_rumpf()):
        if not isinstance(knoten, ast.Call):
            continue
        ziel = knoten.func
        if isinstance(ziel, ast.Name):
            if ziel.id == "_einmal":
                schritt = knoten.args[0]
                assert isinstance(schritt, ast.Name), ast.unparse(knoten)
                alle.add(schritt.id)
                durch_das_buch.add(schritt.id)
            else:
                alle.add(ziel.id)
        elif isinstance(ziel, ast.Attribute):
            wurzel = _wurzel(ziel)
            if isinstance(wurzel, ast.Name) and wurzel.id == "logger":
                continue
            if isinstance(wurzel, ast.Constant):
                continue
            alle.add(ziel.attr)

    return alle, durch_das_buch


def test_jeder_schritt_in_init_db_ist_klassifiziert() -> None:
    """⚠️ **Wird dieser Test rot, heisst die Antwort nicht "nachtragen".**

    Sie heisst: **entscheiden, und im Zweifel Pflege.** Ein Schritt gehoert
    nur dann ins Buch, wenn ein zweiter Lauf Schaden anrichtet. Wer einen
    Pflegeschritt dort eintraegt, schaltet eine Reparatur ab, die noch
    gebraucht wird - ``_altersgrenzen_aufraeumen`` etwa faengt jedes
    Kinderkonto ab, das spaeter hochgestuft wird, und
    ``_gesehen_herkunft_nachtragen`` bekommt vom laufenden Abgleich staendig
    neue Marker ohne Herkunft. Beides faellt niemandem auf, und nichts
    protokolliert es.

    Umgekehrt genauso: Ein neuer Schritt, der Bestandsdaten umdeutet, gehoert
    ins Buch. Ohne diese Entscheidung wiederholt sich genau der Fehler, wegen
    dem es das Buch gibt.
    """
    alle, durch_das_buch = _schritte_in_init_db()

    # Der Waechter muss ueberhaupt etwas sehen. Ohne diese Schwelle bliebe er
    # still gruen, sobald jemand ``init_db`` anders schreibt.
    assert len(alle) >= 12, f"Nur {len(alle)} Schritte gefunden: {sorted(alle)}"

    klassifiziert = set(db_modul.EINMAL_SCHRITTE) | set(db_modul.PFLEGE_SCHRITTE)
    unentschieden = alle - klassifiziert
    assert not unentschieden, (
        f"Nicht klassifizierte Schritte in init_db: {sorted(unentschieden)}. "
        "Die Antwort heisst nicht 'nachtragen', sondern 'entscheiden, und im "
        "Zweifel Pflege' - siehe EINMAL_SCHRITTE und PFLEGE_SCHRITTE in app/db.py."
    )

    doppelt = set(db_modul.EINMAL_SCHRITTE) & set(db_modul.PFLEGE_SCHRITTE)
    assert not doppelt, f"In beiden Listen: {sorted(doppelt)}"

    # Und die Einordnung muss auch wirken: Was einmalig ist, laeuft durch
    # ``_einmal`` - und nur das.
    assert durch_das_buch == set(db_modul.EINMAL_SCHRITTE), (
        f"Durch das Buch gesperrt: {sorted(durch_das_buch)}, "
        f"als einmalig eingeordnet: {sorted(db_modul.EINMAL_SCHRITTE)}"
    )


def test_das_buch_traegt_eigene_spaltennamen() -> None:
    """⚠️ Sonst macht die neue Tabelle den Waechter ueber das Betreiberkonto stumpf.

    ``test_betreiber_waechter`` leitet die ueberwachten Kontofelder ab, indem
    es die Spalten **aller anderen** Tabellen abzieht. Haette das Buch
    ``name``, ``version`` oder ``created_at``, fielen diese Namen dort heraus,
    und niemand wuerde es merken.
    """
    eigene = {spalte.name for spalte in Wanderung.__table__.columns}
    fremde = {
        spalte.name
        for tabelle in Base.metadata.tables.values()
        if tabelle is not Wanderung.__table__
        for spalte in tabelle.columns
    }
    assert not (eigene & fremde), f"Geteilte Spaltennamen: {sorted(eigene & fremde)}"


# ---------------------------------------------------------------------------
# Die Pflege bleibt Pflege
# ---------------------------------------------------------------------------


def test_die_altersgrenze_wird_auch_spaeter_noch_geraeumt(admin_client: TestClient) -> None:
    """``_altersgrenzen_aufraeumen`` sieht wie eine Wanderung aus und ist keine.

    Ein Kinderkonto laesst sich zum vollwertigen Konto hochstufen, und sein
    Alter bleibt dabei stehen. Waere der Schritt ins Buch gewandert, bliebe
    dieses Konto fuer immer altersbeschraenkt - und duerfte nebenbei nie
    wieder Kinder fuehren.
    """
    eltern = create_user(admin_client, "elternteil", "passwort-1234", can_manage_children=True)
    kind = create_user(
        admin_client,
        "kind",
        "passwort-1234",
        role=Role.child,
        age=12,
        parent_id=eltern["id"],
    )

    with db_modul.SessionLocal() as sitzung:
        person = sitzung.get(User, kind["id"])
        person.role = Role.user
        person.parent_id = None
        sitzung.commit()

    db_modul.init_db()

    with db_modul.SessionLocal() as sitzung:
        assert sitzung.get(User, kind["id"]).age is None


def test_ein_gesehen_marker_ohne_herkunft_wird_auch_spaeter_nachgetragen(
    admin_client: TestClient,
) -> None:
    """``_gesehen_herkunft_nachtragen`` ebenso.

    Der laufende Abgleich legt weiterhin Marker ohne Herkunft an. Als
    Einmal-Schritt eingetragen blieben die fuer immer herrenlos, und der
    Abgleich zweier Server koennte sie nicht mehr zuordnen.

    ⚠️ **Neben dem herrenlosen Marker steht ein zugeordneter.** Nur die leere
    Herkunft darf gefuellt werden; ein Nachtragen, das jeden Marker auf den
    gerade verbundenen Server umschreibt, saehe an der einen Zeile genauso
    aus - und wuerde die Zuordnung aller anderen zerstoeren.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    with db_modul.engine.begin() as verbindung:
        verbindung.exec_driver_sql(
            "INSERT INTO media_server_connections"
            " (provider, machine_id, name, url, token, account_id, connected_at)"
            " VALUES ('plex', 'maschine-1', 'Wohnzimmer', '', '', '', '2026-01-01 12:00:00')"
        )
    with db_modul.SessionLocal() as sitzung:
        sitzung.add(
            UserWatched(user_id=konto["id"], media_type="movie", tmdb_id=4711, providers="")
        )
        sitzung.add(
            UserWatched(
                user_id=konto["id"], media_type="movie", tmdb_id=4712, providers="jellyfin"
            )
        )
        sitzung.commit()

    db_modul.init_db()

    with db_modul.SessionLocal() as sitzung:
        marker = sitzung.query(UserWatched).filter(UserWatched.tmdb_id == 4711).one()
        assert marker.providers == "plex"
        zugeordnet = sitzung.query(UserWatched).filter(UserWatched.tmdb_id == 4712).one()
        assert zugeordnet.providers == "jellyfin", (
            "das Nachtragen hat einen Marker umgeschrieben, der schon eine Herkunft hatte"
        )


def test_eine_unbekannte_meldungsart_wird_auch_spaeter_noch_geraeumt(
    admin_client: TestClient,
) -> None:
    """``_verwaiste_meldungsarten_aufraeumen`` ebenso.

    Jede kuenftige Umbenennung einer Meldungsart erzeugt neue solche Zeilen,
    und **eine einzige** legt die Glocke eines Kontos ganz lahm. Die
    vorhandenen Tests dazu laufen nur auf der nachgebauten alten Datenbank -
    eine Fehleinordnung wuerde ihnen deshalb entgehen.

    ⚠️ **Neben der verwaisten Zeile steht eine gesunde.** Ein Aufraeumen, das
    schlicht alles loescht, raeumt die verwaiste ja auch weg - erst die
    ueberlebende Nachbarzeile zeigt, dass das DELETE seine Grenze kennt.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    with db_modul.engine.begin() as verbindung:
        verbindung.exec_driver_sql(
            "INSERT INTO notifications"
            " (user_id, type, message_key, is_read, created_at, mail_pending, mail_attempts)"
            " VALUES (?, 'watchlist_imported', 'alt', 0, '2026-01-01 12:00:00', 0, 0)",
            (konto["id"],),
        )
        verbindung.exec_driver_sql(
            "INSERT INTO notifications"
            " (user_id, type, message_key, is_read, created_at, mail_pending, mail_attempts)"
            " VALUES (?, 'feedback', 'echt', 0, '2026-01-02 12:00:00', 0, 0)",
            (konto["id"],),
        )

    db_modul.init_db()

    with db_modul.engine.connect() as verbindung:
        uebrig = dict(
            verbindung.exec_driver_sql(
                "SELECT type, COUNT(*) FROM notifications GROUP BY type"
            ).all()
        )
    assert uebrig.get("watchlist_imported", 0) == 0
    assert uebrig.get("feedback") == 1, (
        "das Aufraeumen hat auch die gueltige Meldung mitgenommen"
    )
