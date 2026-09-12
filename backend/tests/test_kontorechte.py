"""Welche Rechte ein Konto nach der Einrichtung des Hauses haben kann.

``services/kontorechte.py`` ist die eine Stelle, die der Einladungsassistent und
das Einloesen einer Einladung fragen. Geprueft wird die reine Funktion gegen
echte, gespeicherte Einstellungen und keine nachgebaute Einrichtung, damit das
Erben der 4K-Regeln von der Standard-Instanz mitgeprueft wird.

Was hier **nicht** steht: ob die Pruefungen beim Anfragen weiter greifen. Das
ist das zweite Netz und hat eigene Tests (``test_kontingente_matrix.py``,
``test_freigabe_ziel.py``).
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from app.db import SessionLocal
from app.models import Role, User
from app.services import kontorechte as k
from app.services.kontorechte import Stand
from app.services.settings_service import AppSettings, load_settings, save_settings

RADARR_4K = {"radarr_uhd_url": "http://127.0.0.1:7178", "radarr_uhd_api_key": "schluessel-r4"}
SONARR_4K = {"sonarr_uhd_url": "http://127.0.0.1:8189", "sonarr_uhd_api_key": "schluessel-s4"}


def _einstellungen(werte: dict[str, object] | None) -> AppSettings:
    with SessionLocal() as db:
        if werte:
            save_settings(db, werte)
        return load_settings(db)


def _bewerten(
    werte: dict[str, object] | None = None,
    *,
    veroeffentlicht: bool = True,
    rolle: Role = Role.user,
    **schalter: bool,
) -> tuple[k.Bewertung, k.Wunsch]:
    wunsch = k.Wunsch(rolle=rolle, **schalter)
    bewertung = k.bewerten(_einstellungen(werte), wunsch, hausordnung_veroeffentlicht=veroeffentlicht)
    return bewertung, wunsch


# ---------------------------------------------------------------------------
# 1. Rollen
# ---------------------------------------------------------------------------


def test_ohne_besondere_einrichtung_wirkt_jeder_haken_wie_gesetzt() -> None:
    """Der Normalfall: nichts gesperrt, jeder Haken wirkt genau so, wie er steht."""
    b, _ = _bewerten(
        {**RADARR_4K, **SONARR_4K},
        auto_approve_movies=True,
        can_request_uhd_series=True,
        auto_approve_uhd=True,
        hausordnung=True,
    )

    assert b.kontingent == Stand(frei=True, wirkt=True)
    assert b.auto_approve_movies == Stand(frei=True, wirkt=True)
    assert b.auto_approve_series == Stand(frei=True, wirkt=False)
    assert b.can_request_uhd_movies == Stand(frei=True, wirkt=False)
    assert b.can_request_uhd_series == Stand(frei=True, wirkt=True)
    assert b.auto_approve_uhd == Stand(frei=True, wirkt=True)
    assert b.hausordnung == Stand(frei=True, wirkt=True)


def test_administratoren_bekommen_freigabe_und_4k_aus_der_rolle_und_kein_kontingent() -> None:
    """Die Haken sind gesperrt und wirken trotzdem: Das sagt die Rolle, nicht der Haken."""
    b, _ = _bewerten(RADARR_4K, rolle=Role.admin)
    aus_rolle = Stand(frei=False, wirkt=True, grund=k.ROLLE_ADMIN)

    assert b.kontingent == Stand(frei=False, wirkt=False, grund=k.KEIN_KONTINGENT)
    assert b.auto_approve_movies == aus_rolle
    assert b.auto_approve_series == aus_rolle
    assert b.can_request_uhd_movies == aus_rolle
    assert b.auto_approve_uhd == aus_rolle


def test_ohne_4k_instanz_hilft_auch_die_rolle_nicht() -> None:
    """⚠️ Die Instanz wird vor der Rolle geprueft.

    Beim Anfragen scheitert eine 4K-Anfrage ohne Instanz auch fuer
    Administratoren (``arr_configured``). Stuende hier "aus der Rolle", zeigte
    der Assistent ein Recht an, das es nicht gibt.
    """
    b, _ = _bewerten(RADARR_4K, rolle=Role.admin)

    assert b.can_request_uhd_series == Stand(frei=False, wirkt=False, grund=k.KEINE_4K_INSTANZ_SERIEN)


def test_entscheider_haben_ein_kontingent_aber_die_freigabe_aus_der_rolle() -> None:
    """Anders als Administratoren sind Entscheider begrenzt (``quota._limit_for``).

    Und "der Entscheider waehlt das Ziel" sperrt bei ihnen nichts: Sie waehlen
    ja selbst (``ziel_erst_bei_freigabe`` nimmt sie aus).
    """
    b, _ = _bewerten({"series_root_folder_mode": "approver"}, rolle=Role.approver)

    assert b.kontingent == Stand(frei=True, wirkt=True)
    assert b.auto_approve_series == Stand(frei=False, wirkt=True, grund=k.ROLLE_ENTSCHEIDER)


# ---------------------------------------------------------------------------
# 2. Der Entscheider waehlt Ordner oder Profil
# ---------------------------------------------------------------------------


def test_waehlt_der_entscheider_den_ordner_wirkt_die_sofortfreigabe_nicht() -> None:
    b, _ = _bewerten(
        {"movie_root_folder_mode": "approver"},
        auto_approve_movies=True,
        auto_approve_series=True,
    )

    assert b.auto_approve_movies == Stand(frei=False, wirkt=False, grund=k.ENTSCHEIDER_WAEHLT)
    assert b.auto_approve_series == Stand(frei=True, wirkt=True)


def test_das_profil_beim_entscheider_sperrt_genauso_wie_der_ordner() -> None:
    """``approver_picks_target`` heisst Ordner **oder** Profil."""
    b, _ = _bewerten({"series_profile_mode": "approver"}, auto_approve_series=True)

    assert b.auto_approve_series == Stand(frei=False, wirkt=False, grund=k.ENTSCHEIDER_WAEHLT)


def test_ein_fester_ordner_und_ein_festes_profil_sperren_nichts() -> None:
    """Nur "approver" laesst eine Anfrage warten; "fixed" legt sie sofort an."""
    b, _ = _bewerten(
        {"movie_root_folder_mode": "fixed", "movie_profile_mode": "fixed"},
        auto_approve_movies=True,
    )

    assert b.auto_approve_movies == Stand(frei=True, wirkt=True)


# ---------------------------------------------------------------------------
# 3. 4K
# ---------------------------------------------------------------------------


def test_ohne_4k_instanz_ist_die_ganze_4k_zeile_gesperrt() -> None:
    b, _ = _bewerten(can_request_uhd_movies=True, can_request_uhd_series=True, auto_approve_uhd=True)

    assert b.can_request_uhd_movies == Stand(frei=False, wirkt=False, grund=k.KEINE_4K_INSTANZ_FILME)
    assert b.can_request_uhd_series == Stand(frei=False, wirkt=False, grund=k.KEINE_4K_INSTANZ_SERIEN)
    assert b.auto_approve_uhd == Stand(frei=False, wirkt=False, grund=k.KEINE_4K_INSTANZ)


def test_4k_sofort_freigeben_wird_erst_frei_wenn_4k_erlaubt_ist() -> None:
    ohne, _ = _bewerten(RADARR_4K, auto_approve_uhd=True)
    mit, _ = _bewerten(RADARR_4K, can_request_uhd_movies=True, auto_approve_uhd=True)

    assert ohne.auto_approve_uhd == Stand(frei=False, wirkt=False, grund=k.UHD_ERST_ERLAUBEN)
    assert mit.auto_approve_uhd == Stand(frei=True, wirkt=True)


def test_4k_sofort_bleibt_gesperrt_wo_der_entscheider_das_4k_ziel_waehlt() -> None:
    """Die Regel gilt je Instanz: Standard darf durchlaufen, waehrend 4K wartet."""
    b, _ = _bewerten(
        {**RADARR_4K, "movie_uhd_root_folder_mode": "approver"},
        auto_approve_movies=True,
        can_request_uhd_movies=True,
        auto_approve_uhd=True,
    )

    assert b.auto_approve_movies == Stand(frei=True, wirkt=True)
    assert b.can_request_uhd_movies == Stand(frei=True, wirkt=True)
    assert b.auto_approve_uhd == Stand(frei=False, wirkt=False, grund=k.ENTSCHEIDER_WAEHLT_4K)


def test_die_4k_regel_erbt_vom_standard_solange_sie_nicht_eigens_gesetzt_ist() -> None:
    """⚠️ Wer nur den Standard auf "Entscheider" stellt, hat damit auch 4K dort.

    ``load_settings`` gibt den 4K-Instanzen die Regel der Standard-Instanz,
    solange fuer 4K nichts Eigenes gespeichert ist. Eine nachgebaute
    Einrichtung haette das nicht gewusst.
    """
    b, _ = _bewerten(
        {**SONARR_4K, "series_root_folder_mode": "approver"},
        can_request_uhd_series=True,
        auto_approve_uhd=True,
    )

    assert b.can_request_uhd_series == Stand(frei=True, wirkt=True)
    assert b.auto_approve_uhd == Stand(frei=False, wirkt=False, grund=k.ENTSCHEIDER_WAEHLT_4K)


def test_fuer_4k_sofort_reicht_eine_medienart_ohne_entscheider() -> None:
    """Filme warten in 4K beim Entscheider, Serien nicht: Fuer Serien wirkt der Haken."""
    b, _ = _bewerten(
        {**RADARR_4K, **SONARR_4K, "movie_uhd_root_folder_mode": "approver"},
        can_request_uhd_movies=True,
        can_request_uhd_series=True,
        auto_approve_uhd=True,
    )

    assert b.auto_approve_uhd == Stand(frei=True, wirkt=True)


def test_4k_sofort_verlangt_erst_das_recht_wo_das_helfen_wuerde() -> None:
    """Filme warten in 4K beim Entscheider, Serien waeren frei, sind aber nicht erlaubt.

    Dann heisst der Grund "erst 4K erlauben", denn das Haekchen fuer Serien
    wuerde helfen. "Der Entscheider waehlt" stimmte nur fuer Filme und schickte
    den Administrator an die falsche Stelle.
    """
    b, _ = _bewerten(
        {**RADARR_4K, **SONARR_4K, "movie_uhd_root_folder_mode": "approver"},
        can_request_uhd_movies=True,
        auto_approve_uhd=True,
    )

    assert b.auto_approve_uhd == Stand(frei=False, wirkt=False, grund=k.UHD_ERST_ERLAUBEN)


# ---------------------------------------------------------------------------
# 4. Hausordnung
# ---------------------------------------------------------------------------


def test_ohne_veroeffentlichte_hausordnung_gibt_es_den_schritt_nicht() -> None:
    ohne, _ = _bewerten(veroeffentlicht=False, hausordnung=True)
    mit, _ = _bewerten(veroeffentlicht=True, hausordnung=False)

    assert ohne.hausordnung == Stand(frei=False, wirkt=False, grund=k.KEINE_HAUSORDNUNG)
    assert mit.hausordnung == Stand(frei=True, wirkt=False)


def test_administratoren_werden_nach_der_hausordnung_nicht_gefragt() -> None:
    """Sie schreiben die Regeln (``UNBETEILIGT``); Entscheider werden sehr wohl gefragt."""
    admin, _ = _bewerten(rolle=Role.admin, hausordnung=True)
    entscheider, _ = _bewerten(rolle=Role.approver, hausordnung=True)

    assert admin.hausordnung == Stand(frei=False, wirkt=False, grund=k.ADMIN_NICHT_GEFRAGT)
    assert entscheider.hausordnung == Stand(frei=True, wirkt=True)


# ---------------------------------------------------------------------------
# 5. Was beim Einloesen daraus wird
# ---------------------------------------------------------------------------


def test_entfallen_nennt_nur_gewuenschtes_das_nicht_wirkt() -> None:
    """Was aus der Rolle folgt, entfaellt nicht: Es wirkt ja, nur ohne Haken."""
    b, w = _bewerten(
        {"series_root_folder_mode": "approver"},
        veroeffentlicht=False,
        auto_approve_movies=True,
        auto_approve_series=True,
        can_request_uhd_movies=True,
        hausordnung=True,
    )
    admin, w_admin = _bewerten(
        RADARR_4K, rolle=Role.admin, auto_approve_movies=True, can_request_uhd_movies=True
    )

    assert b.entfallen(w) == ["auto_approve_series", "can_request_uhd_movies", "hausordnung"]
    assert admin.entfallen(w_admin) == []


def test_am_konto_landet_nur_was_frei_ist_und_gewuenscht_wurde() -> None:
    b, w = _bewerten(
        {**RADARR_4K, "series_root_folder_mode": "approver"},
        auto_approve_movies=True,
        auto_approve_series=True,
        can_request_uhd_movies=True,
        auto_approve_uhd=True,
    )

    assert b.werte_fuers_konto(w) == {
        "auto_approve_movies": True,
        "auto_approve_series": False,
        "can_request_uhd_movies": True,
        "can_request_uhd_series": False,
        "auto_approve_uhd": True,
    }


def test_was_aus_der_rolle_folgt_wird_nicht_als_haken_gespeichert() -> None:
    """⚠️ Sonst kaeme der Haken nach einem spaeteren Herabstufen zum Vorschein."""
    b, w = _bewerten(
        RADARR_4K,
        rolle=Role.admin,
        auto_approve_movies=True,
        can_request_uhd_movies=True,
        auto_approve_uhd=True,
    )

    assert set(b.werte_fuers_konto(w).values()) == {False}


def test_jeder_schalter_ist_eine_spalte_am_konto_ein_wunsch_und_eine_bewertung() -> None:
    """Ein Tippfehler in ``SCHALTER`` wuerde am Konto still nichts setzen."""
    wunsch_felder = {f.name for f in fields(k.Wunsch)}
    bewertung_felder = {f.name for f in fields(k.Bewertung)}

    # Bodenschwelle: Eine leere Liste liesse die Schleife unten gruen durchlaufen.
    assert len(k.SCHALTER) == 5
    for name in k.SCHALTER:
        assert hasattr(User, name), name
        assert name in wunsch_felder, name
        assert name in bewertung_felder, name
    assert bewertung_felder == {*k.SCHALTER, "kontingent", "hausordnung"}


def test_jeder_grund_hat_einen_text_in_beiden_sprachen() -> None:
    """⚠️ Der Grund ist eine Kennung; den Satz dazu hat nur die Oberflaeche.

    Fehlt der Text, zeigt ein gesperrter Haken den rohen Schluessel. Das
    Frontend prueft zusammengesetzte Schluessel wie ``rechte.grund.${grund}``
    nicht, deshalb steht der Abgleich hier. Beide Richtungen: Ein Text ohne
    Kennung waere eine Leiche, die beim naechsten Umbenennen stehen bleibt.
    """
    kennungen = {
        wert for name, wert in vars(k).items() if name.isupper() and isinstance(wert, str)
    }
    # Bodenschwelle: Eine leere Menge liesse den Vergleich unten gruen durchlaufen.
    assert len(kennungen) >= 11, kennungen

    sprachen = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
    for sprache in ("de", "en"):
        texte = json.loads((sprachen / f"{sprache}.json").read_text(encoding="utf-8"))
        assert set(texte["rechte"]["grund"]) == kennungen, sprache
