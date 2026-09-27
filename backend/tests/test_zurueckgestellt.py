"""Zurückgestellte Anfragen kommen zurück, sobald sie wieder passen.

⚠️ **Diese Datei gibt es, weil ein Satz mehr versprach, als der Code hielt.**

Wer eine Anfrage stellte, die schon zurückstand, las:

    „… steht bereits zurück – sobald du wieder Platz hast, kann die Anfrage
    freigegeben werden."

Das liest sich wie eine Automatik. Es gab keine: ``deferred`` kam weder im
Poller noch in der Kontingent-Rechnung noch in der Speichermessung vor. Die
Anfrage blieb liegen, bis ein Administrator zufällig in den elften Reiter sah.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
)
from app.services import zurueckgestellt
from app.services.beschaffung import NEX
from app.services.beschaffung.nex import client as nex_client
from app.services.beschaffung.nex import fassungen as nex_fassungen
from app.services.beschaffung.nex import system
from app.services.settings_service import load_settings, save_settings

from .beschaffung.fake_nexcrate import KEY, URL, FakeNexcrate
from .conftest import create_user

GB = 1024**3


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _anfrage(user_id: int, *, tmdb_id: int = 900, status=RequestStatus.deferred) -> int:
    with SessionLocal() as db:
        zeile = MediaRequest(
            user_id=user_id,
            media_type=MediaType.movie,
            fassung_kennung="radarr-standard",
            tmdb_id=tmdb_id,
            title="Zurückgestellt",
            status=status,
        )
        db.add(zeile)
        db.commit()
        return zeile.id


def _grenze(user_id: int, *, filme: int | None = None, gb: int | None = None) -> None:
    """Grenzen am Konto setzen.

    ⚠️ Die Spalten heissen ``quota_movies_limit`` und **``storage_limit_gb``**
    (in Gigabyte, nicht in Bytes). Beim ersten Bau dieser Datei stand hier ein
    erfundener Name - SQLAlchemy nimmt jedes Attribut widerspruchslos an,
    schreibt es aber nirgends hin. Der Test lief dann gegen ein Konto **ohne**
    Grenze und behauptete das Gegenteil dessen, was er pruefen sollte.
    """
    with SessionLocal() as db:
        person = db.get(User, user_id)
        if filme is not None:
            person.quota_movies_limit = filme
        if gb is not None:
            person.storage_limit_gb = gb
        db.commit()
        # Gegenprobe: Der Wert muss wirklich in der Spalte stehen.
        if gb is not None:
            assert db.get(User, user_id).storage_limit_gb == gb


def _belegen(user_id: int, gb: int, *, tmdb_id: int = 990) -> None:
    with SessionLocal() as db:
        db.add(
            StorageEntry(
                key=f"movie:radarr-standard:tmdb:{tmdb_id}",
                user_id=user_id,
                media_type=MediaType.movie,
                fassung_kennung="radarr-standard",
                tmdb_id=tmdb_id,
                title="Belegt",
                size_bytes=gb * GB,
                state=StorageState.owned,
            )
        )
        db.commit()


def _lauf() -> int:
    with SessionLocal() as db:
        return zurueckgestellt.zurueckholen(db, load_settings(db))


def _status(anfrage_id: int) -> RequestStatus:
    with SessionLocal() as db:
        return db.get(MediaRequest, anfrage_id).status


# --------------------------------------------------------------------------
# Der Rückweg
# --------------------------------------------------------------------------


def test_passt_wieder_also_zurueck_auf_den_tisch(admin_client: TestClient) -> None:
    """⚠️ Der Fall, für den es diese Datei gibt.

    Ohne Grenze passt alles - die Anfrage gehört zurück zu den offenen
    Freigaben, wo der Administrator ohnehin hinsieht.
    """
    kim = create_user(admin_client, "kim")
    anfrage_id = _anfrage(kim["id"])

    assert _lauf() == 1
    assert _status(anfrage_id) == RequestStatus.pending_approval


def test_kein_platz_also_bleibt_sie_liegen(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    _grenze(kim["id"], gb=10)
    _belegen(kim["id"], 50)  # deutlich darüber
    anfrage_id = _anfrage(kim["id"])

    assert _lauf() == 0
    assert _status(anfrage_id) == RequestStatus.deferred


def test_wieder_platz_also_wieder_da(admin_client: TestClient) -> None:
    """Der eigentliche Ablauf: Der Benutzer gibt etwas ab, und die alte
    Anfrage kommt von selbst zurück."""
    kim = create_user(admin_client, "kim")
    _grenze(kim["id"], gb=10)
    _belegen(kim["id"], 50)
    anfrage_id = _anfrage(kim["id"])
    assert _lauf() == 0

    # Der Posten geht an den Hausbestand - der Platz ist wieder frei.
    with SessionLocal() as db:
        posten = db.scalar(select(StorageEntry).where(StorageEntry.user_id == kim["id"]))
        posten.user_id = None
        posten.state = StorageState.house
        db.commit()

    assert _lauf() == 1
    assert _status(anfrage_id) == RequestStatus.pending_approval


def test_erschoepfte_stueckzahl_haelt_sie_ebenfalls(admin_client: TestClient) -> None:
    """Beide Kontingente gelten - eines reicht zum Zurückhalten."""
    kim = create_user(admin_client, "kim")
    _grenze(kim["id"], filme=0)
    anfrage_id = _anfrage(kim["id"])

    assert _lauf() == 0
    assert _status(anfrage_id) == RequestStatus.deferred


# --------------------------------------------------------------------------
# Was dabei nicht passiert
# --------------------------------------------------------------------------


def test_zurueckholen_ist_kein_freigeben(admin_client: TestClient) -> None:
    """⚠️ Die Grenze, die diese Automatik nicht überschreitet.

    Die Anfrage kehrt in den **Wartezustand** zurück, nicht in die Bibliothek.
    Ob sie durchgeht, bleibt die Entscheidung eines Menschen; das Kontingent
    sagt nur, dass sie wieder gestellt werden *darf*.
    """
    kim = create_user(admin_client, "kim")
    anfrage_id = _anfrage(kim["id"])
    _lauf()

    assert _status(anfrage_id) == RequestStatus.pending_approval
    assert _status(anfrage_id) != RequestStatus.approved


def test_andere_zustaende_bleiben_unberuehrt(admin_client: TestClient) -> None:
    """Abgelehnt bleibt abgelehnt - nur Vertagtes wird wieder aufgerufen."""
    kim = create_user(admin_client, "kim")
    abgelehnt = _anfrage(kim["id"], tmdb_id=901, status=RequestStatus.rejected)
    geladen = _anfrage(kim["id"], tmdb_id=902, status=RequestStatus.downloaded)

    _lauf()
    assert _status(abgelehnt) == RequestStatus.rejected
    assert _status(geladen) == RequestStatus.downloaded


def test_stillgelegte_konten_bleiben_liegen(admin_client: TestClient) -> None:
    """Ein deaktiviertes Konto soll nicht plötzlich wieder Anfragen im
    Freigabestapel haben."""
    kim = create_user(admin_client, "kim")
    anfrage_id = _anfrage(kim["id"])
    with SessionLocal() as db:
        db.get(User, kim["id"]).is_active = False
        db.commit()

    assert _lauf() == 0
    assert _status(anfrage_id) == RequestStatus.deferred


# --------------------------------------------------------------------------
# Der Besteller erfährt es
# --------------------------------------------------------------------------


def test_der_besteller_bekommt_bescheid(admin_client: TestClient) -> None:
    """Sonst wechselt seine Anfrage stillschweigend den Zustand - und das
    sieht von außen aus wie ein Fehler."""
    kim = create_user(admin_client, "kim")
    _anfrage(kim["id"])
    _lauf()

    with SessionLocal() as db:
        nachricht = db.scalar(
            select(Notification).where(
                Notification.user_id == kim["id"],
                Notification.message_key == "notifications.deferredBack",
            )
        )
        assert nachricht is not None
        assert nachricht.type == NotificationType.request_pending


def test_zweimal_laufen_meldet_nicht_zweimal(admin_client: TestClient) -> None:
    """Beim zweiten Durchgang steht sie nicht mehr auf ``deferred`` - es gibt
    also nichts mehr zurückzuholen und nichts mehr zu melden."""
    kim = create_user(admin_client, "kim")
    _anfrage(kim["id"])

    assert _lauf() == 1
    assert _lauf() == 0

    with SessionLocal() as db:
        anzahl = (
            db.query(Notification)
            .filter(Notification.message_key == "notifications.deferredBack")
            .count()
        )
        assert anzahl == 1


# --------------------------------------------------------------------------
# Zurückstellen nur, wenn es etwas zu warten gibt
# --------------------------------------------------------------------------


def test_ohne_ueberzogene_grenze_laesst_sich_nichts_zurueckstellen(
    admin_client: TestClient,
) -> None:
    """⚠️ Befund: Zurückstellen hielt nicht.

    Ein Entscheider stellte über die Schnittstelle eine Anfrage zurück, deren
    Konto keine Grenze überzogen hatte. Die Antwort sagte ``deferred``, und
    zwei Minuten später stand sie wieder unter den offenen Freigaben: Der
    Rundgang holt zurück, was wieder passt, und diese passte die ganze Zeit.
    Zurückstellen heißt „sobald wieder Platz ist“ (so bietet es auch nur der
    Dialog zum vollen Konto an); ohne überzogene Grenze gibt es darauf nichts
    zu warten, und die Schnittstelle sagt das, statt still zu kippen.
    """
    kim = create_user(admin_client, "kim")
    anfrage_id = _anfrage(kim["id"], status=RequestStatus.pending_approval)

    antwort = admin_client.post(f"/api/admin/requests/{anfrage_id}/defer")
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "defer_nothing_to_wait_for"
    assert _status(anfrage_id) == RequestStatus.pending_approval


def test_zurueckgestellt_bei_vollem_konto_haelt(admin_client: TestClient) -> None:
    """Der Weg, für den es Zurückstellen gibt: Das Konto ist voll, die Anfrage
    bleibt zurück, bis wieder Platz ist - auch über mehrere Rundgänge."""
    kim = create_user(admin_client, "kim")
    _grenze(kim["id"], gb=10)
    _belegen(kim["id"], 50)
    anfrage_id = _anfrage(kim["id"], status=RequestStatus.pending_approval)

    antwort = admin_client.post(f"/api/admin/requests/{anfrage_id}/defer")
    assert antwort.status_code == 200, antwort.text
    assert _lauf() == 0
    assert _lauf() == 0
    assert _status(anfrage_id) == RequestStatus.deferred


# --------------------------------------------------------------------------
# Volle Stückzahl: Die zurückgestellte Anfrage zählt für sich selbst mit
# --------------------------------------------------------------------------


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


@pytest.fixture(params=["arr", "nex"])
def betrieb(request: pytest.FixtureRequest, admin_client: TestClient) -> TestClient:
    """Beide Betriebsarten; das Kontingent kennt keine, der Rundgang schon."""
    if request.param == "nex":
        attrappe = request.getfixturevalue("nexcrate")
        with SessionLocal() as sitzung:
            save_settings(
                sitzung, {"beschaffung": NEX, "nexcrate_url": URL, "nexcrate_api_key": KEY}
            )
            nex_fassungen.schreiben(sitzung, attrappe.versions)
            sitzung.commit()
    return admin_client


def test_volle_stueckzahl_haelt_ueber_mehrere_rundgaenge(betrieb: TestClient) -> None:
    """⚠️ Befund: Zurückstellen bei vollem Kontingent hielt nur eine Minute.

    Zwei Filme je Woche, zwei Filme warten: Das Konto ist voll, der
    Entscheider stellt einen zurück. Beim Zurückstellen zählte die Anfrage
    selbst noch mit (2 von 2, voll), beim nächsten Rundgang nicht mehr (1 von
    2, Platz) - und sie stand wieder offen, mit einer zweiten „Neue
    Freigabeanfrage“ auf allen Kanälen. Geändert hatte sich nichts.
    """
    kim = create_user(betrieb, "kim")
    _grenze(kim["id"], filme=2)
    zurueck = _anfrage(kim["id"], tmdb_id=289, status=RequestStatus.pending_approval)
    andere = _anfrage(kim["id"], tmdb_id=15121, status=RequestStatus.pending_approval)

    antwort = betrieb.post(f"/api/admin/requests/{zurueck}/defer")
    assert antwort.status_code == 200, antwort.text

    for _ in range(3):
        assert _lauf() == 0
    assert _status(zurueck) == RequestStatus.deferred

    # Erst wenn wirklich Platz frei wird - hier eine Ablehnung -, kommt sie
    # zurück, und zwar genau einmal.
    with SessionLocal() as db:
        db.get(MediaRequest, andere).status = RequestStatus.rejected
        db.commit()
    assert _lauf() == 1
    assert _lauf() == 0
    assert _status(zurueck) == RequestStatus.pending_approval
    with SessionLocal() as db:
        anzahl = (
            db.query(Notification)
            .filter(Notification.message_key == "notifications.deferredBack")
            .count()
        )
    assert anzahl == 1


def test_neuer_zeitraum_holt_sie_zurueck(admin_client: TestClient) -> None:
    """Der andere Weg zu neuem Platz: Die Woche ist vorbei. Eine Anfrage aus
    dem alten Zeitraum zählt nicht mehr, auch nicht für sich selbst."""
    kim = create_user(admin_client, "kim")
    _grenze(kim["id"], filme=1)
    anfrage_id = _anfrage(kim["id"])
    assert _lauf() == 0

    with SessionLocal() as db:
        db.get(MediaRequest, anfrage_id).requested_at = _jetzt() - timedelta(days=40)
        db.commit()
    assert _lauf() == 1
    assert _status(anfrage_id) == RequestStatus.pending_approval
