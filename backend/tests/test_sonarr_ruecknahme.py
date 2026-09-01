"""Eine eben angelegte Serie wieder abraeumen, wenn der zweite Schritt scheitert.

Anlegen und Einschalten sind zwei Aufrufe an Sonarr. Geht der zweite schief,
war die Anfrage gescheitert - die Serie blieb aber in Sonarr stehen, ohne
Staffel, ohne Datei, und niemand wusste, woher sie kam.

Live aufgefallen am 01.09.2026: "Still Waters" hat bei TheTVDB einen Eintrag
ganz **ohne** Staffeln. Anlegen ging, Staffel 1 einschalten nicht - und danach
lag die Serie in einer echten Sonarr-Instanz.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.arr import ArrError
from app.services.sonarr import SonarrClient


class _Sonarr(SonarrClient):
    """Ein Client, der nicht spricht, sondern mitschreibt."""

    def __init__(self, scheitern: ArrError | None = None, loeschen_scheitert: bool = False):
        super().__init__("http://sonarr.test", "schluessel")
        self.scheitern = scheitern
        self.loeschen_scheitert = loeschen_scheitert
        self.angelegt = False
        self.geloescht: list[int] = []

    async def lookup(self, tvdb_id: int) -> dict[str, Any]:
        return {"title": "Still Waters", "tvdbId": tvdb_id}

    async def ensure_tag(self, name: str) -> int | None:
        return None

    async def post(self, pfad: str, daten: Any = None) -> Any:
        assert pfad == "/series"
        self.angelegt = True
        return {"id": 216, "title": "Still Waters"}

    async def monitor_seasons(self, arr_id, seasons, such_staffel=None) -> None:
        if self.scheitern is not None:
            raise self.scheitern

    async def remove(self, arr_id: int, delete_files: bool = True) -> None:
        if self.loeschen_scheitert:
            raise ArrError("Sonarr antwortet nicht.", 502, code="arr_unreachable")
        self.geloescht.append(arr_id)
        self.mit_dateien = delete_files


async def _anlegen(client: _Sonarr):
    return await client.add(479935, 1, "/data/TV-Shows", season=1)


@pytest.mark.anyio
async def test_ohne_fehler_bleibt_die_serie_stehen() -> None:
    client = _Sonarr()

    ergebnis = await _anlegen(client)

    assert ergebnis["id"] == 216
    assert client.geloescht == []


@pytest.mark.anyio
async def test_gescheitertes_einschalten_raeumt_die_serie_wieder_ab() -> None:
    """⚠️ Der gemessene Fall: ein TVDB-Eintrag ohne Staffeln."""
    fehler = ArrError(
        "Sonarr kennt Staffel 1 dieser Serie nicht.", 404,
        code="sonarr_season_unknown", season=1,
    )
    client = _Sonarr(scheitern=fehler)

    with pytest.raises(ArrError) as fall:
        await _anlegen(client)

    assert client.angelegt is True
    assert client.geloescht == [216], "die eben angelegte Serie blieb liegen"
    # Der ursprüngliche Fehler kommt durch - er erklärt, was schiefging.
    assert fall.value.code == "sonarr_season_unknown"


@pytest.mark.anyio
async def test_beim_abraeumen_werden_keine_dateien_geloescht() -> None:
    """⚠️ Bewusst **ohne** Dateien, anders als beim Stornieren einer Anfrage.

    Die Serie ist Sekunden alt und hat realistisch keine. Der Ordner aber
    kann älter sein: Wer seine Folgen von Hand dorthin gelegt hat und darauf
    wartet, dass Sonarr sie einliest, verlöre sie - wegen einer Anfrage, die
    nie zustande kam. Eine verwaiste Datei ist der kleinere Schaden als eine
    gelöschte Sammlung.
    """
    fehler = ArrError(
        "Sonarr kennt Staffel 1 dieser Serie nicht.", 404,
        code="sonarr_season_unknown", season=1,
    )
    client = _Sonarr(scheitern=fehler)

    with pytest.raises(ArrError):
        await _anlegen(client)

    assert client.geloescht == [216]
    assert client.mit_dateien is False


@pytest.mark.anyio
async def test_bei_ungewissheit_wird_nichts_geloescht() -> None:
    """⚠️ Eine Zeitüberschreitung heißt nicht, dass nichts passiert ist.

    Vielleicht ist die Staffel längst eingeschaltet und lädt. Dann wäre das
    Löschen der Schaden, nicht die Rettung - der Status-Abgleich klärt solche
    Fälle von selbst.
    """
    fehler = ArrError("Sonarr antwortet nicht.", 504, ungewiss=True, code="arr_timeout")
    client = _Sonarr(scheitern=fehler)

    with pytest.raises(ArrError):
        await _anlegen(client)

    assert client.geloescht == []


@pytest.mark.anyio
async def test_fehler_beim_aufraeumen_verdeckt_den_echten_nicht() -> None:
    """Sonst erklärte die Meldung nur noch, dass auch das Aufräumen scheiterte
    - und verlegte damit die Spur zur eigentlichen Ursache."""
    fehler = ArrError(
        "Sonarr kennt Staffel 1 dieser Serie nicht.", 404,
        code="sonarr_season_unknown", season=1,
    )
    client = _Sonarr(scheitern=fehler, loeschen_scheitert=True)

    with pytest.raises(ArrError) as fall:
        await _anlegen(client)

    assert fall.value.code == "sonarr_season_unknown"


@pytest.mark.anyio
async def test_ohne_staffelwunsch_gibt_es_nichts_einzuschalten() -> None:
    """Wer die ganze Serie anfragt, durchläuft den zweiten Schritt nicht -
    und damit auch die Rücknahme nicht."""
    client = _Sonarr(scheitern=ArrError("darf nicht auffallen", 500))

    ergebnis = await client.add(479935, 1, "/data/TV-Shows")

    assert ergebnis["id"] == 216
    assert client.geloescht == []


def test_das_modul_hat_einen_logger() -> None:
    """⚠️ Lange hatte es keinen, und ``serie_ueberwachen`` benutzte ihn trotzdem.

    Die Zeile wäre mit einem ``NameError`` abgestürzt, sobald sie drankommt -
    und sie kommt nur dran, wenn das Einschalten schlafende Folgen weckt. Also
    selten genug, um es niemandem auffallen zu lassen.
    """
    from app.services import sonarr

    assert sonarr.logger.name == "nexview.arr"


# --- "Noch keine Staffeln" heißt nicht "kennt die Staffel nicht" -------------


class _Staffelstand(SonarrClient):
    """Ein Sonarr, dessen Staffelliste sich einstellen lässt."""

    def __init__(self, staffeln: list[int] | None) -> None:
        super().__init__("http://sonarr.test", "schluessel")
        self.staffeln = staffeln
        self.geschrieben = False

    async def get(self, pfad: str, params: Any = None) -> Any:
        return {
            "id": 216,
            "title": "Still Waters",
            "seasons": [{"seasonNumber": n} for n in (self.staffeln or [])],
        }

    async def put(self, pfad: str, daten: Any = None) -> Any:
        self.geschrieben = True
        return daten

    async def post(self, pfad: str, daten: Any = None) -> Any:
        return {}


@pytest.mark.anyio
async def test_noch_keine_staffeln_ist_kein_fehler() -> None:
    """⚠️ Der Fehler, den Markus beim Testen ausgelöst hat.

    Sonarr lädt die Metadaten einer frisch angelegten Serie **asynchron**. Wer
    unmittelbar danach fragt, bekommt eine Serie ganz ohne Staffeln - und das
    ist ein Zeitpunkt, kein Mangel. Live gemessen: dieselbe Serie meldete
    Minuten später die Staffeln 0 und 1.

    Der Status-Abgleich schaltet die Staffel im nächsten Durchgang ein.
    Abzubrechen hieße, die Anfrage wegen einer zu früh geholten Auskunft
    scheitern zu lassen.
    """
    client = _Staffelstand(staffeln=[])

    await client.monitor_seasons(216, {1}, such_staffel=1)

    assert client.geschrieben is False, "es wurde geschrieben, statt abzuwarten"


@pytest.mark.anyio
async def test_bekannte_staffeln_ohne_die_gewuenschte_bleiben_ein_fehler() -> None:
    """Kennt Sonarr die Serie samt Staffeln, aber die gewünschte ist nicht
    dabei, ist das eine echte Auskunft - dann gibt es die Nummer dort nicht."""
    client = _Staffelstand(staffeln=[0, 1, 2])

    with pytest.raises(ArrError) as fall:
        await client.monitor_seasons(216, {5}, such_staffel=5)

    assert fall.value.code == "sonarr_season_unknown"
    assert fall.value.zahlen["season"] == 5
