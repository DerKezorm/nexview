"""Ein langsamer Medienserver darf den Bibliotheks-Abgleich nicht kippen.

Der Anlass steht in Issue #7: Emby des Melders lief, sein Jellyfin nicht.
Beides derselbe Code - ``EmbyServer`` ist eine Ableitung von ``JellyfinServer``.
Der Unterschied war eine feste Zeitgrenze von 15 Sekunden fuer jede Abfrage:
Sein Emby antwortete darin, sein Jellyfin nicht. Und weil ein einziges
gerissenes Haeppchen den ganzen Durchlauf mitnimmt, stand danach nicht "halb
fertig" in der Karte, sondern "Not synced yet.".

⚠️ **Der Kern ist nicht "es dauert jetzt laenger duerfen".** Eine groessere
feste Zahl waere dieselbe Falle einen Schritt weiter hinten - es gibt immer
eine groessere Bibliothek und einen langsameren Server. Der Kern ist, dass die
Haeppchengroesse vom Server *gelernt* wird: Reisst eine Abfrage die Zeit, wird
halbiert und dieselbe Stelle erneut gefragt.

⚠️ **Das gilt fuer die Bibliothek, nicht fuer den Gesehen-Stand.** Dessen
Preis haengt nicht an der Seitengroesse: Beim Melder kostete eine Seite zu 25
gesehenen Folgen 45 Sekunden, alles auf einmal 25. Der kommt deshalb in einer
einzigen Abfrage - die Leiter wuerde ihn mit jeder Stufe teurer machen.

Was diese Datei deshalb zusehen muss:

* Ein Server, der grosse Haeppchen nicht schafft, liefert am Ende trotzdem
  **jeden** Titel - keiner fehlt, keiner doppelt.
* Die Leiter wird **einmal** hinabgestiegen, nicht bei jeder Abfrage neu.
* Nur eine Zeitueberschreitung fuehrt zum Halbieren - ein HTTP 500 nicht.
* Am Boden der Leiter kommt eine Meldung, die der Leser in seiner Sprache
  bekommt.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.mediaserver.base import (
    SEITE_HOECHSTENS,
    SEITE_MINDESTENS,
    MediaServerError,
    kleineres_haeppchen,
    seiten_timeout,
)
from app.services.mediaserver.jellyfin import (
    GESEHEN_ZEITGRENZE,
    SEITE_FILME,
    JellyfinServer,
)

EINSTELLUNGEN = SimpleNamespace(
    mediaserver_url="http://jelly.test",
    mediaserver_token="token",
    mediaserver_machine_id="maschine",
    mediaserver_client_identifier="nexview-test",
    mediaserver_account_id="konto-1",
)


def _filme(anzahl: int) -> list[dict[str, Any]]:
    return [
        {
            "Id": f"film-{n}",
            "Name": f"Film {n}",
            "ProductionYear": 2000 + n % 20,
            "ProviderIds": {"Tmdb": str(1000 + n)},
        }
        for n in range(anzahl)
    ]


def _serien(anzahl: int) -> list[dict[str, Any]]:
    return [
        {
            "Id": f"serie-{n}",
            "Name": f"Serie {n}",
            "ProductionYear": 2010 + n % 10,
            "ProviderIds": {"Tmdb": str(5000 + n), "Tvdb": str(9000 + n)},
        }
        for n in range(anzahl)
    ]


class LahmerJellyfin(JellyfinServer):
    """Ein Jellyfin, das nur bis zu ``schafft`` Titeln rechtzeitig antwortet.

    Bewusst ueber ``_anfrage`` und nicht ueber ``_seiten``: Genau die Schleife
    darunter ist das, was hier geprueft wird. Und bewusst mit dem echten
    ``__init__`` - ein Test-Doppel, das sich seine Felder selbst setzt, merkt
    nicht, wenn der Bauplan sich aendert.
    """

    def __init__(
        self,
        *,
        bestand: dict[str, list[dict[str, Any]]],
        schafft: int,
        fehler: MediaServerError | None = None,
    ) -> None:
        super().__init__(EINSTELLUNGEN)  # type: ignore[arg-type]
        self.bestand = bestand
        self.schafft = schafft
        self.fehler = fehler
        #: Das ``Limit`` jeder seitenweisen Abfrage, in der Reihenfolge des
        #: Eintreffens.
        self.abgefragt: list[int] = []
        #: Die Parameter jeder ungeteilten Abfrage (der Gesehen-Stand), dazu
        #: die mitgegebene Zeitgrenze unter ``timeout``.
        self.ungeteilt: list[dict[str, Any]] = []

    async def _anfrage(  # type: ignore[override]
        self,
        methode: str,
        pfad: str,
        *,
        token: str | None = None,
        basis: str | None = None,
        zweck: str = "",
        timeout: Any = None,
        **kwargs: Any,
    ) -> Any:
        params = kwargs.get("params") or {}
        if "Limit" not in params:
            # Der Gesehen-Stand: eine Abfrage, keine Seiten.
            self.ungeteilt.append({**params, "timeout": timeout})
            if self.fehler is not None:
                raise self.fehler
            alle = self.bestand.get(str(params["IncludeItemTypes"]), [])
            return {"Items": alle, "TotalRecordCount": len(alle)}

        grenze = int(params["Limit"])
        self.abgefragt.append(grenze)

        if self.fehler is not None:
            raise self.fehler
        if grenze > self.schafft:
            raise MediaServerError(
                "Der Jellyfin-Server antwortet nicht (Zeitüberschreitung).",
                code="mediaserver_timeout",
                service="Jellyfin",
            )

        alle = self.bestand.get(str(params["IncludeItemTypes"]), [])
        start = int(params["StartIndex"])
        return {"Items": alle[start : start + grenze], "TotalRecordCount": len(alle)}


# --------------------------------------------------------------------------
# Die Leiter selbst
# --------------------------------------------------------------------------


def test_die_leiter_endet() -> None:
    """Halbieren muss aufhoeren - sonst laeuft der Abgleich gegen null."""
    groesse: int | None = SEITE_HOECHSTENS
    stufen = []
    while groesse is not None:
        stufen.append(groesse)
        groesse = kleineres_haeppchen(groesse)

    assert stufen[0] == SEITE_HOECHSTENS
    assert stufen[-1] == SEITE_MINDESTENS
    assert stufen == sorted(stufen, reverse=True), "die Leiter muss abwaerts fuehren"
    assert len(stufen) < 10, "zu viele Stufen - ein toter Server braucht ewig"


def test_zeitgrenze_waechst_mit_der_menge() -> None:
    """Sonst waere sie wieder eine feste Zahl, nur an anderer Stelle.

    ⚠️ Der Grund ist nicht Sparsamkeit: Ohne mitwachsende Grenze zahlt ein
    **toter** Server auf jeder Stufe der Leiter die volle Wartezeit. Der
    Administrator drueckt "Sync now" und steht Minuten vor einem Knopf.
    """
    assert seiten_timeout(200).read > seiten_timeout(100).read
    assert seiten_timeout(100).read > seiten_timeout(SEITE_MINDESTENS).read

    ganze_leiter = 0.0
    groesse: int | None = SEITE_HOECHSTENS
    while groesse is not None:
        ganze_leiter += seiten_timeout(groesse).read
        groesse = kleineres_haeppchen(groesse)
    assert ganze_leiter < 180, (
        f"Ein toter Server braucht {ganze_leiter:.0f}s bis zur Auskunft - zu lang."
    )


# --------------------------------------------------------------------------
# Der eigentliche Fall aus Issue #7
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_langsamer_server_liefert_trotzdem_alles() -> None:
    """⚠️ Der Waechter dieser Datei.

    Ein Server, der bei 200 und bei 100 in die Zeitgrenze laeuft und erst bei
    50 antwortet, muss am Ende **jeden** Titel geliefert haben. Vor dieser
    Aenderung kam an dieser Stelle gar nichts zurueck.
    """
    server = LahmerJellyfin(
        bestand={"Movie": _filme(250), "Series": _serien(40)},
        schafft=50,
    )

    werke = await server.library_index()

    filme = {w.rating_key for w in werke if w.media_type == "movie"}
    serien = {w.rating_key for w in werke if w.media_type == "tv"}
    assert filme == {f"film-{n}" for n in range(250)}
    assert serien == {f"serie-{n}" for n in range(40)}
    # Keine Dublette: Beim Halbieren bleibt ``StartIndex`` stehen, die Seite
    # wird also wiederholt - nicht uebersprungen und nicht doppelt genommen.
    assert len(werke) == 290


@pytest.mark.asyncio
async def test_die_leiter_wird_nur_einmal_hinabgestiegen() -> None:
    """Was der Server ueber sich verraten hat, gilt fuer den ganzen Durchlauf.

    Ohne das zahlte jede der drei Abfragen ihre eigenen Fehlversuche - bei
    einem wirklich lahmen Server ein Vielfaches der Wartezeit, jede Stunde
    aufs Neue.
    """
    server = LahmerJellyfin(
        bestand={"Movie": _filme(120), "Series": _serien(10)},
        schafft=50,
    )

    await server.library_index()

    # Erste seitenweise Abfrage sind die Filme - sie beginnt bei SEITE_FILME.
    # (Der Gesehen-Stand davor kommt ungeteilt, siehe unten.)
    assert server.abgefragt[:2] == [SEITE_FILME, 50]
    assert set(server.abgefragt[2:]) == {50}, (
        f"nach dem Lernen noch andere Groessen gefragt: {server.abgefragt}"
    )
    assert server._seitengroesse == 50


@pytest.mark.asyncio
async def test_filme_fangen_kleiner_an_als_der_rest() -> None:
    """Die Film-Abfrage ist die teure - sie liest als einzige Dateiangaben mit.

    Bei einem Server, der alles schafft, muss man das an den Abfragen
    sehen: Serien duerfen gross fragen, Filme nicht.
    """
    server = LahmerJellyfin(
        bestand={"Movie": _filme(10), "Series": _serien(5)},
        schafft=10_000,
    )

    await server.library_index()

    assert SEITE_FILME < SEITE_HOECHSTENS
    assert server.abgefragt[0] == SEITE_FILME, (
        f"die Film-Abfrage fragte nicht mit {SEITE_FILME}: {server.abgefragt}"
    )
    assert SEITE_HOECHSTENS in server.abgefragt  # Serien


@pytest.mark.asyncio
async def test_am_boden_der_leiter_eine_eigene_meldung() -> None:
    """Wer 25 Titel nicht liefert, ist nicht langsam - da stimmt etwas anderes.

    Und die Meldung muss eine **Kennung** tragen: Ohne sie reicht der Router
    den deutschen Satz unveraendert durch, und genau das las der Melder von
    Issue #7 auf seiner englischen Oberflaeche.
    """
    server = LahmerJellyfin(
        bestand={"Movie": _filme(50), "Series": _serien(5)},
        schafft=1,
    )

    with pytest.raises(MediaServerError) as gefangen:
        await server.library_index()

    meldung = gefangen.value.als_meldung()
    assert meldung["code"] == "mediaserver_pages_too_slow"
    assert meldung["service"] == "Jellyfin"
    assert meldung["size"] == SEITE_MINDESTENS
    # Nicht tiefer als der Boden gefragt.
    assert min(server.abgefragt) == SEITE_MINDESTENS


@pytest.mark.asyncio
async def test_ein_serverfehler_wird_nicht_kleiner_gefragt() -> None:
    """Ein HTTP 500 faellt bei 25 Titeln genauso an - nur viermal spaeter.

    Die Leiter ist fuer Langsamkeit da, nicht fuer Fehler. Wer hier alles
    wiederholt, macht aus jeder kaputten Abfrage eine minutenlange.
    """
    server = LahmerJellyfin(
        bestand={"Movie": _filme(50)},
        schafft=10_000,
        fehler=MediaServerError(
            "Der Jellyfin-Server meldet einen Fehler (HTTP 500).", 500
        ),
    )

    with pytest.raises(MediaServerError) as gefangen:
        await server.library_index()

    assert gefangen.value.status_code == 500
    # Kein Halbieren. Die Gesehen-Abfrage einmal - ihr Fehler kippt die
    # Bibliothek nicht mehr, siehe unten -, dann die Film-Abfrage einmal, und
    # deren 500 kommt beim Aufrufer an.
    assert len(server.ungeteilt) == 1
    assert server.abgefragt == [SEITE_FILME], (
        f"ein Serverfehler wurde wiederholt: {server.abgefragt}"
    )


@pytest.mark.asyncio
async def test_emby_erbt_das_verhalten() -> None:
    """Emby ist eine Ableitung - der Melder von Issue #7 betreibt beide.

    Es waere ein stiller Fehler, die Reparatur nur bei Jellyfin wirken zu
    lassen: Emby ist heute nur schneller, nicht anders gebaut.
    """
    from app.services.mediaserver.emby import EmbyServer

    class LahmesEmby(EmbyServer, LahmerJellyfin):  # type: ignore[misc]
        pass

    server = LahmesEmby(bestand={"Movie": _filme(80), "Series": []}, schafft=50)
    werke = await server.library_index()

    assert {w.rating_key for w in werke} == {f"film-{n}" for n in range(80)}
    assert server.abgefragt[:3] == [SEITE_FILME, 50, 50]


# --------------------------------------------------------------------------
# Die Bibliothek haengt nicht an der Gesehen-Abfrage
# --------------------------------------------------------------------------


class GesehenKaputt(LahmerJellyfin):
    """Ein Jellyfin, bei dem nur die Abfrage nach Gesehenem scheitert.

    So sah es beim Melder von Issue #7 aus: Filme und Serien kamen in drei
    Sekunden, die gesehenen Folgen seines Kontos brauchten 53 Sekunden je
    Seite - und rissen jede Zeitgrenze.
    """

    def __init__(self, *, gesehen_fehler: MediaServerError | None = None, **rest: Any) -> None:
        super().__init__(**rest)
        self.gesehen_fehler = gesehen_fehler or MediaServerError(
            "Der Jellyfin-Server antwortet nicht (Zeitüberschreitung).",
            code="mediaserver_timeout",
            service="Jellyfin",
        )
        self.gesehen_versuche = 0

    async def _anfrage(self, methode: str, pfad: str, **kwargs: Any) -> Any:  # type: ignore[override]
        params = kwargs.get("params") or {}
        if params.get("Filters") == "IsPlayed":
            self.gesehen_versuche += 1
            raise self.gesehen_fehler
        return await super()._anfrage(methode, pfad, **kwargs)


@pytest.mark.asyncio
async def test_scheiternde_gesehen_abfrage_kippt_die_bibliothek_nicht(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """⚠️ Der zweite Waechter aus Issue #7.

    Die Abfrage nach gesehenen Folgen liefert nur den Haken "angefangen" an
    den Serien. Scheitert sie, muss die Bibliothek trotzdem vollstaendig
    ankommen - und der Haken darf nicht "nein" werden, sondern "unbekannt".
    Vor dieser Aenderung stand wochenlang "Not synced yet" in der Karte,
    obwohl Filme und Serien laengst lesbar waren.
    """
    server = GesehenKaputt(
        bestand={"Movie": _filme(30), "Series": _serien(5)},
        schafft=10_000,
    )

    with caplog.at_level(logging.WARNING, logger="nexview.mediaserver"):
        werke = await server.library_index()

    assert len(werke) == 35
    serien = [w for w in werke if w.media_type == "tv"]
    assert len(serien) == 5
    assert all(w.owner_watched is None for w in serien), "unbekannt, nicht nein"
    filme = [w for w in werke if w.media_type == "movie"]
    assert all(w.owner_watched is False for w in filme)
    # Einmal gefragt, nicht kleiner wiederholt - kleiner hilft hier nicht ...
    assert server.gesehen_versuche == 1
    # ... und das Protokoll nennt die Kennung, nicht den deutschen Satz.
    assert "played episodes" in caplog.text
    assert "mediaserver_timeout" in caplog.text
    assert "Zeitüberschreitung" not in caplog.text


@pytest.mark.asyncio
async def test_abgelehnter_zugang_wird_nicht_zum_unbekannten_haken() -> None:
    """Ein 401 ist kein "gerade langsam" - daran scheitert die Bibliothek genauso.

    Wer ihn hier schluckte, laese danach mit demselben abgelaufenen Zugang die
    Bibliothek - und bekaeme dieselbe Antwort, nur ohne die richtige Meldung.
    """
    server = GesehenKaputt(
        bestand={"Movie": _filme(3), "Series": _serien(2)},
        schafft=10_000,
        gesehen_fehler=MediaServerError(
            "Der Jellyfin-Server hat die Anmeldung nicht akzeptiert.", 401
        ),
    )

    with pytest.raises(MediaServerError) as gefangen:
        await server.library_index()

    assert gefangen.value.status_code == 401


# --------------------------------------------------------------------------
# Der Gesehen-Stand kommt in einer Abfrage
# --------------------------------------------------------------------------


def _folgen(serien: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "Id": f"folge-{n}",
            "SeriesId": serie,
            "UserData": {"Played": True, "LastPlayedDate": "2026-09-04T10:00:00Z"},
        }
        for n, serie in enumerate(serien)
    ]


@pytest.mark.asyncio
async def test_gesehen_stand_kommt_in_einer_abfrage() -> None:
    """⚠️ Der dritte Waechter aus Issue #7: keine Seiten, keine Zaehlung, keine Bilder.

    Gemessen beim Melder: eine Seite zu 25 gesehenen Folgen 45 Sekunden, alles
    auf einmal 25. Wer hier wieder blaettert, macht aus einer halben Minute
    siebzehn - und jede Halbierung der Leiter schlimmer.
    """
    server = LahmerJellyfin(
        bestand={
            "Movie": _filme(3),
            "Series": _serien(3),
            "Episode": _folgen(["serie-1", "serie-1"]),
        },
        schafft=10_000,
    )

    werke = await server.library_index()

    gesehen = [p for p in server.ungeteilt if p.get("Filters") == "IsPlayed"]
    assert [p["IncludeItemTypes"] for p in gesehen] == ["Episode"]
    abfrage = gesehen[0]
    assert "Limit" not in abfrage and "StartIndex" not in abfrage
    assert abfrage["EnableTotalRecordCount"] == "false"
    assert abfrage["EnableImages"] == "false"
    # Und die Antwort kommt an: Serie 1 ist angefangen, die anderen nicht.
    haken = {w.rating_key: w.owner_watched for w in werke if w.media_type == "tv"}
    assert haken == {"serie-0": False, "serie-1": True, "serie-2": False}


@pytest.mark.asyncio
async def test_gesehen_abfrage_hat_ihre_eigene_zeitgrenze() -> None:
    """Laenger als jede Stufe der Leiter - und fest, mit Absicht.

    Der Preis dieser Abfrage haengt nicht an der Groesse, also gibt es nichts
    zu halbieren. Die Frage ist nur, wie lange man auf die eine Antwort wartet:
    Der Melder braucht 25 Sekunden, die Leiter erlaubte hoechstens 56.
    """
    server = LahmerJellyfin(
        bestand={"Movie": [], "Series": [], "Episode": []}, schafft=10_000
    )

    await server.watched_index("persoenliches-token", "konto-1")

    assert [p["IncludeItemTypes"] for p in server.ungeteilt] == ["Movie", "Episode"]
    assert server.abgefragt == [], "der Gesehen-Stand wurde seitenweise gefragt"
    for abfrage in server.ungeteilt:
        grenze = abfrage["timeout"]
        assert grenze is GESEHEN_ZEITGRENZE
        assert grenze.read > seiten_timeout(SEITE_HOECHSTENS).read
        assert grenze.read >= 60
