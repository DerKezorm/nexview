"""Beim Loeschen eines Kontos: der Zugang auf den Medienservern (``services/serverkonten``).

Entschieden am 12. und 13.09.2026: Der Administrator wird gefragt. Vorausgewaehlt
ist nur, was eine Nexview-Einladung angelegt hat. Wo Nexview nur freigibt, endet
die Freigabe dieses Servers. Scheitert ein Server, bricht das Loeschen ab, bevor
Bestand und Konto angefasst werden.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.crypto import encrypt
from app.db import SessionLocal
from app.models import (
    AuthToken,
    EinladungsServer,
    MediaServerConnection,
    StorageEntry,
    TokenPurpose,
    User,
    UserMediaServerAccount,
    utcnow,
)
from app.services import serverkonten
from app.services.mediaserver import MediaServerError
from app.services.radarr import LibraryEntry as MovieEntry

from .conftest import create_user
from .test_kontoaufloesung import _instanzen, _posten

SPRACHEN = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"


class FakeServer:
    """Ein Medienserver, der sich merkt, was mit ihm geschah."""

    def __init__(self, provider: str, reihenfolge: list[str]) -> None:
        self.provider = provider
        self._reihenfolge = reihenfolge
        #: Kennung -> ist Administrator. Was fehlt, gibt es auf dem Server nicht.
        self.konten: dict[str, bool] = {}
        self.freigaben: set[str] = set()
        #: Antwortet beim Nachsehen nicht.
        self.aus = False
        self.entfernen_fehler: MediaServerError | None = None

    async def ist_administrator(self, konto: str) -> bool | None:
        if self.aus:
            raise MediaServerError("aus", code="mediaserver_offline")
        return self.konten.get(konto)

    async def hat_freigabe(self, konto: str) -> bool:
        if self.aus:
            raise MediaServerError("aus", code="mediaserver_offline")
        return konto in self.freigaben

    async def konto_loeschen(self, konto: str) -> bool:
        if self.entfernen_fehler is not None:
            raise self.entfernen_fehler
        self._reihenfolge.append(f"{self.provider}:konto:{konto}")
        return self.konten.pop(konto, None) is not None

    async def freigabe_entfernen(self, konto: str) -> bool:
        if self.entfernen_fehler is not None:
            raise self.entfernen_fehler
        self._reihenfolge.append(f"{self.provider}:freigabe:{konto}")
        vorhanden = konto in self.freigaben
        self.freigaben.discard(konto)
        return vorhanden


@pytest.fixture
def reihenfolge() -> list[str]:
    return []


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch, reihenfolge: list[str]) -> dict[str, FakeServer]:
    fakes = {anbieter: FakeServer(anbieter, reihenfolge) for anbieter in ("jellyfin", "emby", "plex")}
    monkeypatch.setattr(
        serverkonten,
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


def _verknuepft(user_id: int, provider: str, konto: str, name: str = "kim") -> None:
    with SessionLocal() as db:
        db.add(
            UserMediaServerAccount(
                user_id=user_id, provider=provider, account_id=konto, username=name
            )
        )
        db.commit()


def _aus_einladung(
    user_id: int,
    provider: str,
    konto: str,
    *,
    maschine: str = "",
    zustand: str = EinladungsServer.FERTIG,
) -> None:
    jetzt = utcnow().replace(tzinfo=None)
    with SessionLocal() as db:
        token = AuthToken(
            purpose=TokenPurpose.invitation,
            token_hash=hashlib.sha256(f"{provider}:{konto}".encode()).hexdigest(),
            email="kim@example.com",
            expires_at=jetzt + timedelta(days=7),
            used_at=jetzt,
            redeemed_by=user_id,
        )
        token.server.append(
            EinladungsServer(
                provider=provider,
                machine_id=maschine or f"{provider}-maschine",
                zustand=zustand,
                konto=konto,
                konto_name="kim",
            )
        )
        db.add(token)
        db.commit()


def _kim(client: TestClient) -> int:
    return create_user(client, "kim", "passwort-1234")["id"]


def _vorschau(client: TestClient, user_id: int) -> list[dict]:
    antwort = client.get(f"/api/users/{user_id}/aufloesung")
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["serverkonten"]


def _noch_da(user_id: int) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.id == user_id)) is not None


# --- Die Vorschau ----------------------------------------------------------------


def test_vorausgewaehlt_ist_nur_was_eine_einladung_angelegt_hat(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch, server: dict[str, FakeServer]
) -> None:
    _instanzen(monkeypatch)
    _verbinden("jellyfin", "plex")
    kim = _kim(arr_client)
    _aus_einladung(kim, "jellyfin", "j-1")
    _verknuepft(kim, "jellyfin", "j-1")
    _verknuepft(kim, "plex", "p-1", name="kim-plex")
    # Eine Einladung, deren Konto nie fertig wurde, hat auf dem Server nichts angelegt.
    _aus_einladung(kim, "emby", "e-1", zustand=EinladungsServer.FEHLT)
    server["jellyfin"].konten["j-1"] = False
    server["plex"].freigaben.add("p-1")

    assert _vorschau(arr_client, kim) == [
        {
            "provider": "jellyfin",
            "label": "Jellyfin",
            "konto": "j-1",
            "name": "kim",
            "art": "konto",
            "aus_einladung": True,
            "vorausgewaehlt": True,
            "grund": None,
        },
        {
            "provider": "plex",
            "label": "Plex",
            "konto": "p-1",
            "name": "kim-plex",
            "art": "freigabe",
            "aus_einladung": False,
            "vorausgewaehlt": False,
            "grund": None,
        },
    ]


def test_ein_geloestes_konto_aus_der_einladung_steht_trotzdem_da(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch, server: dict[str, FakeServer]
) -> None:
    """Im Profil geloest heisst nicht auf dem Server geloescht."""
    _instanzen(monkeypatch)
    _verbinden("jellyfin")
    kim = _kim(arr_client)
    _aus_einladung(kim, "jellyfin", "j-1")
    server["jellyfin"].konten["j-1"] = False

    (eintrag,) = _vorschau(arr_client, kim)
    assert (eintrag["konto"], eintrag["vorausgewaehlt"]) == ("j-1", True)


def test_die_vorschau_sagt_warum_etwas_nicht_geht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch, server: dict[str, FakeServer]
) -> None:
    _instanzen(monkeypatch)
    _verbinden("jellyfin", "plex")
    kim = _kim(arr_client)
    _verknuepft(kim, "emby", "e-1")
    _aus_einladung(kim, "jellyfin", "j-alt", maschine="alte-maschine")
    _verknuepft(kim, "plex", "p-1")

    gruende = {k["konto"]: (k["grund"], k["vorausgewaehlt"]) for k in _vorschau(arr_client, kim)}

    assert gruende == {
        "e-1": ("nicht_verbunden", False),
        "j-alt": ("anderer_server", False),
        "p-1": ("schon_weg", False),
    }


@pytest.mark.parametrize(
    ("einrichten", "erwartet"),
    [
        pytest.param(lambda s: s["jellyfin"].konten.update({"j-1": True}), "administrator", id="verwalter"),
        pytest.param(lambda s: None, "schon_weg", id="geloescht"),
        pytest.param(lambda s: setattr(s["jellyfin"], "aus", True), "nicht_erreichbar", id="aus"),
    ],
)
def test_was_nicht_geht_ist_nie_vorausgewaehlt(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    einrichten,
    erwartet: str,
) -> None:
    _instanzen(monkeypatch)
    _verbinden("jellyfin")
    kim = _kim(arr_client)
    _aus_einladung(kim, "jellyfin", "j-1")
    einrichten(server)

    (eintrag,) = _vorschau(arr_client, kim)
    assert (eintrag["grund"], eintrag["vorausgewaehlt"]) == (erwartet, False)


# --- Das Loeschen ------------------------------------------------------------------

BEIDE = [{"provider": "jellyfin", "konto": "j-1"}, {"provider": "plex", "konto": "p-1"}]


def _mit_film(monkeypatch: pytest.MonkeyPatch, reihenfolge: list[str]):
    radarr, _sonarr = _instanzen(
        monkeypatch, filme={603: MovieEntry(arr_id=42, has_file=True, monitored=True)}
    )
    original = radarr.remove

    async def remove(arr_id: int, delete_files: bool = True) -> None:
        reihenfolge.append(f"radarr:{arr_id}")
        await original(arr_id, delete_files)

    radarr.remove = remove
    return radarr


def _zwei_server(client: TestClient, server: dict[str, FakeServer]) -> tuple[int, int]:
    _verbinden("jellyfin", "plex")
    kim = _kim(client)
    _aus_einladung(kim, "jellyfin", "j-1")
    _verknuepft(kim, "jellyfin", "j-1")
    _verknuepft(kim, "plex", "p-1")
    server["jellyfin"].konten["j-1"] = False
    server["plex"].freigaben.add("p-1")
    with SessionLocal() as db:
        posten = _posten(db, kim, tmdb=603)
    return kim, posten


def test_erst_die_server_dann_bestand_und_konto(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    reihenfolge: list[str],
) -> None:
    radarr = _mit_film(monkeypatch, reihenfolge)
    kim, posten = _zwei_server(arr_client, server)

    antwort = arr_client.request(
        "DELETE", f"/api/users/{kim}", json={"loeschen": [posten], "serverkonten": BEIDE}
    )

    assert antwort.status_code == 204, antwort.text
    assert reihenfolge == ["jellyfin:konto:j-1", "plex:freigabe:p-1", "radarr:42"]
    assert radarr.entfernt == [(42, True)]
    assert not _noch_da(kim)


def test_scheitert_ein_server_bleiben_konto_und_bestand(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    reihenfolge: list[str],
) -> None:
    radarr = _mit_film(monkeypatch, reihenfolge)
    kim, posten = _zwei_server(arr_client, server)
    server["plex"].entfernen_fehler = MediaServerError("plex.tv ist nicht erreichbar.")

    antwort = arr_client.request(
        "DELETE", f"/api/users/{kim}", json={"loeschen": [posten], "serverkonten": BEIDE}
    )

    assert antwort.status_code == 502
    detail = antwort.json()["detail"]
    assert (detail["code"], detail["service"]) == ("server_account_removal_failed", "Plex")
    assert _noch_da(kim)
    assert radarr.entfernt == []
    with SessionLocal() as db:
        assert db.get(StorageEntry, posten) is not None
    # Was davor auf einem anderen Server entfernt wurde, bleibt entfernt.
    assert reihenfolge == ["jellyfin:konto:j-1"]


def test_ein_veralteter_bestand_faellt_vor_den_servern_auf(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    reihenfolge: list[str],
) -> None:
    """⚠️ Sonst stuende das Konto ohne seine Serverkonten da, nur weil ein Posten fehlte."""
    _mit_film(monkeypatch, reihenfolge)
    kim, _posten_id = _zwei_server(arr_client, server)

    antwort = arr_client.request("DELETE", f"/api/users/{kim}", json={"serverkonten": BEIDE})

    assert antwort.status_code == 409
    assert reihenfolge == []
    assert _noch_da(kim)


def test_was_seit_der_vorschau_nicht_mehr_geht_wird_abgelehnt(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    reihenfolge: list[str],
) -> None:
    _mit_film(monkeypatch, reihenfolge)
    kim, posten = _zwei_server(arr_client, server)
    server["jellyfin"].konten["j-1"] = True

    antwort = arr_client.request(
        "DELETE", f"/api/users/{kim}", json={"loeschen": [posten], "serverkonten": BEIDE}
    )

    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "server_accounts_changed"
    assert reihenfolge == []
    assert _noch_da(kim)


def test_ohne_auswahl_bleibt_auf_den_servern_alles(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    reihenfolge: list[str],
) -> None:
    """Wer die Schnittstelle ohne das Feld benutzt, loescht auf keinem Server etwas."""
    _mit_film(monkeypatch, reihenfolge)
    kim, posten = _zwei_server(arr_client, server)

    antwort = arr_client.request("DELETE", f"/api/users/{kim}", json={"loeschen": [posten]})

    assert antwort.status_code == 204, antwort.text
    assert reihenfolge == ["radarr:42"]
    assert server["jellyfin"].konten == {"j-1": False}
    assert server["plex"].freigaben == {"p-1"}


def test_ein_schon_entferntes_konto_haelt_nicht_auf(
    arr_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    server: dict[str, FakeServer],
    reihenfolge: list[str],
) -> None:
    _mit_film(monkeypatch, reihenfolge)
    kim, posten = _zwei_server(arr_client, server)
    del server["jellyfin"].konten["j-1"]

    antwort = arr_client.request(
        "DELETE", f"/api/users/{kim}", json={"loeschen": [posten], "serverkonten": BEIDE}
    )

    assert antwort.status_code == 204, antwort.text
    assert reihenfolge == ["plex:freigabe:p-1", "radarr:42"]
    assert not _noch_da(kim)


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_jeder_grund_hat_einen_text(sprache: str) -> None:
    """Die Oberflaeche baut den Schluessel zusammen, und den sieht ihr eigener Waechter nicht."""
    texte = json.loads((SPRACHEN / f"{sprache}.json").read_text(encoding="utf-8"))
    gruende = texte["adminUsers"]["dissolveServerReason"]
    assert set(gruende) == set(serverkonten.GRUENDE)
    assert all(isinstance(text, str) and text for text in gruende.values())
