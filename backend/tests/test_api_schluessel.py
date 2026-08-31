"""Persoenliche Zugriffs-Schluessel fuer die HTTP-Schnittstelle.

⚠️ **Hier wird eine Tuer gebaut, und deshalb wird sie von beiden Seiten
geprueft.** Die Tests fragen nicht nur "geht es?", sondern vor allem: Wo darf
ein Schluessel **nicht** durch? Ein Schluessel, der mehr kann als gedacht, oder
einer, der nach dem Widerruf weiterlaeuft, ist schlimmer als gar keiner.

Alles laeuft ueber die Schnittstelle, nicht ueber den Dienst. Das ist die Lehre
aus den Sicherungen: Dort waren 49 Dienst-Tests gruen, waehrend ein Klick einen
Serverfehler ausloeste - der Fehler sass in der Schicht dazwischen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import ApiKey, Role, User, utcnow
from app.services import api_schluessel

from .conftest import ADMIN, auth_headers, create_user


def _anlegen(client: TestClient, name: str = "Testschluessel", **rest) -> dict:
    antwort = client.post("/api/auth/me/schluessel", json={"name": name, **rest})
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def _kopf(schluessel: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {schluessel}"}


class TestAnlegen:
    def test_der_klartext_kommt_genau_einmal(self, admin_client: TestClient) -> None:
        """⚠️ Der Kern des ganzen Umgangs mit Geheimnissen.

        Gespeichert wird nur die Pruefsumme. Wer den Schluessel verliert, legt
        einen neuen an - wiederherstellen kann ihn niemand, auch kein
        Administrator.
        """
        daten = _anlegen(admin_client, "Homepage-Kachel")
        klartext = daten["schluessel"]
        assert klartext.startswith(api_schluessel.PRAEFIX)

        # In der Liste taucht er nie wieder auf.
        liste = admin_client.get("/api/auth/me/schluessel").json()
        assert liste, "Der Schluessel muss in der Liste stehen."
        for eintrag in liste:
            assert "schluessel" not in eintrag
            assert klartext not in str(eintrag)

    def test_in_der_datenbank_steht_kein_klartext(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client)["schluessel"]
        with SessionLocal() as db:
            eintrag = db.query(ApiKey).one()
            assert eintrag.token_hash != klartext
            assert klartext not in eintrag.token_hash
            # Die Vorschau darf nur ein Anfang sein, kein halber Schluessel.
            assert len(eintrag.vorschau) <= len(api_schluessel.PRAEFIX) + 6

    def test_zwei_schluessel_sind_verschieden(self, admin_client: TestClient) -> None:
        a = _anlegen(admin_client, "eins")["schluessel"]
        b = _anlegen(admin_client, "zwei")["schluessel"]
        assert a != b

    def test_ohne_namen_geht_nicht(self, admin_client: TestClient) -> None:
        """Zwei Wege, und beide werden abgewiesen - aber unterschiedlich.

        Ein fehlendes Feld faengt Pydantic ab (422). Ein Name aus lauter
        Leerzeichen kommt durch die Formpruefung und wird erst im Dienst
        abgewiesen - dafuer mit einer Kennung, die die Oberflaeche uebersetzen
        kann. Das ist besser als ein nacktes 422.
        """
        assert admin_client.post("/api/auth/me/schluessel", json={}).status_code == 422

        leer = admin_client.post("/api/auth/me/schluessel", json={"name": "   "})
        assert leer.status_code == 400
        assert leer.json()["detail"]["code"] == "apikey_needs_name"

    def test_kinderkonten_bekommen_keine(self, admin_client: TestClient) -> None:
        """Kinder sind Unterprofile ihrer Eltern - ein eigener Zugang widerspricht dem.

        ⚠️ Dieser Test hat zuerst still uebersprungen, weil das Anlegen des
        Kindes ueber die falsche Adresse lief. Ein uebersprungener Test ist
        kein Test - bei einer Regel, die Zugaenge verhindern soll, erst recht
        nicht.
        """
        kind = create_user(admin_client, "kind1", role=Role.child)

        with SessionLocal() as db:
            eintrag = db.query(User).filter(User.id == kind["id"]).one()
            assert eintrag.role == Role.child, "Voraussetzung: es ist wirklich ein Kind."

            with pytest.raises(api_schluessel.SchluesselFehler) as fehler:
                api_schluessel.anlegen(db, eintrag, name="geht nicht")
            assert fehler.value.code == "apikey_not_for_children"

        # Und es entstand auch nichts.
        with SessionLocal() as db:
            assert db.query(ApiKey).filter(ApiKey.user_id == kind["id"]).count() == 0


class TestAnmelden:
    def test_der_schluessel_oeffnet_die_tuer(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client)["schluessel"]

        antwort = admin_client.get("/api/auth/me", headers=_kopf(klartext))
        assert antwort.status_code == 200
        assert antwort.json()["username"] == "admin"

    def test_ein_erfundener_schluessel_nicht(self, admin_client: TestClient) -> None:
        gefaelscht = api_schluessel.PRAEFIX + "a" * 50
        assert admin_client.get("/api/auth/me", headers=_kopf(gefaelscht)).status_code == 401

    def test_nach_dem_widerruf_ist_zu(self, admin_client: TestClient) -> None:
        """⚠️ Der Punkt, an dem ein Fehler am teuersten waere."""
        daten = _anlegen(admin_client)
        klartext = daten["schluessel"]
        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200

        assert (
            admin_client.delete(f"/api/auth/me/schluessel/{daten['id']}").status_code == 204
        )
        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 401

    def test_ein_abgelaufener_gilt_nicht_mehr(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client, "laeuft ab", tage=1)["schluessel"]
        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200

        # Das Ablaufdatum in die Vergangenheit ziehen - wie nach einem Jahr.
        with SessionLocal() as db:
            eintrag = db.query(ApiKey).one()
            eintrag.expires_at = utcnow() - timedelta(days=1)
            db.commit()

        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 401

    def test_ein_stillgelegtes_konto_sperrt_auch_den_schluessel(
        self, admin_client: TestClient
    ) -> None:
        """Sonst bliebe eine Hintertuer offen, nachdem das Konto zu ist."""
        create_user(admin_client, "kim", "passwort-1234")
        kopf = auth_headers(admin_client, "kim", "passwort-1234")
        klartext = admin_client.post(
            "/api/auth/me/schluessel", json={"name": "kims"}, headers=kopf
        ).json()["schluessel"]

        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200

        with SessionLocal() as db:
            kim = db.query(User).filter(User.username == "kim").one()
            kim.is_active = False
            db.commit()

        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 401

    def test_die_nutzung_wird_vermerkt(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client)["schluessel"]
        with SessionLocal() as db:
            assert db.query(ApiKey).one().last_used_at is None

        admin_client.get("/api/auth/me", headers=_kopf(klartext))

        with SessionLocal() as db:
            assert db.query(ApiKey).one().last_used_at is not None


class TestRechte:
    """⚠️ Der Unterschied zu Seerr - und der Grund für den ganzen Entwurf.

    Dort haengt **ein** globaler Schluessel am Eigentuemerkonto: Wer ihn hat,
    handelt als Eigentuemer, und jede Anfrage darueber ist automatisch
    genehmigt. Hier erbt ein Schluessel die Rechte seines Besitzers - nicht
    mehr, nicht weniger.
    """

    def test_ein_schluessel_erbt_die_rechte_seines_besitzers(
        self, admin_client: TestClient
    ) -> None:
        create_user(admin_client, "kim", "passwort-1234")
        kopf = auth_headers(admin_client, "kim", "passwort-1234")
        klartext = admin_client.post(
            "/api/auth/me/schluessel", json={"name": "kims"}, headers=kopf
        ).json()["schluessel"]

        # Als Kim: das eigene Profil geht.
        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200
        # Aber nichts, was Administratoren vorbehalten ist.
        assert admin_client.get("/api/logs", headers=_kopf(klartext)).status_code == 403

    def test_niemand_widerruft_fremde_schluessel(self, admin_client: TestClient) -> None:
        """Ohne die Bedingung auf den Besitzer genuegte eine geratene Nummer."""
        fremd = _anlegen(admin_client, "dem admin seiner")
        create_user(admin_client, "kim", "passwort-1234")
        kopf = auth_headers(admin_client, "kim", "passwort-1234")

        antwort = admin_client.delete(
            f"/api/auth/me/schluessel/{fremd['id']}", headers=kopf
        )
        assert antwort.status_code == 404

        # Und er wirkt weiterhin.
        assert (
            admin_client.get("/api/auth/me", headers=_kopf(fremd["schluessel"])).status_code
            == 200
        )


class TestNurLesen:
    """⚠️ Die einzige Abstufung - durchgesetzt an der HTTP-Methode.

    Das ist bei Nexview zulaessig, weil kein einziger GET-Pfad etwas
    veraendert. Waere das anders, waere diese Regel ein Loch.
    """

    def test_lesen_geht(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client, "nur zusehen", nur_lesen=True)["schluessel"]
        assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200

    def test_schreiben_nicht(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client, "nur zusehen", nur_lesen=True)["schluessel"]

        antwort = admin_client.patch(
            "/api/auth/me", json={"display_name": "Neu"}, headers=_kopf(klartext)
        )
        assert antwort.status_code == 403
        assert antwort.json()["detail"]["code"] == "apikey_read_only"

    def test_auch_kein_loeschen(self, admin_client: TestClient) -> None:
        klartext = _anlegen(admin_client, "nur zusehen", nur_lesen=True)["schluessel"]
        eigener = _anlegen(admin_client, "anderer")["id"]

        antwort = admin_client.delete(
            f"/api/auth/me/schluessel/{eigener}", headers=_kopf(klartext)
        )
        assert antwort.status_code == 403

    def test_ein_gewoehnlicher_schluessel_darf_schreiben(self, admin_client: TestClient) -> None:
        """Der Gegenbeweis - sonst prueft der Test oben nur, dass PATCH scheitert."""
        klartext = _anlegen(admin_client, "voller Zugriff")["schluessel"]

        antwort = admin_client.patch(
            "/api/auth/me", json={"display_name": "Neu"}, headers=_kopf(klartext)
        )
        assert antwort.status_code == 200


class TestOhneAnmeldung:
    def test_die_verwaltung_ist_zu(self, client: TestClient) -> None:
        assert client.get("/api/auth/me/schluessel").status_code in (401, 403)
        assert client.post("/api/auth/me/schluessel", json={"name": "x"}).status_code in (
            401,
            403,
        )


class TestAufsichtDesAdministrators:
    """Die Uebersicht unter ``/api/users/api-schluessel``.

    ⚠️ **Aufsicht ist nicht Zugriff.** Ein Administrator soll sehen, *dass*
    jemand einen Token hat und ob er noch benutzt wird - lesen darf er ihn
    nicht. Genau diese Grenze pruefen die Tests hier; ohne sie waere die
    Uebersicht eine bequeme Stelle, an der alle Geheimnisse beisammenliegen.
    """

    def test_der_administrator_sieht_fremde_token(self, admin_client: TestClient) -> None:
        fremder = create_user(admin_client, "leihkonto", "geheim123", role=Role.user)
        kopf = auth_headers(admin_client, "leihkonto", "geheim123")

        assert admin_client.post(
            "/api/auth/me/schluessel", json={"name": "Heimkachel"}, headers=kopf
        ).status_code == 201

        zeilen = admin_client.get("/api/users/api-schluessel").json()
        meiner = [z for z in zeilen if z["username"] == "leihkonto"]
        assert len(meiner) == 1
        assert meiner[0]["name"] == "Heimkachel"
        assert meiner[0]["user_id"] == fremder["id"]

    def test_der_klartext_steht_nicht_darin(self, admin_client: TestClient) -> None:
        """⚠️ Der wichtigste Test der Klasse.

        Der Token existiert genau einmal, beim Anlegen. Taeuchte er in einer
        Liste wieder auf, waere das Versprechen "wird nie wieder angezeigt"
        gebrochen - und zwar an der Stelle mit den meisten Rechten.
        """
        klartext = _anlegen(admin_client, "Sicherungs-Skript")["schluessel"]

        roh = admin_client.get("/api/users/api-schluessel").text
        assert klartext not in roh
        # Auch nicht in Teilen: Der Anfang zum Wiedererkennen ist gewollt, der
        # Rest darf nirgends stehen.
        assert klartext[len("nxv_") + 8 :] not in roh

    def test_ein_gewoehnliches_konto_kommt_nicht_hinein(self, admin_client: TestClient) -> None:
        create_user(admin_client, "neugierig", "geheim123", role=Role.user)
        kopf = auth_headers(admin_client, "neugierig", "geheim123")

        assert admin_client.get("/api/users/api-schluessel", headers=kopf).status_code == 403

    def test_ein_nur_lesen_token_darf_die_uebersicht_sehen(
        self, admin_client: TestClient
    ) -> None:
        """Sie ist ein GET - also faellt sie unter "lesen".

        Das ist gewollt: Wer eine Aufsicht automatisiert ueberwachen will,
        soll das mit dem eingeschraenkten Token tun koennen.
        """
        klartext = _anlegen(admin_client, "Aufsicht", nur_lesen=True)["schluessel"]

        antwort = admin_client.get("/api/users/api-schluessel", headers=_kopf(klartext))
        assert antwort.status_code == 200


# ---------------------------------------------------------------------------
# Der Riegel, hinter dem eine Tuer offen stand
# ---------------------------------------------------------------------------


def test_ueberall_abmelden_widerruft_die_schluessel(admin_client: TestClient) -> None:
    """⚠️ **Sonst hat der Riegel ein Loch, und zwar ein unsichtbares.**

    Ein Zugriffs-Schluessel nimmt einen zweiten Weg durch
    ``deps.get_current_user`` - ``sitzung.gilt_noch`` wird dabei nie gefragt.
    Ein Stempel auf ``sessions_valid_from`` liess ihn also weiterlaufen.

    Wer diesen Knopf drueckt, sagt "jemand liest mit". Ein Riegel, der dabei
    eine Tuer offen laesst, ist schlimmer als keiner: Er gibt Sicherheit vor.
    """
    klartext = _anlegen(admin_client)["schluessel"]
    assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200

    antwort = admin_client.post("/api/auth/me/ueberall-abmelden", json={})
    assert antwort.status_code == 200, antwort.text
    # Der Aufruf beendet auch die eigene Sitzung und liefert ein frisches Paar -
    # sonst haette er den Anrufer selbst hinausgeworfen.
    admin_client.headers["Authorization"] = f"Bearer {antwort.json()['access_token']}"

    assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 401
    assert admin_client.get("/api/auth/me/schluessel").json() == [], (
        "der Schluessel muss wirklich weg sein, nicht nur abgewiesen"
    )


def test_passwortwechsel_laesst_die_schluessel_leben(admin_client: TestClient) -> None:
    """⚠️ Die andere Richtung - und sie ist genauso wichtig.

    Ein Passwortwechsel ist meistens Hausputz. Wuerde er jede Anbindung
    mitnehmen - die Kachel auf dem Uebersichtsbrett, das eigene Skript -,
    stuende der Betreiber ohne Vorwarnung vor lauter toten Verbindungen und
    wuesste nicht warum. Wer wirklich verdaechtigt, hat den anderen Knopf.
    """
    klartext = _anlegen(admin_client, "Bleibt am Leben")["schluessel"]

    antwort = admin_client.post(
        "/api/auth/me/password",
        json={"current_password": ADMIN["password"], "new_password": "neues-passwort-1234"},
    )
    assert antwort.status_code in (200, 204), antwort.text

    assert admin_client.get("/api/auth/me", headers=_kopf(klartext)).status_code == 200
