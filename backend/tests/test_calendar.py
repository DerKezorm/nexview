"""Der Erscheinungs-Kalender.

Der Schwerpunkt liegt auf drei Dingen, die ohne Test still danebenliegen
wuerden: das Zusammenfassen mehrerer Folgen an einem Tag, die Zeitzone und der
Zwischenspeicher-Schluessel der Entdecken-Filter.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import calendar as calendar_service
from app.services import library
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
            "/api/calendar", params={"sources": "mine", "date_type": datumsart}
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

    daten = arr_client.get("/api/calendar", params={"sources": "mine"}).json()
    eintraege = [eintrag for tag in daten["days"] for eintrag in tag["entries"]]

    assert len(eintraege) == 1
    assert eintraege[0]["episode_label"] == "S03E05–06"
    assert eintraege[0]["missing"] is True
    assert eintraege[0]["missing_episodes"] == [6]
    assert eintraege[0]["source"] == "meine"


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
