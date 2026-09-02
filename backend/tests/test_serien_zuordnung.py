"""Eine Serie ohne TVDB-Kennung ueber Sonarr zuordnen (Issue #5).

Die Zahlen und Titel stammen aus vier Messungen an einer echten
Sonarr-Instanz vom 01.09.2026 - nicht aus der Beschreibung der API.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import serien_zuordnung
from app.services.serien_zuordnung import erlaubt, zuordnen


class _FakeSonarr:
    """Antwortet je Suchbegriff mit einer festen Trefferliste."""

    def __init__(self, nach_begriff: dict[str, list[dict[str, Any]]]) -> None:
        self.nach_begriff = nach_begriff
        self.gefragt: list[str] = []

    async def suche(self, begriff: str) -> list[dict[str, Any]]:
        self.gefragt.append(begriff)
        return self.nach_begriff.get(begriff, [])


def _serie(
    tvdb_id: int,
    titel: str,
    jahr: int | None = None,
    tmdb_id: int = 0,
    staffeln: tuple[int, ...] = (0, 1),
) -> dict[str, Any]:
    return {
        "tvdbId": tvdb_id,
        "tmdbId": tmdb_id,
        "title": titel,
        "year": jahr or 0,
        "overview": f"Handlung von {titel}.",
        "images": [{"coverType": "poster", "remoteUrl": f"https://bild/{tvdb_id}.jpg"}],
        "seasons": [{"seasonNumber": n} for n in staffeln],
    }


# --- Der gute Fall: eindeutig ueber die TMDB-Kennung -------------------------


@pytest.mark.anyio
async def test_gleiche_tmdb_kennung_ist_eindeutig() -> None:
    """Der gemessene Fall "Marc Eliot": ein Treffer traegt dieselbe Nummer."""
    client = _FakeSonarr(
        {"Marc Eliot": [
            _serie(334698, "Marc Eliot", 1998, tmdb_id=103594),
            _serie(999001, "Marc Elliott Show", 2005, tmdb_id=555),
        ]}
    )

    ergebnis = await zuordnen(client, 103594, "Marc Eliot")

    assert ergebnis.eindeutig
    assert ergebnis.tvdb_id == 334698
    # ⚠️ Wer eindeutig ist, wird nicht vorgelegt - sonst gaebe es ein Fenster,
    # obwohl nichts zu entscheiden ist.
    assert ergebnis.kandidaten == ()


@pytest.mark.anyio
async def test_null_als_tmdb_kennung_gilt_nicht_als_treffer() -> None:
    """Sonarr schickt 0 statt null, wenn es die Nummer nicht kennt.

    Wer eine 0 fuer echt haelt, ordnet jede kennungslose Serie der Anfrage mit
    ``tmdb_id=0`` zu - und die gibt es nicht, aber der Fehler waere still.
    """
    client = _FakeSonarr({"Irgendwas": [_serie(1, "Irgendwas", 2020, tmdb_id=0)]})

    ergebnis = await zuordnen(client, 0, "Irgendwas")

    assert not ergebnis.eindeutig


@pytest.mark.anyio
async def test_zwei_treffer_mit_derselben_kennung_entscheidet_niemand() -> None:
    """Dann weiss Sonarr selbst nicht, welche gemeint ist."""
    client = _FakeSonarr(
        {"Doppelt": [
            _serie(1, "Doppelt", 2020, tmdb_id=42),
            _serie(2, "Doppelt", 2021, tmdb_id=42),
        ]}
    )

    ergebnis = await zuordnen(client, 42, "Doppelt")

    assert not ergebnis.eindeutig
    assert ergebnis.tvdb_id is None


# --- Der gefaehrliche Fall: aehnlich, aber falsch ---------------------------


@pytest.mark.anyio
async def test_treffer_werden_vorgelegt_statt_genommen() -> None:
    """Der gemessene Fall "Still Water".

    ⚠️ Der Kern von Issue #5: "Still Waters" ist einen Buchstaben entfernt und
    eine voellig andere Serie. Sie darf erscheinen - aber niemals ausgewaehlt
    sein. Entschieden wird ausschliesslich am Fenster, von einem Menschen.
    """
    client = _FakeSonarr(
        {"Still Water": [
            _serie(1001, "Still Waters", None, tmdb_id=0),
            _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
            _serie(1003, "Warszawianka", 2023, tmdb_id=218035),
        ]}
    )

    ergebnis = await zuordnen(client, 331370, "Still Water")

    assert not ergebnis.eindeutig
    assert ergebnis.tvdb_id is None
    # Sonarrs Reihenfolge, unveraendert - wie bei Seerr.
    assert [k.title for k in ergebnis.kandidaten] == [
        "Still Waters", "Stille Waters", "Warszawianka",
    ]


@pytest.mark.anyio
async def test_gar_kein_treffer_heisst_kein_fenster() -> None:
    """Der gemessene Fall der thailaendischen Serie: Sonarr findet nichts.

    Dann gibt es auch nichts vorzulegen - hier ist die Auskunft die einzige
    moegliche Antwort, nicht eine gewaehlte.
    """
    client = _FakeSonarr({"วารี ๑๐๐ ศพ": []})

    ergebnis = await zuordnen(client, 331370, "วารี ๑๐๐ ศพ")

    assert not ergebnis.eindeutig
    assert ergebnis.kandidaten == ()


# --- Suchbegriffe ------------------------------------------------------------


@pytest.mark.anyio
async def test_originaltitel_wird_mitgesucht() -> None:
    """TheTVDB kennt eine thailaendische Serie oft nur unter einem Namen."""
    client = _FakeSonarr(
        {"วารี ๑๐๐ ศพ": [_serie(3001, "Still Water", 2026, tmdb_id=331370)]}
    )

    ergebnis = await zuordnen(client, 331370, "Still Water", "วารี ๑๐๐ ศพ")

    assert ergebnis.tvdb_id == 3001
    assert client.gefragt == ["Still Water", "วารี ๑๐๐ ศพ"]


@pytest.mark.anyio
async def test_gleicher_titel_wird_nicht_zweimal_gesucht() -> None:
    client = _FakeSonarr({"Gleich": []})

    await zuordnen(client, 1, "Gleich", "Gleich")

    assert client.gefragt == ["Gleich"]


@pytest.mark.anyio
async def test_derselbe_treffer_aus_zwei_suchen_zaehlt_einmal() -> None:
    treffer = _serie(4001, "Still Waters", 1995, tmdb_id=0)
    client = _FakeSonarr({"Still Water": [treffer], "Still Wasser": [treffer]})

    ergebnis = await zuordnen(client, 331370, "Still Water", "Still Wasser")

    assert len(ergebnis.kandidaten) == 1


@pytest.mark.anyio
async def test_treffer_ohne_tvdb_kennung_taugt_nicht() -> None:
    """Ohne TVDB-Kennung kann Sonarr die Serie nicht anlegen - sie waere ein
    Vorschlag, den anzuklicken nichts bewirkt."""
    client = _FakeSonarr({"Ohne": [_serie(0, "Ohne Kennung", 2020, tmdb_id=77)]})

    ergebnis = await zuordnen(client, 77, "Ohne")

    assert not ergebnis.eindeutig
    assert ergebnis.kandidaten == ()


@pytest.mark.anyio
async def test_hoechstens_sechs_vorschlaege() -> None:
    client = _FakeSonarr(
        {"Serie": [_serie(5000 + n, f"Serie {n}", 2020, tmdb_id=n) for n in range(20)]}
    )

    ergebnis = await zuordnen(client, 99999, "Serie")

    assert len(ergebnis.kandidaten) == serien_zuordnung.HOECHSTENS


# --- Die Auswahl darf nicht frei sein ----------------------------------------


@pytest.mark.anyio
async def test_nur_vorgelegte_kennungen_sind_erlaubt() -> None:
    """⚠️ Ohne diese Pruefung koennte die Oberflaeche jede beliebige Serie
    anlegen lassen - an TMDB und damit an der Altersbeschraenkung vorbei."""
    client = _FakeSonarr(
        {"Still Water": [
            _serie(1001, "Still Waters", None, tmdb_id=0),
            _serie(1002, "Stille Waters", 2001, tmdb_id=42728),
        ]}
    )
    ergebnis = await zuordnen(client, 331370, "Still Water")

    assert erlaubt(ergebnis, 1001) is True
    assert erlaubt(ergebnis, 1002) is True
    # Eine Kennung, die nie vorgelegt wurde.
    assert erlaubt(ergebnis, 8888) is False


@pytest.mark.anyio
async def test_ohne_vorlage_ist_nichts_erlaubt() -> None:
    leer = serien_zuordnung.Zuordnung()

    assert erlaubt(leer, 334698) is False


# --- Was die Oberflaeche zum Anzeigen braucht --------------------------------


@pytest.mark.anyio
async def test_kandidat_traegt_poster_jahr_und_handlung() -> None:
    """Ohne diese drei ist die Gegenueberstellung wertlos - genau daran
    erkennt jemand, dass "Still Waters" nicht seine Serie ist."""
    client = _FakeSonarr({"Still Water": [_serie(1001, "Still Waters", 1995, tmdb_id=0)]})

    kandidat = (await zuordnen(client, 331370, "Still Water")).kandidaten[0]

    assert kandidat.title == "Still Waters"
    assert kandidat.year == 1995
    assert kandidat.overview
    assert kandidat.poster_url == "https://bild/1001.jpg"
    assert kandidat.tmdb_id is None  # die 0 kommt nicht als Zahl heraus


@pytest.mark.anyio
async def test_jahr_null_kommt_als_unbekannt_heraus() -> None:
    """Gemessen: "Still Waters" traegt bei Sonarr das Jahr 0. Als Jahreszahl
    angezeigt waere das schlimmer als eine Leerstelle."""
    client = _FakeSonarr({"Still Water": [_serie(1001, "Still Waters", 0, tmdb_id=0)]})

    kandidat = (await zuordnen(client, 331370, "Still Water")).kandidaten[0]

    assert kandidat.year is None


# --- Warum hier NICHT nach Staffeln gefiltert wird --------------------------


@pytest.mark.anyio
async def test_serie_ohne_staffeln_bleibt_in_der_liste() -> None:
    """⚠️ Ein Umweg, den es wert war, festgehalten zu werden.

    Kurz stand hier ein Filter: Vorschläge ohne die gewünschte Staffel fielen
    weg. Der Anlass war echt - "Still Waters" meldete null Staffeln und ließ
    sich danach nicht bedienen. Die Zahl war aber nur **vorübergehend** null:
    Sonarr lädt die Metadaten asynchron, und Minuten später meldete dieselbe
    Serie die Staffeln 0 und 1.

    Ein Filter darauf hätte richtige Serien verschwinden lassen, je nachdem
    wie warm Sonarrs Zwischenspeicher gerade ist - und niemand hätte den
    Grund gesehen.
    """
    client = _FakeSonarr(
        {"Still Water": [_serie(479935, "Still Waters", 0, tmdb_id=0, staffeln=())]}
    )

    ergebnis = await zuordnen(client, 331370, "Still Water")

    assert [k.title for k in ergebnis.kandidaten] == ["Still Waters"]
    assert ergebnis.kandidaten[0].seasons == ()
