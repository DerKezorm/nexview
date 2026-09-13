"""Einladungen mit Zugang zu Medienservern: einladen, verknuepfen, einloesen, nachholen.

Gemockt wird an der Abstraktions-Grenze, wie in ``test_mediaserver_login.py``:
Ein erfundener Server antwortet auf Zuruf. Was dabei ueber die Leitung geht,
prueft ``test_serverkonten_anbindung.py``; hier geht es um die Reihenfolge und
um das, was Nexview sich merkt.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.crypto import decrypt, encrypt
from app.db import SessionLocal
from app.models import (
    AuthToken,
    EinladungsServer,
    MediaServerConnection,
    Notification,
    NotificationType,
    TokenPurpose,
    User,
    UserMediaServerAccount,
)
from app.services import einladung_server, mail, oidc_accounts
from app.services import mediaserver_accounts as konten
from app.services.mediaserver import Bibliothek, ExternalAccount, LoginChallenge, MediaServerError
from app.services.mediaserver_accounts import KontoFehler
from app.services.settings_service import load_settings

from .conftest import create_user
from .test_oidc_dienst import _identitaet
from .test_onboarding import ZUGANG, _link_aus, _token_aus

PASSWORT = "eigenes-pw-123"


@pytest.fixture
def postfach(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Eingerichteter Mailversand, der nur einsammelt - wie in ``test_onboarding``.

    ⚠️ Hier noch einmal statt importiert: Ein importierter Fixture-Name, der als
    Parameter wiederkehrt, meldet ruff als F811, und die CI prueft auch die Tests.
    """
    admin_client.put("/api/settings", json=ZUGANG)
    gesendet: list[EmailMessage] = []

    def _sende(_config: mail.MailConfig, nachricht: EmailMessage) -> None:
        gesendet.append(nachricht)

    monkeypatch.setattr(mail, "_sende", _sende)
    return gesendet


class FakeServer:
    """Ein Medienserver, der genau das antwortet, was der Test vorgibt."""

    def __init__(self, provider: str, label: str, bibliotheken: list[Bibliothek]) -> None:
        self.provider = provider
        self.label = label
        self._bibliotheken = bibliotheken
        self.vorhandene_namen: set[str] = set()
        self.angelegt: list[tuple[str, str, list[str], str | None]] = []
        #: Scheitert einmal mit diesem Fehler, danach klappt es.
        self.anlegen_fehler: MediaServerError | None = None
        self.freigaben: list[tuple[str, list[str]]] = []
        self.freigabe_fehler: MediaServerError | None = None
        self.schon_freigegeben: set[str] = set()
        self.angenommen: list[str] = []
        self.gast = ExternalAccount(
            provider=provider,
            account_id="4711",
            username="gast-plex",
            email="gast@example.com",
            thumb=None,
        )

    async def bibliotheken(self) -> list[Bibliothek]:
        return list(self._bibliotheken)

    async def name_vergeben(self, name: str) -> bool:
        return name.casefold() in {n.casefold() for n in self.vorhandene_namen}

    async def konto_anlegen(
        self, name: str, passwort: str, bibliotheken: list[str], *, konto: str | None = None
    ) -> str:
        self.angelegt.append((name, passwort, list(bibliotheken), konto))
        if self.anlegen_fehler is not None:
            fehler, self.anlegen_fehler = self.anlegen_fehler, None
            raise fehler
        return konto or f"{self.provider}-konto-1"

    async def begin_login(self) -> LoginChallenge:
        return LoginChallenge(ref="99", code="ABCD", auth_url="https://app.plex.tv/auth#?code=ABCD")

    async def poll_login(self, ref: str, code: str = "") -> str | None:
        return "gast-token"

    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        return self.gast

    async def hat_freigabe(self, konto: str) -> bool:
        return konto in self.schon_freigegeben

    async def freigeben(self, eingeladen: str, bibliotheken: list[str]) -> None:
        if self.freigabe_fehler is not None:
            fehler, self.freigabe_fehler = self.freigabe_fehler, None
            raise fehler
        self.freigaben.append((eingeladen, list(bibliotheken)))

    async def einladung_annehmen(self, gast_token: str) -> bool:
        self.angenommen.append(gast_token)
        return True


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeServer]:
    fakes = {
        "plex": FakeServer(
            "plex", "Plex", [Bibliothek("11", "Filme", "movie"), Bibliothek("12", "Serien", "show")]
        ),
        "jellyfin": FakeServer(
            "jellyfin",
            "Jellyfin",
            [Bibliothek("jf-filme", "Filme", "movies"), Bibliothek("jf-serien", "Serien", "tvshows")],
        ),
        "emby": FakeServer("emby", "Emby", [Bibliothek("em-filme", "Filme", "movies")]),
    }
    monkeypatch.setattr(
        einladung_server,
        "media_server_for_setup",
        lambda _settings, provider="plex", url="": fakes[provider],
    )
    return fakes


def _verbinden(*anbieter: str) -> None:
    with SessionLocal() as db:
        for provider in anbieter:
            db.add(
                MediaServerConnection(
                    provider=provider,
                    machine_id=f"{provider}-maschine",
                    name=f"{provider} daheim",
                    token=encrypt("admin-token"),
                )
            )
        db.commit()


def _einladen(client: TestClient, *server: dict) -> dict:
    antwort = client.post(
        "/api/users/invitations", json={"email": "neu@example.com", "server": list(server)}
    )
    assert antwort.status_code == 201, antwort.text
    return antwort.json()


def _roh(nachrichten: list[EmailMessage]) -> str:
    return _token_aus(_link_aus(nachrichten[-1], "/einladung/"))


def _einloesen(client: TestClient, roh: str, name: str = "neuer"):
    return client.post(
        f"/api/onboarding/invitation/{roh}", json={"username": name, "password": PASSWORT}
    )


def _plex_verknuepfen(client: TestClient, roh: str):
    start = client.post(f"/api/onboarding/invitation/{roh}/server/plex/start")
    assert start.status_code == 200, start.text
    return client.post(
        f"/api/onboarding/invitation/{roh}/server/plex/poll",
        json={"poll_token": start.json()["poll_token"]},
    )


def _ziele() -> dict[str, EinladungsServer]:
    with SessionLocal() as db:
        return {ziel.provider: ziel for ziel in db.query(EinladungsServer).all()}


def _meldungen(art: NotificationType) -> list[str]:
    with SessionLocal() as db:
        return [m.message_key for m in db.query(Notification).filter(Notification.type == art)]


def _verknuepfungen(name: str) -> dict[str, UserMediaServerAccount]:
    with SessionLocal() as db:
        benutzer = db.query(User).filter(User.username == name).one()
        return {zeile.provider: zeile for zeile in benutzer.mediaserver_accounts}


def _gibt_es(name: str) -> bool:
    with SessionLocal() as db:
        return db.query(User).filter(User.username == name).one_or_none() is not None


# --- Einladen ----------------------------------------------------------------


def test_der_assistent_sieht_alle_drei_auch_die_nicht_verbundenen(
    admin_client: TestClient, server: dict[str, FakeServer]
) -> None:
    _verbinden("jellyfin")

    antwort = admin_client.get("/api/users/invitations/server")

    assert antwort.status_code == 200, antwort.text
    je_anbieter = {eintrag["provider"]: eintrag for eintrag in antwort.json()}
    assert set(je_anbieter) == {"plex", "jellyfin", "emby"}
    assert je_anbieter["jellyfin"]["stand"]["frei"] is True
    assert [b["kennung"] for b in je_anbieter["jellyfin"]["bibliotheken"]] == ["jf-filme", "jf-serien"]
    assert je_anbieter["plex"]["stand"] == {"frei": False, "wirkt": False, "grund": "server_not_connected"}
    assert je_anbieter["plex"]["bibliotheken"] == []


def test_einladen_merkt_sich_server_und_bibliotheken(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("jellyfin", "plex")

    antwort = _einladen(
        admin_client,
        {"provider": "jellyfin", "bibliotheken": ["jf-filme"]},
        {"provider": "plex", "bibliotheken": ["11", "12"]},
    )

    assert [(z["provider"], z["bibliotheken"], z["zustand"]) for z in antwort["server"]] == [
        ("jellyfin", ["Filme"], "offen"),
        ("plex", ["Filme", "Serien"], "offen"),
    ]
    assert _ziele()["jellyfin"].machine_id == "jellyfin-maschine"
    info = admin_client.get(f"/api/onboarding/invitation/{_roh(postfach)}").json()
    assert [(z["provider"], z["art"]) for z in info["server"]] == [
        ("jellyfin", "konto"),
        ("plex", "freigabe"),
    ]


@pytest.mark.parametrize(
    ("verbunden", "wunsch", "kennung"),
    [
        pytest.param(("jellyfin",), ["weg"], "invite_library_unknown", id="unbekannte-bibliothek"),
        pytest.param((), ["jf-filme"], "invite_server_not_connected", id="nicht-verbunden"),
        pytest.param(("jellyfin",), [], "invite_server_no_library", id="ohne-bibliothek"),
    ],
)
def test_eine_unpassende_serverwahl_legt_gar_keine_einladung_an(
    admin_client: TestClient,
    postfach: list[EmailMessage],
    server: dict[str, FakeServer],
    verbunden: tuple[str, ...],
    wunsch: list[str],
    kennung: str,
) -> None:
    _verbinden(*verbunden)

    antwort = admin_client.post(
        "/api/users/invitations",
        json={
            "email": "neu@example.com",
            "server": [{"provider": "jellyfin", "bibliotheken": wunsch}],
        },
    )

    assert antwort.status_code == 422, antwort.text
    assert antwort.json()["detail"]["code"] == kennung
    with SessionLocal() as db:
        assert db.query(AuthToken).filter(AuthToken.purpose == TokenPurpose.invitation).count() == 0
    assert postfach == []


# --- Jellyfin und Emby -------------------------------------------------------


def test_das_serverkonto_entsteht_mit_name_und_passwort_aus_dem_formular(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("jellyfin")
    _einladen(admin_client, {"provider": "jellyfin", "bibliotheken": ["jf-filme"]})

    antwort = _einloesen(admin_client, _roh(postfach))

    assert antwort.status_code == 201, antwort.text
    assert server["jellyfin"].angelegt == [("neuer", PASSWORT, ["jf-filme"], None)]
    assert [(z["provider"], z["zustand"]) for z in antwort.json()["server"]] == [("jellyfin", "fertig")]
    assert _verknuepfungen("neuer")["jellyfin"].account_id == "jellyfin-konto-1"
    assert _meldungen(NotificationType.invitation_redeemed) == ["notifications.invitationRedeemed"]


def test_scheitert_ein_serverkonto_entsteht_kein_nexview_konto_und_der_naechste_versuch_setzt_fort(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("jellyfin")
    _einladen(admin_client, {"provider": "jellyfin", "bibliotheken": ["jf-filme"]})
    roh = _roh(postfach)
    server["jellyfin"].anlegen_fehler = MediaServerError(
        "Jellyfin zeigt dem Konto andere Bibliotheken.",
        code="mediaserver_libraries_mismatch",
        service="Jellyfin",
        konto="jf-7",
    )

    erster = _einloesen(admin_client, roh)

    assert erster.status_code == 502, erster.text
    assert erster.json()["detail"]["code"] == "invite_server_incomplete"
    assert not _gibt_es("neuer")
    assert admin_client.get(f"/api/onboarding/invitation/{roh}").status_code == 200
    assert _ziele()["jellyfin"].konto == "jf-7"

    zweiter = _einloesen(admin_client, roh)

    assert zweiter.status_code == 201, zweiter.text
    # Fortgesetzt, nicht neu angelegt: Das zweite Mal geht die Kontonummer mit.
    assert [aufruf[3] for aufruf in server["jellyfin"].angelegt] == [None, "jf-7"]
    assert _verknuepfungen("neuer")["jellyfin"].account_id == "jf-7"


def test_ein_angefangenes_serverkonto_haelt_den_namen_fest(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("emby")
    _einladen(admin_client, {"provider": "emby", "bibliotheken": ["em-filme"]})
    roh = _roh(postfach)
    server["emby"].anlegen_fehler = MediaServerError("Emby hakt.", konto="em-3")
    assert _einloesen(admin_client, roh).status_code == 502

    antwort = _einloesen(admin_client, roh, name="anderer")

    assert antwort.status_code == 409, antwort.text
    assert antwort.json()["detail"]["code"] == "invite_username_fixed"
    assert len(server["emby"].angelegt) == 1
    assert not _gibt_es("anderer")


def test_die_namenspruefung_fragt_auch_den_server(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("jellyfin")
    _einladen(admin_client, {"provider": "jellyfin", "bibliotheken": ["jf-filme"]})
    server["jellyfin"].vorhandene_namen = {"Neuer"}

    antwort = admin_client.get(
        f"/api/onboarding/invitation/{_roh(postfach)}/namen", params={"username": "neuer"}
    )

    assert antwort.json() == {"nexview": True, "server": {"jellyfin": False}}


def test_ein_getrennter_server_entfaellt_beim_einloesen(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("jellyfin")
    _einladen(admin_client, {"provider": "jellyfin", "bibliotheken": ["jf-filme"]})
    with SessionLocal() as db:
        db.query(MediaServerConnection).delete()
        db.commit()

    antwort = _einloesen(admin_client, _roh(postfach))

    assert antwort.status_code == 201, antwort.text
    assert server["jellyfin"].angelegt == []
    assert _ziele()["jellyfin"].zustand == EinladungsServer.FEHLT
    assert '"invite_server_gone"' in (_ziele()["jellyfin"].fehler or "")
    assert _meldungen(NotificationType.invitation_redeemed) == [
        "notifications.invitationRedeemedPartly"
    ]


# --- Plex ----------------------------------------------------------------------


def test_ohne_verknuepftes_plex_konto_entsteht_nichts(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("plex")
    _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11"]})

    antwort = _einloesen(admin_client, _roh(postfach))

    assert antwort.status_code == 409, antwort.text
    assert antwort.json()["detail"]["code"] == "invite_link_first"
    assert not _gibt_es("neuer")


def test_plex_verknuepfen_einloesen_freigeben_und_annehmen(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("plex")
    _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11", "12"]})
    roh = _roh(postfach)

    verknuepft = _plex_verknuepfen(admin_client, roh)
    assert verknuepft.json() == {"status": "ready", "konto_name": "gast-plex"}

    antwort = _einloesen(admin_client, roh)

    assert antwort.status_code == 201, antwort.text
    assert server["plex"].freigaben == [("gast@example.com", ["11", "12"])]
    assert server["plex"].angenommen == ["gast-token"]
    assert _ziele()["plex"].zustand == EinladungsServer.FERTIG
    # Das Token der Person liegt jetzt an ihrer Verknuepfung, nicht mehr an der Einladung.
    zeile = _verknuepfungen("neuer")["plex"]
    assert (zeile.account_id, decrypt(zeile.token or "")) == ("4711", "gast-token")
    assert _ziele()["plex"].token is None
    assert _meldungen(NotificationType.invitation_redeemed) == ["notifications.invitationRedeemed"]


def test_wer_schon_zugang_hat_behaelt_seine_freigabe(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("plex")
    _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11"]})
    roh = _roh(postfach)
    server["plex"].schon_freigegeben = {"4711"}
    _plex_verknuepfen(admin_client, roh)

    assert _einloesen(admin_client, roh).status_code == 201

    assert server["plex"].freigaben == []
    assert _ziele()["plex"].zustand == EinladungsServer.FERTIG


def test_ein_fremder_anmeldevorgang_gilt_nicht_fuer_die_einladung(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("plex")
    _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11"]})
    with SessionLocal() as db:
        fremd = konten.start_challenge(
            db, "plex", LoginChallenge(ref="1", code="XYZ", auth_url="https://app.plex.tv")
        )

    antwort = admin_client.post(
        f"/api/onboarding/invitation/{_roh(postfach)}/server/plex/poll",
        json={"poll_token": fremd},
    )

    assert antwort.status_code == 403, antwort.text
    assert antwort.json()["detail"]["code"] == "mediaserver_challenge_foreign"
    assert _ziele()["plex"].konto is None


def test_ein_schon_verknuepftes_plex_konto_haelt_die_einladung_an(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    """Entscheidung vom 12.09.2026: anhalten, Hinweis, Einladung bleibt offen, nichts geht ueber."""
    _verbinden("plex")
    create_user(admin_client, "vorhanden")
    with SessionLocal() as db:
        vorhanden = db.query(User).filter(User.username == "vorhanden").one()
        konten.link(vorhanden, server["plex"].gast)
        db.commit()
    _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11"]})
    roh = _roh(postfach)

    erster = _plex_verknuepfen(admin_client, roh)
    zweiter = _plex_verknuepfen(admin_client, roh)

    for antwort in (erster, zweiter):
        assert antwort.status_code == 409, antwort.text
        assert antwort.json()["detail"]["code"] == "invite_account_linked_elsewhere"
    # Einmal Bescheid, nicht bei jedem Versuch.
    assert _meldungen(NotificationType.invitation_on_hold) == ["notifications.invitationAccountTaken"]
    assert _ziele()["plex"].konto is None
    assert admin_client.get(f"/api/onboarding/invitation/{roh}").status_code == 200
    assert _einloesen(admin_client, roh).json()["detail"]["code"] == "invite_link_first"
    assert not _gibt_es("neuer")


def test_eine_gescheiterte_freigabe_steht_zum_nachholen_in_der_liste(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("plex")
    einladung = _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11"]})
    roh = _roh(postfach)
    _plex_verknuepfen(admin_client, roh)
    server["plex"].freigabe_fehler = MediaServerError("plex.tv antwortet nicht.")

    antwort = _einloesen(admin_client, roh)

    # Das Nexview-Konto steht trotzdem, und geloescht wurde nichts.
    assert antwort.status_code == 201, antwort.text
    assert _gibt_es("neuer")
    assert _meldungen(NotificationType.invitation_redeemed) == [
        "notifications.invitationRedeemedPartly"
    ]
    liste = admin_client.get("/api/users/invitations").json()
    assert [(z["provider"], z["zustand"], z["nachholbar"]) for z in liste[0]["server"]] == [
        ("plex", "fehlt", True)
    ]

    nachgeholt = admin_client.post(
        f"/api/users/invitations/{einladung['id']}/server/plex/nachholen"
    )

    assert nachgeholt.status_code == 200, nachgeholt.text
    assert nachgeholt.json()["server"][0]["zustand"] == "fertig"
    assert server["plex"].freigaben == [("gast@example.com", ["11"])]
    assert server["plex"].angenommen == ["gast-token"]


def test_gesehen_nimmt_den_hinweis_und_das_nachholen_weg(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    _verbinden("plex")
    einladung = _einladen(admin_client, {"provider": "plex", "bibliotheken": ["11"]})
    roh = _roh(postfach)
    _plex_verknuepfen(admin_client, roh)
    server["plex"].freigabe_fehler = MediaServerError("plex.tv antwortet nicht.")
    assert _einloesen(admin_client, roh).status_code == 201

    gesehen = admin_client.post(f"/api/users/invitations/{einladung['id']}/gesehen")

    assert gesehen.status_code == 204, gesehen.text
    assert admin_client.get("/api/users/invitations").json() == []
    nachholen = admin_client.post(f"/api/users/invitations/{einladung['id']}/server/plex/nachholen")
    assert nachholen.status_code == 404


# --- Andere Wege herein ------------------------------------------------------


def test_mit_servern_gilt_die_einladung_nur_ueber_den_link(
    admin_client: TestClient, postfach: list[EmailMessage], server: dict[str, FakeServer]
) -> None:
    """Sonst entstuende das Nexview-Konto still ohne die Konten auf den Servern."""
    _verbinden("jellyfin")
    _einladen(admin_client, {"provider": "jellyfin", "bibliotheken": ["jf-filme"]})
    ueber_plex = ExternalAccount(
        provider="plex", account_id="999", username="neu", email="neu@example.com", thumb=None
    )

    with SessionLocal() as db:
        with pytest.raises(KontoFehler) as medienserver:
            konten.resolve(db, load_settings(db), ueber_plex)
        db.rollback()
        with pytest.raises(KontoFehler) as anmeldedienst:
            oidc_accounts.resolve(
                db, load_settings(db), _identitaet(email="neu@example.com"), auto_create=True
            )
        db.rollback()

    assert medienserver.value.code == "invite_use_link"
    assert anmeldedienst.value.code == "invite_use_link"
    with SessionLocal() as db:
        assert db.query(User).filter(User.email == "neu@example.com").count() == 0
        assert db.query(AuthToken).filter(AuthToken.purpose == TokenPurpose.invitation).one().open
