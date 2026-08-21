"""Die zweite Tuer: Was sieht der Entscheider beim Freigeben?

Bis hierhin pruefte die Freigabe **gar nichts**. Wer fuenf Anfragen anlegt,
solange noch Platz ist, dann ueber seine Grenze rutscht und danach per
Sammelfreigabe alle fuenf durchgewunken bekommt, landet beliebig weit im Minus -
und niemand sieht es dabei.

**Gewarnt wird, nicht gesperrt.** Die Anfrage liegt schon vor; sie jetzt noch
abzulehnen hiesse, jemanden fuer eine Entscheidung zu bestrafen, die zum
Zeitpunkt des Klicks erlaubt war. Der Entscheider soll es sehen und selbst
entscheiden.

⚠️ **Entscheider sehen den Stand, obwohl ``/storage/overview`` admin-only ist.**
Das ist kein Widerspruch: Die Uebersicht ist eine Rangliste ueber *alle* - hier
steht eine Zahl ueber *die eine* Person, deren Anfrage gerade vor dem
Entscheider liegt.
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    QualityTier,
    RequestStatus,
    Role,
    StorageEntry,
    StorageState,
    User,
)

from .conftest import auth_headers, create_user

GB = 1024**3


def _wartende_anfrage(db, user_id: int, *, tmdb: int = 603, titel: str = "Matrix") -> int:
    anfrage = MediaRequest(
        user_id=user_id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        title=titel,
        status=RequestStatus.pending_approval,
        quality_profile_id=1,
        root_folder_path="/data/Movies",
    )
    db.add(anfrage)
    db.commit()
    return anfrage.id


def _belegung(db, user_id: int, gb: int, *, tmdb: int = 900) -> None:
    db.add(
        StorageEntry(
            key=f"movie:standard:tmdb:{tmdb}",
            user_id=user_id,
            media_type=MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=tmdb,
            title="Was schon liegt",
            size_bytes=gb * GB,
            state=StorageState.owned,
        )
    )
    db.commit()


def _kim_im_minus(client) -> tuple[int, int]:
    """kim mit 50 GB Grenze, 60 GB belegt und einer wartenden Anfrage."""
    client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        _belegung(db, konto["id"], 60)
        return konto["id"], _wartende_anfrage(db, konto["id"])


# --- Was in der Liste steht ------------------------------------------------


def test_die_liste_zeigt_den_stand_des_anfragenden(arr_client) -> None:
    """Ohne diese Zahl gaebe es die Warnung nicht dort, wo entschieden wird."""
    _kim_im_minus(arr_client)

    zeile = arr_client.get("/api/admin/requests").json()[0]
    assert zeile["storage"]["used_bytes"] == 60 * GB
    assert zeile["storage"]["limit_bytes"] == 50 * GB
    assert zeile["storage"]["exhausted"] is True


def test_ohne_speicher_kontingente_steht_dort_nichts(arr_client) -> None:
    """Es gilt immer nur eine Waehrung.

    Ist der Schalter aus, zaehlt die Stueckzahl - eine Speicher-Zahl daneben
    waere eine zweite Waehrung in derselben Zeile und genau die Verwirrung,
    die das Feature vermeiden soll.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _wartende_anfrage(db, konto["id"])

    zeile = arr_client.get("/api/admin/requests").json()[0]
    assert zeile["storage"] is None


def test_wer_im_rahmen_bleibt_wird_nicht_markiert(arr_client) -> None:
    arr_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(arr_client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        _belegung(db, konto["id"], 10)
        _wartende_anfrage(db, konto["id"])

    zeile = arr_client.get("/api/admin/requests").json()[0]
    assert zeile["storage"]["exhausted"] is False


def test_ohne_grenze_bleibt_die_zahl_ohne_marke(arr_client) -> None:
    """Unbegrenzt heisst: es gibt nichts zu warnen, aber die Zahl steht da."""
    arr_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(arr_client, "kim", "passwort-1234", storage_limit_gb=0)
    with SessionLocal() as db:
        _belegung(db, konto["id"], 400)
        _wartende_anfrage(db, konto["id"])

    zeile = arr_client.get("/api/admin/requests").json()[0]
    assert zeile["storage"]["limit_bytes"] is None
    assert zeile["storage"]["exhausted"] is False
    assert zeile["storage"]["used_bytes"] == 400 * GB


def test_entscheider_sehen_den_stand_ebenfalls(arr_client) -> None:
    """Sonst waere die Warnung ausgerechnet fuer die unsichtbar, die klicken.

    Bewusst anders als ``/api/storage/overview``, das admin-only bleibt: Dort
    stehen alle nebeneinander, hier eine einzige Person - und zwar die, ueber
    deren Anfrage gerade entschieden wird.
    """
    _kim_im_minus(arr_client)
    create_user(arr_client, "eva", "test1234", role=Role.approver)
    kopf = auth_headers(arr_client, "eva", "test1234")

    zeile = arr_client.get("/api/admin/requests", headers=kopf).json()[0]
    assert zeile["storage"]["exhausted"] is True
    # Die Rangliste ueber alle bleibt ihr trotzdem verschlossen.
    assert arr_client.get("/api/storage/overview", headers=kopf).status_code == 403


def test_der_stand_wird_je_person_einmal_gerechnet(arr_client) -> None:
    """Zehn Anfragen einer Person sind zehn Zeilen, aber ein Konto.

    Geprueft ueber das Ergebnis: Alle Zeilen derselben Person tragen denselben
    Stand. Ginge er je Zeile durch eine eigene Rechnung, waere das der Ort, an
    dem sie auseinanderliefen.
    """
    arr_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(arr_client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        _belegung(db, konto["id"], 60)
        for nummer in range(3):
            _wartende_anfrage(db, konto["id"], tmdb=600 + nummer, titel=f"Film {nummer}")

    zeilen = arr_client.get("/api/admin/requests").json()
    assert len(zeilen) == 3
    assert {zeile["storage"]["used_bytes"] for zeile in zeilen} == {60 * GB}


# --- Warnen, nicht sperren -------------------------------------------------


def test_freigabe_wird_vom_speicher_nicht_verhindert(arr_client) -> None:
    """Die Anfrage lag schon vor - jetzt abzulehnen waere eine Strafe im Nachhinein.

    Radarr ist in Tests nicht erreichbar, die Uebergabe endet deshalb mit 502.
    **Genau das ist der Beleg:** Die Freigabe ist bis zu Radarr durchgelaufen,
    statt vorher am Kontingent zu scheitern. Eine Sperre haette 429 geliefert
    und die Anfrage unveraendert wartend gelassen.
    """
    _, anfrage_id = _kim_im_minus(arr_client)

    antwort = arr_client.post(f"/api/admin/requests/{anfrage_id}/approve")
    assert antwort.status_code != 429
    assert antwort.status_code == 502

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).status == RequestStatus.failed


def test_sammelfreigabe_wird_vom_speicher_nicht_verhindert(arr_client) -> None:
    """Der Weg, auf dem ein Konto am schnellsten ins Minus rutscht."""
    arr_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(arr_client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        _belegung(db, konto["id"], 60)
        for nummer in range(3):
            _wartende_anfrage(db, konto["id"], tmdb=600 + nummer, titel=f"Film {nummer}")

    antwort = arr_client.post(f"/api/admin/requests/approve-all/{konto['id']}")
    assert antwort.status_code != 429

    with SessionLocal() as db:
        wartend = (
            db.query(MediaRequest)
            .filter(MediaRequest.status == RequestStatus.pending_approval)
            .count()
        )
        assert wartend == 0, "Keine Anfrage darf am Kontingent haengengeblieben sein."


def test_die_antwort_zeigt_den_stand_nach_der_entscheidung(arr_client) -> None:
    """Die Warnung dort, wo sie ankommt - unmittelbar nach dem Klick."""
    arr_client.put("/api/settings", json={"storage_enabled": True})
    konto = create_user(arr_client, "kim", "passwort-1234", storage_limit_gb=50)
    with SessionLocal() as db:
        _belegung(db, konto["id"], 60)
        anfrage_id = _wartende_anfrage(db, konto["id"])

    antwort = arr_client.post(f"/api/admin/requests/{anfrage_id}/reject", json={})
    assert antwort.status_code == 200
    assert antwort.json()["storage"]["exhausted"] is True


def test_admins_tragen_keinen_stand(arr_client) -> None:
    """Ein Administrator hat kein Kontingent - eine Grenze waere erfunden."""
    arr_client.put("/api/settings", json={"storage_enabled": True})
    with SessionLocal() as db:
        admin_id = db.query(User).filter(User.role == Role.admin).one().id
        _wartende_anfrage(db, admin_id)

    zeile = arr_client.get("/api/admin/requests").json()[0]
    assert zeile["storage"]["limit_bytes"] is None
    assert zeile["storage"]["exhausted"] is False
