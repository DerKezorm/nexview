"""„Wo streambar" - die Streaming-Anbieter auf der Detailseite.

Die Daten kommen von TMDB (Quelle: JustWatch). Geprueft wird die Auswahl der
Region und die Gruppierung - nicht TMDB selbst.
"""

from __future__ import annotations

from app.services.media import _watch_providers


def _raw(results: dict) -> dict:
    return {"watch/providers": {"results": results}}


def test_waehlt_die_region_des_nutzers() -> None:
    raw = _raw(
        {
            "DE": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]},
            "US": {"flatrate": [{"provider_id": 9, "provider_name": "Hulu"}]},
        }
    )
    w = _watch_providers(raw, "DE")
    assert w is not None
    assert [p.name for p in w.flatrate] == ["Netflix"]
    assert w.region == "DE"


def test_region_gross_und_klein_egal() -> None:
    raw = _raw({"DE": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]}})
    assert _watch_providers(raw, "de") is not None


def test_gruppen_getrennt_und_frei_faellt_mit_werbung_zusammen() -> None:
    raw = _raw(
        {
            "DE": {
                "flatrate": [{"provider_id": 8, "provider_name": "Netflix"}],
                "rent": [{"provider_id": 3, "provider_name": "Google Play"}],
                "buy": [{"provider_id": 3, "provider_name": "Google Play"}],
                "free": [{"provider_id": 7, "provider_name": "Freevee"}],
                "ads": [{"provider_id": 7, "provider_name": "Freevee"}],
            }
        }
    )
    w = _watch_providers(raw, "DE")
    assert [p.name for p in w.flatrate] == ["Netflix"]
    assert [p.name for p in w.rent] == ["Google Play"]
    assert [p.name for p in w.buy] == ["Google Play"]
    # free + ads mit demselben Anbieter -> nur einmal.
    assert [p.name for p in w.free] == ["Freevee"]


def test_fehlende_region_gibt_nichts() -> None:
    raw = _raw({"DE": {"flatrate": [{"provider_id": 8, "provider_name": "Netflix"}]}})
    assert _watch_providers(raw, "FR") is None


def test_region_ohne_anbieter_gibt_nichts() -> None:
    # TMDB liefert manchmal einen Eintrag mit nur einem "link" und leeren Listen.
    raw = _raw({"DE": {"link": "https://example/x"}})
    assert _watch_providers(raw, "DE") is None


def test_ganz_ohne_daten_gibt_nichts() -> None:
    assert _watch_providers({}, "DE") is None
