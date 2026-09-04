"""Der monatliche Aufräum-Bericht per Mail.

Der Schwerpunkt liegt auf dem, was bei terminierten Mails schiefgeht und beim
Empfänger auffällt statt beim Bauen: **doppelter Versand** und **Post, die
niemand bestellt hat**.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaType,
    QualityTier,
    StorageEntry,
    StorageState,
    User,
)
from app.services import aufraeum_bericht, mail
from app.services.settings_service import load_settings

from .conftest import create_user

GB = 1024**3


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _posten(*, tmdb_id: int, titel: str, gb: int, user_id: int | None = None) -> None:
    with SessionLocal() as db:
        db.add(
            StorageEntry(
                key=f"movie:standard:tmdb:{tmdb_id}",
                user_id=user_id,
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=tmdb_id,
                title=titel,
                size_bytes=gb * GB,
                state=StorageState.owned if user_id else StorageState.house,
                added_at=_jetzt() - timedelta(days=900),
            )
        )
        db.commit()


@pytest.fixture
def postfach(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Fängt jede Mail ab, statt sie zu verschicken."""
    kasten: list[dict] = []

    async def fang(_config, to, subject, html, text):
        kasten.append({"to": to, "subject": subject, "html": html, "text": text})

    monkeypatch.setattr(mail, "send", fang)

    # Mailserver und öffentliche Adresse einrichten - ohne beides passiert
    # bewusst gar nichts.
    admin_client.put(
        "/api/settings",
        json={
            "smtp_host": "mail.example.org",
            "smtp_from_address": "nexview@example.org",
            "public_url": "https://nexview.example.org",
        },
    )
    return kasten


def _lauf(erwartet_admin_mail: bool = True) -> aufraeum_bericht.Versand:
    with SessionLocal() as db:
        return asyncio.run(
            aufraeum_bericht.vielleicht_verschicken(db, load_settings(db))
        )


def _anmelden(username: str) -> None:
    with SessionLocal() as db:
        person = db.scalar(
            __import__("sqlalchemy").select(User).where(User.username == username)
        )
        person.mail_cleanup = True
        person.email_verified = True
        db.commit()


# --------------------------------------------------------------------------
# Doppelter Versand - der klassische Fehler
# --------------------------------------------------------------------------


def test_hoechstens_einer_je_monat(admin_client: TestClient, postfach: list[dict]) -> None:
    """⚠️ Der Fehler, der beim Empfänger auffällt statt beim Bauen.

    Ohne Stempel am Konto schickt ein Container, der am Ersten fünfmal neu
    startet, fünf Berichte.
    """
    _anmelden("admin")
    _posten(tmdb_id=800, titel="Ladenhüter", gb=90)

    assert _lauf().verschickt == 1
    assert len(postfach) == 1

    # Drei weitere Durchgänge im selben Monat.
    for _ in range(3):
        _lauf()
    assert len(postfach) == 1, "Es ging mehr als ein Bericht im selben Monat hinaus"


def test_im_naechsten_monat_wieder(admin_client: TestClient, postfach: list[dict]) -> None:
    _anmelden("admin")
    _posten(tmdb_id=801, titel="Ladenhüter", gb=90)
    _lauf()
    assert len(postfach) == 1

    # Den Stempel auf den Vormonat zurückdrehen.
    with SessionLocal() as db:
        person = db.scalar(
            __import__("sqlalchemy").select(User).where(User.username == "admin")
        )
        person.cleanup_mail_at = _jetzt() - timedelta(days=40)
        db.commit()

    _lauf()
    assert len(postfach) == 2


# --------------------------------------------------------------------------
# Wer bekommt überhaupt Post
# --------------------------------------------------------------------------


def test_ohne_schalter_keine_post(admin_client: TestClient, postfach: list[dict]) -> None:
    """⚠️ Opt-in, ausnahmslos.

    Ein ungefragter monatlicher Brief über das, was man angeblich nicht mehr
    guckt, ist genau die Sorte Post, die man wegfiltert - und danach auch
    alles andere von diesem Absender.
    """
    _posten(tmdb_id=802, titel="Ladenhüter", gb=90)
    assert _lauf().verschickt == 0
    assert postfach == []


def test_ohne_bestaetigte_adresse_keine_post(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    _posten(tmdb_id=803, titel="Ladenhüter", gb=90)
    with SessionLocal() as db:
        person = db.scalar(
            __import__("sqlalchemy").select(User).where(User.username == "admin")
        )
        person.mail_cleanup = True
        person.email_verified = False
        db.commit()

    assert _lauf().verschickt == 0


def test_leere_liste_geht_nicht_hinaus(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    """⚠️ Bei den meisten Konten der Normalfall.

    „Du hast nichts herumliegen", jeden Monat, wäre eine Abmeldung mit Ansage.
    """
    _anmelden("admin")
    ergebnis = _lauf()
    assert ergebnis.verschickt == 0
    assert ergebnis.uebersprungen_leer == 1
    assert postfach == []


def test_auch_die_leere_runde_setzt_den_stempel(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    """Sonst rechnete Nexview den ganzen Monat lang stündlich für nichts."""
    _anmelden("admin")
    _lauf()
    with SessionLocal() as db:
        person = db.scalar(
            __import__("sqlalchemy").select(User).where(User.username == "admin")
        )
        assert person.cleanup_mail_at is not None


# --------------------------------------------------------------------------
# Was drinsteht
# --------------------------------------------------------------------------


def test_der_admin_sieht_die_ganze_bibliothek(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    """Samt Hausbestand - auf einer gewachsenen Anlage der Hauptfall."""
    _anmelden("admin")
    _posten(tmdb_id=810, titel="Gehört niemandem", gb=90)

    _lauf()
    assert "Gehört niemandem" in postfach[0]["html"]


def test_ein_nutzer_sieht_nur_seines(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    kim = create_user(admin_client, "kim")
    _posten(tmdb_id=811, titel="Von Kim", gb=50, user_id=kim["id"])
    _posten(tmdb_id=812, titel="Vom Haus", gb=90)
    _anmelden("kim")

    _lauf()
    assert len(postfach) == 1
    assert "Von Kim" in postfach[0]["html"]
    assert "Vom Haus" not in postfach[0]["html"], "Fremder Bestand im persönlichen Bericht"


def test_der_betreff_nennt_den_platz(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    """Was in der Übersicht des Mailprogramms steht, entscheidet über das Öffnen."""
    _anmelden("admin")
    _posten(tmdb_id=813, titel="Großer Brocken", gb=90)

    _lauf()
    assert "90 GiB" in postfach[0]["subject"]


def test_die_mail_hat_beide_fassungen(
    admin_client: TestClient, postfach: list[dict]
) -> None:
    _anmelden("admin")
    _posten(tmdb_id=814, titel="Ladenhüter", gb=90)
    _lauf()

    assert postfach[0]["html"].startswith("<!doctype html>")
    assert "Ladenhüter" in postfach[0]["text"]


def test_ohne_mailserver_passiert_nichts(admin_client: TestClient) -> None:
    """Eine Mail ohne Absender geht nicht - und ein Knopf ins Leere wäre
    schlimmer als keine Mail."""
    _anmelden("admin")
    _posten(tmdb_id=815, titel="Ladenhüter", gb=90)

    ergebnis = _lauf()
    assert ergebnis.verschickt == 0
