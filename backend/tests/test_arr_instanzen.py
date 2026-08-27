"""Die Instanzen-Liste: eine Quelle fuer "alle eingerichteten Radarr/Sonarr".

Das ist der Vertrag fuer alles, was "je Instanz" arbeitet - die Papierkoerbe
heute, der Webhook morgen. Festgenagelt wird vor allem, was sich **nicht**
aendern darf:

* Die **Kennungen sind stabil.** An ihnen haengt gespeicherter Zustand
  (spaeter etwa das Anruf-Geheimnis des Webhooks je Instanz). Wer sie
  umbenennt, verliert diesen Zustand - deshalb stehen sie hier woertlich.
* Nicht eingerichtete Instanzen **fehlen ganz**, statt leer mitzulaufen.
  Ein Verbraucher soll nie pruefen muessen, ob ein Listeneintrag "echt" ist.
* Eine **reine 4K-Installation zaehlt als eingerichtet.** Genau daran ist
  der Takt-Laeufer frueher vorbeigelaufen: Sein Start pruefte nur die
  Standard-Plaetze, und eine Installation mit ausschliesslich 4K-Instanzen
  wurde nie abgeglichen.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.services.settings_service import AppSettings, load_settings, save_settings


def _einstellungen(werte: dict[str, object]) -> AppSettings:
    with SessionLocal() as db:
        save_settings(db, werte)
        return load_settings(db)


RADARR = {"radarr_url": "http://127.0.0.1:7878", "radarr_api_key": "schluessel-r"}
SONARR = {"sonarr_url": "http://127.0.0.1:8989", "sonarr_api_key": "schluessel-s"}
RADARR_4K = {"radarr_uhd_url": "http://127.0.0.1:7178", "radarr_uhd_api_key": "schluessel-r4"}
SONARR_4K = {"sonarr_uhd_url": "http://127.0.0.1:8189", "sonarr_uhd_api_key": "schluessel-s4"}


def test_ohne_einrichtung_ist_die_liste_leer() -> None:
    assert _einstellungen({}).arr_instanzen() == ()


def test_standard_instanzen_mit_stabilen_kennungen() -> None:
    instanzen = _einstellungen({**RADARR, **SONARR}).arr_instanzen()

    assert [i.kennung for i in instanzen] == ["radarr-standard", "sonarr-standard"]
    radarr, sonarr = instanzen
    assert (radarr.media_type, radarr.tier, radarr.name) == ("movie", "standard", "Radarr")
    assert (sonarr.media_type, sonarr.tier, sonarr.name) == ("tv", "standard", "Sonarr")
    assert radarr.url == "http://127.0.0.1:7878"
    assert radarr.api_key == "schluessel-r"


def test_alle_vier_in_anzeigereihenfolge() -> None:
    instanzen = _einstellungen(
        {**RADARR, **RADARR_4K, **SONARR, **SONARR_4K}
    ).arr_instanzen()

    assert [i.kennung for i in instanzen] == [
        "radarr-standard",
        "radarr-uhd",
        "sonarr-standard",
        "sonarr-uhd",
    ]
    assert [i.name for i in instanzen] == ["Radarr", "Radarr 4K", "Sonarr", "Sonarr 4K"]


def test_reine_4k_installation_zaehlt_als_eingerichtet() -> None:
    """Der Fall, an dem der Takt-Laeufer frueher vorbeigelaufen ist."""
    instanzen = _einstellungen({**RADARR_4K}).arr_instanzen()

    assert [i.kennung for i in instanzen] == ["radarr-uhd"]
    assert instanzen[0].tier == "uhd"


def test_halb_eingetragen_heisst_nicht_eingerichtet() -> None:
    """Adresse ohne Schluessel ist keine Instanz - dieselbe Schwelle wie
    ``arr_configured``, sonst gaeben beide verschiedene Antworten."""
    instanzen = _einstellungen(
        {**SONARR, "radarr_url": "http://127.0.0.1:7878"}
    ).arr_instanzen()

    assert [i.kennung for i in instanzen] == ["sonarr-standard"]
