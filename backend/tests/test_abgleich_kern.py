"""Der Abgleich-Kern: reine Fragen, reine Antworten.

Diese Tests schreiben das heutige Verhalten fest, **bevor** die Folgen-Pakete
darauf aufbauen - und sie sind der Vertrag fuer jeden Zulieferer: den
Takt-Laeufer heute, den geplanten Webhook spaeter. Der Kern kennt weder Netz
noch Datenbank, deshalb kommen die Befunde hier als schlichte Attrappen.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.models import MediaRequest, MediaType, RequestStatus, utcnow
from app.services import abgleich_kern
from app.services.sonarr import Staffelstand


def _serie(
    season: int | None = 2, status: RequestStatus = RequestStatus.searching
) -> MediaRequest:
    return MediaRequest(media_type=MediaType.tv, season=season, status=status)


def _eintrag(**felder) -> SimpleNamespace:
    werte: dict = {"has_file": False, "staffeln": {}, "arr_id": 77}
    werte.update(felder)
    return SimpleNamespace(**werte)


# --- ist_fertig ---------------------------------------------------------------


def test_ganze_serie_ist_fertig_sobald_etwas_liegt() -> None:
    assert abgleich_kern.ist_fertig(_serie(season=None), _eintrag(has_file=True))
    assert not abgleich_kern.ist_fertig(_serie(season=None), _eintrag(has_file=False))


def test_staffel_ist_erst_mit_allen_folgen_fertig() -> None:
    voll = _eintrag(staffeln={2: Staffelstand(dateien=8, folgen=8)})
    halb = _eintrag(staffeln={2: Staffelstand(dateien=3, folgen=8)})
    assert abgleich_kern.ist_fertig(_serie(season=2), voll)
    assert not abgleich_kern.ist_fertig(_serie(season=2), halb)


def test_staffel_ohne_bekannte_folgen_ist_nie_fertig() -> None:
    """``folgen == 0`` heisst "Sonarr kennt noch nichts" - nicht "alles da"."""
    leer = _eintrag(staffeln={2: Staffelstand(dateien=0, folgen=0)})
    assert not abgleich_kern.ist_fertig(_serie(season=2), leer)


def test_unbekannte_staffel_ist_nicht_fertig() -> None:
    """Eine geladene Nachbar-Staffel darf nicht als Treffer durchgehen."""
    fremd = _eintrag(has_file=True, staffeln={1: Staffelstand(dateien=8, folgen=8)})
    assert not abgleich_kern.ist_fertig(_serie(season=2), fremd)


# --- ist_noch_da --------------------------------------------------------------


def test_angebrochene_staffel_gilt_noch_als_da() -> None:
    """Bewusst schwaecher als fertig: Eine entfernte Einzelfolge ist kein
    "geloescht" - sonst spraenge der Status hin und her."""
    angebrochen = _eintrag(staffeln={2: Staffelstand(dateien=1, folgen=8)})
    assert abgleich_kern.ist_noch_da(_serie(season=2), angebrochen)
    assert not abgleich_kern.ist_fertig(_serie(season=2), angebrochen)


def test_leere_staffel_ist_weg() -> None:
    leer = _eintrag(staffeln={2: Staffelstand(dateien=0, folgen=8)})
    assert not abgleich_kern.ist_noch_da(_serie(season=2), leer)


def test_ganze_serie_haengt_am_serien_bestand() -> None:
    assert abgleich_kern.ist_noch_da(_serie(season=None), _eintrag(has_file=True))
    assert not abgleich_kern.ist_noch_da(_serie(season=None), _eintrag(has_file=False))


# --- ist_wirklich_weg ---------------------------------------------------------


def _alte_anfrage(minuten: int, status: RequestStatus = RequestStatus.searching) -> MediaRequest:
    anfrage = _serie(status=status)
    anfrage.requested_at = utcnow() - timedelta(minutes=minuten)
    return anfrage


def test_ohne_antwort_der_instanz_ist_nichts_weg() -> None:
    """Ein Ausfall darf nie reihenweise Anfragen abbrechen."""
    assert not abgleich_kern.ist_wirklich_weg(_alte_anfrage(60), instanz_hat_geantwortet=False)


def test_nur_uebergebene_anfragen_koennen_verschwinden() -> None:
    wartend = _alte_anfrage(60, status=RequestStatus.pending_approval)
    assert not abgleich_kern.ist_wirklich_weg(wartend, instanz_hat_geantwortet=True)


def test_die_schonfrist_schuetzt_frische_anfragen() -> None:
    assert not abgleich_kern.ist_wirklich_weg(_alte_anfrage(5), instanz_hat_geantwortet=True)
    assert abgleich_kern.ist_wirklich_weg(_alte_anfrage(20), instanz_hat_geantwortet=True)


def test_frische_freigabe_zaehlt_statt_alter_anfrage() -> None:
    """Die Schonfrist laeuft ab der Uebergabe, nicht ab dem Wunsch."""
    anfrage = _alte_anfrage(60)
    anfrage.approved_at = utcnow() - timedelta(minutes=2)
    assert not abgleich_kern.ist_wirklich_weg(anfrage, instanz_hat_geantwortet=True)


# --- heilung_noetig -----------------------------------------------------------


def test_heilung_nur_fuer_laufende_staffelanfragen() -> None:
    abgeraeumt = _eintrag(staffeln={2: Staffelstand(dateien=0, folgen=8, monitored=False)})

    film = MediaRequest(media_type=MediaType.movie, season=None, status=RequestStatus.searching)
    assert not abgleich_kern.heilung_noetig(film, abgeraeumt)
    assert not abgleich_kern.heilung_noetig(_serie(season=None), abgeraeumt)
    assert not abgleich_kern.heilung_noetig(
        _serie(status=RequestStatus.downloaded), abgeraeumt
    )
    assert abgleich_kern.heilung_noetig(_serie(season=2), abgeraeumt)


def test_heilung_braucht_befund_und_kennung() -> None:
    abgeraeumt = {2: Staffelstand(dateien=0, folgen=8, monitored=False)}
    assert not abgleich_kern.heilung_noetig(_serie(season=2), None)
    assert not abgleich_kern.heilung_noetig(
        _serie(season=2), _eintrag(staffeln=abgeraeumt, arr_id=None)
    )
    assert not abgleich_kern.heilung_noetig(_serie(season=2), _eintrag(staffeln={}))


def test_ueberwachte_staffel_braucht_keine_heilung() -> None:
    gesund = _eintrag(staffeln={2: Staffelstand(dateien=0, folgen=8, monitored=True)})
    assert not abgleich_kern.heilung_noetig(_serie(season=2), gesund)
