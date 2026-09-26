"""Der Erscheinungs-Kalender.

Der Schwerpunkt liegt auf drei Dingen, die ohne Test still danebenliegen
wuerden: das Zusammenfassen mehrerer Folgen an einem Tag, die Zeitzone und der
Zwischenspeicher-Schluessel der Entdecken-Filter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import calendar as calendar_service
from app.services.beschaffung.arr import client as arr_client_module
from app.services.beschaffung.arr import library
from app.services.filters import (
    HERKUNFTSLAENDER,
    KNOWN_TITLES_MIN_VOTES,
    NETWORK_IDS,
    NETWORKS,
    DiscoverFilters,
)
from app.services.tmdb import release_date_for

from .conftest import auth_headers, create_user

HEUTE = "2026-08-19"


def folge(
    *,
    serien_id: int = 7,
    staffel: int = 3,
    nummer: int,
    datum: str = f"{HEUTE}T18:00:00Z",
    hat_datei: bool = True,
    titel: str = "Irgendeine Folge",
    serie: dict | None = None,
) -> dict:
    return {
        "seriesId": serien_id,
        "seasonNumber": staffel,
        "episodeNumber": nummer,
        "airDateUtc": datum,
        "hasFile": hat_datei,
        "title": titel,
        "series": serie
        or {"id": serien_id, "title": "Yellowstone", "tvdbId": 341164, "monitored": True},
    }


# --- Folgen zusammenfassen -------------------------------------------------


def test_eine_folge_bekommt_die_schlichte_beschriftung() -> None:
    eintraege = calendar_service._falte_folgen([folge(nummer=5)], HEUTE)

    assert len(eintraege) == 1
    assert eintraege[0].episode_label == "S03E05"
    # Bei genau einer Folge darf ihr Titel dabeistehen.
    assert eintraege[0].episode_title == "Irgendeine Folge"


def test_luekenlose_folgen_werden_zu_einem_bereich() -> None:
    """Streamingdienste veroeffentlichen mehrere Folgen am selben Abend."""
    eintraege = calendar_service._falte_folgen(
        [folge(nummer=5), folge(nummer=6)], HEUTE
    )

    assert len(eintraege) == 1
    assert eintraege[0].episode_label == "S03E05–06"
    # Ein Titel stellvertretend fuer zwei Folgen waere irrefuehrend.
    assert eintraege[0].episode_title is None


def test_luecke_wird_nicht_zum_bereich_geschoent() -> None:
    eintraege = calendar_service._falte_folgen(
        [folge(nummer=5), folge(nummer=7)], HEUTE
    )

    assert eintraege[0].episode_label == "S03E05, 07"


def test_viele_folgen_mit_luecke_bekommen_die_anzahl() -> None:
    nummern = [1, 2, 3, 5, 8]
    eintraege = calendar_service._falte_folgen([folge(nummer=n) for n in nummern], HEUTE)

    assert eintraege[0].episode_label == "S03E01–08 (5)"


def test_zwei_staffeln_am_selben_tag_bleiben_zwei_zeilen() -> None:
    """Ein Staffelfinale und der naechste Auftakt koennen zusammenfallen.

    "S03E13-S04E01" waere kein Bereich, sondern Unsinn.
    """
    eintraege = calendar_service._falte_folgen(
        [folge(staffel=3, nummer=13), folge(staffel=4, nummer=1)], HEUTE
    )

    assert len(eintraege) == 2
    assert {e.episode_label for e in eintraege} == {"S03E13", "S04E01"}


def test_fehlende_folgen_werden_gemeldet() -> None:
    eintraege = calendar_service._falte_folgen(
        [folge(nummer=5, hat_datei=True), folge(nummer=6, hat_datei=False)],
        HEUTE,
    )

    eintrag = eintraege[0]
    assert eintrag.missing_episodes == [6]
    assert eintrag.missing is True
    # Nicht alle da -> die Serie gilt als "wird gesucht", nicht als geladen.
    assert eintrag.status == "searching"


def test_kuenftige_folgen_gelten_nicht_als_fehlend() -> None:
    """Was noch gar nicht lief, kann auch nicht fehlen."""
    eintraege = calendar_service._falte_folgen(
        [folge(nummer=5, hat_datei=False, datum="2026-09-01T18:00:00Z")], HEUTE
    )

    assert eintraege[0].aired is False
    assert eintraege[0].missing is False
    # Der Zustand richtet sich allein nach der Datei - genau wie ueberall
    # sonst. "Nicht angefragt" waere doppelt falsch: Die Serie steht bereits in
    # Sonarr, und die Kachel traege einen Einkaufswagen.
    assert eintraege[0].status == "searching"


def test_eigene_titel_gelten_nie_als_nicht_angefragt() -> None:
    """Sonst widerspricht die Kachel der Ueberschrift, unter der sie steht."""
    eintraege = calendar_service._falte_folgen(
        [
            folge(nummer=1, hat_datei=True),
            folge(serien_id=8, nummer=2, hat_datei=False),
            folge(serien_id=9, nummer=3, hat_datei=False, datum="2026-12-01T18:00:00Z"),
        ],
        HEUTE,
    )

    assert {e.status for e in eintraege} == {"downloaded", "searching"}
    assert all(e.status != "not_requested" for e in eintraege)


# --- Zeitzone --------------------------------------------------------------


def test_zeitstempel_wird_auf_den_lokalen_tag_gebracht() -> None:
    """Sonarr liefert UTC.

    Eine US-Serie, die dort um 21 Uhr laeuft, traegt 01:30 UTC des Folgetags.
    Ohne Umrechnung stuende sie im Kalender am falschen Tag.
    """
    assert calendar_service._lokaler_tag("2026-08-19T00:00:00Z") is not None
    # Reine Datumsangaben kommen unveraendert durch.
    assert calendar_service._lokaler_tag("2026-08-19") == "2026-08-19"
    assert calendar_service._lokaler_tag(None) is None
    assert calendar_service._lokaler_tag("") is None


# --- Regionale Termine -----------------------------------------------------


def _detail_mit_terminen() -> dict:
    return {
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "DE",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-03-05T00:00:00.000Z"},
                        {"type": 4, "release_date": "2026-06-18T00:00:00.000Z"},
                        {"type": 5, "release_date": "2026-07-01T00:00:00.000Z"},
                    ],
                },
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 4, "release_date": "2026-05-01T00:00:00.000Z"}
                    ],
                },
            ]
        }
    }


def test_digitaler_termin_schlaegt_den_kinostart() -> None:
    """Der eigentliche Zweck: TMDB liefert in Listen das Kino-Datum.

    Ein Film mit Kinostart im Maerz und digitaler Veroeffentlichung im Juni
    stuende sonst im Juni-Fenster, aber unter Maerz.
    """
    from app.services.filters import DIGITAL_ARTEN, KINO_ARTEN

    assert release_date_for(_detail_mit_terminen(), "DE", DIGITAL_ARTEN) == (
        "2026-06-18",
        4,
    )
    assert release_date_for(_detail_mit_terminen(), "DE", KINO_ARTEN) == ("2026-03-05", 3)


def test_termin_richtet_sich_nach_der_region() -> None:
    from app.services.filters import DIGITAL_ARTEN

    assert release_date_for(_detail_mit_terminen(), "US", DIGITAL_ARTEN) == (
        "2026-05-01",
        4,
    )
    assert release_date_for(_detail_mit_terminen(), "FR", DIGITAL_ARTEN) is None


# --- Gruppierung -----------------------------------------------------------


def test_tage_sind_sortiert_und_eigene_titel_stehen_vorn() -> None:
    from app.models import MediaType
    from app.schemas_calendar import CalendarEntry

    def eintrag(tag: str, quelle: str, titel: str) -> CalendarEntry:
        return CalendarEntry(
            key=f"{quelle}:{titel}",
            date=tag,
            source=quelle,  # type: ignore[arg-type]
            origin="tmdb" if quelle == "neu" else "radarr",
            media_type=MediaType.movie,
            title=titel,
        )

    tage = calendar_service._gruppiere(
        [
            eintrag("2026-08-20", "neu", "Zebra"),
            eintrag("2026-08-19", "neu", "Anton"),
            eintrag("2026-08-19", "meine", "Xaver"),
        ]
    )

    assert [tag.date for tag in tage] == ["2026-08-19", "2026-08-20"]
    # Was einen selbst betrifft, ist die dringendere Auskunft.
    assert [e.title for e in tage[0].entries] == ["Xaver", "Anton"]


def test_eigener_bestand_erscheint_nicht_zweimal() -> None:
    """Radarr und TMDB melden denselben Film zum selben Termin.

    Ohne Entdoppelung stuende er zweimal untereinander - einmal mit Zustand,
    einmal ohne.
    """
    from app.models import MediaType
    from app.schemas_calendar import CalendarEntry

    def eintrag(quelle: str, kennung: int | None) -> CalendarEntry:
        return CalendarEntry(
            key=f"{quelle}:{kennung}",
            date="2026-08-19",
            source=quelle,  # type: ignore[arg-type]
            origin="tmdb" if quelle == "neu" else "radarr",
            media_type=MediaType.movie,
            tmdb_id=kennung,
            title="Derselbe Film",
        )

    uebrig = calendar_service._entdoppeln(
        [eintrag("meine", 550), eintrag("neu", 550), eintrag("neu", 999)]
    )

    assert [(e.source, e.tmdb_id) for e in uebrig] == [("meine", 550), ("neu", 999)]


def test_eintraege_ohne_kennung_werden_nicht_verwechselt() -> None:
    """Zwei unbekannte Titel sind nicht derselbe Titel.

    Wuerde ``None`` als Schluessel zaehlen, verschwaende der zweite Eintrag.
    """
    from app.models import MediaType
    from app.schemas_calendar import CalendarEntry

    ohne = [
        CalendarEntry(
            key=f"sonarr:{nummer}",
            date="2026-08-19",
            source="meine",
            origin="sonarr",
            media_type=MediaType.tv,
            tmdb_id=None,
            title=f"Serie {nummer}",
        )
        for nummer in (1, 2)
    ]

    assert len(calendar_service._entdoppeln(ohne)) == 2


# --- Der Zwischenspeicher-Schluessel ---------------------------------------


def test_neue_filterfelder_trennen_den_zwischenspeicher() -> None:
    """Der gefaehrlichste Fehler der ganzen Aenderung.

    ``cache_key`` zaehlt seine Felder von Hand auf. Fehlt eines, teilen sich
    eine Kalender-Abfrage (digital) und eine Entdecken-Abfrage (Kino) dieselbe
    Zeile in der Datenbank - drei Stunden lang und ohne Fehlermeldung.
    """
    kino = DiscoverFilters(release_types="3|2|1")
    digital = DiscoverFilters(release_types="4|5")
    assert kino.cache_key("movie") != digital.cache_key("movie")

    ein_studio = DiscoverFilters(company_ids="2")
    viele_studios = DiscoverFilters(company_ids="2|3")
    assert ein_studio.cache_key("movie") != viele_studios.cache_key("movie")

    ein_sender = DiscoverFilters(network_ids="213")
    viele_sender = DiscoverFilters(network_ids="213|1024")
    assert ein_sender.cache_key("tv") != viele_sender.cache_key("tv")

    alle_arten = DiscoverFilters(network_ids="213")
    nur_erzaehlend = DiscoverFilters(network_ids="213", series_types="0|2|4")
    assert alle_arten.cache_key("tv") != nur_erzaehlend.cache_key("tv")


def test_grosse_studios_beschraenkt_serien_auf_erzaehlendes() -> None:
    """Sonst ist die Rubrik bei Serien unbrauchbar.

    Netflix und Hulu fuehren bei TMDB auch ihre Begleit-Podcasts, Talkshows und
    Spielshows als Serien - gemessen war rund die Haelfte der Treffer so etwas.
    """
    serien = calendar_service._filter(
        von="2026-07-29",
        bis="2026-10-14",
        region="DE",
        datumsart="digital",
        schaerfe="studios",
        seite=1,
        fuer_film=False,
    )
    assert serien.series_types == "0|2|4"
    assert serien.network_ids

    # Bei Filmen gibt es diese Einteilung nicht - dort zaehlen die Studios.
    filme = calendar_service._filter(
        von="2026-07-29",
        bis="2026-10-14",
        region="DE",
        datumsart="digital",
        schaerfe="studios",
        seite=1,
        fuer_film=True,
    )
    assert filme.series_types == ""
    assert filme.company_ids

    # "Bekannte Titel" filtert ueber die Stimmen, nicht ueber die Art.
    bekannt = calendar_service._filter(
        von="2026-07-29",
        bis="2026-10-14",
        region="DE",
        datumsart="digital",
        schaerfe="known",
        seite=1,
        fuer_film=False,
    )
    assert bekannt.series_types == ""
    assert bekannt.network_ids == ""
    assert bekannt.min_votes == KNOWN_TITLES_MIN_VOTES


def test_herkunftsland_siebt_die_weltproduktion_aus() -> None:
    """Netflix produziert weltweit, TMDB fuehrt alles unter demselben Sender.

    Ohne diese Pruefung stehen koreanische, thailaendische und japanische
    Eigenproduktionen zwischen den hiesigen Neuerscheinungen.
    """
    from app.schemas_media import MediaItem

    def serie(*laender: str) -> MediaItem:
        return MediaItem(
            media_type="tv", tmdb_id=1, title="Egal", origin_country=list(laender)
        )

    assert calendar_service._aus_bekanntem_land(serie("US")) is True
    assert calendar_service._aus_bekanntem_land(serie("DE", "AT")) is True
    # Eine Koproduktion zaehlt, sobald ein bekanntes Land dabei ist.
    assert calendar_service._aus_bekanntem_land(serie("KR", "US")) is True

    assert calendar_service._aus_bekanntem_land(serie("KR")) is False
    assert calendar_service._aus_bekanntem_land(serie("TH", "JP")) is False
    # Ohne Angabe faellt der Titel durch: lieber einen Grenzfall uebersehen
    # als die Rubrik mit Unbekanntem fluten.
    assert calendar_service._aus_bekanntem_land(serie()) is False

    assert "KR" not in HERKUNFTSLAENDER


def test_vorbelegung_entspricht_dem_bisherigen_verhalten() -> None:
    """Die Entdecken-Seite darf sich durch den Kalender nicht veraendern."""
    assert DiscoverFilters().release_types == "2|3"
    assert DiscoverFilters().company_ids == ""
    assert DiscoverFilters().network_ids == ""


def test_senderliste_ist_widerspruchsfrei() -> None:
    assert NETWORK_IDS == {kennung for kennung, _ in NETWORKS}
    assert len(NETWORK_IDS) == len(NETWORKS), "doppelte Kennung in NETWORKS"
    namen = [name for _, name in NETWORKS]
    assert len(set(namen)) == len(namen), "doppelter Name in NETWORKS"


# --- Der Endpunkt ----------------------------------------------------------


def test_kino_zeigt_keine_serien(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serien haben keinen Kinostart.

    Sie unter dieser Auswahl zu zeigen, hiesse einen Termin zu behaupten, den
    es nicht gibt - und der Kalender saehe unter "Kino" genauso aus wie unter
    "Digital".
    """

    async def serien(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [folge(nummer=5)]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(library, "series_calendar", serien)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    def eintraege(datumsart: str) -> list[dict]:
        antwort = arr_client.get(
            "/api/calendar",
            params={
                "sources": "mine",
                "date_type": datumsart,
                # Das Standardfenster liegt bei "heute" - die Folge traegt den
                # festen Testtag HEUTE, der seit dem Zuschnitt aufs Fenster
                # sonst herausfiele.
                "date_from": HEUTE,
                "date_to": HEUTE,
            },
        ).json()
        return [e for tag in antwort["days"] for e in tag["entries"]]

    assert len(eintraege("digital")) == 1
    assert eintraege("kino") == []


def test_kalender_braucht_eine_anmeldung(client: TestClient) -> None:
    assert client.get("/api/calendar").status_code == 401


def test_demo_modus_liefert_eine_brauchbare_seite(admin_client: TestClient) -> None:
    """Ohne TMDB-Schluessel muss der Kalender trotzdem etwas zeigen.

    Jede andere Seite laesst sich mit Beispieldaten ansehen; ohne das waere der
    Kalender die eine Seite, die auf einer frischen Installation kaputt aussieht.
    """
    antwort = admin_client.get("/api/calendar")
    assert antwort.status_code == 200

    daten = antwort.json()
    assert daten["demo"] is True
    assert daten["days"], "der Demo-Kalender ist leer"
    assert all(eintrag["source"] == "neu" for tag in daten["days"] for eintrag in tag["entries"])


def test_zeitraum_wird_geprueft(admin_client: TestClient) -> None:
    verdreht = admin_client.get(
        "/api/calendar", params={"date_from": "2026-09-01", "date_to": "2026-08-01"}
    )
    assert verdreht.status_code == 422

    zu_lang = admin_client.get(
        "/api/calendar", params={"date_from": "2026-01-01", "date_to": "2026-12-31"}
    )
    assert zu_lang.status_code == 422

    unsinn = admin_client.get("/api/calendar", params={"date_type": "papier"})
    assert unsinn.status_code == 422


def test_sonarr_folgen_kommen_bis_in_die_antwort(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vom Sonarr-Rohdatensatz bis zur fertigen Zeitleiste."""

    async def kalender(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [folge(nummer=5, hat_datei=True), folge(nummer=6, hat_datei=False)]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(library, "series_calendar", kalender)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    # Das Standardfenster liegt bei "heute" - die Folge traegt den festen
    # Testtag HEUTE, der seit dem Zuschnitt aufs Fenster sonst
    # herausfiele.
    daten = arr_client.get(
        "/api/calendar",
        params={"sources": "mine", "date_from": HEUTE, "date_to": HEUTE},
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert len(eintraege) == 1
    assert eintraege[0]["episode_label"] == "S03E05–06"
    assert eintraege[0]["missing"] is True
    assert eintraege[0]["missing_episodes"] == [6]
    assert eintraege[0]["source"] == "meine"


def test_sonarr_kalender_ueber_echten_http_aufruf_zeigt_laufende_serie(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nachgestellter Fall aus dem Prüfgang (25./26.09.2026): eine laufende, schon
    im Bestand befindliche Serie ohne neue Staffel muss trotzdem mit
    Staffel und Folgennummer im Kalender ankommen.

    Anders als ``test_sonarr_folgen_kommen_bis_in_die_antwort`` wird hier nicht
    ``library.series_calendar`` ersetzt, sondern Sonarrs dokumentierte Antwort
    auf ``GET /api/v3/calendar?start=&end=&includeSeries=true`` ueber
    ``httpx.MockTransport`` bedient - derselbe Weg, den ``SonarrClient.calendar``
    im Betrieb wirklich geht.
    """
    library.invalidate()
    admin_client.put(
        "/api/settings",
        json={
            "sonarr_url": "http://sonarr.example.com",
            "sonarr_api_key": "test-sonarr-key",
        },
    )

    def antwort(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/calendar":
            parameter = dict(request.url.params)
            assert parameter["includeSeries"] == "true"
            return httpx.Response(
                200,
                json=[
                    {
                        "seriesId": 208,
                        "episodeFileId": 0,
                        "seasonNumber": 23,
                        "episodeNumber": 1180,
                        "title": "Episode 1180",
                        "airDate": "2026-09-27",
                        "airDateUtc": "2026-09-27T15:00:00Z",
                        "overview": "",
                        "hasFile": False,
                        "monitored": True,
                        "series": {
                            "id": 208,
                            "title": "One Piece",
                            "tvdbId": 81797,
                            "monitored": True,
                            "images": [],
                            "ratings": {"votes": 10, "value": 8.7},
                            "genres": ["Animation"],
                        },
                    }
                ],
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        arr_client_module,
        "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(antwort)),
    )

    daten = admin_client.get(
        "/api/calendar",
        params={"date_from": "2026-09-21", "date_to": "2026-09-27", "sources": "mine"},
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert daten["arr_warning"] is None
    treffer = [e for e in eintraege if e["title"] == "One Piece"]
    assert len(treffer) == 1
    assert treffer[0]["episode_label"] == "S23E1180"
    assert treffer[0]["season"] == 23


#: Nachgestellt mit der echten Sonarr-Antwort vom 25.09.2026, gekuerzt auf
#: die Felder, die ``_falte_folgen`` liest. "One Piece" S23E25
#: laeuft am 27.09.2026 um 14:15 UTC, "The Simpsons" S38E01 um 00:00 UTC am
#: 28.09.2026 - beide echt, beide unveraendert aus dem Mitschnitt.
ECHTE_SONARR_ANTWORT = [
    {
        "seriesId": 1,
        "seasonNumber": 23,
        "episodeNumber": 22,
        "airDate": "2026-09-06",
        "airDateUtc": "2026-09-06T14:15:00Z",
        "hasFile": False,
        "monitored": True,
        "series": {
            "id": 1,
            "title": "One Piece",
            "tvdbId": 81797,
            "tmdbId": 37854,
            "monitored": True,
            "images": [],
            "ratings": {"votes": 367896, "value": 9.0},
            "genres": ["Action", "Adventure"],
        },
    },
    {
        "seriesId": 1,
        "seasonNumber": 23,
        "episodeNumber": 25,
        "airDate": "2026-09-27",
        "airDateUtc": "2026-09-27T14:15:00Z",
        "hasFile": False,
        "monitored": True,
        "series": {
            "id": 1,
            "title": "One Piece",
            "tvdbId": 81797,
            "tmdbId": 37854,
            "monitored": True,
            "images": [],
            "ratings": {"votes": 367896, "value": 9.0},
            "genres": ["Action", "Adventure"],
        },
    },
    {
        "seriesId": 3,
        "seasonNumber": 38,
        "episodeNumber": 1,
        "airDate": "2026-09-27",
        "airDateUtc": "2026-09-28T00:00:00Z",
        "hasFile": False,
        "monitored": True,
        "series": {
            "id": 3,
            "title": "The Simpsons",
            "tvdbId": 71663,
            "tmdbId": 456,
            "monitored": True,
            "images": [],
            "ratings": {"votes": 473305, "value": 8.6},
            "genres": ["Animation", "Comedy"],
        },
    },
]


def _sonarr_kalender_attrappe(request: httpx.Request) -> httpx.Response:
    """Sonarrs eigene Auswahl nachgebaut: ``start``/``end`` sind blosse Daten
    und werden als Mitternacht gelesen, verglichen wird ohne Zeitzonen-Umrechnung
    direkt gegen ``airDateUtc`` - **einschliesslich** beider Grenzen. Ein
    ``end`` ohne Uhrzeit deckt damit nur die Mitternacht des letzten Tages ab,
    nicht den ganzen Tag. Genau das zeigte der Rundgang an einer echten
    Instanz: eine spaeter am letzten Tag laufende Folge fehlte.
    """
    if request.url.path != "/api/v3/calendar":
        return httpx.Response(404)
    parameter = dict(request.url.params)
    start = datetime.fromisoformat(parameter["start"])
    ende = datetime.fromisoformat(parameter["end"])
    treffer = [
        folge
        for folge in ECHTE_SONARR_ANTWORT
        if start <= datetime.fromisoformat(folge["airDateUtc"].replace("Z", "")) <= ende
    ]
    return httpx.Response(200, json=treffer)


def test_folge_am_letzten_tag_des_fensters_fehlt_nicht(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nachgestellter Fall aus dem Prüfgang (25./26.09.2026, Arr-Betrieb, unabhaengig
    zweimal gesehen): Fuer die Woche 21.-27.09.2026 fehlte "One
    Piece" S23E25 (27.09., 14:15 UTC) im Kalender, obwohl Sonarrs eigener
    Kalender die Folge fuer genau diesen Zeitraum nennt. Ursache:
    ``SonarrClient.calendar`` schickte ``end`` als blosses Datum; Sonarr las
    das als Mitternacht und liess die Folge aus, weil sie spaeter am letzten
    Tag des Fensters laeuft.

    "The Simpsons" S38E01 (airDateUtc 28.09., 00:00 UTC) gehoert bei dieser
    Zeitzone (UTC+2) nach ``_lokaler_tag`` zum 28.09. - also wirklich zum Tag
    **nach** diesem Fenster, nicht mehr hinein. Seit dem Zuschnitt aufs
    Fenster erscheint sie hier folgerichtig nicht mehr; sie war nie Teil
    dieser Woche, nur des Aufschlags auf ``end``.
    """
    library.invalidate()
    admin_client.put(
        "/api/settings",
        json={"sonarr_url": "http://sonarr.example.com", "sonarr_api_key": "test-sonarr-key"},
    )
    monkeypatch.setattr(
        arr_client_module,
        "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(_sonarr_kalender_attrappe)),
    )

    daten = admin_client.get(
        "/api/calendar",
        params={
            "date_from": "2026-09-21",
            "date_to": "2026-09-27",
            "sources": "all",
            "date_type": "digital",
            "noise": "none",
        },
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert daten["arr_warning"] is None
    titel = {eintrag["title"] for eintrag in eintraege if eintrag["source"] == "meine"}
    assert "One Piece" in titel
    # Siehe Docstring: gehoert lokal zum 28.09., liegt also wirklich ausserhalb
    # dieses Fensters (21.-27.09.) - bleibt hier bewusst aussen vor.
    assert "The Simpsons" not in titel


def _lokale_uhrzeit_als_utc(tag: str, stunde: int, minute: int = 0) -> str:
    """Wandelt eine Uhrzeit **in der Zeitzone dieses Rechners** in den
    ``airDateUtc``-Zeitstempel um, den ``_lokaler_tag`` wieder auf ``tag``
    zurueckrechnen wuerde. So bleiben die beiden Tests unten unabhaengig davon,
    in welcher Zeitzone sie laufen - ``datetime.astimezone()`` auf einem naiven
    Wert nimmt die Systemzeitzone an, genau wie ``_lokaler_tag`` es beim
    Zurueckrechnen tut.
    """
    lokal = datetime.fromisoformat(f"{tag}T{stunde:02d}:{minute:02d}:00").astimezone()
    return lokal.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_folge_am_tag_nach_dem_fenster_erscheint_nicht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kehrseite der Notiz-#35-Reparatur: Der Tag Aufschlag auf ``end`` darf
    keine Folge eintauschen, die es vorher nicht gab. Eine Folge, deren
    lokaler Tag (derselbe, den die Kachel zeigt) erst nach ``date_to`` liegt,
    darf im Ergebnis nicht auftauchen - auch wenn Sonarr sie wegen des
    Aufschlags mitschickt.
    """
    bis = "2026-09-27"
    von = "2026-09-21"
    tag_danach = (date.fromisoformat(bis) + timedelta(days=1)).isoformat()

    async def kalender(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [
            folge(
                nummer=1,
                datum=_lokale_uhrzeit_als_utc(tag_danach, 12),
                serie={"id": 7, "title": "Zu spaet", "tvdbId": 12345, "monitored": True},
            )
        ]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(library, "series_calendar", kalender)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    daten = arr_client.get(
        "/api/calendar", params={"date_from": von, "date_to": bis, "sources": "mine"}
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert eintraege == []


def test_folge_spaet_am_letzten_tag_des_fensters_erscheint(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Schnitt darf nicht zu eng werden: Eine Folge, die spaet am letzten
    Tag des Fensters laeuft (derselbe Tag, den die Kachel zeigen wird), muss
    trotzdem erscheinen - genau das war der urspruengliche Befund."""
    bis = "2026-09-27"
    von = "2026-09-21"

    async def kalender(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [
            folge(
                nummer=1,
                datum=_lokale_uhrzeit_als_utc(bis, 23, 30),
                serie={"id": 7, "title": "Spaet dran", "tvdbId": 54321, "monitored": True},
            )
        ]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(library, "series_calendar", kalender)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    daten = arr_client.get(
        "/api/calendar", params={"date_from": von, "date_to": bis, "sources": "mine"}
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert [e["title"] for e in eintraege] == ["Spaet dran"]


async def test_sonarr_client_polstert_das_ende_um_einen_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unmittelbarer Beleg fuer den Aufschlag von oben, ohne den ganzen
    Router: ``SonarrClient.calendar`` muss Sonarr einen Tag mehr auf ``end``
    schicken, als der Aufrufer uebergibt."""
    from app.services.beschaffung.arr.sonarr import SonarrClient

    gesehen: dict[str, str] = {}

    def antwort(request: httpx.Request) -> httpx.Response:
        gesehen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        arr_client_module, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antwort))
    )

    await SonarrClient("http://sonarr.example.com", "key").calendar("2026-09-21", "2026-09-27")

    assert gesehen["start"] == "2026-09-21"
    assert gesehen["end"] == "2026-09-28"


async def test_radarr_client_polstert_das_ende_um_einen_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dieselbe Uhrzeiten-Grenze wie bei Sonarr: Radarr vergleicht
    seine Termine ebenso direkt gegen den rohen Zeitstempel von ``end``."""
    from app.services.beschaffung.arr.radarr import RadarrClient

    gesehen: dict[str, str] = {}

    def antwort(request: httpx.Request) -> httpx.Response:
        gesehen.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        arr_client_module, "_client", httpx.AsyncClient(transport=httpx.MockTransport(antwort))
    )

    await RadarrClient("http://radarr.example.com", "key").calendar("2026-09-21", "2026-09-27")

    assert gesehen["start"] == "2026-09-21"
    assert gesehen["end"] == "2026-09-28"


def test_fremder_bibliotheksbestand_zeigt_ehrlich_in_der_bibliothek(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nachgestellter Fall aus dem Prüfgang (25.09.2026): Eine Serie, die schon in
    Sonarr liegt, aber ueber Nexview nie angefragt wurde (kein Datensatz in
    ``requests_service.badges_for``), darf nicht wie eine eigene Anfrage
    aussehen. Zuvor setzte der Kalender ``status="downloaded"`` allein aus
    Sonarrs ``hasFile`` - unabhaengig davon, ob ueberhaupt jemand angefragt
    hatte. ``GET /api/requests/mine`` blieb fuer dasselbe Konto leer.
    """

    async def kalender(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [
            folge(
                nummer=6,
                serien_id=99,
                serie={
                    "id": 99,
                    "title": "Fremde Serie",
                    "tvdbId": 341199,
                    "tmdbId": 4242,
                    "monitored": True,
                },
            )
        ]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(library, "series_calendar", kalender)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    # Das Standardfenster liegt bei "heute" - die Folge traegt den festen
    # Testtag HEUTE, der seit dem Zuschnitt aufs Fenster sonst
    # herausfiele.
    daten = arr_client.get(
        "/api/calendar",
        params={"sources": "mine", "date_from": HEUTE, "date_to": HEUTE},
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert len(eintraege) == 1
    assert eintraege[0]["status"] == "in_library"


def test_nur_meine_fragt_tmdb_gar_nicht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Filter muss wirklich kurzschliessen, nicht nur nachtraeglich sieben."""

    async def explodiere(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("TMDB haette nicht gefragt werden duerfen")

    async def leer(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(calendar_service, "_neuerscheinungen", explodiere)
    monkeypatch.setattr(library, "series_calendar", leer)
    monkeypatch.setattr(library, "movie_calendar", leer)

    assert arr_client.get("/api/calendar", params={"sources": "mine"}).status_code == 200


def test_ausfall_von_radarr_laesst_die_seite_stehen(arr_client: TestClient) -> None:
    """Port 9 lehnt sofort ab - die Antwort muss trotzdem 200 sein.

    Haus-Konvention: eine kaputte Quelle erzeugt einen Hinweis, keine
    Fehlerseite. Sonst saehe ein Ausfall aus wie "diese Woche kommt nichts".
    """
    antwort = arr_client.get("/api/calendar", params={"sources": "mine"})

    assert antwort.status_code == 200
    assert antwort.json()["arr_warning"]


def test_arr_warning_ist_eine_kennung_kein_fertiger_satz(arr_client: TestClient) -> None:
    """⚠️ `arr_warning` ging als fertiger deutscher Satz aus dem Backend.

    Auf Englisch gestellt bekam der Kalender trotzdem Deutsch zu sehen; die
    Oberfläche übersetzt Kennungen, keine Sätze. Der Ausfall an Port 9 wirft
    ``ArrError(code="arr_unreachable", ...)`` - genau diese Kennung muss
    ankommen, kein Text mit "nicht erreichbar" darin.
    """
    antwort = arr_client.get("/api/calendar", params={"sources": "mine"})

    assert antwort.status_code == 200
    warnung = antwort.json()["arr_warning"]
    assert warnung == "arr_unreachable"


def test_bestandstitel_ohne_datei_bleibt_im_kalender_anfragbar(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Im Kalender laufen nur die Neuerscheinungen durch ``status_setzen``.

    Radarr kennt so eine Neuerscheinung manchmal schon (Listen-Sync, zweite
    Instanz), ohne dass jemand sie angefragt hat - "searching" darf dann
    nicht wie eine laufende Suche behandelt werden.
    """

    async def wird_gesucht(_einstellungen, _art, items, _stufe="standard", **_rest):
        for eintrag in items:
            eintrag.status = "searching"
        return library.MatchResult(items=items)

    monkeypatch.setattr(library, "apply_status", wird_gesucht)

    daten = arr_client.get("/api/calendar").json()
    neuerscheinungen = [
        eintrag
        for tag in daten["days"]
        for eintrag in tag["entries"]
        if eintrag["source"] == "neu"
    ]
    assert neuerscheinungen, "keine Neuerscheinung im Demo-Kalender gefunden"
    assert all(eintrag["status"] == "not_requested" for eintrag in neuerscheinungen)


def test_altersgrenze_verbirgt_eigene_titel_ohne_zuordnung(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Radarr und Sonarr umgehen TMDB - hier waere ein neues Leck.

    Ohne aufloesbare TMDB-Kennung laesst sich die Freigabe nicht pruefen. Dann
    faellt der Eintrag weg; im Zweifel verbergen, nicht zeigen. Genau diesen
    Zweig schreibt niemand versehentlich richtig.
    """
    create_user(admin_client, "kind-kalender", age=6, password="passwort-1234")

    admin_client.put(
        "/api/settings",
        json={
            "sonarr_url": "http://127.0.0.1:9",
            "sonarr_api_key": "test-sonarr-key",
        },
    )

    async def kalender(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [
            folge(
                nummer=5,
                serie={"id": 7, "title": "Ohne Zuordnung", "tvdbId": None, "monitored": True},
            )
        ]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    monkeypatch.setattr(library, "series_calendar", kalender)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    kopf = auth_headers(admin_client, "kind-kalender", "passwort-1234")
    daten = admin_client.get(
        "/api/calendar", params={"sources": "mine"}, headers=kopf
    ).json()

    assert daten["days"] == []


def test_sonarr_null_kennung_wird_nicht_zur_null(admin_client: TestClient) -> None:
    """Sonarr und Radarr melden **0**, wenn sie keine TMDB-Kennung kennen.

    Eine durchgelassene 0 ist schlimmer als gar keine Kennung: Die
    Nachaufloesung ueber die TVDB-Kennung prueft auf ``None`` und
    uebergeht den Eintrag, die Entdopplung hielte ihn fuer eigenstaendig,
    die Detailseite waere ``/titel/tv/0``, und der Altersfilter wuerfe ihn
    weg, weil sich zu einer 0 nichts pruefen laesst.

    Gemessen an einer echten Bibliothek: 2 von 175 Serien haben ``tmdbId: 0``.
    """
    from app.services.calendar import _kennung

    assert _kennung(0) is None
    assert _kennung(None) is None
    assert _kennung(-1) is None
    assert _kennung("3863") is None
    assert _kennung(3863) == 3863


# --- Poster, wenn der Weg keine liefert (Rundgang-Befund) ----------------


async def test_eigene_eintraege_ohne_poster_bekommen_es_ueber_tmdb(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemessen an der Live-Instanz am 24.09.2026: Im NEX-Betrieb stand an
    jeder Karte unter „Bereits angefragt“ „Kein Poster“. nexcrates Kalender
    nennt keine Bilder. Eine gespeicherte Anfrage kennt das Poster schon; sonst
    fragt der Kalender TMDB, ueber denselben Zwischenspeicher wie die
    Titelseite."""
    from app.db import SessionLocal
    from app.models import MediaRequest, MediaType, RequestStatus, User
    from app.schemas_calendar import CalendarEntry
    from app.services import media
    from app.services.settings_service import load_settings, save_settings

    gefragt: list[tuple[str, int]] = []

    class TmdbAttrappe:
        async def detail(self, art: str, kennung: int) -> dict:
            gefragt.append((art, kennung))
            return {"id": kennung, "poster_path": f"/p{kennung}.jpg"}

    monkeypatch.setattr(media, "_client", lambda *_a, **_k: TmdbAttrappe())

    def eintrag(kennung: int, art: str = "movie", poster: str | None = None, herkunft: str = "radarr"):
        return CalendarEntry(
            key=f"{herkunft}:{kennung}",
            date=HEUTE,
            source="meine",
            origin=herkunft,
            media_type=MediaType(art),
            tmdb_id=kennung,
            title=f"Titel {kennung}",
            poster_url=poster,
        )

    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "x" * 32, "demo_mode": "off"})
        nutzer = db.query(User).first()
        assert nutzer is not None
        db.add(
            MediaRequest(
                user_id=nutzer.id,
                media_type=MediaType.movie,
                tmdb_id=11,
                title="Titel 11",
                poster_path="https://image.tmdb.org/t/p/w500/gespeichert.jpg",
                status=RequestStatus.searching,
                fassung_kennung="radarr-standard",
            )
        )
        # Dieselbe Nummer bei TMDB ist bei Film und Serie ein anderer Titel.
        db.add(
            MediaRequest(
                user_id=nutzer.id,
                media_type=MediaType.tv,
                tmdb_id=12,
                title="Serie 12",
                poster_path="https://image.tmdb.org/t/p/w500/falsche-art.jpg",
                status=RequestStatus.searching,
                fassung_kennung="sonarr-standard",
            )
        )
        db.commit()
        eintraege = [
            eintrag(11),
            eintrag(12),
            eintrag(13, "tv"),
            eintrag(14, poster="https://example.com/schon-da.jpg"),
        ]

        await calendar_service._poster_nachtragen(db, load_settings(db, frisch=True), eintraege)

    assert [e.poster_url for e in eintraege] == [
        "https://image.tmdb.org/t/p/w500/gespeichert.jpg",
        "https://image.tmdb.org/t/p/w500/p12.jpg",
        "https://image.tmdb.org/t/p/w500/p13.jpg",
        "https://example.com/schon-da.jpg",
    ]
    # Die gespeicherte Anfrage spart die Abfrage; wer ein Poster hat, auch.
    assert sorted(gefragt) == [("movie", 12), ("tv", 13)]


def test_der_kalender_traegt_poster_bis_in_die_antwort(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vom Rohdatensatz ohne Bild bis zur Karte mit Poster."""
    from app.db import SessionLocal
    from app.services import media
    from app.services.settings_service import save_settings

    class TmdbAttrappe:
        async def detail(self, art: str, kennung: int) -> dict:
            return {"id": kennung, "poster_path": f"/p{kennung}.jpg"}

    async def kalender(_settings: object, _von: str, _bis: str) -> list[dict]:
        return [
            folge(
                nummer=5,
                serie={"id": 7, "title": "Yellowstone", "tmdbId": 73586, "images": [], "monitored": True},
            )
        ]

    async def keine_filme(_settings: object, _von: str, _bis: str) -> list[dict]:
        return []

    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "x" * 32, "demo_mode": "off"})
        db.commit()
    monkeypatch.setattr(media, "_client", lambda *_a, **_k: TmdbAttrappe())
    monkeypatch.setattr(library, "series_calendar", kalender)
    monkeypatch.setattr(library, "movie_calendar", keine_filme)

    # Das Standardfenster liegt bei "heute" - die Folge traegt den festen
    # Testtag HEUTE, der seit dem Zuschnitt aufs Fenster sonst
    # herausfiele.
    daten = arr_client.get(
        "/api/calendar",
        params={"sources": "mine", "date_from": HEUTE, "date_to": HEUTE},
    ).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert [e["poster_url"] for e in eintraege] == ["https://image.tmdb.org/t/p/w500/p73586.jpg"]


async def test_der_kalender_holt_hoechstens_vierzig_poster_je_aufruf(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Deckel haelt die Kosten eines Aufrufs fest; der Rest kommt beim
    naechsten, dann aus dem Zwischenspeicher der ersten."""
    from app.db import SessionLocal
    from app.models import MediaType
    from app.schemas_calendar import CalendarEntry
    from app.services import media
    from app.services.settings_service import load_settings, save_settings

    gefragt: list[int] = []

    class TmdbAttrappe:
        async def detail(self, art: str, kennung: int) -> dict:
            gefragt.append(kennung)
            return {"id": kennung, "poster_path": f"/p{kennung}.jpg"}

    monkeypatch.setattr(media, "_client", lambda *_a, **_k: TmdbAttrappe())
    eintraege = [
        CalendarEntry(
            key=f"radarr:{kennung}", date=HEUTE, source="meine", origin="radarr",
            media_type=MediaType.movie, tmdb_id=kennung, title=f"Titel {kennung}",
        )
        for kennung in range(1000, 1045)
    ]
    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "x" * 32, "demo_mode": "off"})
        db.commit()
        await calendar_service._poster_nachtragen(db, load_settings(db, frisch=True), eintraege)

    assert len(gefragt) == 40
    assert sum(1 for e in eintraege if e.poster_url) == 40


async def test_im_demo_modus_fragt_der_kalender_tmdb_nicht(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import SessionLocal
    from app.models import MediaType
    from app.schemas_calendar import CalendarEntry
    from app.services import media
    from app.services.settings_service import load_settings, save_settings

    def verboten(*_a: object, **_k: object) -> None:
        raise AssertionError("TMDB haette nicht gefragt werden duerfen")

    monkeypatch.setattr(media, "_client", verboten)
    eintrag = CalendarEntry(
        key="radarr:77", date=HEUTE, source="meine", origin="radarr",
        media_type=MediaType.movie, tmdb_id=77, title="Titel 77",
    )
    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "x" * 32, "demo_mode": "on"})
        db.commit()
        await calendar_service._poster_nachtragen(db, load_settings(db, frisch=True), [eintrag])

    assert eintrag.poster_url is None
