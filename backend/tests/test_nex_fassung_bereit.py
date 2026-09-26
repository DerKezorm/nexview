"""Wann eine Fassung "bereit" heisst - und dass jeder ihrer Gruende einen Text hat.

Grosser Pruefgang, 26.09.2026: Eine neue Fassung ohne Profil und Ordner stand
in ``/api/config`` als bereit, die Anfrage darin wurde angenommen und stand
fuer immer auf "wird gesucht"; nexcrate suchte nie. Und der Grund
``fed_by_source`` stand roh in der Oberflaeche, in beiden Sprachen.

⚠️ **Nexviews "bereit" ist nicht nexcrates ``ready``.** Mit ausgeschalteter
Automatik sucht nexcrate einen Wunsch trotzdem, und eine Fassung, die noch
Sonarr fuettert, laedt neue Titel. Beides sperrt keine Anfrage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.db import SessionLocal
from app.models import MediaRequest
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import FILM_HD, FILM_UHD, KEY, SERIE_HD, URL, FakeNexcrate

WURZEL = Path(__file__).resolve().parents[2]
SPRACHEN = WURZEL / "frontend" / "src" / "i18n"
VERTRAG = Path(__file__).parent / "beschaffung" / "nexcrate_openapi.json"


@pytest.fixture
def nexcrate() -> Iterator[FakeNexcrate]:
    attrappe = FakeNexcrate()
    nex_client.use_transport(attrappe.transport())
    system.merken(attrappe._system())
    nex_fassungen.vergessen()
    try:
        yield attrappe
    finally:
        nex_client.use_transport(None)
        system.vergessen()
        nex_fassungen.vergessen()


def _nex(admin_client: Any, nexcrate: FakeNexcrate) -> Any:
    """Koppeln und die Fassungen so schreiben, wie nexcrate sie gerade nennt."""
    with SessionLocal() as sitzung:
        save_settings(
            sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
        )
        nex_fassungen.schreiben(sitzung, nexcrate.versions)
        sitzung.commit()
    antwort = admin_client.put(
        "/api/settings/fassungen",
        json=[{"kennung": k, "offen_fuer_alle": True} for k in (FILM_HD, FILM_UHD, SERIE_HD)],
    )
    assert antwort.status_code == 200, antwort.text
    return admin_client


def _bereit(client: Any) -> dict[str, bool]:
    return {f["kennung"]: f["bereit"] for f in client.get("/api/config").json()["fassungen"]}


@pytest.mark.parametrize(
    ("gruende", "erwartet"),
    [
        (("no_profile", "no_folder", "automatic_off"), False),
        (("no_indexer",), False),
        (("no_download_client",), False),
        # Ein Grund, den Nexview noch nicht kennt: lieber gesperrt als "bereit".
        (("something_new",), False),
        (("automatic_off",), True),
        (("fed_by_source",), True),
        (("automatic_off", "fed_by_source"), True),
    ],
)
def test_bereit_folgt_den_gruenden_die_eine_anfrage_aufhalten(
    admin_client: Any, nexcrate: FakeNexcrate, gruende: tuple[str, ...], erwartet: bool
) -> None:
    nexcrate.nicht_bereit(FILM_UHD, *gruende)
    client = _nex(admin_client, nexcrate)

    stand = _bereit(client)

    assert stand[FILM_UHD] is erwartet
    assert stand[FILM_HD] is True


def test_eine_anfrage_in_eine_fassung_ohne_profil_wird_abgelehnt(
    admin_client: Any, nexcrate: FakeNexcrate
) -> None:
    """Sonst steht sie fuer immer auf "wird gesucht"."""
    nexcrate.nicht_bereit(FILM_UHD, "no_profile", "no_folder", "automatic_off")
    client = _nex(admin_client, nexcrate)
    item = client.get("/api/discover/movie").json()["items"][0]
    nexcrate.film(item["tmdb_id"], versionen=[])

    antwort = client.post(
        "/api/requests",
        json={"media_type": "movie", "tmdb_id": item["tmdb_id"], "fassung": FILM_UHD},
    )

    assert antwort.status_code == 409, antwort.text
    assert antwort.json()["detail"]["code"] == "fassung_not_ready"
    assert not [k for k in nexcrate.calls if k[1].endswith("/requests")]
    with SessionLocal() as sitzung:
        assert sitzung.query(MediaRequest).count() == 0


def test_im_arr_betrieb_heisst_bereit_weiter_eingerichtet(arr_client: Any) -> None:
    """Der ARR-Betrieb fragt wie von jeher nur, ob die Instanz steht."""
    fassungen = arr_client.get("/api/config").json()["fassungen"]
    assert fassungen
    with SessionLocal() as sitzung:
        settings = load_settings(sitzung)
    for fassung in fassungen:
        assert fassung["bereit"] is (settings.fassung(fassung["kennung"]) is not None)


# --- Jeder Grund hat einen Text -----------------------------------------------------


def _gruende_im_vertrag() -> set[str]:
    """Die Gruende, die nexcrate an einer Fassung nennen kann (``VersionReason.code``)."""
    text = VERTRAG.read_text(encoding="utf-8")
    beschreibung = re.search(r'"description": "(no_profile[^"]*)"', text)
    assert beschreibung, "Die Liste der Gruende steht nicht mehr im Vertrag"
    return set(re.findall(r"[a-z_]+", beschreibung.group(1))) - {"or"}


@pytest.mark.parametrize("sprache", ["de", "en"])
def test_jeder_grund_einer_fassung_hat_einen_text(sprache: str) -> None:
    """Sonst steht der rohe Code in der Oberflaeche (``fed_by_source``, 25.09.2026)."""
    gruende = _gruende_im_vertrag()
    assert "fed_by_source" in gruende and len(gruende) >= 6
    texte = json.loads((SPRACHEN / f"{sprache}.json").read_text(encoding="utf-8"))
    vorhanden = set(texte["nexcrate"]["reason"])
    assert sorted(gruende - vorhanden) == []
