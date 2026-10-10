"""Die Seitentueren: den Betreiber treffen, ohne sein Konto anzufassen.

⚠️ **Der Grund, warum es diese Datei gibt.** Die Wache an den Konto-Adressen
schuetzt genau das: Konto-Adressen. Sie sieht nicht, dass man den Betreiber auch
erledigen kann, ohne sein Konto je anzufassen:

* **Den Anmeldeweg abschalten.** Wer nur ueber einen Anbieter (OIDC) oder ueber
  den Medienserver hereinkommt, steht draussen, sobald der weg ist. Sein Konto
  ist unversehrt und unerreichbar.
* **Eine alte Sicherung einspielen.** Sie kopiert die ganze Datenbank, der
  Haken reiste mit - und eine Uebergabe waere damit rueckgaengig zu machen.

Beides ist bewusst geschlossen. Was hier steht, ist der Beweis dafuer - und
zugleich die Erinnerung, dass eine Wache an einer Liste von Adressen niemals
der ganze Schutz ist.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import OidcLink, OidcProvider, Role, User
from app.security import hash_password, unusable_password
from app.services import settings_service

from .conftest import ADMIN, auth_headers, create_user

ISSUER = "https://sso.example.com"


def _mit_adresse() -> None:
    with SessionLocal() as db:
        settings_service.save_settings(db, {"public_url": "https://nexview.beispiel.de"})
        db.commit()


def _anbieter() -> int:
    """Ein Anmelde-Anbieter, direkt in der Tabelle (die Discovery spielt hier keine Rolle)."""
    with SessionLocal() as db:
        eintrag = OidcProvider(slug="firma", label="Firmen-SSO", issuer=ISSUER, client_id="nexview")
        db.add(eintrag)
        db.commit()
        return eintrag.id


def _betreiber_kommt_nur_ueber_oidc(anbieter: int) -> None:
    """Dem Betreiber Passwort und Mail nehmen und ihn an den Anbieter haengen.

    So sieht ein Konto aus, das ueber einen Anbieter entstanden ist: kein
    brauchbares Passwort - nur die Verknuepfung.
    """
    with SessionLocal() as db:
        chef = db.query(User).filter(User.username == ADMIN["username"]).one()
        chef.password_hash = unusable_password()
        chef.email = None
        chef.email_verified = False
        db.add(OidcLink(provider_id=anbieter, subject="chef-1", account_id=chef.id, issuer=ISSUER))
        db.commit()


def _zweiter_admin(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
    return auth_headers(admin_client, "zweiter", "zweites-passwort")


FORM = {"label": "Firmen-SSO", "issuer": ISSUER, "client_id": "nexview"}


class TestAnmeldewegAbschalten:
    def test_zweiter_admin_kann_den_anbieter_des_betreibers_nicht_entfernen(
        self, admin_client: TestClient
    ) -> None:
        """Weder entfernen noch abschalten noch einen anderen Aussteller eintragen.

        Fuer jedes andere Konto fragt die Oberflaeche vorher und der
        Administrator darf es - er kann den Schaden hinterher beheben, indem er
        ein Passwort setzt. Beim Betreiber kann er das gerade **nicht**: Dessen
        Passwort zu setzen ist ihm verboten.
        """
        _mit_adresse()
        anbieter = _anbieter()
        _betreiber_kommt_nur_ueber_oidc(anbieter)
        kopf_b = _zweiter_admin(admin_client)

        weg = admin_client.delete(f"/api/oidc/admin/providers/{anbieter}", headers=kopf_b)
        aus = admin_client.put(
            f"/api/oidc/admin/providers/{anbieter}", json={**FORM, "enabled": False}, headers=kopf_b
        )
        anders = admin_client.put(
            f"/api/oidc/admin/providers/{anbieter}",
            json={**FORM, "issuer": "https://anderes.example.com"},
            headers=kopf_b,
        )
        for antwort in (weg, aus, anders):
            assert antwort.status_code == 403, antwort.text
            assert antwort.json()["detail"]["code"] == "betreiber_wuerde_ausgesperrt"
        with SessionLocal() as db:
            assert db.get(OidcProvider, anbieter).enabled
            assert db.query(OidcLink).count() == 1

    def test_der_betreiber_selbst_darf_es(self, admin_client: TestClient) -> None:
        """Der Haken sagt, was **andere** nicht duerfen - nicht, was er nicht darf."""
        _mit_adresse()
        anbieter = _anbieter()
        _betreiber_kommt_nur_ueber_oidc(anbieter)

        antwort = admin_client.delete(f"/api/oidc/admin/providers/{anbieter}")
        assert antwort.status_code == 200, antwort.text
        assert antwort.json()["count"] == 1 and antwort.json()["only"] == 1

    def test_ein_gewoehnliches_konto_haelt_nichts_auf(self, admin_client: TestClient) -> None:
        """⚠️ Die Gegenprobe: Der Schutz gilt nur dem Betreiber.

        Fuer alle anderen fragt die Oberflaeche mit der Zahl der Konten, fuer
        die es der einzige Weg herein ist (``impact``); der Server haelt nichts
        auf.
        """
        _mit_adresse()
        anbieter = _anbieter()
        with SessionLocal() as db:
            gast = User(username="nur-sso", password_hash=unusable_password(), email=None)
            db.add(gast)
            db.flush()
            db.add(OidcLink(provider_id=anbieter, subject="p-1", account_id=gast.id, issuer=ISSUER))
            db.commit()
        kopf_b = _zweiter_admin(admin_client)

        folgen = admin_client.get(f"/api/oidc/admin/providers/{anbieter}/impact", headers=kopf_b)
        assert folgen.json()["only_names"] == ["nur-sso"]
        antwort = admin_client.delete(f"/api/oidc/admin/providers/{anbieter}", headers=kopf_b)
        assert antwort.status_code == 200, antwort.text
        with SessionLocal() as db:
            assert db.query(OidcLink).count() == 0


class TestSicherungEinspielen:
    """Der Haken ist der eine Wert, der die Zeitmaschine nicht mitmacht."""

    def test_eine_alte_sicherung_dreht_die_uebergabe_nicht_zurueck(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Der Angriff, den dieser Test verhindert.

        A ist Betreiber. Es wird gesichert. A uebergibt an B. Ein Administrator
        spielt die Sicherung von vorher ein - und ohne diese Regel saesse der
        Haken wieder bei A, obwohl A ihn abgegeben hat.

        Nach dem Einspielen ist der Traeger derselbe wie davor: B.
        """
        from app.services import sicherung

        create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
        with SessionLocal() as db:
            b_id = db.query(User).filter(User.username == "zweiter").one().id

        # Der Stand von vorher: A traegt den Haken.
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, "ein-langes-testpasswort")

        # Danach uebergibt A an B.
        assert (
            admin_client.post(
                "/api/users/betreiber/uebergeben", json={"user_id": b_id}
            ).status_code
            == 200
        )

        sicherung.wiederherstellen(daten, "ein-langes-testpasswort")

        with SessionLocal() as db:
            traeger = db.query(User).filter(User.is_betreiber.is_(True)).one()
            assert traeger.username == "zweiter", (
                "Die Sicherung hat die Übergabe zurückgedreht - genau der Angriff, "
                "gegen den der Haken steht."
            )

    def test_alles_andere_kommt_sehr_wohl_aus_der_sicherung(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Die Gegenprobe: Die Sicherung bleibt eine Sicherung.

        Nur der Haken ist die Ausnahme. Waere hier plotzlich mehr ausgenommen,
        waere das Einspielen keine Wiederherstellung mehr.
        """
        from app.services import sicherung

        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, "ein-langes-testpasswort")

        with SessionLocal() as db:
            db.add(
                User(
                    username="danach",
                    password_hash=hash_password("x"),
                    email="danach@beispiel.de",
                )
            )
            db.commit()

        sicherung.wiederherstellen(daten, "ein-langes-testpasswort")

        with SessionLocal() as db:
            assert db.query(User).filter(User.username == "danach").count() == 0

    def test_ist_der_traeger_in_der_sicherung_kein_admin_bleibt_er_unvergeben(
        self, admin_client: TestClient
    ) -> None:
        """Lieber unvergeben als geraten.

        Traegt ihn jemand, den es in der eingespielten Datenbank gar nicht gibt,
        waere jede Wahl eine Erfindung. Die Uebersicht sagt dann "niemand", und
        ``NEXVIEW_BETREIBER`` ist der Weg zurueck.
        """
        from app.services import sicherung

        # Eine Sicherung, in der es "spaeter" noch nicht gibt.
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, "ein-langes-testpasswort")

        create_user(admin_client, "spaeter", "spaeteres-passwort", role=Role.admin)
        with SessionLocal() as db:
            spaeter_id = db.query(User).filter(User.username == "spaeter").one().id
        admin_client.post("/api/users/betreiber/uebergeben", json={"user_id": spaeter_id})

        sicherung.wiederherstellen(daten, "ein-langes-testpasswort")

        with SessionLocal() as db:
            assert db.query(User).filter(User.is_betreiber.is_(True)).count() == 0
            assert db.query(User).filter(User.username == "spaeter").count() == 0
