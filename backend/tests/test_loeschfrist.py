"""Die Schonfrist vor dem Löschen.

Löschen ist der einzige Vorgang in Nexview ohne Rückweg. Zwischen „der
Administrator entscheidet" und „die Datei ist weg" liegt deshalb eine Frist,
in der der Haushalt widersprechen kann - **oder den Titel einfach ansieht**,
was dasselbe bewirkt und der eigentliche Kniff der Sache ist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    Favorite,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    StorageEntry,
    StorageState,
    TitleRating,
    UserWatched,
)
from app.services import loeschfrist

from .conftest import auth_headers, create_user

GB = 1024**3


def _jetzt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _posten(*, tmdb_id: int = 700, title: str = "Ladenhüter", verwaltet: bool = True) -> int:
    with SessionLocal() as db:
        zeile = StorageEntry(
            key=f"movie:standard:tmdb:{tmdb_id}",
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=tmdb_id,
            title=title,
            size_bytes=40 * GB,
            state=StorageState.house,
            added_at=_jetzt() - timedelta(days=900),
            arr_managed=verwaltet,
        )
        db.add(zeile)
        db.commit()
        return zeile.id


def _nachrichten(art: NotificationType) -> int:
    with SessionLocal() as db:
        return db.query(Notification).filter(Notification.type == art).count()


# --------------------------------------------------------------------------
# Vormerken
# --------------------------------------------------------------------------


def test_vormerken_loescht_nichts(admin_client: TestClient) -> None:
    """⚠️ Der wichtigste Satz: Es passiert nichts an der Datei.

    Der Posten bleibt, zählt weiter und lässt sich bis zur letzten Minute
    retten. Wäre es anders, wäre es keine Frist, sondern eine Löschung mit
    Verzögerung.
    """
    posten_id = _posten()
    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        assert zeile is not None
        assert zeile.delete_after is not None
        assert zeile.size_bytes == 40 * GB


def test_die_frist_ist_vierzehn_tage(admin_client: TestClient) -> None:
    posten_id = _posten()
    daten = admin_client.post(f"/api/storage/entries/{posten_id}/vormerken").json()
    assert daten["tage_uebrig"] in (13, 14)


def test_eine_eigene_frist_geht_auch(admin_client: TestClient) -> None:
    posten_id = _posten()
    daten = admin_client.post(
        f"/api/storage/entries/{posten_id}/vormerken", json={"tage": 3}
    ).json()
    assert daten["tage_uebrig"] in (2, 3)


def test_null_tage_sind_keine_frist(admin_client: TestClient) -> None:
    """Sofort löschen ist ein eigener Weg mit eigener Rückfrage. Eine Frist von
    null Tagen wäre eine Ankündigung, die niemand mehr lesen kann."""
    posten_id = _posten()
    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/vormerken", json={"tage": 0}
    )
    assert antwort.status_code == 422


def test_was_radarr_nicht_kennt_laesst_sich_nicht_vormerken(admin_client: TestClient) -> None:
    """Nexview löscht ausschließlich über Radarr/Sonarr. Etwas vorzumerken, das
    dort niemand mehr führt, wäre eine Ankündigung ohne Deckung."""
    posten_id = _posten(verwaltet=False)
    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert antwort.status_code == 409


def test_nur_administratoren(admin_client: TestClient) -> None:
    posten_id = _posten()
    create_user(admin_client, "kim")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/vormerken", headers=kopf
    )
    assert antwort.status_code == 403


# --------------------------------------------------------------------------
# Wer erfährt davon
# --------------------------------------------------------------------------


def test_wer_den_titel_mag_wird_benachrichtigt(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    posten_id = _posten(tmdb_id=710)
    with SessionLocal() as db:
        db.add(Favorite(user_id=kim["id"], media_type=MediaType.movie, tmdb_id=710))
        db.commit()

    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert _nachrichten(NotificationType.storage_scheduled) >= 1


def test_wer_den_titel_gesehen_hat_wird_benachrichtigt(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    posten_id = _posten(tmdb_id=711)
    with SessionLocal() as db:
        db.add(
            UserWatched(
                user_id=kim["id"],
                media_type=MediaType.movie,
                tmdb_id=711,
                watched_at=_jetzt() - timedelta(days=800),
            )
        )
        db.commit()

    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert _nachrichten(NotificationType.storage_scheduled) >= 1


def test_wer_nichts_damit_zu_tun_hat_bekommt_nichts(admin_client: TestClient) -> None:
    """Wer den Titel nie angefasst hat, braucht keine Nachricht über seinen
    Verlust. Sonst bekämen bei zwölf Vormerkungen alle zwölf Nachrichten über
    Filme, die sie nicht kennen."""
    create_user(admin_client, "unbeteiligt")
    posten_id = _posten(tmdb_id=712)

    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert _nachrichten(NotificationType.storage_scheduled) == 0


def test_kinderkonten_bekommen_keine_verlustmeldung(admin_client: TestClient) -> None:
    """Sie können nichts entscheiden. Eine Nachricht ohne jede
    Handlungsmöglichkeit ist keine Information, sondern eine Verstimmung."""
    from app.models import Role

    kind = create_user(admin_client, "kind", role=Role.child)
    posten_id = _posten(tmdb_id=713)
    with SessionLocal() as db:
        db.add(Favorite(user_id=kind["id"], media_type=MediaType.movie, tmdb_id=713))
        db.commit()

    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert _nachrichten(NotificationType.storage_scheduled) == 0


# --------------------------------------------------------------------------
# Der stille Widerspruch - der Kern der Sache
# --------------------------------------------------------------------------


def test_ansehen_hebt_die_vormerkung_auf(admin_client: TestClient) -> None:
    """⚠️ Der beste denkbare Widerspruch.

    Die Aufräum-Liste behauptet „das sieht niemand mehr an". Wer den Titel in
    der Frist anschaut, hat das widerlegt - ohne einen Knopf zu suchen und
    ohne von der Vormerkung überhaupt zu wissen.
    """
    kim = create_user(admin_client, "kim")
    posten_id = _posten(tmdb_id=720)
    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")

    # Jetzt sieht Kim ihn an.
    with SessionLocal() as db:
        db.add(
            UserWatched(
                user_id=kim["id"],
                media_type=MediaType.movie,
                tmdb_id=720,
                watched_at=_jetzt() + timedelta(seconds=5),
            )
        )
        db.commit()
        assert loeschfrist.angesehen_hebt_auf(db, kim["id"]) == 1
        db.commit()

    with SessionLocal() as db:
        assert db.get(StorageEntry, posten_id).delete_after is None


def test_ein_alter_blick_widerlegt_nichts(admin_client: TestClient) -> None:
    """⚠️ Die Schranke, ohne die der ganze Mechanismus wirkungslos wäre.

    Ein Seh-Eintrag von vor drei Jahren ist ja **gerade der Grund**, warum
    vorgemerkt wurde. Würde er die Vormerkung aufheben, hübe sich jede
    Vormerkung sofort selbst auf.
    """
    kim = create_user(admin_client, "kim")
    posten_id = _posten(tmdb_id=721)
    with SessionLocal() as db:
        db.add(
            UserWatched(
                user_id=kim["id"],
                media_type=MediaType.movie,
                tmdb_id=721,
                watched_at=_jetzt() - timedelta(days=1000),
            )
        )
        db.commit()

    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")

    with SessionLocal() as db:
        assert loeschfrist.angesehen_hebt_auf(db, kim["id"]) == 0
        assert db.get(StorageEntry, posten_id).delete_after is not None


def test_das_aufheben_wird_auch_gemeldet(admin_client: TestClient) -> None:
    """Wer die Verlustmeldung bekommen hat, soll auch die Entwarnung bekommen."""
    kim = create_user(admin_client, "kim")
    posten_id = _posten(tmdb_id=722)
    with SessionLocal() as db:
        db.add(
            TitleRating(
                user_id=kim["id"], media_type=MediaType.movie, tmdb_id=722, rating=4, title="X"
            )
        )
        db.commit()

    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")
    assert admin_client.post(
        f"/api/storage/entries/{posten_id}/vormerkung-aufheben"
    ).status_code == 204
    assert _nachrichten(NotificationType.storage_unscheduled) >= 1


# --------------------------------------------------------------------------
# Was auf der Startseite steht
# --------------------------------------------------------------------------


def test_alle_sehen_was_bald_verschwindet(admin_client: TestClient) -> None:
    """⚠️ Bewusst nicht nur für Administratoren.

    Der ganze Sinn der Frist ist, dass der Haushalt sie mitbekommt. Eine
    Ankündigung, die nur der liest, der sie ausgesprochen hat, ist keine.
    """
    posten_id = _posten(tmdb_id=730, title="Verschwindet bald")
    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")

    create_user(admin_client, "kim")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    antwort = admin_client.get("/api/storage/vorgemerkt", headers=kopf)
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert len(daten) == 1
    assert daten[0]["title"] == "Verschwindet bald"
    assert daten[0]["tage_uebrig"] >= 0


def test_ohne_vormerkung_ist_die_liste_leer(admin_client: TestClient) -> None:
    _posten(tmdb_id=731)
    assert admin_client.get("/api/storage/vorgemerkt").json() == []


def test_das_naechste_zuerst(admin_client: TestClient) -> None:
    """Was in zwei Tagen weg ist, ist dringender als das in zwei Wochen."""
    spaet = _posten(tmdb_id=740, title="Später")
    frueh = _posten(tmdb_id=741, title="Früher")
    admin_client.post(f"/api/storage/entries/{spaet}/vormerken", json={"tage": 14})
    admin_client.post(f"/api/storage/entries/{frueh}/vormerken", json={"tage": 2})

    titel = [e["title"] for e in admin_client.get("/api/storage/vorgemerkt").json()]
    assert titel == ["Früher", "Später"]


# --------------------------------------------------------------------------
# Fällig werden
# --------------------------------------------------------------------------


def test_erst_nach_ablauf_faellig(admin_client: TestClient) -> None:
    posten_id = _posten(tmdb_id=750)
    admin_client.post(f"/api/storage/entries/{posten_id}/vormerken")

    with SessionLocal() as db:
        assert loeschfrist.faellig(db) == []

        # Die Frist vorspulen.
        zeile = db.get(StorageEntry, posten_id)
        zeile.delete_after = _jetzt() - timedelta(minutes=1)
        db.commit()

        assert [z.id for z in loeschfrist.faellig(db)] == [posten_id]
