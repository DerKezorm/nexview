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

Was diese Datei deshalb zusehen muss:

* Ein Server, der grosse Haeppchen nicht schafft, liefert am Ende trotzdem
  **jeden** Titel - keiner fehlt, keiner doppelt.
* Die Leiter wird **einmal** hinabgestiegen, nicht bei jeder Abfrage neu.
* Nur eine Zeitueberschreitung fuehrt zum Halbieren - ein HTTP 500 nicht.
* Am Boden der Leiter kommt eine Meldung, die der Leser in seiner Sprache
  bekommt.
"""

from __future__ import annotations

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
from app.services.mediaserver.jellyfin import SEITE_FILME, JellyfinServer

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
        #: Das ``Limit`` jeder Abfrage, in der Reihenfolge des Eintreffens.
        self.abgefragt: list[int] = []

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

    # Erste Abfrage ist der Gesehen-Stand der Folgen - sie beginnt oben.
    assert server.abgefragt[:3] == [SEITE_HOECHSTENS, 100, 50]
    assert set(server.abgefragt[3:]) == {50}, (
        f"nach dem Lernen noch andere Groessen gefragt: {server.abgefragt}"
    )
    assert server._seitengroesse == 50


@pytest.mark.asyncio
async def test_filme_fangen_kleiner_an_als_der_rest() -> None:
    """Die Film-Abfrage ist die teure - sie liest als einzige Dateiangaben mit.

    Bei einem Server, der alles schafft, muss man das an der ersten Abfrage
    sehen: Folgen und Serien duerfen gross fragen, Filme nicht.
    """
    server = LahmerJellyfin(
        bestand={"Movie": _filme(10), "Series": _serien(5)},
        schafft=10_000,
    )

    await server.library_index()

    assert SEITE_FILME < SEITE_HOECHSTENS
    assert server.abgefragt[0] == SEITE_HOECHSTENS  # Folgen (Gesehen-Stand)
    assert SEITE_FILME in server.abgefragt, (
        f"die Film-Abfrage fragte nicht mit {SEITE_FILME}: {server.abgefragt}"
    )


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
    assert len(server.abgefragt) == 1, (
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
    assert server.abgefragt[:3] == [SEITE_HOECHSTENS, 100, 50]
