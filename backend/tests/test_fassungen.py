"""Das Fassungsmodell: Kennungen, Rechte je Fassung, alte Haken als Sicht.

Bauplan NEX-Modus, Abschnitt 2. Im ARR-Betrieb ist eine Fassung eine der vier
Instanzen; Anfragen, Posten und Rechte tragen ihre Kennung, die Stufe ist eine
Ableitung. Diese Datei prueft die Teile, die neu sind. Dass sich im
ARR-Betrieb nach aussen nichts bewegt, pruefen die bestehenden Tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.db import SessionLocal
from app.models import (
    AuthToken,
    Fassung,
    FassungRecht,
    MediaRequest,
    MediaType,
    Role,
    StorageEntry,
    TokenPurpose,
    User,
    utcnow,
)
from app.services import fassungen, kontorechte, regeln
from app.services.settings_service import load_settings, save_settings


@pytest.fixture
def db() -> Session:
    with SessionLocal() as sitzung:
        yield sitzung


def _konto(db: Session, name: str = "kim", rolle: Role = Role.user, **felder) -> User:
    konto = User(
        username=name, email=f"{name}@example.com", role=rolle, password_hash="x", **felder
    )
    db.add(konto)
    db.commit()
    return konto


def _alle_instanzen(db: Session) -> None:
    save_settings(
        db,
        {
            "radarr_url": "http://radarr.example.com",
            "radarr_api_key": "a" * 32,
            "radarr_uhd_url": "http://radarr4k.example.com",
            "radarr_uhd_api_key": "b" * 32,
            "sonarr_url": "http://sonarr.example.com",
            "sonarr_api_key": "c" * 32,
            "sonarr_uhd_url": "http://sonarr4k.example.com",
            "sonarr_uhd_api_key": "d" * 32,
        },
    )


# --------------------------------------------------------------- Kennungen


def test_kennung_und_stufe_gehen_ineinander_auf() -> None:
    """Jede Art und Stufe hat genau eine Kennung, und die Stufe folgt aus ihr."""
    gesehen = set()
    for art in ("movie", "tv"):
        for stufe in ("standard", "uhd"):
            kennung = fassungen.arr_kennung(art, stufe)
            assert fassungen.stufe(kennung) == stufe
            assert fassungen.arr_fassung(kennung).media_type == art
            gesehen.add(kennung)
    assert gesehen == set(fassungen.ARR_KENNUNGEN)
    assert len(gesehen) == 4


def test_die_kennungen_im_modell_sind_die_der_fassungen() -> None:
    """``models`` fuehrt die beiden 4K-Kennungen selbst - sie muessen passen."""
    assert models.UHD_FILME == fassungen.arr_kennung("movie", "uhd")
    assert models.UHD_SERIEN == fassungen.arr_kennung("tv", "uhd")


def test_eine_unbekannte_kennung_ist_standard_ohne_klasse() -> None:
    assert fassungen.klasse("v_7c1e90ab") is None
    assert fassungen.stufe(None) == "standard"


def test_die_instanzen_tragen_die_kennungen_der_fassungen(db: Session) -> None:
    _alle_instanzen(db)
    instanzen = load_settings(db, frisch=True).arr_instanzen()
    assert [i.kennung for i in instanzen] == list(fassungen.ARR_KENNUNGEN)
    assert [(i.media_type, i.tier) for i in instanzen] == [
        ("movie", "standard"),
        ("movie", "uhd"),
        ("tv", "standard"),
        ("tv", "uhd"),
    ]


def test_fassungen_fuer_nennt_nur_eingerichtete(db: Session) -> None:
    save_settings(db, {"radarr_url": "http://radarr.example.com", "radarr_api_key": "a" * 32})
    settings = load_settings(db, frisch=True)
    assert [f.kennung for f in settings.fassungen_fuer("movie")] == ["radarr-standard"]
    assert settings.fassungen_fuer("tv") == ()
    assert settings.fassung("radarr-uhd") is None
    assert settings.fassung("radarr-standard").klasse == "hd"


# ----------------------------------------------------------------- Abgleich


def test_abgleich_legt_vier_zeilen_an_und_merkt_sich_das_verschwinden(db: Session) -> None:
    _alle_instanzen(db)
    db.commit()
    zeilen = {z.kennung: z for z in db.scalars(select(Fassung))}
    assert set(zeilen) == set(fassungen.ARR_KENNUNGEN)
    assert all(z.aktiv and z.bereit for z in zeilen.values())
    assert {k for k, z in zeilen.items() if z.offen_fuer_alle} == {
        "radarr-standard",
        "sonarr-standard",
    }

    save_settings(db, {"sonarr_uhd_url": ""})
    db.commit()
    weg = db.get(Fassung, "sonarr-uhd")
    db.refresh(weg)
    assert weg.aktiv is False
    assert weg.verschwunden_am is not None
    # Der Name bleibt fuer Anfragen und Posten, die ihn noch zeigen.
    assert weg.name == "Sonarr 4K"


def test_der_abgleich_nimmt_eine_freigabe_nicht_zurueck(db: Session) -> None:
    """``offen_fuer_alle`` setzt nur die erste Zeile, danach gehoert es dem Betreiber."""
    _alle_instanzen(db)
    db.commit()
    db.get(Fassung, "radarr-uhd").offen_fuer_alle = True
    db.commit()

    fassungen.abgleichen(db, load_settings(db, frisch=True))
    db.commit()

    assert db.get(Fassung, "radarr-uhd").offen_fuer_alle is True


# ------------------------------------------------------------------ Leiter


def test_offene_fassung_darf_jeder_geschlossene_nur_mit_recht(db: Session) -> None:
    _alle_instanzen(db)
    db.commit()
    kim = _konto(db)

    assert fassungen.darf_anfragen(db, kim, "radarr-standard")
    assert not fassungen.darf_anfragen(db, kim, "radarr-uhd")

    kim.can_request_uhd_movies = True
    db.commit()
    assert fassungen.darf_anfragen(db, kim, "radarr-uhd")
    assert not fassungen.darf_anfragen(db, kim, "sonarr-uhd")


def test_die_zeile_entscheidet_nicht_die_vorgabe(db: Session) -> None:
    """Wird eine 4K-Fassung offen, darf jeder - ohne Haken am Konto."""
    _alle_instanzen(db)
    db.commit()
    kim = _konto(db)
    db.get(Fassung, "radarr-uhd").offen_fuer_alle = True
    db.commit()

    assert fassungen.darf_anfragen(db, kim, "radarr-uhd")
    assert fassungen.offene_kennungen(db) == {"radarr-standard", "radarr-uhd", "sonarr-standard"}


def test_entscheider_duerfen_jede_fassung(db: Session) -> None:
    chefin = _konto(db, "chefin", Role.approver)
    assert fassungen.darf_anfragen(db, chefin, "sonarr-uhd")
    assert fassungen.auto_freigabe(db, chefin, MediaType.tv, "sonarr-uhd")


def test_auto_freigabe_offen_folgt_dem_konto_geschlossen_dem_recht(db: Session) -> None:
    kim = _konto(db, auto_approve_movies=True)
    assert fassungen.auto_freigabe(db, kim, MediaType.movie, "radarr-standard")
    assert not fassungen.auto_freigabe(db, kim, MediaType.tv, "sonarr-standard")
    assert not fassungen.auto_freigabe(db, kim, MediaType.movie, "radarr-uhd")

    kim.auto_approve_uhd = True
    db.commit()
    assert fassungen.auto_freigabe(db, kim, MediaType.movie, "radarr-uhd")
    assert fassungen.auto_freigabe(db, kim, MediaType.tv, "sonarr-uhd")


# --------------------------------------------------------- Alte Haken als Sicht


def test_die_alten_haken_schreiben_rechte_je_fassung(db: Session) -> None:
    kim = _konto(db, can_request_uhd_series=True, auto_approve_uhd=True)
    rechte = {
        r.fassung_kennung: (r.anfragen, r.auto_freigabe)
        for r in db.scalars(select(FassungRecht).where(FassungRecht.user_id == kim.id))
    }
    assert rechte == {"radarr-uhd": (False, True), "sonarr-uhd": (True, True)}
    assert kim.can_request_uhd_movies is False
    assert kim.can_request_uhd_series is True
    assert kim.auto_approve_uhd is True


def test_ein_leeres_recht_verschwindet(db: Session) -> None:
    """Keine Zeile heisst "kein Recht" - eine Zeile voller Nein bleibt nicht liegen."""
    kim = _konto(db, can_request_uhd_movies=True)
    kim.can_request_uhd_movies = False
    db.commit()
    assert db.scalars(select(FassungRecht)).all() == []


def test_die_einladung_schreibt_eine_neue_liste(db: Session) -> None:
    """⚠️ In-place an einer JSON-Liste geschrieben, kaeme nichts in der Datenbank an."""
    token = AuthToken(
        purpose=TokenPurpose.invitation,
        token_hash="h" * 64,
        email="neu@example.com",
        expires_at=utcnow().replace(tzinfo=None),
    )
    token.invite_can_request_uhd_movies = True
    db.add(token)
    db.commit()

    token.invite_auto_approve_uhd = True
    db.commit()
    db.expire_all()

    frisch = db.get(AuthToken, token.id)
    assert frisch.invite_fassung_rechte == [
        {"kennung": "radarr-uhd", "anfragen": True, "auto_freigabe": True},
        {"kennung": "sonarr-uhd", "anfragen": False, "auto_freigabe": True},
    ]
    assert frisch.invite_can_request_uhd_movies is True
    assert frisch.invite_can_request_uhd_series is False
    assert frisch.invite_auto_approve_uhd is True


# -------------------------------------------------------------- Pflichtfeld


@pytest.mark.parametrize("modell", [MediaRequest, StorageEntry])
def test_ohne_fassung_wird_nichts_geschrieben(db: Session, modell) -> None:
    """Eine stille Vorgabe "standard" waere im NEX-Betrieb falsch - lieber laut."""
    kim = _konto(db)
    felder = {
        MediaRequest: {"user_id": kim.id, "media_type": MediaType.movie, "tmdb_id": 1, "title": "x"},
        StorageEntry: {"key": "movie:x:tmdb:1", "media_type": MediaType.movie},
    }[modell]
    db.add(modell(**felder))
    with pytest.raises(ValueError, match="fassung_kennung"):
        db.flush()


def test_die_stufe_folgt_der_fassung() -> None:
    assert MediaRequest(fassung_kennung="sonarr-uhd").tier == "uhd"
    assert StorageEntry(fassung_kennung="radarr-standard").tier == "standard"


# -------------------------------------------------------------------- Regeln


def test_eine_regel_darf_eine_fassung_nennen() -> None:
    sauber = regeln.bedingungen_pruefen([{"feld": "qualitaet", "werte": ["radarr-uhd"]}])
    assert sauber == [{"feld": "qualitaet", "werte": ["radarr-uhd"]}]
    with pytest.raises(regeln.RegelFehler):
        regeln.bedingungen_pruefen([{"feld": "qualitaet", "werte": ["v_gibtsnicht"]}])
    # Mit den Kennungen aus der Datenbank geht auch eine Fassung von nexcrate.
    regeln.bedingungen_pruefen(
        [{"feld": "bestand", "werte": ["v_7c1e90ab"]}], fassungen=["v_7c1e90ab"]
    )


def test_klasse_trifft_jede_fassung_kennung_nur_ihre() -> None:
    """``uhd`` ist eine Klasse und trifft beide 4K-Fassungen, eine Kennung nur sich."""
    film = regeln.Titel(typ=MediaType.movie, qualitaet="uhd", fassung="radarr-uhd")
    serie = regeln.Titel(typ=MediaType.tv, qualitaet="uhd", fassung="sonarr-uhd")

    def regel(werte: list[str]) -> models.Regel:
        return models.Regel(bedingungen=[{"feld": "qualitaet", "werte": werte}])

    assert regeln.passt(regel(["uhd"]), film) and regeln.passt(regel(["uhd"]), serie)
    assert regeln.passt(regel(["radarr-uhd"]), film)
    assert not regeln.passt(regel(["radarr-uhd"]), serie)


def test_bestand_nennt_klasse_und_fassung() -> None:
    titel = regeln.Titel(
        typ=MediaType.movie, qualitaet="uhd", bestand="hd", bestand_fassung="radarr-standard"
    )
    bestand = models.Regel(bedingungen=[{"feld": "bestand", "werte": ["radarr-standard"]}])
    assert regeln.passt(bestand, titel)


# --------------------------------------------------------------- Kontorechte


def test_die_rechte_werden_je_fassung_bewertet(db: Session) -> None:
    _alle_instanzen(db)
    settings = load_settings(db, frisch=True)
    wunsch = kontorechte.Wunsch(rolle=Role.user, can_request_uhd_movies=True, auto_approve_uhd=True)

    bewertung = kontorechte.bewerten(settings, wunsch, hausordnung_veroeffentlicht=False)

    # Nur Fassungen, die nicht offen sind, bekommen Schalter.
    assert set(bewertung.fassungen) == {"radarr-uhd", "sonarr-uhd"}
    film = bewertung.fassungen["radarr-uhd"]
    assert (film.anfragen.wirkt, film.auto.frei, film.auto.wirkt) == (True, True, True)
    serie = bewertung.fassungen["sonarr-uhd"]
    assert serie.anfragen.wirkt is False
    assert serie.auto.grund == kontorechte.FASSUNG_ERST_ERLAUBEN
    # Die alten Felder sind die Sicht darauf.
    assert bewertung.can_request_uhd_movies == film.anfragen
    assert bewertung.auto_approve_uhd.wirkt is True


def test_eine_offene_fassung_braucht_keinen_haken(db: Session) -> None:
    _alle_instanzen(db)
    settings = load_settings(db, frisch=True)
    offen = fassungen.ARR_OFFEN | {"radarr-uhd"}

    bewertung = kontorechte.bewerten(
        settings,
        kontorechte.Wunsch(rolle=Role.user),
        hausordnung_veroeffentlicht=False,
        offene=offen,
    )

    assert "radarr-uhd" not in bewertung.fassungen
    assert bewertung.can_request_uhd_movies == kontorechte.Stand(
        frei=False, wirkt=True, grund=kontorechte.FASSUNG_OFFEN
    )


def test_der_speicherschluessel_traegt_die_kennung() -> None:
    """Zweites Glied ist die Fassung - so passt spaeter auch ``v_...`` von nexcrate hinein."""
    from app.services import storage

    assert storage.schluessel(MediaType.movie, "radarr-uhd", tmdb_id=603) == (
        "movie:radarr-uhd:tmdb:603"
    )
    assert storage.schluessel(
        MediaType.tv, "sonarr-standard", tvdb_id=81189, season=3, request_id=7
    ) == "tv:sonarr-standard:tvdb:81189:s3:r7"
