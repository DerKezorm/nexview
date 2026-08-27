"""Gespeicherte Fehlermeldungen tragen eine Kennung - sonst bleiben sie deutsch.

Alle anderen Meldungen des Servers gehen als ``{"code", "message", ...}`` über
die HTTP-Antwort hinaus, und das Frontend baut den Satz daraus in der
eingestellten Sprache (siehe ``app/meldungen.py``).

**Eine Gruppe nahm einen anderen Weg**: Was beim Übergeben an Radarr oder
Sonarr schiefgeht, landet als fertiger Satz in ``MediaRequest.error_message``
und steht von dort im Verlauf - Wochen später, ohne die Antwort, die ihn
erzeugt hat. Gemeldet aus dem Betrieb: Die Oberfläche stand auf Englisch, und
unter der fehlgeschlagenen Serie stand trotzdem *„Für diese Serie kennt TMDB
noch keine TVDB-Kennung"*.

Das ist dieselbe Fehlerklasse wie in ``test_fehlermeldungen.py`` - sie fällt
beim Bauen nicht auf und zeigt sich erst im Betrieb. Deshalb steht hier ein
Test darauf, dass die Kennung wirklich **mitgespeichert** wird; dass es zu
jeder Kennung einen Text in beiden Sprachen gibt, hält der andere Test zu.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus, User
from app.services import library, requests_service
from app.services.arr import ArrError
from app.services.settings_service import load_settings


async def _uebergeben(anfrage_bauen) -> MediaRequest:
    """Eine Anfrage bis zur Übergabe durchspielen und zurückgeben."""
    with SessionLocal() as db:
        benutzer = db.query(User).filter(User.username == "admin").one()
        anfrage = anfrage_bauen(benutzer.id)
        db.add(anfrage)
        db.commit()

        with pytest.raises(requests_service.RequestError):
            await requests_service.push_to_arr(db, load_settings(db), anfrage)

        db.refresh(anfrage)
        db.expunge(anfrage)
        return anfrage


@pytest.mark.asyncio
async def test_fehlende_tvdb_kennung_wird_mit_kennung_gespeichert(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Fall aus dem Betrieb: Serie ohne TVDB-Kennung.

    Der frische Nachschlag wird stillgelegt - er ginge sonst wirklich zu TMDB
    (Tests laufen ohne Netz), und sein Erfolgsfall hat einen eigenen Test.
    """

    async def kein_nachschlag(_db: object, _settings: object, _tmdb_id: int) -> None:
        return None

    monkeypatch.setattr(
        requests_service.media, "tvdb_kennung_nachschlagen", kein_nachschlag
    )

    anfrage = await _uebergeben(
        lambda user_id: MediaRequest(
            user_id=user_id,
            media_type=MediaType.tv,
            tmdb_id=331616,
            tvdb_id=None,
            title="Death of the Pastor's Wife",
            season=1,
            status=RequestStatus.approved,
            quality_profile_id=1,
            root_folder_path="/data/TV-Shows",
        )
    )

    assert anfrage.status == RequestStatus.failed
    assert anfrage.error_detail is not None
    assert anfrage.error_detail["code"] == "tvdb_id_missing"
    # Der deutsche Satz bleibt daneben stehen: Er ist der Rückfall für alles,
    # was die API ohne die Nexview-Oberfläche benutzt.
    assert "TVDB" in (anfrage.error_message or "")


@pytest.mark.asyncio
async def test_werte_zum_einsetzen_liegen_bei(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Satz wie „{{service}} antwortet nicht" braucht seinen Dienstnamen.

    Ohne die Werte stünde dort nach dem Übersetzen eine Lücke - und die Meldung
    wäre schlechter als der deutsche Satz, den sie ersetzt.
    """

    class Attrappe:
        async def ensure_tag(self, _name: str) -> None:
            return None

        async def add(self, *_args: object, **_kwargs: object) -> None:
            raise ArrError(
                "Radarr antwortet nicht (Zeitüberschreitung).",
                code="arr_timeout",
                service="Radarr",
            )

    monkeypatch.setattr(
        library, "radarr_client", lambda _settings, _tier="standard": Attrappe()
    )

    anfrage = await _uebergeben(
        lambda user_id: MediaRequest(
            user_id=user_id,
            media_type=MediaType.movie,
            tmdb_id=42,
            title="Irgendein Film",
            status=RequestStatus.approved,
            quality_profile_id=1,
            root_folder_path="/data/Movies",
        )
    )

    assert anfrage.error_detail == {
        "code": "arr_timeout",
        "message": "Radarr antwortet nicht (Zeitüberschreitung).",
        "service": "Radarr",
    }


@pytest.mark.asyncio
async def test_geglueckte_uebergabe_raeumt_die_alte_meldung_weg(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst klebt die Begründung von gestern an einer Anfrage, die längst läuft."""

    class Attrappe:
        async def ensure_tag(self, _name: str) -> None:
            return None

        async def add(self, *_args: object, **_kwargs: object) -> dict[str, int]:
            return {"id": 4711}

    monkeypatch.setattr(
        library, "radarr_client", lambda _settings, _tier="standard": Attrappe()
    )

    with SessionLocal() as db:
        benutzer = db.query(User).filter(User.username == "admin").one()
        anfrage = MediaRequest(
            user_id=benutzer.id,
            media_type=MediaType.movie,
            tmdb_id=43,
            title="Zweiter Versuch",
            status=RequestStatus.approved,
            quality_profile_id=1,
            root_folder_path="/data/Movies",
            error_message="Radarr antwortet nicht (Zeitüberschreitung).",
            error_detail={"code": "arr_timeout", "service": "Radarr"},
        )
        db.add(anfrage)
        db.commit()

        await requests_service.push_to_arr(db, load_settings(db), anfrage)
        db.refresh(anfrage)

        assert anfrage.status == RequestStatus.searching
        assert anfrage.error_message is None
        assert anfrage.error_detail is None


@pytest.mark.asyncio
async def test_fehlende_tvdb_kennung_wird_frisch_nachgeschlagen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ist die Kennung inzwischen bei TMDB, laeuft die Anfrage durch.

    Der Fall aus dem Betrieb: Detaildaten liegen sieben Tage im
    Zwischenspeicher, und neuen Serien fehlt die TVDB-Kennung bei TMDB
    anfangs regelmaessig. Ohne den Nachschlag scheiterte dieselbe Serie eine
    Woche lang mit einer Begruendung, die laengst nicht mehr stimmte.
    """
    angelegt: list[tuple[int, int | None]] = []

    class SonarrAttrappe:
        async def ensure_tag(self, _name: str) -> None:
            return None

        async def add(self, tvdb_id: int, *_args: object, **kwargs: object) -> dict:
            angelegt.append((tvdb_id, kwargs.get("season")))
            return {"id": 4711}

    async def bibliothek(_settings: object, _tier: str = "standard"):
        return {}, {}

    async def nachschlag(_db: object, _settings: object, tmdb_id: int) -> int:
        assert tmdb_id == 331616
        return 481321

    monkeypatch.setattr(library, "sonarr_client", lambda _s, _t="standard": SonarrAttrappe())
    monkeypatch.setattr(library, "series_library", bibliothek)
    monkeypatch.setattr(
        requests_service.media, "tvdb_kennung_nachschlagen", nachschlag
    )

    with SessionLocal() as db:
        benutzer = db.query(User).filter(User.username == "admin").one()
        anfrage = MediaRequest(
            user_id=benutzer.id,
            media_type=MediaType.tv,
            tmdb_id=331616,
            tvdb_id=None,
            title="Death of the Pastor's Wife",
            season=1,
            status=RequestStatus.approved,
            quality_profile_id=1,
            root_folder_path="/data/TV-Shows",
        )
        db.add(anfrage)
        db.commit()

        await requests_service.push_to_arr(db, load_settings(db), anfrage)
        db.refresh(anfrage)

        assert anfrage.tvdb_id == 481321
        assert anfrage.status == RequestStatus.searching
        assert angelegt == [(481321, 1)]


@pytest.mark.asyncio
async def test_ohne_kennung_bleibt_es_beim_fehler(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kennt auch der Nachschlag keine Kennung, stimmt die alte Meldung noch."""

    async def nachschlag(_db: object, _settings: object, _tmdb_id: int) -> None:
        return None

    class SonarrAttrappe:
        async def ensure_tag(self, _name: str) -> None:
            return None

    monkeypatch.setattr(library, "sonarr_client", lambda _s, _t="standard": SonarrAttrappe())
    monkeypatch.setattr(
        requests_service.media, "tvdb_kennung_nachschlagen", nachschlag
    )

    anfrage = await _uebergeben(
        lambda user_id: MediaRequest(
            user_id=user_id,
            media_type=MediaType.tv,
            tmdb_id=999999,
            tvdb_id=None,
            title="Ganz neue Serie",
            season=1,
            status=RequestStatus.approved,
            quality_profile_id=1,
            root_folder_path="/data/TV-Shows",
        )
    )

    assert anfrage.status == RequestStatus.failed
    assert anfrage.error_detail is not None
    assert anfrage.error_detail["code"] == "tvdb_id_missing"
