"""Einladungen mit Rechten und Hausordnung - und was beim Einloesen noch gilt.

Die Regeln selbst prueft ``test_kontorechte.py`` als reine Funktion. Hier geht es
um die Wege: den Assistenten (``/rechte/bewerten``), das Anlegen, das
Einloesen ueber den Link und das, was der Administrator danach sieht. Den Weg
ueber den Medienserver prueft ``test_mediaserver_login.py``.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AuthToken, Notification, NotificationType, TokenPurpose, User
from app.services import mail
from app.services.settings_service import save_settings

from .conftest import auth_headers, create_user
from .test_hausordnung_pfade import _speichern as hausordnung_speichern
from .test_onboarding import ZUGANG, _link_aus, _token_aus

RADARR_4K = {"radarr_uhd_url": "http://127.0.0.1:7178", "radarr_uhd_api_key": "schluessel-r4"}


@pytest.fixture
def postfach(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Eingerichteter Mailversand, der die Nachrichten nur einsammelt."""
    admin_client.put("/api/settings", json=ZUGANG)
    gesendet: list[EmailMessage] = []

    def _sende(_config: mail.MailConfig, nachricht: EmailMessage) -> None:
        gesendet.append(nachricht)

    monkeypatch.setattr(mail, "_sende", _sende)
    return gesendet


def _einrichten(**werte: object) -> None:
    with SessionLocal() as db:
        save_settings(db, werte)


def _einladen(client: TestClient, **daten: object) -> dict:
    antwort = client.post("/api/users/invitations", json={"email": "neu@example.com", **daten})
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def _roh(postfach: list[EmailMessage]) -> str:
    return _token_aus(_link_aus(postfach[-1], "/einladung/"))


def _einloesen(client: TestClient, postfach: list[EmailMessage], **daten: object):
    return client.post(
        f"/api/onboarding/invitation/{_roh(postfach)}",
        json={"username": "neuer", "password": "eigenes-pw-123", **daten},
    )


def _konto(name: str = "neuer") -> dict[str, object]:
    felder = (
        "auto_approve_movies",
        "auto_approve_series",
        "can_request_uhd_movies",
        "auto_approve_uhd",
        "storage_limit_gb",
        "hausordnung_gelesen",
        "hausordnung_gelesen_am",
        "hausordnung_akzeptiert",
    )
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == name).one()
        return {feld: getattr(konto, feld) for feld in felder}


def _einladung() -> AuthToken:
    with SessionLocal() as db:
        return db.query(AuthToken).filter(AuthToken.purpose == TokenPurpose.invitation).one()


def _meldungen() -> list[tuple[str, str | None]]:
    with SessionLocal() as db:
        return [
            (m.message_key, m.message_title)
            for m in db.query(Notification).filter(
                Notification.type == NotificationType.invitation_redeemed
            )
        ]


# ---------------------------------------------------------------------------
# 1. Der Assistent fragt nach
# ---------------------------------------------------------------------------


def test_der_assistent_erfaehrt_warum_ein_haken_gesperrt_ist(admin_client: TestClient) -> None:
    _einrichten(movie_root_folder_mode="approver")

    antwort = admin_client.post(
        "/api/users/rechte/bewerten", json={"role": "user", "auto_approve_movies": True}
    )

    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["auto_approve_movies"] == {
        "frei": False,
        "wirkt": False,
        "grund": "approver_picks_target",
    }
    assert daten["auto_approve_series"] == {"frei": True, "wirkt": False, "grund": None}
    assert daten["entfallen"] == ["auto_approve_movies"]


def test_pruefen_duerfen_nur_administratoren(admin_client: TestClient) -> None:
    create_user(admin_client, "leser")
    kopf = auth_headers(admin_client, "leser", "passwort-1234")

    antwort = admin_client.post("/api/users/rechte/bewerten", json={}, headers=kopf)

    assert antwort.status_code == 403


def test_auch_beim_pruefen_gibt_es_keine_einladung_an_ein_kind(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/users/rechte/bewerten", json={"role": "child"})

    assert antwort.status_code == 422


# ---------------------------------------------------------------------------
# 2. Anlegen und Einloesen
# ---------------------------------------------------------------------------


def test_die_einladung_bringt_ihre_rechte_ans_konto(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    _einrichten(**RADARR_4K)
    angelegt = _einladen(
        admin_client,
        auto_approve_movies=True,
        can_request_uhd_movies=True,
        auto_approve_uhd=True,
        storage_limit_gb=250,
    )
    assert angelegt["entfallen"] == []

    assert _einloesen(admin_client, postfach).status_code == 201

    konto = _konto()
    assert konto["auto_approve_movies"] is True
    assert konto["auto_approve_series"] is False
    assert konto["can_request_uhd_movies"] is True
    assert konto["auto_approve_uhd"] is True
    assert konto["storage_limit_gb"] == 250
    assert _meldungen() == [("notifications.invitationRedeemed", "neuer")]


def test_was_das_haus_nicht_hergibt_wird_gar_nicht_erst_gespeichert(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Wer an der Oberflaeche vorbei schickt, bekommt trotzdem nicht mehr - und erfaehrt es."""
    _einrichten(series_root_folder_mode="approver")

    angelegt = _einladen(admin_client, auto_approve_series=True, can_request_uhd_movies=True)

    assert angelegt["entfallen"] == ["auto_approve_series", "can_request_uhd_movies"]
    einladung = _einladung()
    assert einladung.invite_auto_approve_series is False
    assert einladung.invite_can_request_uhd_movies is False


def test_eine_einladung_fuer_administratoren_traegt_keine_grenzen(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Administratoren sind nie begrenzt. Eine gespeicherte Grenze kaeme erst
    nach einem Herabstufen zum Vorschein - als Ueberraschung."""
    _einladen(admin_client, role="admin", quota_movies_limit=3, storage_limit_gb=100)

    einladung = _einladung()
    assert einladung.invite_quota_movies is None
    assert einladung.invite_storage_limit_gb is None


def test_aendert_sich_das_haus_bis_zum_einloesen_gilt_der_stand_von_jetzt(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    _einladen(admin_client, auto_approve_movies=True, auto_approve_series=True)
    # Nach dem Einladen waehlt bei Filmen ploetzlich der Entscheider das Ziel.
    _einrichten(movie_root_folder_mode="approver")

    assert _einloesen(admin_client, postfach).status_code == 201

    konto = _konto()
    assert konto["auto_approve_movies"] is False
    assert konto["auto_approve_series"] is True
    assert _meldungen() == [("notifications.invitationRedeemedPartly", "neuer")]

    liste = admin_client.get("/api/users/invitations").json()
    assert len(liste) == 1
    assert liste[0]["konto"] == "neuer"
    assert liste[0]["eingeloest_am"] is not None
    assert liste[0]["entfallen"] == ["auto_approve_movies"]


def test_eine_eingeloeste_einladung_ohne_hinweis_verschwindet_aus_der_liste(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    _einladen(admin_client)
    _einloesen(admin_client, postfach)

    assert admin_client.get("/api/users/invitations").json() == []


def test_gesehen_nimmt_den_hinweis_weg(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    _einladen(admin_client, auto_approve_movies=True)
    _einrichten(movie_root_folder_mode="approver")
    _einloesen(admin_client, postfach)
    eintrag = admin_client.get("/api/users/invitations").json()[0]

    antwort = admin_client.post(f"/api/users/invitations/{eintrag['id']}/gesehen")

    assert antwort.status_code == 204
    assert admin_client.get("/api/users/invitations").json() == []
    # Am Konto aendert das nichts: Was entfallen ist, bleibt entfallen.
    assert _konto()["auto_approve_movies"] is False


def test_gesehen_gibt_es_nur_fuer_eingeloeste_mit_hinweis(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    offen = _einladen(admin_client)

    antwort = admin_client.post(f"/api/users/invitations/{offen['id']}/gesehen")

    assert antwort.status_code == 404
    assert len(admin_client.get("/api/users/invitations").json()) == 1


# ---------------------------------------------------------------------------
# 3. Hausordnung
# ---------------------------------------------------------------------------


def test_die_hausordnung_kommt_mit_wenn_die_einladung_sie_zeigen_soll(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    hausordnung_speichern(admin_client)
    _einladen(admin_client, hausordnung=True)

    info = admin_client.get(f"/api/onboarding/invitation/{_roh(postfach)}").json()

    assert info["hausordnung"] == {
        "titel": "Bei uns zu Hause",
        "inhalt": "## Regeln\n\nBitte lesen.",
        "quittierbar": True,
    }


def test_ohne_haken_kommt_keine_hausordnung_mit(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    hausordnung_speichern(admin_client)
    _einladen(admin_client)

    info = admin_client.get(f"/api/onboarding/invitation/{_roh(postfach)}").json()

    assert info["hausordnung"] is None


def test_ein_entwurf_kommt_nie_mit(admin_client: TestClient, postfach: list[EmailMessage]) -> None:
    """Ein unveroeffentlichter Text gehoert dem Betreiber allein - auch vor Eingeladenen."""
    hausordnung_speichern(admin_client)
    _einladen(admin_client, hausordnung=True)
    hausordnung_speichern(admin_client, veroeffentlicht=False)

    info = admin_client.get(f"/api/onboarding/invitation/{_roh(postfach)}").json()

    assert info["hausordnung"] is None


def test_eine_spaeter_veroeffentlichte_hausordnung_kommt_nicht_nachtraeglich_dazu(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Beim Einladen gab es keine Hausordnung, also war der Haken gesperrt.

    Wird sie danach veroeffentlicht, zeigt die Einladung sie trotzdem nicht: Der
    Administrator hat nie zugesagt, dass diese Person sie beim Einloesen sieht.
    """
    angelegt = _einladen(admin_client, hausordnung=True)
    assert angelegt["entfallen"] == ["hausordnung"]
    hausordnung_speichern(admin_client)

    info = admin_client.get(f"/api/onboarding/invitation/{_roh(postfach)}").json()

    assert info["hausordnung"] is None


def test_administratoren_bekommen_die_hausordnung_nicht_vorgelegt(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    hausordnung_speichern(admin_client)
    angelegt = _einladen(admin_client, role="admin", hausordnung=True)

    info = admin_client.get(f"/api/onboarding/invitation/{_roh(postfach)}").json()

    assert info["hausordnung"] is None
    assert angelegt["entfallen"] == ["hausordnung"]


def test_die_entscheidung_zur_hausordnung_landet_am_neuen_konto(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """⚠️ Und nicht am Administrator, der im selben Browser noch angemeldet ist.

    Festgehalten wird die laufende Fassung. Hier ist es die zweite, damit eine
    fest eingetragene 1 auffiele.
    """
    hausordnung_speichern(admin_client)
    hausordnung_speichern(admin_client, erneut_lesen=True)
    _einladen(admin_client, hausordnung=True)

    assert _einloesen(admin_client, postfach, hausordnung_akzeptiert=False).status_code == 201

    konto = _konto()
    assert konto["hausordnung_gelesen"] == 2
    assert konto["hausordnung_gelesen_am"] is not None
    assert konto["hausordnung_akzeptiert"] is False
    assert _konto("admin")["hausordnung_gelesen"] is None


def test_ohne_quittierbare_hausordnung_wird_nichts_festgehalten(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    hausordnung_speichern(admin_client, quittierbar=False)
    _einladen(admin_client, hausordnung=True)

    _einloesen(admin_client, postfach, hausordnung_akzeptiert=True)

    konto = _konto()
    assert konto["hausordnung_gelesen"] is None
    assert konto["hausordnung_akzeptiert"] is None


def test_ohne_haken_zaehlt_eine_mitgeschickte_entscheidung_nicht(
    admin_client: TestClient, postfach: list[EmailMessage]
) -> None:
    """Wer die Hausordnung nie gesehen hat, kann ihr auch nicht zugestimmt haben."""
    hausordnung_speichern(admin_client)
    _einladen(admin_client)

    _einloesen(admin_client, postfach, hausordnung_akzeptiert=True)

    assert _konto()["hausordnung_akzeptiert"] is None
