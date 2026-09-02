"""Startseite: die zuletzt fertig geladenen Titel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, MediaType, RequestStatus
from app.routers import home
from app.services import library, media

from .conftest import auth_headers, create_user


def _anfrage(client: TestClient, headers: dict, index: int = 0) -> int:
    item = client.get("/api/discover/movie").json()["items"][index]
    return client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    ).json()["id"]


def _fertig(request_id: int, minuten_her: int = 0) -> None:
    with SessionLocal() as session:
        request = session.get(MediaRequest, request_id)
        assert request is not None
        request.status = RequestStatus.downloaded
        request.completed_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            minutes=minuten_her
        )
        session.commit()


def test_ohne_downloads_ist_die_liste_leer(admin_client: TestClient) -> None:
    assert admin_client.get("/api/home/recent").json() == []


def test_zeigt_geladene_titel(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    eintraege = arr_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["requested_by"] == "kim"
    assert eintraege[0]["title"]


def test_nur_fertige_titel(arr_client: TestClient) -> None:
    """Was noch wartet oder abgelehnt wurde, gehört nicht auf die Startseite."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _anfrage(arr_client, kim, 0)  # wartet auf Freigabe
    _fertig(_anfrage(arr_client, kim, 1))

    assert len(arr_client.get("/api/home/recent").json()) == 1


def test_neuester_zuerst(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    alt = _anfrage(arr_client, kim, 0)
    neu = _anfrage(arr_client, kim, 1)
    _fertig(alt, minuten_her=120)
    _fertig(neu, minuten_her=1)

    eintraege = arr_client.get("/api/home/recent").json()
    assert [e["request_id"] for e in eintraege] == [neu, alt]


def test_hoechstens_zwoelf_titel(arr_client: TestClient) -> None:
    """Die Demo-Daten reichen nicht so weit - die Anfragen kommen direkt in die
    Datenbank."""
    created = create_user(arr_client, "kim")

    with SessionLocal() as session:
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        for nummer in range(home.LIMIT + 2):
            session.add(
                MediaRequest(
                    user_id=created["id"],
                    media_type=MediaType.movie,
                    tmdb_id=900000 + nummer,
                    title=f"Testtitel {nummer}",
                    status=RequestStatus.downloaded,
                    completed_at=jetzt - timedelta(minutes=nummer),
                )
            )
        session.commit()

    assert len(arr_client.get("/api/home/recent").json()) == home.LIMIT


def test_alle_sehen_die_startseite(arr_client: TestClient) -> None:
    """Auch Titel, die jemand anderes angefragt hat."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    create_user(arr_client, "alex")
    alex = auth_headers(arr_client, "alex", "passwort-1234")

    eintraege = arr_client.get("/api/home/recent", headers=alex).json()
    assert len(eintraege) == 1
    assert eintraege[0]["requested_by"] == "kim"


def test_startseite_bleibt_stehen_wenn_tmdb_streikt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne TMDB fehlen Handlung und Hintergrundbild - der Titel bleibt."""
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    async def kaputt(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("TMDB nicht erreichbar")

    monkeypatch.setattr(media, "detail", kaputt)

    antwort = arr_client.get("/api/home/recent")
    assert antwort.status_code == 200
    eintraege = antwort.json()
    assert len(eintraege) == 1
    assert eintraege[0]["title"]
    assert eintraege[0]["backdrop_url"] is None


def test_ohne_anmeldung_keine_startseite(client: TestClient) -> None:
    assert client.get("/api/home/recent").status_code == 401


# --- Kuratierte Empfehlungen -------------------------------------------------


def _favorit(client: TestClient, media_type: str, tmdb_id: int, headers: dict):
    return client.post(
        "/api/favorites",
        json={"media_type": media_type, "tmdb_id": tmdb_id, "title": f"Test {tmdb_id}"},
        headers=headers,
    )


def test_ohne_favoriten_sagt_die_startseite_das_auch(arr_client: TestClient) -> None:
    daten = arr_client.get("/api/home/curated").json()
    assert daten["has_favorites"] is False
    assert daten["items"] == []


def test_serien_favoriten_zaehlen_genauso_wie_filme(
    client: TestClient, arr_client: TestClient
) -> None:
    """Der eigentliche Fehler: die Auskunft sah nur Filme an.

    Wer ausschliesslich Serien markiert hatte, bekam "noch nichts markiert" zu
    lesen - obwohl sein Herz an mehreren Serien hing. Das Herz gibt es an
    beidem, also muss auch beides zaehlen.
    """
    create_user(arr_client, "serienfan")
    headers = auth_headers(client, "serienfan", "passwort-1234")

    assert client.get("/api/home/curated", headers=headers).json()["has_favorites"] is False

    # Nur Serien markieren - kein einziger Film.
    assert _favorit(client, "tv", 1396, headers).status_code == 201
    assert _favorit(client, "tv", 1399, headers).status_code == 201

    daten = client.get("/api/home/curated", headers=headers).json()
    assert daten["has_favorites"] is True, "Serien-Favoriten müssen zählen"


def test_film_favoriten_zaehlen_weiterhin(client: TestClient, arr_client: TestClient) -> None:
    create_user(arr_client, "filmfan")
    headers = auth_headers(client, "filmfan", "passwort-1234")

    _favorit(client, "movie", 9800, headers)
    assert client.get("/api/home/curated", headers=headers).json()["has_favorites"] is True


@pytest.mark.asyncio
async def test_serien_verschwinden_nicht_aus_frisch_geladen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eine fertige Serie muss in "Frisch geladen" stehen bleiben.

    Der Bereich prueft neuerdings nach, ob die Datei ueberhaupt noch da ist.
    Dafuer baut er Kacheln aus den Anfragen - und beim ersten Anlauf ohne
    TVDB-Kennung und ohne Jahr. Serien werden aber genau darueber abgeglichen
    (TVDB, sonst Titel **und** Jahr): Ohne beides fand der Abgleich keine
    einzige Serie, und der Bereich liess sie alle stillschweigend weg.
    """
    from app.services.sonarr import LibraryEntry as SeriesEntry

    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    item = arr_client.get("/api/discover/tv").json()["items"][0]
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "tv",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/TV-Shows",
        },
        headers=headers,
    )
    assert antwort.status_code == 201, antwort.json()
    _fertig(antwort.json()["id"])

    with SessionLocal() as sitzung:
        anfrage = sitzung.query(MediaRequest).filter(MediaRequest.media_type == MediaType.tv).one()
        titel = anfrage.title
        jahr = int((anfrage.release_date or "2020")[:4])

    schluessel = "".join(c for c in titel.casefold() if c.isalnum())
    eintrag = SeriesEntry(
        arr_id=99,
        has_file=True,
        monitored=True,
        episode_file_count=5,
        episode_count=5,
        title_key=schluessel,
        year=jahr,
    )

    async def bibliothek(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        # Nur ueber den Titel auffindbar - so wie bei einer Serie, fuer die
        # TMDB keine TVDB-Kennung kennt.
        return {}, {schluessel: eintrag}

    monkeypatch.setattr(library, "series_library", bibliothek)

    eintraege = arr_client.get("/api/home/recent").json()
    titel_liste = [e["title"] for e in eintraege]
    assert titel in titel_liste, (
        f"Die Serie {titel!r} fehlt in 'Frisch geladen' - gefunden: {titel_liste}"
    )


# --------------------------------------------------------------------------
# Vorschlaege und der Media-Server
# --------------------------------------------------------------------------


async def test_nur_in_plex_vorhandene_titel_werden_nicht_vorgeschlagen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Startseite muss dieselbe Wahrheit kennen wie die Suche.

    Der gemeldete Fall ("Backrooms"): Eintrag aus Radarr entfernt, Datei
    weiter in Plex. Suche und Detailseite zeigten "vorhanden", die Startseite
    bot denselben Film als Vorschlag an - sie fragte als einzige Stelle den
    Media-Server nicht. Zwei Seiten, zwei Wahrheiten; das faellt genau dem
    auf, der beides direkt nacheinander sieht.
    """
    from app.models import MediaServerLibraryItem
    from app.schemas_media import MediaItem
    from app.services.sonarr import normalize_title

    plex_nur = MediaItem(
        media_type=MediaType.movie,
        tmdb_id=1083381,
        title="Backrooms",
        release_date="2026-01-01",
        vote_average=7.0,
        vote_count=500,
    )
    frisch = MediaItem(
        media_type=MediaType.movie,
        tmdb_id=777001,
        title="Wirklich neu",
        release_date="2026-01-01",
        vote_average=7.0,
        vote_count=500,
    )

    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid="plex://movie/backrooms",
                title="Backrooms",
                title_key=normalize_title("Backrooms"),
                tmdb_id=1083381,
                year=2026,
            )
        )
        session.commit()

    async def vorschlaege(_db, _settings, _art, page=1):  # noqa: ANN001
        return [plex_nur, frisch] if page == 1 else []

    monkeypatch.setattr(media, "suggestions", vorschlaege)

    daten = arr_client.get("/api/home/trending").json()
    kennungen = [eintrag["tmdb_id"] for eintrag in daten]
    assert 777001 in kennungen
    # Der Film liegt in Plex - er hat auf der Vorschlagsliste nichts verloren.
    assert 1083381 not in kennungen


async def test_kuratierte_vorschlaege_kennen_den_media_server(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dieselbe Luecke gab es bei den Empfehlungen aus Favoriten."""
    from app.models import MediaServerLibraryItem
    from app.schemas_media import MediaItem
    from app.services.sonarr import normalize_title

    with SessionLocal() as session:
        session.add(
            MediaServerLibraryItem(
                provider="plex",
                media_type=MediaType.movie,
                guid="plex://movie/backrooms",
                title="Backrooms",
                title_key=normalize_title("Backrooms"),
                tmdb_id=1083381,
                year=2026,
            )
        )
        session.commit()

    async def kuratiert(_db, _settings, _art, _favoriten, _personen):  # noqa: ANN001
        return [
            MediaItem(
                media_type=MediaType.movie,
                tmdb_id=1083381,
                title="Backrooms",
                release_date="2026-01-01",
            ),
            MediaItem(
                media_type=MediaType.movie,
                tmdb_id=777001,
                title="Wirklich neu",
                release_date="2026-01-01",
            ),
        ]

    monkeypatch.setattr(media, "curated", kuratiert)
    # Ein Favorit, damit der Bereich ueberhaupt rechnet.
    item = arr_client.get("/api/discover/movie").json()["items"][0]
    antwort = arr_client.post(
        "/api/favorites",
        json={"media_type": "movie", "tmdb_id": item["tmdb_id"]},
    )
    assert antwort.status_code == 201, antwort.text

    daten = arr_client.get("/api/home/curated").json()
    kennungen = [eintrag["tmdb_id"] for eintrag in daten["items"]]
    assert 1083381 not in kennungen
    assert 777001 in kennungen


# --------------------------------------------------------------------------
# Issue #3: eine Serie ist ein Titel, nicht eine Staffel
# --------------------------------------------------------------------------


@pytest.fixture
def serien_client(arr_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Wie ``arr_client``, aber Sonarr kennt auch etwas.

    Die Attrappe in ``conftest`` liefert fuer Serien eine **leere** Bibliothek -
    dann wirft ``_noch_vorhanden`` jede Serie weg, und die Tests hier haetten
    nie etwas zu pruefen. Sie spiegelt deshalb, wie die Film-Attrappe, die
    Anfrage-Tabelle.
    """
    from sqlalchemy import select

    from app.services.sonarr import LibraryEntry

    async def serien(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        with SessionLocal() as sitzung:
            kennungen = sitzung.scalars(
                select(MediaRequest.tvdb_id).where(
                    MediaRequest.media_type == MediaType.tv,
                    MediaRequest.status == RequestStatus.downloaded,
                    MediaRequest.tvdb_id.is_not(None),
                )
            ).all()
        return {
            kennung: LibraryEntry(
                arr_id=kennung,
                has_file=True,
                monitored=True,
                episode_file_count=10,
                episode_count=10,
                title_key=f"serie-{kennung}",
            )
            for kennung in kennungen
        }, {}

    monkeypatch.setattr(library, "series_library", serien)
    return arr_client


def _serie(
    user_id: int,
    tmdb_id: int,
    *,
    staffeln: list[int | None],
    titel: str = "Testserie",
    minuten_her: int = 0,
) -> None:
    """Je Staffel eine erledigte Anfrage - so, wie sie im Betrieb entstehen."""
    with SessionLocal() as session:
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        for versatz, staffel in enumerate(staffeln):
            session.add(
                MediaRequest(
                    user_id=user_id,
                    media_type=MediaType.tv,
                    tmdb_id=tmdb_id,
                    tvdb_id=tmdb_id,
                    season=staffel,
                    title=titel,
                    status=RequestStatus.downloaded,
                    completed_at=jetzt - timedelta(minutes=minuten_her + versatz),
                )
            )
        session.commit()


def test_vier_staffeln_sind_eine_kachel(serien_client: TestClient) -> None:
    """⚠️ Issue #3, der gemeldete Fall.

    Vier fertige Staffeln ergaben vier Kacheln derselben Serie. Der Bereich
    heisst "Frisch geladen" und beantwortet die Frage "was ist neu bei euch?" -
    und die Antwort ist der Titel, nicht die Datei.
    """
    kim = create_user(serien_client, "kim")
    _serie(kim["id"], 770001, staffeln=[1, 2, 3, 4])

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["seasons"] == [1, 2, 3, 4]


def test_eine_staffel_nennt_ihre_nummer(serien_client: TestClient) -> None:
    kim = create_user(serien_client, "kim")
    _serie(kim["id"], 770002, staffeln=[3])

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["seasons"] == [3]


def test_ganze_serie_nennt_keine_staffel(serien_client: TestClient) -> None:
    """``season = NULL`` heisst "ganze Serie" - dann waere jede Zahl gelogen."""
    kim = create_user(serien_client, "kim")
    _serie(kim["id"], 770003, staffeln=[None])

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["seasons"] == []


def test_ganze_serie_schlaegt_die_einzelne_staffel(serien_client: TestClient) -> None:
    """Wer erst Staffel 3 und spaeter die ganze Serie holt, hat beides in der
    Tabelle. "Staffel 3" waere dann untertrieben und "2 Staffeln" schlicht
    falsch, wenn die Serie sechs hat."""
    kim = create_user(serien_client, "kim")
    _serie(kim["id"], 770004, staffeln=[3, None])

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["seasons"] == []


def test_zwei_serien_bleiben_zwei_kacheln(serien_client: TestClient) -> None:
    """Zusammengelegt wird je Titel, nicht ueber Titel hinweg."""
    kim = create_user(serien_client, "kim")
    _serie(kim["id"], 770005, staffeln=[1, 2], titel="Erste", minuten_her=1)
    _serie(kim["id"], 770006, staffeln=[1], titel="Zweite", minuten_her=30)

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == 2
    assert [e["tmdb_id"] for e in eintraege] == [770005, 770006]


def test_die_neueste_staffel_stellt_die_serie_dar(serien_client: TestClient) -> None:
    """Wer zuletzt etwas geholt hat, steht auf der Kachel - "frisch geladen"
    meint schliesslich die neueste Staffel."""
    alt = create_user(serien_client, "kim")
    neu = create_user(serien_client, "alex")

    with SessionLocal() as session:
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        session.add(
            MediaRequest(
                user_id=alt["id"],
                media_type=MediaType.tv,
                tmdb_id=770007,
                tvdb_id=770007,
                season=1,
                title="Geteilt",
                status=RequestStatus.downloaded,
                completed_at=jetzt - timedelta(days=3),
            )
        )
        session.add(
            MediaRequest(
                user_id=neu["id"],
                media_type=MediaType.tv,
                tmdb_id=770007,
                tvdb_id=770007,
                season=2,
                title="Geteilt",
                status=RequestStatus.downloaded,
                completed_at=jetzt - timedelta(minutes=1),
            )
        )
        session.commit()

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["requested_by"] == "alex"
    assert eintraege[0]["seasons"] == [1, 2]


def test_derselbe_film_in_zwei_stufen_ist_eine_kachel(arr_client: TestClient) -> None:
    """Dasselbe Doppelbild wie bei den Staffeln, nur bei Filmen: 1080p und 4K
    sind zwei Dateien, aber ein Titel."""
    from app.models import QualityTier

    kim = create_user(arr_client, "kim")
    with SessionLocal() as session:
        jetzt = datetime.now(UTC).replace(tzinfo=None)
        for versatz, stufe in enumerate((QualityTier.standard, QualityTier.uhd)):
            session.add(
                MediaRequest(
                    user_id=kim["id"],
                    media_type=MediaType.movie,
                    tmdb_id=770008,
                    tier=stufe,
                    title="Doppelt",
                    status=RequestStatus.downloaded,
                    completed_at=jetzt - timedelta(minutes=versatz),
                )
            )
        session.commit()

    eintraege = arr_client.get("/api/home/recent").json()
    assert len(eintraege) == 1
    assert eintraege[0]["seasons"] == []


def test_die_grenze_zaehlt_titel_nicht_zeilen(serien_client: TestClient) -> None:
    """⚠️ Der Grund, warum das Zusammenlegen in die Abfrage muss.

    Frueher sass ``LIMIT`` an der rohen Anfragetabelle. Eine einzige Serie mit
    zwoelf Staffeln fuellte damit die ganze Startseite - und nur in der
    Oberflaeche zusammenzulegen haette daraus **eine** Kachel gemacht statt
    zwoelf verschiedener Titel.
    """
    kim = create_user(serien_client, "kim")
    for nummer in range(home.LIMIT + 2):
        _serie(
            kim["id"],
            780000 + nummer,
            staffeln=[1, 2, 3],
            titel=f"Serie {nummer}",
            minuten_her=nummer * 10,
        )

    eintraege = serien_client.get("/api/home/recent").json()
    assert len(eintraege) == home.LIMIT
    assert len({e["tmdb_id"] for e in eintraege}) == home.LIMIT
    assert all(e["seasons"] == [1, 2, 3] for e in eintraege)


def test_filme_tragen_keine_staffeln(arr_client: TestClient) -> None:
    create_user(arr_client, "kim")
    kim = auth_headers(arr_client, "kim", "passwort-1234")
    _fertig(_anfrage(arr_client, kim))

    eintraege = arr_client.get("/api/home/recent").json()
    assert eintraege[0]["seasons"] == []
