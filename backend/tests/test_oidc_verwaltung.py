"""OIDC, Stufe 3: die Verwaltung im Admin-Bereich.

Anlegen, Aendern, Loeschen mit Folgen-Warnung, Pruef-Knopf - und die Regel,
dass ohne oeffentliche Adresse gar nicht erst etwas entsteht.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import OidcLink, OidcProvider, User
from app.security import hash_password, unusable_password
from app.services import oidc as oidc_dienst
from app.services import settings_service

from .conftest import auth_headers, create_user
from .oidc_helfer import ISSUER, KAPUTTE_ADRESSE, transport

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


def test_ohne_oeffentliche_adresse_kein_anlegen(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/admin/oidc", json=NEU)
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "oidc_no_public_url"


def test_anlegen_aendern_und_liste(admin_client: TestClient) -> None:
    _mit_adresse()
    antwort = admin_client.post("/api/admin/oidc", json=NEU)
    assert antwort.status_code == 201, antwort.text
    eintrag = antwort.json()
    assert eintrag["slug"] == "firma"
    assert eintrag["auto_create"] is False
    assert eintrag["enabled"] is True
    # Das Geheimnis verlaesst die Datenbank nie - auch nicht maskiert.
    assert eintrag["client_secret_vorschau"] == "••••"
    assert "client_secret" not in eintrag
    assert (
        eintrag["rueckkehr_adresse"]
        == "https://nexview.beispiel.de/api/auth/oidc/firma/callback"
    )

    # Und in der Datenbank liegt es verschluesselt, nicht woertlich.
    with SessionLocal() as db:
        roh = db.query(OidcProvider).one().client_secret
        assert roh.startswith("enc:") and "sehr-geheim" not in roh

    geaendert = admin_client.patch(
        f"/api/admin/oidc/{eintrag['id']}",
        json={"label": "Haustuer", "auto_create": True, "client_secret": ""},
    )
    assert geaendert.status_code == 200, geaendert.text
    assert geaendert.json()["label"] == "Haustuer"
    assert geaendert.json()["auto_create"] is True
    # Leeres Geheimnis heisst "behalten".
    with SessionLocal() as db:
        assert db.query(OidcProvider).one().client_secret == roh

    liste = admin_client.get("/api/admin/oidc")
    assert [e["label"] for e in liste.json()] == ["Haustuer"]


@pytest.mark.parametrize(
    ("feld", "wert", "kennung"),
    [
        ("slug", "Böses Kürzel!", "oidc_slug_invalid"),
        ("issuer_url", "sso.beispiel.de", "url_needs_scheme"),
    ],
)
def test_anlegen_weist_unbrauchbares_ab(
    admin_client: TestClient, feld: str, wert: str, kennung: str
) -> None:
    _mit_adresse()
    antwort = admin_client.post("/api/admin/oidc", json={**NEU, feld: wert})
    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == kennung


def test_doppeltes_kuerzel_und_doppelte_adresse(admin_client: TestClient) -> None:
    _mit_adresse()
    admin_client.post("/api/admin/oidc", json=NEU)

    gleiche_kennung = admin_client.post(
        "/api/admin/oidc", json={**NEU, "issuer_url": "https://anderes.beispiel.de"}
    )
    assert gleiche_kennung.status_code == 409
    assert gleiche_kennung.json()["detail"]["code"] == "oidc_slug_taken"

    gleiche_adresse = admin_client.post("/api/admin/oidc", json={**NEU, "slug": "zwei"})
    assert gleiche_adresse.status_code == 409
    assert gleiche_adresse.json()["detail"]["code"] == "oidc_issuer_taken"


def test_nur_fuer_administratoren(admin_client: TestClient) -> None:
    create_user(admin_client, "gast")
    kopf = auth_headers(admin_client, "gast", "passwort-1234")
    assert admin_client.get("/api/admin/oidc", headers=kopf).status_code == 403
    assert admin_client.post("/api/admin/oidc", json=NEU, headers=kopf).status_code == 403


def test_loeschen_warnt_vor_dem_aussperren(admin_client: TestClient) -> None:
    """Ein Konto, dessen einziger Weg dieser Anbieter ist, blockt das Loeschen -
    bis der Administrator es ausdruecklich ueberstimmt."""
    _mit_adresse()
    eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()

    with SessionLocal() as db:
        # Ein Konto wie aus der automatischen Anlage ohne Adresse: kein
        # Passwort, keine bestaetigte Mail - nur die Verknuepfung.
        gefaehrdet = User(
            username="nur-sso",
            password_hash=unusable_password(),
            email=None,
            email_verified=False,
        )
        gefaehrdet.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-1"))
        # Und eines mit Passwort - das darf in der Warnliste nicht auftauchen.
        sicher = User(username="mit-passwort", password_hash=hash_password("passwort-1234"))
        sicher.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-2"))
        db.add_all([gefaehrdet, sicher])
        db.commit()

    folgen = admin_client.get(f"/api/admin/oidc/{eintrag['id']}/folgen").json()
    assert folgen["verknuepft"] == 2
    assert [k["username"] for k in folgen["gefaehrdet"]] == ["nur-sso"]

    abgelehnt = admin_client.delete(f"/api/admin/oidc/{eintrag['id']}")
    assert abgelehnt.status_code == 409
    assert abgelehnt.json()["detail"]["code"] == "oidc_would_lock_out_others"

    ueberstimmt = admin_client.delete(f"/api/admin/oidc/{eintrag['id']}?bestaetigt=true")
    assert ueberstimmt.status_code == 204
    with SessionLocal() as db:
        assert db.query(OidcProvider).count() == 0
        # Die Verknuepfungen bleiben - wer denselben Anbieter wieder eintraegt,
        # findet alles vor.
        assert db.query(OidcLink).count() == 2


def test_sperrliste_zeigen_und_aufheben(admin_client: TestClient) -> None:
    """Ohne diesen Weg waere eine Sperre fuer immer - sie entsteht ja still
    beim Loeschen eines Kontos."""
    with SessionLocal() as db:
        konto = User(username="wegdamit", password_hash=unusable_password())
        konto.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-9", display="weg@bsp.de"))
        db.add(konto)
        db.commit()
        kennung = konto.id

    geloescht = admin_client.delete(f"/api/users/{kennung}")
    assert geloescht.status_code == 204, geloescht.text

    liste = admin_client.get("/api/admin/oidc/blocks").json()
    assert len(liste) == 1
    assert liste[0]["issuer"] == ISSUER
    assert liste[0]["display"] == "weg@bsp.de"

    aufgehoben = admin_client.delete(f"/api/admin/oidc/blocks/{liste[0]['id']}")
    assert aufgehoben.status_code == 204
    assert admin_client.get("/api/admin/oidc/blocks").json() == []


def test_pruef_knopf(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _mit_adresse()
    eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()

    oidc_dienst.cache_leeren()
    monkeypatch.setattr(
        oidc_dienst, "_client", httpx.AsyncClient(transport=transport())
    )
    gut = admin_client.post(f"/api/admin/oidc/{eintrag['id']}/pruefen").json()
    assert gut == {"ok": True, "code": None, "aussteller": ISSUER}

    def nichts_da(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("aus")

    monkeypatch.setattr(
        oidc_dienst, "_client", httpx.AsyncClient(transport=httpx.MockTransport(nichts_da))
    )
    schlecht = admin_client.post(f"/api/admin/oidc/{eintrag['id']}/pruefen").json()
    assert schlecht["ok"] is False
    assert schlecht["code"] == "oidc_provider_unreachable"
    oidc_dienst.cache_leeren()


def test_pruef_knopf_ueberlebt_eine_unbrauchbare_adresse(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **Ein Tippfehler ergibt eine Auskunft, keine Fehlerseite.**

    ``httpx.InvalidURL`` erbt direkt von ``Exception``, nicht von
    ``httpx.HTTPError``, und die Ausnahme entsteht schon beim **Zerlegen** der
    Adresse. Ohne den groben Faenger flog sie aus genau dem Knopf heraus, der
    dem Administrator sagen soll, ob seine Eingabe taugt - als nackte 500, mit
    einem Stapelabzug im Protokoll und ohne ein Wort darueber, was zu tun ist.

    Die Adresse hier ist eine unvollstaendige IPv6-Klammer: derselbe
    Tippfehler, wie er in einer handgeschriebenen Anbieter-Adresse im Heimnetz
    vorkommt.
    """
    _mit_adresse()
    eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()
    with SessionLocal() as db:
        db.query(OidcProvider).update({"issuer_url": KAPUTTE_ADRESSE})
        db.commit()

    oidc_dienst.cache_leeren()
    monkeypatch.setattr(oidc_dienst, "_client", httpx.AsyncClient(transport=transport()))
    antwort = admin_client.post(f"/api/admin/oidc/{eintrag['id']}/pruefen")
    assert antwort.status_code == 200, antwort.text
    assert antwort.json() == {
        "ok": False,
        "code": "oidc_provider_unreachable",
        "aussteller": None,
    }
    oidc_dienst.cache_leeren()


def _anbieter_mit_gefaehrdetem_konto(admin_client: TestClient) -> int:
    """Ein Anbieter plus ein Konto, dessen einziger Weg hinein er ist."""
    _mit_adresse()
    eintrag = admin_client.post("/api/admin/oidc", json=NEU).json()
    with SessionLocal() as db:
        konto = User(
            username="nur-sso",
            password_hash=unusable_password(),
            email=None,
            email_verified=False,
        )
        konto.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-1"))
        db.add(konto)
        db.commit()
    return eintrag["id"]


def test_abschalten_warnt_genauso_wie_loeschen(admin_client: TestClient) -> None:
    """⚠️ **Abschalten ist Loeschen, aus Sicht des Ausgesperrten.**

    Der Riegel hing lange nur an ``DELETE``. Ein Administrator, dem das
    Loeschen mit 403 oder 409 verweigert wurde, kam mit einem Klick weiter:
    ``PATCH {"enabled": false}`` ging mit 200 durch, danach antworteten
    ``/login`` und ``/callback`` mit 404 und die Knopfliste war leer - fuer ein
    Konto ohne brauchbares Passwort dasselbe wie geloescht, nur ohne Warnung,
    ohne Bestaetigung und ohne Zeile im Protokoll.
    """
    provider_id = _anbieter_mit_gefaehrdetem_konto(admin_client)

    abgelehnt = admin_client.patch(f"/api/admin/oidc/{provider_id}", json={"enabled": False})
    assert abgelehnt.status_code == 409, abgelehnt.text
    assert abgelehnt.json()["detail"]["code"] == "oidc_would_lock_out_others"
    assert [k["username"] for k in abgelehnt.json()["detail"]["gefaehrdet"]] == ["nur-sso"]
    with SessionLocal() as db:
        assert db.get(OidcProvider, provider_id).enabled is True

    ueberstimmt = admin_client.patch(
        f"/api/admin/oidc/{provider_id}?bestaetigt=true", json={"enabled": False}
    )
    assert ueberstimmt.status_code == 200, ueberstimmt.text
    with SessionLocal() as db:
        assert db.get(OidcProvider, provider_id).enabled is False


def test_andere_adresse_warnt_genauso_wie_loeschen(admin_client: TestClient) -> None:
    """Eine neue Adresse haengt jede Verknuepfung ab - eine Identitaet besteht
    aus Aussteller UND Subjekt. Fuer den Betroffenen ist das ein Loeschen."""
    provider_id = _anbieter_mit_gefaehrdetem_konto(admin_client)

    abgelehnt = admin_client.patch(
        f"/api/admin/oidc/{provider_id}", json={"issuer_url": "https://woanders.beispiel.de"}
    )
    assert abgelehnt.status_code == 409, abgelehnt.text
    assert abgelehnt.json()["detail"]["code"] == "oidc_would_lock_out_others"

    # Was die Verknuepfungen nicht anfasst, geht ohne Bestaetigung durch.
    harmlos = admin_client.patch(
        f"/api/admin/oidc/{provider_id}", json={"label": "Anderer Name"}
    )
    assert harmlos.status_code == 200, harmlos.text
    # Und dieselbe Adresse noch einmal ist keine Aenderung.
    gleich = admin_client.patch(
        f"/api/admin/oidc/{provider_id}", json={"issuer_url": ISSUER}
    )
    assert gleich.status_code == 200, gleich.text


def test_nur_leerzeichen_ist_kein_name(admin_client: TestClient) -> None:
    """⚠️ ``min_length=1`` allein laesst ``"   "`` durch - drei Zeichen sind
    mehr als eins. Gespeichert wurde danach die leere Zeichenkette: ein Knopf
    ohne Beschriftung auf der Anmeldeseite, und mit leerer Client-ID scheiterte
    jede Anmeldung beim Anbieter, waehrend der Pruef-Knopf gruen meldete."""
    _mit_adresse()

    abgelehnt = admin_client.post(
        "/api/admin/oidc", json={**NEU, "label": "   ", "client_id": "   "}
    )
    assert abgelehnt.status_code == 422, abgelehnt.text

    # Und Leerzeichen aussen herum werden geputzt, nicht gespeichert.
    angelegt = admin_client.post(
        "/api/admin/oidc", json={**NEU, "label": "  Firmen-SSO  "}
    )
    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["label"] == "Firmen-SSO"

    beim_aendern = admin_client.patch(
        f"/api/admin/oidc/{angelegt.json()['id']}", json={"label": "   "}
    )
    assert beim_aendern.status_code == 422, beim_aendern.text


def test_neue_zugangsdaten_warnen_genauso_wie_loeschen(admin_client: TestClient) -> None:
    """⚠️ Die Seitentuer neben der Seitentuer.

    Der Riegel hing zuerst nur an ``enabled`` und der Adresse. Wer stattdessen
    ein falsches Client-Geheimnis eintraegt, laesst jeden Token-Tausch mit
    ``invalid_client`` scheitern - niemand kommt mehr herein. Das ist
    schlimmer als Abschalten: Das richtige Geheimnis verlaesst die Datenbank
    nie, aus Nexview heraus ist es nicht wiederherstellbar.
    """
    provider_id = _anbieter_mit_gefaehrdetem_konto(admin_client)

    fuer_geheimnis = admin_client.patch(
        f"/api/admin/oidc/{provider_id}", json={"client_secret": "falsch"}
    )
    assert fuer_geheimnis.status_code == 409, fuer_geheimnis.text
    assert fuer_geheimnis.json()["detail"]["code"] == "oidc_would_lock_out_others"

    fuer_kennung = admin_client.patch(
        f"/api/admin/oidc/{provider_id}", json={"client_id": "falsch"}
    )
    assert fuer_kennung.status_code == 409, fuer_kennung.text

    ueberstimmt = admin_client.patch(
        f"/api/admin/oidc/{provider_id}?bestaetigt=true", json={"client_id": "falsch"}
    )
    assert ueberstimmt.status_code == 200, ueberstimmt.text


def test_verwaiste_verknuepfung_ist_kein_weg_hinein(admin_client: TestClient) -> None:
    """⚠️ **Der Riegel fiel still aus, sobald irgendwo eine tote Zeile lag.**

    Verknuepfungen bleiben beim Loeschen eines Anbieters absichtlich stehen,
    und eine geaenderte Anbieter-Adresse laesst sie ebenfalls verwaist zurueck.
    Gezaehlt wurde aber "irgendeine andere Verknuepfung" - ohne nachzusehen, ob
    es den Anbieter dazu noch gibt. Wer je einen zweiten Anbieter geloescht
    oder abgeschaltet hatte, war fuer die betroffenen Konten danach voellig
    ungeschuetzt: keine Rueckfrage, kein Betreiber-Riegel, kein Ton.
    """
    _mit_adresse()
    erster = admin_client.post("/api/admin/oidc", json=NEU).json()
    zweiter = admin_client.post(
        "/api/admin/oidc",
        json={**NEU, "slug": "zweiter", "issuer_url": "https://zweiter.beispiel.de"},
    ).json()

    with SessionLocal() as db:
        konto = User(
            username="nur-sso",
            password_hash=unusable_password(),
            email=None,
            email_verified=False,
        )
        konto.oidc_links.append(OidcLink(issuer=ISSUER, subject="p-1"))
        konto.oidc_links.append(
            OidcLink(issuer="https://zweiter.beispiel.de", subject="p-2")
        )
        db.add(konto)
        db.commit()

    # Mit zwei lebenden Anbietern ist niemand gefaehrdet - richtig so.
    folgen = admin_client.get(f"/api/admin/oidc/{erster['id']}/folgen").json()
    assert folgen["gefaehrdet"] == []

    # Den zweiten abschalten. Die Verknuepfung dorthin bleibt stehen, taugt
    # aber nicht mehr als Weg hinein.
    aus = admin_client.patch(
        f"/api/admin/oidc/{zweiter['id']}?bestaetigt=true", json={"enabled": False}
    )
    assert aus.status_code == 200, aus.text

    folgen = admin_client.get(f"/api/admin/oidc/{erster['id']}/folgen").json()
    assert [k["username"] for k in folgen["gefaehrdet"]] == ["nur-sso"], folgen
    abgelehnt = admin_client.patch(
        f"/api/admin/oidc/{erster['id']}", json={"enabled": False}
    )
    assert abgelehnt.status_code == 409, abgelehnt.text

    # Und dasselbe, wenn der zweite ganz geloescht ist.
    weg = admin_client.delete(f"/api/admin/oidc/{zweiter['id']}?bestaetigt=true")
    assert weg.status_code == 204, weg.text
    folgen = admin_client.get(f"/api/admin/oidc/{erster['id']}/folgen").json()
    assert [k["username"] for k in folgen["gefaehrdet"]] == ["nur-sso"], folgen
