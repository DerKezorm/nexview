"""Erkennt ein Qualitätsprofil 2160p, auch versteckt in einer Radarr/Sonarr-Gruppe?

⚠️ **Warum eigens geprüft.** ``_erlaubt_2160p``/``uhd_profil_kennungen`` entscheiden,
ob ein Konto ohne 4K-Recht ein Profil wählen darf, das innerhalb der
Standard-Instanz trotzdem 2160p lädt. Bis dahin lief das nur mittelbar über
``requests_service``, mit einer selbst erfundenen Attrappe, die Radarrs
tatsächliche Antwortform nicht nachbildete - insbesondere die Bündelung
mehrerer Qualitäten zu einer Gruppe (``WEB 2160p``) und den Unterschied
zwischen einer *vorhandenen* und einer *erlaubten* 2160p-Stufe. Die Formen
hier sind an echten Radarr- und Sonarr-Antworten abgeglichen.
"""

from __future__ import annotations

import pytest

from app.services.beschaffung.arr import library

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _leerer_zwischenspeicher():
    library.invalidate()
    yield
    library.invalidate()


# --- Echte Radarr-Profile ---------------------------------------------------

#: Radarrs eigenes "Ultra-HD"-Profil (Radarr 5, gemessen 26.09.2026): einzelne
#: Stufen bis 1080p, ab da zwei gebündelte Gruppen ("WEB 1080p", "WEB 2160p")
#: und zwei einzelne Bluray/Remux-Stufen in 2160p.
RADARR_ULTRA_HD = {
    "id": 5,
    "name": "Ultra-HD",
    "upgradeAllowed": True,
    "cutoff": 1002,
    "items": [
        {"quality": {"id": 0, "name": "Unknown", "source": "unknown", "resolution": 0}, "items": [], "allowed": False},
        {"quality": {"id": 2, "name": "SDTV", "source": "television", "resolution": 480}, "items": [], "allowed": False},
        {"quality": {"id": 1, "name": "WORKPRINT", "source": "workprint", "resolution": 0}, "items": [], "allowed": False},
        {"quality": {"id": 8, "name": "WEBDL-480p", "source": "web", "resolution": 480}, "items": [], "allowed": False},
        {
            "id": 1000,
            "name": "WEB 720p",
            "allowed": True,
            "items": [
                {"quality": {"id": 5, "name": "WEBDL-720p", "source": "web", "resolution": 720}, "items": [], "allowed": True},
                {"quality": {"id": 6, "name": "WEBRip-720p", "source": "webRip", "resolution": 720}, "items": [], "allowed": True},
            ],
        },
        {"quality": {"id": 4, "name": "HDTV-720p", "source": "television", "resolution": 720}, "items": [], "allowed": True},
        {"quality": {"id": 9, "name": "Bluray-720p", "source": "bluray", "resolution": 720}, "items": [], "allowed": True},
        {
            "id": 1001,
            "name": "WEB 1080p",
            "allowed": True,
            "items": [
                {"quality": {"id": 3, "name": "WEBDL-1080p", "source": "web", "resolution": 1080}, "items": [], "allowed": True},
                {"quality": {"id": 12, "name": "WEBRip-1080p", "source": "webRip", "resolution": 1080}, "items": [], "allowed": True},
            ],
        },
        {"quality": {"id": 16, "name": "HDTV-1080p", "source": "television", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 7, "name": "Bluray-1080p", "source": "bluray", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 30, "name": "Remux-1080p", "source": "bluray", "resolution": 1080}, "items": [], "allowed": True},
        {
            "id": 1002,
            "name": "WEB 2160p",
            "allowed": True,
            "items": [
                {"quality": {"id": 17, "name": "WEBDL-2160p", "source": "web", "resolution": 2160}, "items": [], "allowed": True},
                {"quality": {"id": 18, "name": "WEBRip-2160p", "source": "webRip", "resolution": 2160}, "items": [], "allowed": True},
            ],
        },
        {"quality": {"id": 20, "name": "HDTV-2160p", "source": "television", "resolution": 2160}, "items": [], "allowed": True},
        {"quality": {"id": 19, "name": "Bluray-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": True},
        {"quality": {"id": 31, "name": "Remux-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": True},
    ],
    "minFormatScore": 0,
    "cutoffFormatScore": 0,
    "formatItems": [],
}

#: Ein gewöhnliches Profil bis 1080p - dieselben 2160p-Stufen stehen zwar in
#: der Liste (Radarr listet immer alle bekannten Qualitäten), sind hier aber
#: gesperrt (``allowed: False``). Genau der Fall, an dem eine Prüfung, die nur
#: nach dem *Vorhandensein* einer 2160p-Zeile sucht, danebenläge.
RADARR_HD_1080P = {
    "id": 4,
    "name": "HD-1080p",
    "upgradeAllowed": True,
    "cutoff": 7,
    "items": [
        {"quality": {"id": 0, "name": "Unknown", "source": "unknown", "resolution": 0}, "items": [], "allowed": False},
        {"quality": {"id": 2, "name": "SDTV", "source": "television", "resolution": 480}, "items": [], "allowed": False},
        {"quality": {"id": 4, "name": "HDTV-720p", "source": "television", "resolution": 720}, "items": [], "allowed": True},
        {"quality": {"id": 16, "name": "HDTV-1080p", "source": "television", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 7, "name": "Bluray-1080p", "source": "bluray", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 20, "name": "HDTV-2160p", "source": "television", "resolution": 2160}, "items": [], "allowed": False},
        {"quality": {"id": 19, "name": "Bluray-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": False},
        {"quality": {"id": 31, "name": "Remux-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": False},
    ],
    "minFormatScore": 0,
    "cutoffFormatScore": 0,
    "formatItems": [],
}

#: ⚠️ Genau der Fall aus dem Befund: Ein Administrator kann ein Profil
#: "4K" nennen, ohne dass es tatsächlich 2160p enthält - etwa weil er es aus
#: Versehen von einem HD-Profil kopiert hat. Der Name darf nichts entscheiden,
#: nur die Qualitätsstufen selbst.
RADARR_NAME_4K_OHNE_UHD = {
    "id": 6,
    "name": "4K",
    "upgradeAllowed": True,
    "cutoff": 7,
    "items": [
        {"quality": {"id": 4, "name": "HDTV-720p", "source": "television", "resolution": 720}, "items": [], "allowed": True},
        {"quality": {"id": 7, "name": "Bluray-1080p", "source": "bluray", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 19, "name": "Bluray-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": False},
    ],
    "minFormatScore": 0,
    "cutoffFormatScore": 0,
    "formatItems": [],
}


# --- Echte Sonarr-Profile ----------------------------------------------------

#: Sonarrs "Ultra-HD" (Sonarr 4, gemessen 26.09.2026) - dieselbe Bauart wie
#: Radarr, mit Sonarrs eigenen Qualitätsnamen (HDTV statt reiner TV-Angabe).
SONARR_ULTRA_HD = {
    "id": 8,
    "name": "Ultra-HD",
    "upgradeAllowed": True,
    "cutoff": 19,
    "items": [
        {"quality": {"id": 0, "name": "Unknown", "source": "unknown", "resolution": 0}, "items": [], "allowed": False},
        {"quality": {"id": 1, "name": "SDTV", "source": "television", "resolution": 480}, "items": [], "allowed": False},
        {"quality": {"id": 8, "name": "WEBDL-480p", "source": "web", "resolution": 480}, "items": [], "allowed": False},
        {"quality": {"id": 4, "name": "HDTV-720p", "source": "television", "resolution": 720}, "items": [], "allowed": True},
        {"quality": {"id": 9, "name": "HDTV-1080p", "source": "television", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 3, "name": "WEBDL-1080p", "source": "web", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 18, "name": "WEBRip-1080p", "source": "webRip", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 7, "name": "Bluray-1080p", "source": "bluray", "resolution": 1080}, "items": [], "allowed": True},
        {
            "id": 1001,
            "name": "WEB 2160p",
            "allowed": True,
            "items": [
                {"quality": {"id": 17, "name": "WEBDL-2160p", "source": "web", "resolution": 2160}, "items": [], "allowed": True},
                {"quality": {"id": 20, "name": "WEBRip-2160p", "source": "webRip", "resolution": 2160}, "items": [], "allowed": True},
            ],
        },
        {"quality": {"id": 16, "name": "HDTV-2160p", "source": "television", "resolution": 2160}, "items": [], "allowed": True},
        {"quality": {"id": 19, "name": "Bluray-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": True},
    ],
    "minFormatScore": 0,
    "cutoffFormatScore": 0,
    "formatItems": [],
}

#: Sonarrs Standard-HD-Profil - dieselbe Falle wie bei Radarr: die 2160p-Zeile
#: steht in der Liste, ist aber gesperrt.
SONARR_HD_1080P = {
    "id": 6,
    "name": "HD-1080p",
    "upgradeAllowed": True,
    "cutoff": 7,
    "items": [
        {"quality": {"id": 4, "name": "HDTV-720p", "source": "television", "resolution": 720}, "items": [], "allowed": True},
        {"quality": {"id": 9, "name": "HDTV-1080p", "source": "television", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 7, "name": "Bluray-1080p", "source": "bluray", "resolution": 1080}, "items": [], "allowed": True},
        {"quality": {"id": 19, "name": "Bluray-2160p", "source": "bluray", "resolution": 2160}, "items": [], "allowed": False},
    ],
    "minFormatScore": 0,
    "cutoffFormatScore": 0,
    "formatItems": [],
}


# --- ``_erlaubt_2160p`` direkt -----------------------------------------------


@pytest.mark.parametrize(
    ("profil", "erwartet"),
    [
        pytest.param(RADARR_ULTRA_HD, True, id="radarr-ultra-hd"),
        pytest.param(RADARR_HD_1080P, False, id="radarr-hd-1080p-mit-gesperrtem-2160p"),
        pytest.param(RADARR_NAME_4K_OHNE_UHD, False, id="radarr-name-4k-ohne-uhd-stufe"),
        pytest.param(SONARR_ULTRA_HD, True, id="sonarr-ultra-hd"),
        pytest.param(SONARR_HD_1080P, False, id="sonarr-hd-1080p-mit-gesperrtem-2160p"),
    ],
)
async def test_erlaubt_2160p_erkennt_die_qualitaetsstufe(profil: dict, erwartet: bool) -> None:
    assert library._erlaubt_2160p(profil["items"]) is erwartet


async def test_erlaubt_2160p_findet_die_stufe_auch_verschachtelt_in_einer_gruppe() -> None:
    """Nur die gebündelte Gruppe enthält 2160p - ohne Rekursion bliebe sie unsichtbar."""
    nur_gruppe = [
        {
            "id": 1002,
            "name": "WEB 2160p",
            "allowed": True,
            "items": [
                {"quality": {"id": 17, "name": "WEBDL-2160p", "source": "web", "resolution": 2160}, "items": [], "allowed": True},
            ],
        },
    ]
    assert library._erlaubt_2160p(nur_gruppe) is True


async def test_erlaubt_2160p_ignoriert_eine_gesperrte_gruppe() -> None:
    """Die Gruppe selbst ist gesperrt - ihre Kinder zählen dann nicht, auch wenn sie es nicht wären."""
    gesperrte_gruppe = [
        {
            "id": 1002,
            "name": "WEB 2160p",
            "allowed": False,
            "items": [
                {"quality": {"id": 17, "name": "WEBDL-2160p", "source": "web", "resolution": 2160}, "items": [], "allowed": True},
            ],
        },
    ]
    assert library._erlaubt_2160p(gesperrte_gruppe) is False


async def test_erlaubt_2160p_mit_leerem_profil() -> None:
    assert library._erlaubt_2160p([]) is False


# --- ``uhd_profil_kennungen`` mit einer nachgebauten Instanz ----------------


class GespielterArrClient:
    """Nur ``quality_profiles()`` - mehr braucht ``uhd_profil_kennungen`` nicht."""

    def __init__(self, profile: list[dict]) -> None:
        self._profile = profile
        self.abgefragt = 0

    async def quality_profiles(self) -> list[dict]:
        self.abgefragt += 1
        return [dict(p) for p in self._profile]


async def test_uhd_profil_kennungen_liest_nur_die_wirklich_erlaubten_radarr_profile(
    monkeypatch,
) -> None:
    client = GespielterArrClient([RADARR_HD_1080P, RADARR_ULTRA_HD, RADARR_NAME_4K_OHNE_UHD])
    monkeypatch.setattr(library, "radarr_client", lambda settings, tier="standard": client)

    kennungen = await library.uhd_profil_kennungen(object(), "movie", "standard")

    assert kennungen == frozenset({RADARR_ULTRA_HD["id"]})


async def test_uhd_profil_kennungen_liest_sonarr_ueber_denselben_weg(monkeypatch) -> None:
    client = GespielterArrClient([SONARR_HD_1080P, SONARR_ULTRA_HD])
    monkeypatch.setattr(library, "sonarr_client", lambda settings, tier="standard": client)

    kennungen = await library.uhd_profil_kennungen(object(), "tv", "standard")

    assert kennungen == frozenset({SONARR_ULTRA_HD["id"]})


async def test_uhd_profil_kennungen_wird_zwischengespeichert(monkeypatch) -> None:
    """Die Auswahl-Seite fragt für jedes Profil einzeln - das darf nicht Radarr treffen."""
    client = GespielterArrClient([RADARR_ULTRA_HD])
    monkeypatch.setattr(library, "radarr_client", lambda settings, tier="standard": client)

    for _ in range(3):
        assert await library.uhd_profil_kennungen(object(), "movie", "standard") == frozenset(
            {RADARR_ULTRA_HD["id"]}
        )

    assert client.abgefragt == 1


async def test_profil_ist_uhd_prueft_eine_einzelne_kennung(monkeypatch) -> None:
    client = GespielterArrClient([RADARR_HD_1080P, RADARR_ULTRA_HD])
    monkeypatch.setattr(library, "radarr_client", lambda settings, tier="standard": client)

    assert await library.profil_ist_uhd(object(), "movie", "standard", RADARR_ULTRA_HD["id"]) is True
    assert await library.profil_ist_uhd(object(), "movie", "standard", RADARR_HD_1080P["id"]) is False
    # Eine unbekannte Kennung ist nicht UHD - eine ungueltige Kennung scheitert
    # ohnehin gleich an Radarr selbst.
    assert await library.profil_ist_uhd(object(), "movie", "standard", 999) is False


async def test_uhd_profil_kennungen_ohne_eingerichtete_instanz(monkeypatch) -> None:
    """Kein Radarr eingerichtet (``radarr_client`` gibt ``None``): keine Kennung ist UHD, kein Fehler."""
    monkeypatch.setattr(library, "radarr_client", lambda settings, tier="standard": None)

    assert await library.uhd_profil_kennungen(object(), "movie", "standard") == frozenset()
