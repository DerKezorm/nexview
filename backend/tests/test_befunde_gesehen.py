"""Das Abzeichen am Menü zählt Ungelesenes, nicht Probleme.

Befunde sind **Zustände**: „sucht seit über 14 Tagen" ist morgen genauso wahr.
Am reinen Zähler hing das Abzeichen deshalb dauerhaft auf derselben Ziffer,
auch nachdem jemand nachgesehen hatte. Es sagte damit nichts mehr, und eines,
das immer leuchtet, sieht bald niemand mehr an.

Die Befunde selbst bleiben stehen, solange sie zutreffen. Nur das Abzeichen
geht auf null, sobald das Dashboard geöffnet war.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.db import SessionLocal, engine
from app.models import BefundGesehen, Role, User
from app.security import hash_password
from app.services import befunde
from app.services.befunde import Befund, Bereich, Schwere


def _befund(kennung: str, anzahl: int | None = None, zusatz: str = "") -> Befund:
    return Befund(
        kennung=kennung,
        schwere=Schwere.fehler,
        bereich=Bereich.nachschub,
        werte={"anzahl": anzahl} if anzahl is not None else {},
        zusatz=zusatz,
    )


def _betreiber(db, name: str = "chefin") -> User:
    person = User(
        username=name,
        email=f"{name}@beispiel.de",
        password_hash=hash_password("passwort-1234"),
        role=Role.admin,
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


# --- Der Kern ----------------------------------------------------------------


def test_alles_ist_ungesehen_bis_jemand_hinsieht() -> None:
    with SessionLocal() as db:
        chef = _betreiber(db)
        liste = [_befund("nachschub.haengt", 1), _befund("arr.stumm")]

        assert len(befunde.ungesehen(db, chef.id, liste)) == 2


def test_nach_dem_hinsehen_ist_nichts_mehr_ungesehen() -> None:
    """⚠️ Das ist der ganze Punkt: Der Zähler geht auf null."""
    with SessionLocal() as db:
        chef = _betreiber(db)
        liste = [_befund("nachschub.haengt", 1), _befund("arr.stumm")]

        befunde.als_gesehen(db, chef.id, liste)

        assert befunde.ungesehen(db, chef.id, liste) == []


def test_die_befunde_selbst_bleiben_stehen() -> None:
    """Hinsehen heißt nicht erledigt - das Problem ist ja noch da."""
    with SessionLocal() as db:
        chef = _betreiber(db)
        liste = [_befund("nachschub.haengt", 1)]

        befunde.als_gesehen(db, chef.id, liste)

        # ``sammeln`` liefert weiterhin; nur ``ungesehen`` ist leer.
        assert befunde.zaehlen(liste)["fehler"] == 1


def test_ein_neuer_befund_meldet_sich_wieder() -> None:
    with SessionLocal() as db:
        chef = _betreiber(db)
        befunde.als_gesehen(db, chef.id, [_befund("nachschub.haengt", 1)])

        spaeter = [_befund("nachschub.haengt", 1), _befund("sicherung.faellig")]

        offen = befunde.ungesehen(db, chef.id, spaeter)
        assert [b.kennung for b in offen] == ["sicherung.faellig"]


# --- Wenn es schlimmer wird --------------------------------------------------


def test_ein_zweiter_haengender_titel_meldet_sich_wieder() -> None:
    """⚠️ Sonst versteckte ein einziges Hinsehen jede künftige Verschlechterung.

    Der Befund bündelt: Sein Schlüssel bleibt ``nachschub.haengt``, egal ob ein
    Titel hängt oder fünf. Nur die Anzahl verrät den Unterschied.
    """
    with SessionLocal() as db:
        chef = _betreiber(db)
        befunde.als_gesehen(db, chef.id, [_befund("nachschub.haengt", 1)])

        offen = befunde.ungesehen(db, chef.id, [_befund("nachschub.haengt", 2)])

        assert len(offen) == 1


def test_weniger_meldet_sich_nicht_wieder() -> None:
    """Es wird besser - das ist keine Nachricht wert."""
    with SessionLocal() as db:
        chef = _betreiber(db)
        befunde.als_gesehen(db, chef.id, [_befund("nachschub.haengt", 3)])

        assert befunde.ungesehen(db, chef.id, [_befund("nachschub.haengt", 1)]) == []


def test_wachsende_zeitangaben_wecken_das_abzeichen_nicht() -> None:
    """⚠️ Manche Befunde tragen Zahlen, die von selbst wachsen.

    „Seit X Tagen", „X Minuten", „X Bytes". Hinge das Abzeichen daran, schaltete
    es sich jeden Tag von selbst wieder ein - und wäre damit genauso wertlos wie
    vorher, nur auf umständlichere Weise.
    """
    with SessionLocal() as db:
        chef = _betreiber(db)
        vorher = Befund(
            kennung="sicherung.alt",
            schwere=Schwere.fehler,
            bereich=Bereich.nachschub,
            werte={"tage": 8},
        )
        befunde.als_gesehen(db, chef.id, [vorher])

        spaeter = Befund(
            kennung="sicherung.alt",
            schwere=Schwere.fehler,
            bereich=Bereich.nachschub,
            werte={"tage": 30},
        )
        assert befunde.ungesehen(db, chef.id, [spaeter]) == []


# --- Aufräumen und Abgrenzung ------------------------------------------------


def test_was_nicht_mehr_zutrifft_wird_vergessen() -> None:
    """⚠️ Sonst käme ein Problem, das nach Monaten wiederkehrt, stumm zurück.

    Vermerkt wäre es ja noch. Und die Tabelle wüchse mit jedem Befund, den es
    je gab.
    """
    with SessionLocal() as db:
        chef = _betreiber(db)
        befunde.als_gesehen(db, chef.id, [_befund("arr.stumm"), _befund("sicherung.alt")])

        # Nur noch einer trifft zu.
        befunde.als_gesehen(db, chef.id, [_befund("arr.stumm")])

        zeilen = db.scalars(
            select(BefundGesehen).where(BefundGesehen.user_id == chef.id)
        ).all()
        assert [z.schluessel for z in zeilen] == ["arr.stumm"]

        # Und der Verschwundene meldet sich wieder, wenn er zurückkommt.
        assert len(befunde.ungesehen(db, chef.id, [_befund("sicherung.alt")])) == 1


def test_jeder_betreiber_sieht_fuer_sich() -> None:
    """Was der eine gesehen hat, hat der andere nicht."""
    with SessionLocal() as db:
        eine = _betreiber(db, "chefin")
        andere = _betreiber(db, "kollege")
        liste = [_befund("nachschub.haengt", 1)]

        befunde.als_gesehen(db, eine.id, liste)

        assert befunde.ungesehen(db, eine.id, liste) == []
        assert len(befunde.ungesehen(db, andere.id, liste)) == 1


def test_derselbe_befund_je_instanz_zaehlt_getrennt() -> None:
    """``zusatz`` macht den Schlüssel eindeutig - zwei Instanzen, zwei Nachrichten."""
    with SessionLocal() as db:
        chef = _betreiber(db)
        eine = _befund("arr.stumm", zusatz="radarr")
        andere = _befund("arr.stumm", zusatz="sonarr")

        befunde.als_gesehen(db, chef.id, [eine])

        offen = befunde.ungesehen(db, chef.id, [eine, andere])
        assert [b.schluessel for b in offen] == ["arr.stumm|sonarr"]


# --- Zwei Aufrufe zugleich ---------------------------------------------------


def test_zwei_aufrufe_zugleich_vermerken_ohne_fehler() -> None:
    """⚠️ Zwei Aufrufe zugleich sind der Normalfall, kein Randfall.

    React ruft Effekte im Entwicklungsmodus doppelt auf, zwei offene Tabs tun
    dasselbe. Am 13.09.2026 antwortete ``POST /api/admin/dashboard/gesehen``
    darauf mit HTTP 500: Beide Aufrufe lasen „fehlt", beide legten an, und der
    zweite scheiterte am eindeutigen Index.

    Der eine Aufruf läuft hier genau vor dem ersten Schreibbefehl des anderen.
    Das trifft den alten Ablauf (erst lesen, dann anlegen) genauso wie einen,
    der gar nicht erst liest. Der Zähler beweist, dass es wirklich geschah.
    """
    with SessionLocal() as vorbereitung:
        chef_id = _betreiber(vorbereitung).id

    dazwischen: list[str] = []
    zweite_verbindung: list[object] = []

    def vor_dem_schreiben(conn, cursor, statement, parameters, context, executemany) -> None:
        if dazwischen or not zweite_verbindung or conn is not zweite_verbindung[0]:
            return
        if statement.lstrip().split(None, 1)[0].upper() not in {"INSERT", "UPDATE", "DELETE"}:
            return
        dazwischen.append(statement)
        with SessionLocal() as erste:
            befunde.als_gesehen(
                erste, chef_id, [_befund("nachschub.haengt", 1), _befund("arr.stumm")]
            )

    # Der Horcher hängt an der ganzen Engine. Der eingeschobene Aufruf läuft
    # über eine eigene Verbindung und fällt deshalb schon an der ersten
    # Bedingung im Horcher heraus.
    event.listen(engine, "before_cursor_execute", vor_dem_schreiben)
    try:
        with SessionLocal() as zweite:
            zweite_verbindung.append(zweite.connection())
            befunde.als_gesehen(
                zweite, chef_id, [_befund("nachschub.haengt", 2), _befund("arr.stumm")]
            )
    finally:
        event.remove(engine, "before_cursor_execute", vor_dem_schreiben)

    assert len(dazwischen) == 1, "der erste Aufruf kam gar nicht dazwischen"
    with SessionLocal() as db:
        zeilen = {
            z.schluessel: z.anzahl
            for z in db.scalars(select(BefundGesehen).where(BefundGesehen.user_id == chef_id))
        }
    # Der spätere gewinnt, und keiner fehlt.
    assert zeilen == {"nachschub.haengt": 2, "arr.stumm": None}


def test_erneutes_hinsehen_frischt_anzahl_und_zeitpunkt_auf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eine schon vermerkte Zeile bekommt die neue Anzahl und den neuen Zeitpunkt.

    Bliebe die alte Anzahl stehen, meldete sich ein Befund, der von eins auf
    drei wuchs und dann auf zwei fiel, fälschlich wieder als ungesehen.
    """
    frueh = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    spaet = datetime(2026, 9, 13, 9, 30, tzinfo=UTC)
    with SessionLocal() as db:
        chef_id = _betreiber(db).id
        monkeypatch.setattr(befunde, "utcnow", lambda: frueh)
        befunde.als_gesehen(db, chef_id, [_befund("nachschub.haengt", 1)])
        monkeypatch.setattr(befunde, "utcnow", lambda: spaet)
        befunde.als_gesehen(db, chef_id, [_befund("nachschub.haengt", 3)])

    with SessionLocal() as db:
        (zeile,) = db.scalars(
            select(BefundGesehen).where(BefundGesehen.user_id == chef_id)
        ).all()
    assert zeile.anzahl == 3
    assert zeile.gesehen_am.replace(tzinfo=None) == spaet.replace(tzinfo=None)


def test_eine_festgehaltene_zeile_zeigt_danach_den_neuen_stand() -> None:
    """Eine Zeile, die jemand aus derselben Sitzung in der Hand hält, zieht mit.

    Die Sitzung läuft mit ``expire_on_commit=False`` und frischt geladene
    Objekte nicht von selbst auf. Der alte Ablauf änderte genau diese Objekte;
    ein Upsert, das an ihnen vorbei schreibt, ließe sie mit der alten Anzahl
    stehen. Hält niemand sie fest, fällt das nicht auf, weil die Sitzung nur
    schwach auf ihre Objekte verweist. Deshalb hält dieser Test eine fest.
    """
    with SessionLocal() as db:
        chef_id = _betreiber(db).id
        befunde.als_gesehen(db, chef_id, [_befund("nachschub.haengt", 1)])
        (zeile,) = db.scalars(
            select(BefundGesehen).where(BefundGesehen.user_id == chef_id)
        ).all()

        befunde.als_gesehen(db, chef_id, [_befund("nachschub.haengt", 3)])

        assert zeile.anzahl == 3


# --- Über die echte Adresse --------------------------------------------------


@pytest.fixture
def mit_befund(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Befund, der zutrifft.

    ⚠️ **Ohne den beweisen die Tests unten nichts.** Eine frische
    Testdatenbank hat keine Befunde; „ungesehen == 0" wäre dann immer wahr,
    und der Fallstrick - dass schon das Abfragen als gesehen gilt - fiele
    nicht auf. Genau das hat die Mutationsprobe gezeigt.
    """
    from app.routers import dashboard as router

    monkeypatch.setattr(
        router.befunde_service, "sammeln", lambda db, settings: [_befund("arr.stumm")]
    )


def test_das_abfragen_allein_vermerkt_nichts(
    admin_client: TestClient, mit_befund: None
) -> None:
    """⚠️ Der Fallstrick, an dem die ganze Sache hängt.

    Das Menü fragt ``GET /api/admin/dashboard`` im Minutentakt ab. Zählte schon
    das Abfragen als gesehen, wäre das Abzeichen nie zu sehen - es ginge auf
    null, bevor irgendjemand hingeschaut hat.
    """
    erste = admin_client.get("/api/admin/dashboard").json()
    zweite = admin_client.get("/api/admin/dashboard").json()

    assert erste["ungesehen"] == 1, "der Testbefund kam gar nicht an"
    assert zweite["ungesehen"] == 1, "schon das Abfragen hat ihn als gesehen vermerkt"


def test_das_oeffnen_setzt_den_zaehler_auf_null(
    admin_client: TestClient, mit_befund: None
) -> None:
    assert admin_client.get("/api/admin/dashboard").json()["ungesehen"] == 1

    admin_client.post("/api/admin/dashboard/gesehen")

    assert admin_client.get("/api/admin/dashboard").json()["ungesehen"] == 0


def test_die_befunde_bleiben_in_der_antwort(
    admin_client: TestClient, mit_befund: None
) -> None:
    """Der Zähler geht auf null, die Liste nicht."""
    vorher = admin_client.get("/api/admin/dashboard").json()
    admin_client.post("/api/admin/dashboard/gesehen")
    nachher = admin_client.get("/api/admin/dashboard").json()

    assert len(nachher["befunde"]) == len(vorher["befunde"])
    assert nachher["zaehler"] == vorher["zaehler"]


def test_nur_betreiber_duerfen_vermerken(client: TestClient) -> None:
    assert client.post("/api/admin/dashboard/gesehen").status_code == 401
