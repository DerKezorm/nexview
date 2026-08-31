"""Das geschuetzte Betreiberkonto - alle Wege einmal durchgespielt.

Der Waechter (``test_betreiber_waechter.py``) fragt, ob **ueberall** eine
Entscheidung getroffen wurde. Hier steht, was die Entscheidungen im Einzelnen
bewirken - Fall fuer Fall, so wie sie im Betrieb vorkommen.

⚠️ **Ein Haken am Konto, keine vierte Rolle.** Faellt jemandem ein Test ein, der
zeigt, dass der Betreiber *mehr darf* als ein anderer Administrator, dann ist
nicht der Test falsch, sondern der Code.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.models import Role, User
from app.services import betreiber

from .conftest import ADMIN, auth_headers, create_user


def _konto(username: str) -> User:
    with SessionLocal() as db:
        return db.query(User).filter(User.username == username).one()


def _ist_betreiber(username: str) -> bool:
    return _konto(username).is_betreiber


@pytest.fixture()
def zweiter_admin(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
    return auth_headers(admin_client, "zweiter", "zweites-passwort")


@pytest.fixture()
def umgebung_leer() -> Iterator[None]:
    """``NEXVIEW_BETREIBER`` ist im Regelfall nicht gesetzt.

    ``get_settings`` ist zwischengespeichert - der Wert wird deshalb direkt am
    Einstellungsobjekt gesetzt und hinterher zurueckgedreht. Ueber die
    Umgebungsvariable ginge es nur mit geleertem Zwischenspeicher, und der
    haengt an der halben Anwendung.
    """
    einstellungen = get_settings()
    vorher = einstellungen.betreiber
    einstellungen.betreiber = ""
    yield
    einstellungen.betreiber = vorher


pytestmark = pytest.mark.usefixtures("umgebung_leer")


# ---------------------------------------------------------------------------
# Wer bekommt ihn ueberhaupt
# ---------------------------------------------------------------------------


class TestWerIhnBekommt:
    def test_der_erste_administrator_aus_dem_assistenten(
        self, admin_client: TestClient
    ) -> None:
        """Der Regelfall: eine neue Installation braucht nichts einzutragen."""
        assert _ist_betreiber(ADMIN["username"])

        antwort = admin_client.get("/api/users/betreiber")
        assert antwort.status_code == 200
        assert antwort.json()["username"] == ADMIN["username"]
        assert antwort.json()["aus_umgebung"] is False

    def test_bestehende_installation_bekommt_den_aeltesten_aktiven_admin(
        self, admin_client: TestClient
    ) -> None:
        """Das Update: niemand traegt ihn, also der aelteste aktive Administrator.

        Nachgestellt wie im Betrieb - der Haken wird abgeraeumt, als haette es
        die Spalte nie gegeben, und dann laeuft der Startweg.
        """
        create_user(admin_client, "spaeter", "spaeteres-passwort", role=Role.admin)
        with SessionLocal() as db:
            for konto in db.query(User).all():
                konto.is_betreiber = False
            db.commit()

        with SessionLocal() as db:
            betreiber.beim_start(db)

        assert _ist_betreiber(ADMIN["username"])
        assert not _ist_betreiber("spaeter")

    def test_ohne_aktiven_administrator_bleibt_er_unvergeben(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Lieber unvergeben als still jemanden ernennen.

        Die Datenbank kann "hoechstens einer" erzwingen, "mindestens einer"
        nicht. Dieser Zustand ist vorgesehen - und die Uebersicht muss ihn
        zeigen koennen, statt zu schweigen.
        """
        create_user(admin_client, "nurnutzer", "irgendein-passwort")
        with SessionLocal() as db:
            for konto in db.query(User).all():
                konto.is_betreiber = False
                if konto.role == Role.admin:
                    konto.is_active = False
            db.commit()

        with SessionLocal() as db:
            betreiber.beim_start(db)
            assert betreiber.traeger(db) is None

    def test_ein_neuer_administrator_bekommt_ihn_nicht(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """Wer ernannt wird, bekommt Administratorrechte - nicht den Haken."""
        assert not _ist_betreiber("zweiter")
        assert _ist_betreiber(ADMIN["username"])


# ---------------------------------------------------------------------------
# Was ein zweiter Administrator nicht mehr kann
# ---------------------------------------------------------------------------


class TestWasEinZweiterAdminNichtKann:
    """Admin B gegen Betreiber A - an der Oberflaeche vorbei, direkt an der Adresse."""

    def test_deaktivieren(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        a = _konto(ADMIN["username"]).id
        antwort = admin_client.patch(
            f"/api/users/{a}", json={"is_active": False}, headers=zweiter_admin
        )
        assert antwort.status_code == 403
        assert antwort.json()["detail"]["code"] == "betreiber_geschuetzt"
        assert _konto(ADMIN["username"]).is_active is True

    def test_herabstufen(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        a = _konto(ADMIN["username"]).id
        assert (
            admin_client.patch(
                f"/api/users/{a}", json={"role": "user"}, headers=zweiter_admin
            ).status_code
            == 403
        )
        assert _konto(ADMIN["username"]).role == Role.admin

    def test_passwort_setzen(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """⚠️ Die schlimmste der fuenf Tueren - und bis 0.25 die einzige ganz offene.

        Sie hatte nicht einmal die Bremse fuer den letzten Administrator. Admin B
        musste A gar nicht hinauswerfen: Er setzte ein Passwort und **wurde** A.
        """
        a = _konto(ADMIN["username"])
        vorher = a.password_hash
        antwort = admin_client.post(
            f"/api/users/{a.id}/password",
            json={"password": "uebernommen-123"},
            headers=zweiter_admin,
        )
        assert antwort.status_code == 403
        assert _konto(ADMIN["username"]).password_hash == vorher

    def test_loeschen(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        a = _konto(ADMIN["username"]).id
        antwort = admin_client.request(
            "DELETE", f"/api/users/{a}", json={}, headers=zweiter_admin
        )
        assert antwort.status_code == 403
        assert _konto(ADMIN["username"]) is not None

    def test_kontingente_umstellen(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        a = _konto(ADMIN["username"]).id
        assert (
            admin_client.patch(
                f"/api/users/{a}", json={"quota_movies_limit": 0}, headers=zweiter_admin
            ).status_code
            == 403
        )
        assert (
            admin_client.post(
                f"/api/users/{a}/quota/reset", headers=zweiter_admin
            ).status_code
            == 403
        )
        assert (
            admin_client.post(
                f"/api/users/{a}/storage/reset", headers=zweiter_admin
            ).status_code
            == 403
        )

    def test_den_haken_an_sich_selbst_geben(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """Es gibt keinen Weg, sich den Haken zu holen - nur einen, ihn zu geben."""
        b = _konto("zweiter").id
        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": b}, headers=zweiter_admin
        )
        assert antwort.status_code == 403
        assert antwort.json()["detail"]["code"] == "betreiber_not_owner"
        assert _ist_betreiber(ADMIN["username"])


# ---------------------------------------------------------------------------
# Was er weiterhin kann
# ---------------------------------------------------------------------------


class TestWasEinZweiterAdminWeiterhinKann:
    """⚠️ Die Gegenprobe. Ein Schutz, der zu viel sperrt, ist genauso kaputt."""

    def test_alle_anderen_konten_wie_bisher(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        create_user(admin_client, "gast", "gast-passwort")
        gast = _konto("gast").id

        assert (
            admin_client.patch(
                f"/api/users/{gast}", json={"is_active": False}, headers=zweiter_admin
            ).status_code
            == 200
        )
        assert (
            admin_client.post(
                f"/api/users/{gast}/password",
                json={"password": "neues-passwort"},
                headers=zweiter_admin,
            ).status_code
            == 204
        )

    def test_auch_ein_anderer_administrator(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """Der Haken schuetzt **ein** Konto, nicht den Rang.

        Ein dritter Administrator bleibt so abwaehlbar wie zuvor - das ist die
        alte Regel, und sie bleibt. Nur der Betreiber steht ausserhalb.
        """
        create_user(admin_client, "dritter", "drittes-passwort", role=Role.admin)
        dritter = _konto("dritter").id
        assert (
            admin_client.patch(
                f"/api/users/{dritter}", json={"is_active": False}, headers=zweiter_admin
            ).status_code
            == 200
        )

    def test_der_betreiber_bekommt_keine_zusaetzlichen_rechte(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Die wichtigste Regel des ganzen Umbaus.

        Der Haken sagt ausschliesslich, was **andere** nicht duerfen. Er gibt
        keine automatische Freigabe, keinen Kontingent-Freifahrtschein und keine
        Macht ueber fremde Schluessel. Geprueft wird das an den Feldern selbst:
        Der Betreiber sieht aus wie jeder andere Administrator.
        """
        create_user(admin_client, "vergleich", "vergleichs-passwort", role=Role.admin)
        a = _konto(ADMIN["username"])
        b = _konto("vergleich")

        for feld in (
            "auto_approve_uhd",
            "can_request_uhd_movies",
            "can_request_uhd_series",
            "quota_movies_limit",
            "quota_series_limit",
            "storage_limit_gb",
            "can_manage_children",
        ):
            assert getattr(a, feld) == getattr(b, feld), feld

        # Und die Adresse, die fremde Schluessel widerrufen wuerde, gibt es
        # weiterhin nicht - auch nicht fuer den Betreiber.
        from app.main import app

        pfade = {str(getattr(r, "path", "")) for r in app.routes}
        assert "/api/users/{user_id}/api-schluessel/{schluessel_id}" not in pfade


# ---------------------------------------------------------------------------
# Die Uebergabe
# ---------------------------------------------------------------------------


class TestUebergabe:
    def test_der_traeger_gibt_weiter_und_kommt_nicht_zurueck(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """A gibt an B - und A kann es sich danach nicht zurueckholen."""
        a = _konto(ADMIN["username"]).id
        b = _konto("zweiter").id

        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": b}
        )
        assert antwort.status_code == 200, antwort.text
        assert antwort.json()["username"] == "zweiter"
        assert _ist_betreiber("zweiter")
        assert not _ist_betreiber(ADMIN["username"])

        # A ist jetzt ein gewoehnlicher Administrator.
        zurueck = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": a}
        )
        assert zurueck.status_code == 403
        assert zurueck.json()["detail"]["code"] == "betreiber_not_owner"
        assert _ist_betreiber("zweiter")

    def test_die_uebergabe_ueberlebt_einen_neustart(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """⚠️ Diese Luecke hat erst die Mutationsprobe gezeigt.

        ``beim_start`` ernennt den aeltesten aktiven Administrator, wenn niemand
        den Haken traegt. Die Zeile davor - "traegt ihn schon jemand? dann Finger
        weg" - sah aus wie eine Selbstverstaendlichkeit und war von **keinem**
        Test gedeckt. Ohne sie holte sich jeder Neustart den Haken zum aeltesten
        Konto zurueck, und die Uebergabe waere bis zum naechsten Update gueltig
        gewesen. Niemand haette das mit dem Neustart in Verbindung gebracht.

        Der Neustart wird hier durch ``beim_start`` selbst nachgestellt - genau
        das ruft ``init_db`` beim Hochfahren auf.
        """
        b = _konto("zweiter").id
        assert (
            admin_client.post(
                "/api/users/betreiber/uebergeben", json={"user_id": b}
            ).status_code
            == 200
        )

        for _ in range(3):
            with SessionLocal() as db:
                betreiber.beim_start(db)

        assert _ist_betreiber("zweiter"), "Der Neustart hat die Übergabe zurückgedreht."
        assert not _ist_betreiber(ADMIN["username"])

    def test_und_danach_ist_a_selbst_angreifbar(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """Die Kehrseite, die in der Warnung stehen muss: A verliert den Schutz.

        Nach der Uebergabe kann B den bisherigen Betreiber A deaktivieren wie
        jeden anderen Administrator. Genau deshalb ist die Uebergabe eine
        Entscheidung mit Warnung und nicht ein Schalter.
        """
        a = _konto(ADMIN["username"]).id
        b = _konto("zweiter").id
        admin_client.post("/api/users/betreiber/uebergeben", json={"user_id": b})

        antwort = admin_client.patch(
            f"/api/users/{a}", json={"is_active": False}, headers=zweiter_admin
        )
        assert antwort.status_code == 200, antwort.text

    def test_nicht_an_ein_gewoehnliches_konto(self, admin_client: TestClient) -> None:
        create_user(admin_client, "gast", "gast-passwort")
        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": _konto("gast").id}
        )
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "betreiber_nur_admin"
        assert _ist_betreiber(ADMIN["username"])

    def test_nicht_an_einen_entscheider(self, admin_client: TestClient) -> None:
        """Ein Entscheider darf ueber Anfragen bestimmen, nicht ueber das Haus."""
        create_user(admin_client, "chef", "chef-passwort", role=Role.approver)
        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": _konto("chef").id}
        )
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "betreiber_nur_admin"

    def test_nicht_an_ein_kinderkonto(self, admin_client: TestClient) -> None:
        """Ein Kind ist nie Administrator - die Rollenpruefung faengt es mit ab."""
        create_user(admin_client, "kind", "kind-passwort", role=Role.child)
        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": _konto("kind").id}
        )
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "betreiber_nur_admin"
        assert _konto("kind").is_betreiber is False

    def test_nicht_an_sich_selbst(self, admin_client: TestClient) -> None:
        a = _konto(ADMIN["username"]).id
        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": a}
        )
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "betreiber_selbst"
        assert _ist_betreiber(ADMIN["username"])

    def test_nicht_an_ein_deaktiviertes_konto(self, admin_client: TestClient) -> None:
        """Sonst traegt den Haken jemand, der nicht mehr hereinkommt.

        Das waere dieselbe Sackgasse wie ein ausgesperrter Betreiber - nur
        schneller erreicht.
        """
        create_user(
            admin_client, "stillgelegt", "stilles-passwort", role=Role.admin, is_active=False
        )
        antwort = admin_client.post(
            "/api/users/betreiber/uebergeben", json={"user_id": _konto("stillgelegt").id}
        )
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "betreiber_ziel_inaktiv"

    def test_nicht_an_ein_konto_das_es_nicht_gibt(self, admin_client: TestClient) -> None:
        assert (
            admin_client.post(
                "/api/users/betreiber/uebergeben", json={"user_id": 999999}
            ).status_code
            == 404
        )

    def test_der_neue_traeger_kann_danach_deaktiviert_werden(
        self, admin_client: TestClient, zweiter_admin: dict[str, str]
    ) -> None:
        """⚠️ Der unangenehme Fall: der Haken sitzt auf einem stillgelegten Konto.

        Uebergeben geht nur an ein aktives Konto - aber danach kann der neue
        Traeger sich selbst deaktivieren, und **niemand sonst** kann es. Der
        letzte-Administrator-Schutz greift nur, wenn er wirklich der letzte ist.

        Nexview laesst das zu, und das ist eine bewusste Entscheidung: Alles
        andere hiesse, dem Betreiber ein Recht zu **nehmen**, das jeder andere
        Administrator hat. Der Weg zurueck ist NEXVIEW_BETREIBER - genau
        dafuer gibt es den Nothammer.
        """
        b = _konto("zweiter").id
        admin_client.post("/api/users/betreiber/uebergeben", json={"user_id": b})

        # B legt sich selbst still. Erlaubt, solange noch ein Admin aktiv ist.
        neuer_kopf = auth_headers(admin_client, "zweiter", "zweites-passwort")
        antwort = admin_client.patch(
            f"/api/users/{_konto(ADMIN['username']).id}",
            json={"display_name": "noch da"},
            headers=neuer_kopf,
        )
        assert antwort.status_code == 200

        with SessionLocal() as db:
            db.query(User).filter(User.username == "zweiter").one().is_active = False
            db.commit()

        # Der Haken bleibt, wo er ist - er wandert nicht von selbst.
        assert _ist_betreiber("zweiter")
        # Und niemand kann das Konto mehr anfassen, um es wieder anzuschalten.
        a_kopf = auth_headers(admin_client, ADMIN["username"], ADMIN["password"])
        assert (
            admin_client.patch(
                f"/api/users/{b}", json={"is_active": True}, headers=a_kopf
            ).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# Das eigene Konto
# ---------------------------------------------------------------------------


class TestDasEigeneKonto:
    def test_der_betreiber_kann_sein_konto_nicht_loeschen(
        self, admin_client: TestClient
    ) -> None:
        """"Erst weitergeben, dann gehen."

        ⚠️ Diese Regel gilt heute schon fuer *jeden* Administrator - ``delete_user``
        weist das eigene Konto ab, und den Aufloesungsantrag stellen
        Administratoren gar nicht erst. Der Test steht trotzdem hier: Wer die
        Selbstloesch-Sperre spaeter lockert, soll an dieser Stelle merken, dass
        damit der Betreiber-Haken mit dem Konto verschwaende - und die
        Installation stuende ohne Betreiber da.
        """
        a = _konto(ADMIN["username"]).id
        antwort = admin_client.request("DELETE", f"/api/users/{a}", json={})
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "cannot_delete_self"

        antrag = admin_client.post("/api/tickets/kontoaufloesung")
        assert antrag.status_code == 403

    def test_er_kann_sein_konto_weiter_einstellen(self, admin_client: TestClient) -> None:
        """Der Haken nimmt ihm nichts weg."""
        a = _konto(ADMIN["username"]).id
        antwort = admin_client.patch(f"/api/users/{a}", json={"display_name": "Chef"})
        assert antwort.status_code == 200
        assert antwort.json()["display_name"] == "Chef"
        assert antwort.json()["is_betreiber"] is True


# ---------------------------------------------------------------------------
# Die Datenbank
# ---------------------------------------------------------------------------


class TestDieDatenbankErzwingtEs:
    def test_nie_zwei_betreiber(self, admin_client: TestClient) -> None:
        """⚠️ Nicht der Code haelt das - die Datenbank tut es.

        Der Versuch laeuft am Dienst vorbei, direkt in die Tabelle. Genau so
        muss er scheitern: Waere es nur eine Pruefung im Code, koennten zwei
        Vorgaenge in derselben Tausendstelsekunde beide durchkommen.
        """
        create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
        with SessionLocal() as db:
            db.query(User).filter(User.username == "zweiter").one().is_betreiber = True
            with pytest.raises(IntegrityError):
                db.commit()

    def test_viele_nichtbetreiber_sind_kein_problem(self, admin_client: TestClient) -> None:
        """Die Gegenprobe zum teil-eindeutigen Index.

        Ein gewoehnlicher eindeutiger Index liesse nur *einen* Nichtbetreiber
        zu - alle Nullen waeren Doppelte. Ohne diesen Test faellt der
        Unterschied erst dem zweiten Benutzer auf.
        """
        for name in ("eins", "zwei", "drei"):
            create_user(admin_client, name, f"{name}-passwort-123")
        with SessionLocal() as db:
            assert db.query(User).filter(User.is_betreiber.is_(False)).count() == 3


# ---------------------------------------------------------------------------
# Der Nothammer
# ---------------------------------------------------------------------------


class TestUmgebungsvariable:
    def test_holt_den_haken_zu_einem_ausgesperrten_konto_zurueck(
        self, admin_client: TestClient
    ) -> None:
        """Der Fall, fuer den es sie gibt: A ist weg, der Haken haengt fest."""
        create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
        einstellungen = get_settings()
        einstellungen.betreiber = "zweiter"
        try:
            with SessionLocal() as db:
                betreiber.beim_start(db)
        finally:
            einstellungen.betreiber = ""

        assert _ist_betreiber("zweiter")
        assert not _ist_betreiber(ADMIN["username"])

    def test_ein_unbekannter_name_legt_kein_konto_an(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Ein Vertipper darf kein Geisterkonto mit Administratorrechten erzeugen."""
        vorher = len(_alle_namen())
        einstellungen = get_settings()
        einstellungen.betreiber = "gibtsnicht"
        try:
            with SessionLocal() as db:
                betreiber.beim_start(db)
        finally:
            einstellungen.betreiber = ""

        assert len(_alle_namen()) == vorher
        # Und der bisherige Traeger behaelt ihn - nichts ist passiert.
        assert _ist_betreiber(ADMIN["username"])

    def test_solange_sie_gesetzt_ist_wird_die_uebergabe_verweigert(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Verweigern statt spaeter still zurueckdrehen.

        Die Variable wird bei **jedem** Start gelesen. Liesse Nexview die
        Uebergabe zu, spraenge der Haken beim naechsten Neustart zurueck - die
        Uebergabe haette gehalten, bis zum naechsten Update, und niemand haette
        verstanden warum.
        """
        create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
        einstellungen = get_settings()
        einstellungen.betreiber = ADMIN["username"]
        try:
            antwort = admin_client.post(
                "/api/users/betreiber/uebergeben",
                json={"user_id": _konto("zweiter").id},
            )
            assert antwort.status_code == 409
            assert antwort.json()["detail"]["code"] == "betreiber_von_umgebung"

            # Und die Uebersicht sagt es, statt den Knopf wortlos zu sperren.
            stand = admin_client.get("/api/users/betreiber").json()
            assert stand["aus_umgebung"] is True
        finally:
            einstellungen.betreiber = ""

        assert _ist_betreiber(ADMIN["username"])


def _alle_namen() -> list[str]:
    with SessionLocal() as db:
        return [u.username for u in db.query(User).all()]
