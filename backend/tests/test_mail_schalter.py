"""Die Mail-Schalter im Profil - und dass jeder von ihnen wirklich etwas tut.

⚠️ **Diese Datei gibt es, weil zwei Schalter nichts taten.**

``update_me`` schrieb die Schalter über eine **von Hand gepflegte Liste**
zurück. Als später ``mail_storage`` und ``mail_child_wish`` dazukamen, wurden
sie ins Schema und in die Oberfläche eingetragen - aber nicht in diese Liste.
Ergebnis: Zwei Haken ließen sich setzen, sahen danach richtig aus und
bewirkten nichts. Nichts ist dabei je gescheitert, also fiel es auch nichts
auf.

Die Liste ist jetzt aus dem Schema abgeleitet. Die Tests hier halten die drei
Ebenen zusammen: Konto, Schema, gespeicherter Wert.

Seit Web Push gilt dasselbe fuer die ``push_*``-Haken: dieselbe Bauart, ein
zweiter Weg, derselbe Fehler moeglich. Deshalb laufen beide Vorsilben hier
durch.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import User
from app.schemas import ProfileUpdate

from .conftest import ADMIN

#: Die Haken, die ``update_me`` aus dem Schema ableitet - Mail und Web Push.
VORSILBEN = ("mail_", "push_")


def _schalter_am_konto() -> list[str]:
    return sorted(c.name for c in User.__table__.columns if c.name.startswith(VORSILBEN))


def _schalter_im_schema() -> list[str]:
    return sorted(f for f in ProfileUpdate.model_fields if f.startswith(VORSILBEN))


def test_beide_wege_fuehren_dieselben_haken() -> None:
    """Web Push hat jeden Mail-Haken - bis auf den Monatsbericht.

    ⚠️ Der ist eine Tabelle in einer Mail, keine Meldung fuer den
    Sperrbildschirm. Alles andere, was per Mail zu haben ist, muss auch per
    Push zu haben sein, sonst fehlt auf der zweiten Seite still ein Haken.
    """
    mail = {f.removeprefix("mail_") for f in _schalter_im_schema() if f.startswith("mail_")}
    push = {f.removeprefix("push_") for f in _schalter_im_schema() if f.startswith("push_")}

    assert mail - push == {"cleanup"}
    assert push - mail == set()


def test_konto_und_schema_fuehren_dieselben_schalter() -> None:
    """⚠️ Die Probe, die den Fehler gefunden hätte.

    Ein Schalter am Konto, den das Schema nicht kennt, lässt sich nie setzen.
    Einer im Schema, den das Konto nicht hat, ist ein Feld ins Leere.
    """
    am_konto = set(_schalter_am_konto())
    im_schema = set(_schalter_im_schema())

    assert im_schema - am_konto == set(), "Im Schema, aber nicht am Konto"
    assert am_konto - im_schema == set(), "Am Konto, aber im Schema vergessen"


@pytest.mark.parametrize("schalter", _schalter_im_schema())
def test_jeder_schalter_laesst_sich_wirklich_setzen(
    admin_client: TestClient, schalter: str
) -> None:
    """Einzeln, damit im Fehlerfall dasteht **welcher** nicht ankommt.

    Genau das war der Fall: Zwei von zehn kamen nicht an, und weil die Antwort
    trotzdem 200 war, sah alles richtig aus.
    """
    # Alle Schalter stehen anfangs auf aus - also einschalten und nachsehen.
    antwort = admin_client.patch("/api/auth/me", json={schalter: True})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()[schalter] is True, f"{schalter} kam nicht an"

    # Und wieder aus: Ein Schalter, der nur in eine Richtung geht, ist keiner.
    antwort = admin_client.patch("/api/auth/me", json={schalter: False})
    assert antwort.json()[schalter] is False, f"{schalter} liess sich nicht abschalten"


def test_der_gespeicherte_wert_ueberlebt_das_neuladen(admin_client: TestClient) -> None:
    """Nicht nur die Antwort stimmen lassen, sondern die Datenbank."""
    admin_client.patch("/api/auth/me", json={"mail_cleanup": True})
    assert admin_client.get("/api/auth/me").json()["mail_cleanup"] is True


def test_nicht_genannte_schalter_bleiben_unberuehrt(admin_client: TestClient) -> None:
    """Eine Teiländerung darf nichts anderes umlegen."""
    admin_client.patch("/api/auth/me", json={"mail_ticket": True, "mail_cleanup": True})
    admin_client.patch("/api/auth/me", json={"mail_ticket": False})

    daten = admin_client.get("/api/auth/me").json()
    assert daten["mail_ticket"] is False
    assert daten["mail_cleanup"] is True


def test_alle_schalter_starten_aus(client: TestClient) -> None:
    """⚠️ Post ist Opt-in, ausnahmslos.

    Ein Konto, das ungefragt Mail bekommt, ist der schnellste Weg, jemandem
    die Anwendung zu verleiden - und danach filtert er auch das weg, was er
    gebraucht hätte.
    """
    antwort = client.post("/api/setup/admin", json=ADMIN)
    assert antwort.status_code == 201

    daten = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {antwort.json()['access_token']}"},
    ).json()
    for schalter in _schalter_im_schema():
        assert daten[schalter] is False, f"{schalter} steht ungefragt an"
