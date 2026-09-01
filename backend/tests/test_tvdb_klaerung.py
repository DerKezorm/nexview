"""Der Weg durch ``create_request``, wenn TMDB keine TVDB-Kennung kennt.

``test_serien_zuordnung`` prueft das Zuordnen selbst. Hier geht es um das, was
davon beim Anfragenden ankommt: laeuft es still durch, kommt ein Fenster, oder
kommt eine Auskunft - und welche.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models import Role
from app.schemas_media import MediaItem, MediaType
from app.db import SessionLocal
from app.services import requests_service, serien_zuordnung
from app.services.arr import ArrError
from app.services.requests_service import RequestError, _tvdb_klaeren


class _FakeSonarr:
    def __init__(self, treffer: list[dict[str, Any]] | None = None, fehler: bool = False) -> None:
        self.treffer = treffer or []
        self.fehler = fehler

    async def suche(self, begriff: str) -> list[dict[str, Any]]:
        if self.fehler:
            raise ArrError("Sonarr ist nicht erreichbar.", 502, code="arr_unreachable")
        return self.treffer


class _Nutzer:
    role = Role.user


class _Kind:
    role = Role.child


class _Einstellungen:
    """Nur die Felder, die dieser Weg anfasst.

    ``use_demo_data`` ist wahr: Ohne echten TMDB-Schluessel soll der
    Nachschlag des englischen Titels gar nicht erst loslaufen - er ist hier
    nicht der Gegenstand, und ein echter Aufruf haette in einem Test nichts
    zu suchen.
    """

    age_limit: int | None = None
    use_demo_data = True
    default_language = "de"
    default_region = "DE"


def _serie(tvdb_id: int, titel: str, jahr: int = 2020, tmdb_id: int = 0) -> dict[str, Any]:
    return {
        "tvdbId": tvdb_id,
        "tmdbId": tmdb_id,
        "title": titel,
        "year": jahr,
        "overview": f"Handlung von {titel}.",
        "images": [{"coverType": "poster", "remoteUrl": f"https://bild/{tvdb_id}.jpg"}],
    }


def _titel(tmdb_id: int = 331370, name: str = "Still Water", erschienen: str | None = "2026-08-28"):
    return MediaItem(
        media_type=MediaType.tv,
        tmdb_id=tmdb_id,
        title=name,
        overview="",
        release_date=erschienen,
        vote_average=0.0,
        vote_count=0,
    )


@pytest.fixture
def sonarr(monkeypatch: pytest.MonkeyPatch):
    """Setzt den Sonarr-Client, den ``_tvdb_klaeren`` bekommt."""

    def setzen(client: object) -> None:
        monkeypatch.setattr(
            requests_service.library, "sonarr_client", lambda settings, tier: client
        )

    return setzen


async def _klaeren(item=None, wahl=None, nutzer=None, auswahl=True):
    """Wie der Weg über ``/api/requests`` - der kann nachfragen.

    ``auswahl=False`` steht für jeden anderen Weg, etwa das Freigeben eines
    Kinderwunsches: dieselbe Prüfung, aber niemand, der antworten könnte.
    """
    from app.models import QualityTier

    with SessionLocal() as db:
        return await _tvdb_klaeren(
            db, _Einstellungen(), nutzer or _Nutzer(), item or _titel(),
            QualityTier.standard, wahl, None, auswahl_moeglich=auswahl,
        )


# --- Still durchlaufen -------------------------------------------------------


@pytest.mark.anyio
async def test_eindeutiger_treffer_laeuft_ohne_rueckfrage_durch(sonarr) -> None:
    sonarr(_FakeSonarr([_serie(334698, "Marc Eliot", 1998, tmdb_id=103594)]))

    ergebnis = await _klaeren(_titel(103594, "Marc Eliot", "1998-12-17"))

    assert ergebnis == 334698


@pytest.mark.anyio
async def test_ohne_sonarr_bleibt_es_beim_alten_weg(sonarr) -> None:
    """Ohne eingerichtetes Sonarr entsteht die Anfrage wie bisher; die
    verstaendliche Meldung kommt dann aus ``push_to_arr``."""
    sonarr(None)

    assert await _klaeren() is None


@pytest.mark.anyio
async def test_stummes_sonarr_kippt_die_anfrage_nicht(sonarr) -> None:
    """⚠️ Ein Verbindungsfehler ist keine Antwort auf "welche Serie?".

    Wer hier abbraeche, liesse den Anfragenden ueber eine fehlende
    TVDB-Kennung raetseln, waehrend in Wahrheit der Server aus war.
    """
    sonarr(_FakeSonarr(fehler=True))

    assert await _klaeren() is None


# --- Das Auswahlfenster ------------------------------------------------------


@pytest.mark.anyio
async def test_aehnliche_treffer_loesen_das_auswahlfenster_aus(sonarr) -> None:
    sonarr(_FakeSonarr([
        _serie(1001, "Still Waters", 0, tmdb_id=0),
        _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
    ]))

    with pytest.raises(RequestError) as fall:
        await _klaeren()

    assert fall.value.code == "tvdb_choice_needed"
    assert fall.value.status_code == 428
    kandidaten = fall.value.zahlen["candidates"]
    assert [k["title"] for k in kandidaten] == ["Still Waters", "Stille Waters"]
    # Die Oberflaeche braucht mehr als den Namen, um vergleichen zu lassen.
    assert kandidaten[0]["poster_url"] == "https://bild/1001.jpg"
    assert kandidaten[0]["overview"]
    assert kandidaten[0]["year"] is None  # Sonarrs 0 kommt nicht als Zahl durch


@pytest.mark.anyio
async def test_ohne_frageweg_kommt_die_auskunft_statt_des_fensters(sonarr) -> None:
    """⚠️ Der Kinderwunsch-Weg, und die Vorgabe für jeden neuen.

    Gibt ein Elternteil einen Wunsch frei, läuft derselbe Dienst - aber jene
    Oberfläche kennt kein Auswahlfenster. Käme die Rückfrage dort an, läse das
    Elternteil "Bitte wähle die richtige aus" und hätte nichts zum Wählen; der
    Wunsch bliebe für immer offen. Deshalb ist Fragen ausdrücklich zu
    erlauben, nicht stillschweigend erlaubt.
    """
    sonarr(_FakeSonarr([
        _serie(1001, "Still Waters", 0, tmdb_id=0),
        _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
    ]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(auswahl=False)

    assert fall.value.code == "tvdb_id_missing_new"


@pytest.mark.anyio
async def test_eindeutiger_treffer_braucht_keinen_frageweg(sonarr) -> None:
    """Wer eindeutig ist, läuft auch beim Kinderwunsch still durch - da gibt
    es ja nichts zu fragen."""
    sonarr(_FakeSonarr([_serie(334698, "Marc Eliot", 1998, tmdb_id=103594)]))

    ergebnis = await _klaeren(
        _titel(103594, "Marc Eliot", "1998-12-17"), auswahl=False
    )

    assert ergebnis == 334698


@pytest.mark.anyio
async def test_kinderkonto_bekommt_kein_fenster(sonarr) -> None:
    """⚠️ Die Vorschlaege kommen aus Sonarr und damit an TMDB vorbei - und an
    TMDB haengt die Alterspruefung."""
    sonarr(_FakeSonarr([_serie(1001, "Still Waters", 1995, tmdb_id=0)]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(nutzer=_Kind())

    assert fall.value.code != "tvdb_choice_needed"


@pytest.mark.anyio
async def test_altersgrenze_bekommt_kein_fenster(sonarr) -> None:
    sonarr(_FakeSonarr([_serie(1001, "Still Waters", 1995, tmdb_id=0)]))

    class Beschraenkt(_Einstellungen):
        age_limit = 12

    from app.models import QualityTier

    with SessionLocal() as db, pytest.raises(RequestError) as fall:
        await _tvdb_klaeren(
            db, Beschraenkt(), _Nutzer(), _titel(), QualityTier.standard, None, None,
            auswahl_moeglich=True,
        )

    assert fall.value.code != "tvdb_choice_needed"


# --- Die Auswahl kommt zurueck -----------------------------------------------


@pytest.mark.anyio
async def test_vorgelegte_auswahl_wird_uebernommen(sonarr) -> None:
    sonarr(_FakeSonarr([
        _serie(1001, "Still Waters", 0, tmdb_id=0),
        _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
    ]))

    assert await _klaeren(wahl=1002) == 1002


@pytest.mark.anyio
async def test_erfundene_auswahl_wird_abgewiesen(sonarr) -> None:
    """⚠️ Der Kern: Die Zahl kommt aus dem Browser. Ungeprueft waere sie ein
    Weg, an TMDB und damit an der Altersbeschraenkung vorbei eine beliebige
    Serie anlegen zu lassen."""
    sonarr(_FakeSonarr([_serie(1001, "Still Waters", 1995, tmdb_id=0)]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(wahl=999999)

    assert fall.value.code == "tvdb_choice_invalid"


@pytest.mark.anyio
async def test_auswahl_wird_auch_beim_kind_geprueft(sonarr) -> None:
    """Kein Fenster heisst nicht, dass eine mitgeschickte Zahl durchginge."""
    sonarr(_FakeSonarr([_serie(1001, "Still Waters", 1995, tmdb_id=0)]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(wahl=999999, nutzer=_Kind())

    assert fall.value.code == "tvdb_choice_invalid"


# --- Wenn nichts passt: welcher Rat? -----------------------------------------


@pytest.mark.anyio
async def test_neue_serie_bekommt_versuchs_spaeter(sonarr) -> None:
    sonarr(_FakeSonarr([]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(_titel(erschienen="2026-08-27"))

    assert fall.value.code == "tvdb_id_missing_new"


@pytest.mark.anyio
async def test_alte_serie_bekommt_die_ehrliche_auskunft(sonarr) -> None:
    """⚠️ "Versuch es spaeter" waere bei einem Titel von 1978 eine
    Vertroestung - es traegt niemand mehr etwas nach."""
    sonarr(_FakeSonarr([]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(_titel(328178, "Ciné regards", "1978-01-04"))

    assert fall.value.code == "tvdb_id_missing"


@pytest.mark.anyio
async def test_ohne_erscheinungsdatum_wird_vertroestet(sonarr) -> None:
    """Ein Titel ohne Erstausstrahlung ist meist einer, der noch nicht lief."""
    sonarr(_FakeSonarr([]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(_titel(erschienen=None))

    assert fall.value.code == "tvdb_id_missing_new"


@pytest.mark.anyio
async def test_beide_auskuenfte_tragen_den_titel(sonarr) -> None:
    """Ohne ihn stuende in der Oberflaeche ein Satz ohne Gegenstand."""
    sonarr(_FakeSonarr([]))

    with pytest.raises(RequestError) as fall:
        await _klaeren(_titel(328178, "Ciné regards", "1978-01-04"))

    assert fall.value.zahlen["title"] == "Ciné regards"


def test_frisch_zieht_die_grenze_bei_einem_jahr() -> None:
    from datetime import timedelta

    from app.models import utcnow

    heute = utcnow().replace(tzinfo=None)
    gerade = (heute - timedelta(days=30)).strftime("%Y-%m-%d")
    lange = (heute - timedelta(days=400)).strftime("%Y-%m-%d")

    assert requests_service._frisch(gerade) is True
    assert requests_service._frisch(lange) is False
    # Unlesbares Datum: lieber vertroesten als abwuergen.
    assert requests_service._frisch("kein datum") is True


# --- Der ganze Weg, vom Aufruf bis zur Antwort -------------------------------
#
# Die Tests oben pruefen ``_tvdb_klaeren``. Sie waeren genauso gruen, wenn der
# Helfer gar nicht aufgerufen wuerde oder sein Ergebnis nicht an der Anfrage
# landete. Deshalb hier einmal ueber die echte Adresse.

from fastapi.testclient import TestClient  # noqa: E402

from .conftest import auth_headers, create_user  # noqa: E402


def _kontingent_auf_null(client: TestClient, benutzername: str) -> None:
    """Das Serien-Kontingent dieses Kontos erschöpfen."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        nutzer = db.scalar(select(User).where(User.username == benutzername))
        nutzer.quota_series_limit = 0
        db.commit()


def _gespeicherte_kennung(request_id: int) -> int | None:
    """Was wirklich an der Anfrage steht - nicht, was die Antwort zeigt.

    ``RequestPublic`` fuehrt die TVDB-Kennung gar nicht; sie ist eine Angabe
    fuer Sonarr, nicht fuer den Anfragenden. Geprueft wird deshalb dort, wo
    sie ankommen muss.
    """
    from app.db import SessionLocal
    from app.models import MediaRequest

    with SessionLocal() as db:
        return db.get(MediaRequest, request_id).tvdb_id


def _serie_ohne_kennung(monkeypatch: pytest.MonkeyPatch, treffer: list[dict[str, Any]]) -> None:
    """Eine Serie, zu der TMDB keine TVDB-Kennung fuehrt - und ein Sonarr dazu."""
    from app.routers import requests as requests_router

    async def detail(db, settings, media_type, tmdb_id):
        return MediaItem(
            media_type=MediaType.tv,
            tmdb_id=331370,
            title="Still Water",
            overview="",
            release_date="2026-08-28",
            tvdb_id=None,
            vote_average=0.0,
            vote_count=0,
        )

    monkeypatch.setattr(requests_router.media, "detail", detail)
    monkeypatch.setattr(
        requests_service.library, "sonarr_client", lambda settings, tier: _FakeSonarr(treffer)
    )


def test_auswahlfenster_kommt_ueber_die_echte_adresse(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serie_ohne_kennung(monkeypatch, [
        _serie(1001, "Still Waters", 0, tmdb_id=0),
        _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
    ])
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows"},
        headers=headers,
    )

    assert antwort.status_code == 428
    detail = antwort.json()["detail"]
    assert detail["code"] == "tvdb_choice_needed"
    assert [k["title"] for k in detail["candidates"]] == ["Still Waters", "Stille Waters"]


def test_die_gewaehlte_kennung_landet_an_der_anfrage(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Punkt, an dem sich alles entscheidet: Kommt die geklaerte
    Kennung wirklich an der Anfrage an, oder wird sie unterwegs vergessen?"""
    _serie_ohne_kennung(monkeypatch, [_serie(1002, "Stille Waters", 2001, tmdb_id=42728)])
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows", "tvdb_id": 1002},
        headers=headers,
    )

    assert antwort.status_code == 201, antwort.text
    assert _gespeicherte_kennung(antwort.json()["id"]) == 1002


def test_eindeutiger_treffer_kommt_ohne_fenster_durch(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was Markus ausdruecklich wollte: Wer gefunden wird, wird nicht gefragt."""
    _serie_ohne_kennung(monkeypatch, [_serie(3001, "Still Water", 2026, tmdb_id=331370)])
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows"},
        headers=headers,
    )

    assert antwort.status_code == 201, antwort.text
    assert _gespeicherte_kennung(antwort.json()["id"]) == 3001


def test_erfundene_kennung_wird_auch_ueber_die_adresse_abgewiesen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serie_ohne_kennung(monkeypatch, [_serie(1002, "Stille Waters", 2001, tmdb_id=42728)])
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows", "tvdb_id": 999999},
        headers=headers,
    )

    assert antwort.status_code == 400
    assert antwort.json()["detail"]["code"] == "tvdb_choice_invalid"


def test_serie_mit_kennung_fragt_sonarr_gar_nicht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Normalfall, und er muss unberuehrt bleiben.

    Von 58 Serien im Zwischenspeicher tragen 54 ihre TVDB-Kennung bei TMDB.
    Die duerfen von dieser ganzen Mechanik nichts merken - weder ein Fenster
    noch eine zusaetzliche Abfrage an Sonarr, die jede Anfrage langsamer
    machte, ohne je etwas beizutragen.
    """
    from app.routers import requests as requests_router

    async def detail(db, settings, media_type, tmdb_id):
        return MediaItem(
            media_type=MediaType.tv,
            tmdb_id=1399,
            title="Game of Thrones",
            overview="",
            release_date="2011-04-17",
            tvdb_id=121361,
            vote_average=0.0,
            vote_count=0,
        )

    gefragt: list[str] = []

    class _Zaehlend(_FakeSonarr):
        async def suche(self, begriff: str) -> list[dict[str, Any]]:
            gefragt.append(begriff)
            return []

    monkeypatch.setattr(requests_router.media, "detail", detail)
    monkeypatch.setattr(
        requests_service.library, "sonarr_client", lambda settings, tier: _Zaehlend()
    )
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 1399, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows"},
        headers=headers,
    )

    assert antwort.status_code == 201, antwort.text
    assert _gespeicherte_kennung(antwort.json()["id"]) == 121361
    assert gefragt == [], "Sonarr wurde gefragt, obwohl die Kennung längst da war"


def test_englischer_titel_geht_mit_in_die_suche(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Fall, der die ganze Sache erst nützlich macht.

    Live gemessen: In deutscher Oberfläche liefert TMDB für eine
    thailändische Serie Titel *und* Originaltitel auf Thai. Sonarr findet
    damit null Treffer - unter "Still Water" dagegen zwanzig. Ohne diesen
    dritten Suchbegriff liefe der Rückfall bei genau den Serien ins Leere,
    für die er gedacht ist.
    """
    from app.routers import requests as requests_router

    async def detail(db, settings, media_type, tmdb_id):
        return MediaItem(
            media_type=MediaType.tv,
            tmdb_id=331370,
            title="วารี ๑๐๐ ศพ",
            original_title="วารี ๑๐๐ ศพ",
            overview="",
            release_date="2026-08-28",
            vote_average=0.0,
            vote_count=0,
        )

    gefragt: list[str] = []

    class _Merkend(_FakeSonarr):
        async def suche(self, begriff: str) -> list[dict[str, Any]]:
            gefragt.append(begriff)
            return [_serie(3001, "Still Water", 2026, tmdb_id=331370)]

    async def englisch(db, settings, media_type, tmdb_id):
        return "Still Water"

    monkeypatch.setattr(requests_router.media, "detail", detail)
    monkeypatch.setattr(requests_service.media, "englischer_titel", englisch)
    monkeypatch.setattr(
        requests_service.library, "sonarr_client", lambda settings, tier: _Merkend()
    )
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows"},
        headers=headers,
    )

    assert antwort.status_code == 201, antwort.text
    assert "Still Water" in gefragt, f"nur auf Thai gesucht: {gefragt}"
    assert _gespeicherte_kennung(antwort.json()["id"]) == 3001


def test_rueckfrage_verbraucht_kein_kontingent(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Eine Frage ist keine Anfrage.

    Wer das Auswahlfenster sieht und abbricht, darf nichts verbraucht haben -
    sonst kostete jeder Fehlversuch einen Platz, und bei einem Kontingent von
    fünf wären fünf Rückfragen die Woche vorbei.

    Geprüft wird am Zähler, den die Oberfläche anzeigt, nicht am Datenbestand:
    Es ist die Zahl, die der Mensch sieht.
    """
    _serie_ohne_kennung(monkeypatch, [
        _serie(1001, "Still Waters", 0, tmdb_id=0),
        _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
    ])
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    vorher = arr_client.get("/api/requests/quota", headers=headers).json()

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows"},
        headers=headers,
    )
    assert antwort.status_code == 428

    nachher = arr_client.get("/api/requests/quota", headers=headers).json()
    assert nachher == vorher, "die Rückfrage hat Kontingent gekostet"


def test_volles_kontingent_schlaegt_vor_der_rueckfrage_zu(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Die Reihenfolge zählt.

    Wessen Kontingent voll ist, bekommt das gesagt - und nicht erst eine
    Rückfrage, welche Serie er meint, um danach abgewiesen zu werden. Die
    Klärung steht deshalb bewusst hinter der Kontingentprüfung, und sie kostet
    dann auch keine zwei Abfragen an Sonarr.
    """
    gefragt: list[str] = []

    class _Zaehlend(_FakeSonarr):
        async def suche(self, begriff: str) -> list[dict[str, Any]]:
            gefragt.append(begriff)
            return [_serie(1001, "Still Waters", 0, tmdb_id=0)]

    from app.routers import requests as requests_router

    async def detail(db, settings, media_type, tmdb_id):
        return MediaItem(
            media_type=MediaType.tv, tmdb_id=331370, title="Still Water",
            overview="", release_date="2026-08-28", vote_average=0.0, vote_count=0,
        )

    monkeypatch.setattr(requests_router.media, "detail", detail)
    monkeypatch.setattr(
        requests_service.library, "sonarr_client", lambda settings, tier: _Zaehlend()
    )

    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    _kontingent_auf_null(arr_client, "kim")

    antwort = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows"},
        headers=headers,
    )

    assert antwort.status_code != 428
    assert gefragt == [], "Sonarr wurde gefragt, obwohl das Kontingent voll war"


@pytest.mark.anyio
async def test_wer_die_vorgabe_nimmt_bekommt_die_auskunft(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Schutz für jeden Weg, der nicht ``/api/requests`` ist.

    So ruft ``child_wishes.freigeben`` den Dienst: ohne ein Wort über
    Rückfragen. Käme dort ein ``tvdb_choice_needed`` heraus, läse das
    Elternteil "Bitte wähle die richtige aus" und hätte nichts zum Wählen -
    der Wunsch bliebe für immer offen.

    Deshalb wird hier der Dienst **direkt** gerufen, nicht über die Adresse:
    Genau diese Aufrufform ist der Gegenstand.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import QualityTier, User
    from app.services.settings_service import for_user, load_settings

    monkeypatch.setattr(
        requests_service.library,
        "sonarr_client",
        lambda settings, tier: _FakeSonarr([
            _serie(1001, "Still Waters", 0, tmdb_id=0),
            _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
        ]),
    )
    create_user(arr_client, "kim")

    with SessionLocal() as db:
        nutzer = db.scalar(select(User).where(User.username == "kim"))
        einstellungen = for_user(load_settings(db), nutzer)
        with pytest.raises(RequestError) as fall:
            await requests_service.create_request(
                db,
                einstellungen,
                nutzer,
                MediaItem(
                    media_type=MediaType.tv, tmdb_id=331370, title="Still Water",
                    overview="", release_date="2026-08-28",
                    vote_average=0.0, vote_count=0,
                ),
                1,
                "/data/TV-Shows",
                tier=QualityTier.standard,
            )

    assert fall.value.code == "tvdb_id_missing_new"


# --- Was "Abbrechen" ankündigt, muss stimmen ---------------------------------


def test_fehlgeschlagene_anfrage_ist_nicht_mit_sonarr_verknuepft(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Der Dialog drohte mit dem Löschen von Dateien, die es nicht gab.

    "Abbrechen" kündigte immer an, der Titel werde aus Radarr bzw. Sonarr
    entfernt und heruntergeladene Dateien würden mitgelöscht. Bei einer
    fehlgeschlagenen Anfrage stimmt davon nichts - sie kam nie bis zum
    Anlegen. Gemeldet, weil in der Liste Serien standen, die der Betreiber
    längst geladen hatte.

    ``arr_linked`` ist die Angabe, an der die Oberfläche das unterscheidet.
    """
    _serie_ohne_kennung(monkeypatch, [])
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    gescheitert = arr_client.post(
        "/api/requests",
        json={"media_type": "tv", "tmdb_id": 331370, "quality_profile_id": 1,
              "root_folder_path": "/data/TV-Shows", "season": 1},
        headers=headers,
    )
    assert gescheitert.status_code == 422

    # Eine Anfrage, die durchkam, trägt die Verknüpfung - eine gescheiterte nicht.
    offen = arr_client.get("/api/requests/mine", headers=headers).json()
    assert all(eintrag["arr_linked"] is False for eintrag in offen)


def test_verknuepfte_anfrage_meldet_sich_als_verknuepft() -> None:
    """Die Gegenprobe: Mit Eintrag in Sonarr sagt die Anfrage das auch."""
    from app.models import MediaRequest

    anfrage = MediaRequest()
    assert anfrage.arr_linked is False
    anfrage.arr_id = 216
    assert anfrage.arr_linked is True
