"""``/api/v1/me``: was darf dieser Schluessel wirklich.

⚠️ **Warum das mehr ist als ein Blick auf die Rolle.** Nexview kennt zwei
Beschraenkungen, die nichts miteinander zu tun haben: was ein Konto darf, und
ob der benutzte Schluessel ueberhaupt schreiben darf. Wer nur die Rolle liest,
baut einer Anbindung Knoepfe, die immer scheitern - ein Administrator mit einem
Nur-Lese-Schluessel traegt ``role: admin`` und bekommt auf jedes POST ein 403.

Genau das ist der Zweck der Adresse: ``darf`` rechnet beides zusammen. Diese
Datei haelt fest, dass es das auch wirklich tut - fuer jede Rolle und fuer
beide Arten von Schluessel.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Role
from tests.conftest import create_user


def _schluessel(client: TestClient, name: str, *, nur_lesen: bool = False) -> str:
    """Einen Zugriffs-Schluessel anlegen und seinen Klartext zurueckgeben."""
    antwort = client.post(
        "/api/auth/me/schluessel", json={"name": name, "nur_lesen": nur_lesen}
    )
    assert antwort.status_code == 201, antwort.text
    return antwort.json()["schluessel"]


def _me(client: TestClient, schluessel: str | None = None) -> dict:
    kopf = {"Authorization": f"Bearer {schluessel}"} if schluessel else None
    antwort = client.get("/api/v1/me", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


class TestWerKlopftAn:
    def test_eine_sitzung_hat_keinen_schluessel(self, admin_client: TestClient) -> None:
        """Aus dem Browser angemeldet: kein Schluessel, keine Beschraenkung."""
        daten = _me(admin_client)

        assert daten["schluessel"] is None
        assert daten["konto"]["role"] == "admin"
        assert daten["konto"]["betreiber"] is True
        assert daten["darf"] == ["lesen", "anfragen", "entscheiden", "verwalten", "einrichten"]

    def test_der_schluessel_wird_benannt(self, admin_client: TestClient) -> None:
        """⚠️ **Der Name gehoert in die Antwort.**

        Wer zwanzig Anbindungen betreibt, muss aus der Anbindung heraus
        erkennen koennen, welcher Schluessel gerade benutzt wird - sonst weiss
        niemand, welchen er widerrufen darf.
        """
        klartext = _schluessel(admin_client, "Home Assistant")
        daten = _me(admin_client, klartext)

        assert daten["schluessel"] == {"name": "Home Assistant", "nur_lesen": False}
        assert daten["darf"] == ["lesen", "anfragen", "entscheiden", "verwalten", "einrichten"]

    def test_der_name_faellt_auf_den_benutzernamen_zurueck(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        """``name`` ist nie leer - auch ohne gesetzten Anzeigenamen."""
        create_user(admin_client, "ohnenamen", display_name=None)
        from tests.conftest import auth_headers

        kopf = auth_headers(client, "ohnenamen", "passwort-1234")
        daten = _me(client, kopf["Authorization"].split(" ", 1)[1])

        assert daten["konto"]["name"] == "ohnenamen"


class TestDarfRechnetBeidesZusammen:
    def test_nur_lesen_nimmt_alles_veraendernde(self, admin_client: TestClient) -> None:
        """⚠️ **Der Fall, fuer den es die Adresse gibt.**

        Rolle und Schluessel widersprechen sich: Das Konto duerfte alles, der
        Schluessel nichts davon. Wer hier ``role`` liest, baut einen
        Genehmigen-Knopf, der bei jedem Druck 403 bekommt.
        """
        klartext = _schluessel(admin_client, "Nur zum Zusehen", nur_lesen=True)
        daten = _me(admin_client, klartext)

        assert daten["konto"]["role"] == "admin", "Die Rolle bleibt, was sie ist."
        assert daten["schluessel"]["nur_lesen"] is True
        assert daten["darf"] == ["lesen", "verwalten"]
        assert "anfragen" not in daten["darf"]
        assert "entscheiden" not in daten["darf"]
        assert "einrichten" not in daten["darf"]

    def test_ein_gewoehnliches_konto_darf_anfragen_und_sonst_nichts(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        from tests.conftest import auth_headers

        create_user(admin_client, "gast", role=Role.user)
        kopf = auth_headers(client, "gast", "passwort-1234")
        daten = _me(client, kopf["Authorization"].split(" ", 1)[1])

        assert daten["darf"] == ["lesen", "anfragen"]
        assert daten["konto"]["betreiber"] is False

    def test_ein_entscheider_entscheidet_aber_verwaltet_nicht(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        """Die Zwischenstufe - sie trennt ``entscheiden`` von ``verwalten``."""
        from tests.conftest import auth_headers

        create_user(admin_client, "entscheider", role=Role.approver)
        kopf = auth_headers(client, "entscheider", "passwort-1234")
        daten = _me(client, kopf["Authorization"].split(" ", 1)[1])

        assert daten["darf"] == ["lesen", "anfragen", "entscheiden"]
        assert "verwalten" not in daten["darf"]

    def test_ein_entscheider_mit_nur_lese_schluessel_entscheidet_nicht(
        self, admin_client: TestClient, client: TestClient
    ) -> None:
        """Beide Beschraenkungen greifen gleichzeitig, nicht nur die staerkere."""
        from tests.conftest import auth_headers

        create_user(admin_client, "entscheider2", role=Role.approver)
        kopf = auth_headers(client, "entscheider2", "passwort-1234")
        client.headers["Authorization"] = kopf["Authorization"]
        klartext = _schluessel(client, "Anzeigetafel", nur_lesen=True)

        daten = _me(client, klartext)
        assert daten["darf"] == ["lesen"]


class TestDieVersionIstDieselbe:
    def test_me_und_about_melden_dieselbe_version(self, admin_client: TestClient) -> None:
        """⚠️ **Zwei zugesagte Adressen, eine Wahrheit.**

        Eine Anbindung prueft ihre Mindestversion an ``/me``, weil sie die
        Adresse ohnehin aufruft. Meldete sie etwas anderes als ``/about``,
        haetten wir zwei Versionen im Umlauf und niemand wuesste, welche gilt.
        """
        me = _me(admin_client)
        about = admin_client.get("/api/v1/about").json()

        assert me["version"] == about["version"]
        assert me["version"], "Eine leere Version waere schlimmer als eine falsche."
