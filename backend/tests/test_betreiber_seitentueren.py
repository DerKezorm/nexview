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
from app.models import OidcLink, Role, User
from app.security import hash_password, unusable_password
from app.services import settings_service

from .conftest import ADMIN, auth_headers, create_user
from .oidc_helfer import ISSUER

NEU = {
    "slug": "firma",
    "label": "Firmen-SSO",
    "issuer_url": ISSUER,
    "client_id": "nexview",
    "client_secret": "sehr-geheim",
}


def _mit_adresse() -> None:
    with SessionLocal() as db:
        settings_service.save_settings(db, {"public_url": "https://nexview.beispiel.de"})
        db.commit()


def _betreiber_kommt_nur_ueber_oidc() -> None:
    """Dem Betreiber Passwort und Mail nehmen und ihn an den Anbieter haengen.

    So sieht ein Konto aus, das ueber die automatische Anlage entstanden ist:
    kein brauchbares Passwort, keine bestaetigte Adresse - nur die Verknuepfung.
    """
    with SessionLocal() as db:
        chef = db.query(User).filter(User.username == ADMIN["username"]).one()
        chef.password_hash = unusable_password()
        chef.email = None
        chef.email_verified = False
        chef.oidc_links.append(OidcLink(issuer=ISSUER, subject="chef-1"))
        db.commit()


class TestAnmeldewegAbschalten:
    def test_zweiter_admin_kann_den_anbieter_des_betreibers_nicht_loeschen(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Und ``bestaetigt=true`` hilft ihm hier nicht.

        Fuer jedes andere Konto darf er die Warnung ueberstimmen - er kann den
        Schaden hinterher beheben, indem er ein Passwort setzt. Beim Betreiber
        kann er das gerade **nicht**: Dessen Passwort zu setzen ist ihm
        verboten. Ein ueberstimmbarer Schutz waere hier also gar keiner.
        """
        _mit_adresse()
        eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()
        _betreiber_kommt_nur_ueber_oidc()

        create_user(admin_client, "zweiter", "zweites-passwort", role=Role.admin)
        kopf_b = auth_headers(admin_client, "zweiter", "zweites-passwort")

        ohne = admin_client.delete(f"/api/admin/oidc/{eintrag['id']}", headers=kopf_b)
        assert ohne.status_code == 403
        assert ohne.json()["detail"]["code"] == "betreiber_wuerde_ausgesperrt"

        mit = admin_client.delete(
            f"/api/admin/oidc/{eintrag['id']}?bestaetigt=true", headers=kopf_b
        )
        assert mit.status_code == 403, "bestaetigt=true darf hier nichts ausrichten"
        assert mit.json()["detail"]["code"] == "betreiber_wuerde_ausgesperrt"

    def test_der_betreiber_selbst_darf_es(self, admin_client: TestClient) -> None:
        """Der Haken sagt, was **andere** nicht duerfen - nicht, was er nicht darf.

        Wer sich selbst aussperren will, darf das. Es ist sein Server.
        """
        _mit_adresse()
        eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()
        _betreiber_kommt_nur_ueber_oidc()

        assert (
            admin_client.delete(
                f"/api/admin/oidc/{eintrag['id']}?bestaetigt=true"
            ).status_code
            == 204
        )

    def test_ein_gewoehnliches_konto_bleibt_ueberstimmbar(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ Die Gegenprobe: Der neue Schutz darf die alte Regel nicht ersetzen.

        Fuer alle anderen bleibt es wie bisher - Warnung, und der Administrator
        kann sie ueberstimmen.
        """
        _mit_adresse()
        eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()
        with SessionLocal() as db:
            gast = User(
                username="nur-sso", password_hash=unusable_password(), email=None
            )
            gast.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-1"))
            db.add(gast)
            db.commit()

        abgelehnt = admin_client.delete(f"/api/admin/oidc/{eintrag['id']}")
        assert abgelehnt.status_code == 409
        assert abgelehnt.json()["detail"]["code"] == "oidc_would_lock_out_others"

        assert (
            admin_client.delete(
                f"/api/admin/oidc/{eintrag['id']}?bestaetigt=true"
            ).status_code
            == 204
        )


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
