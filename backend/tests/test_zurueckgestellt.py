"""Zurueckgestellte Anfragen: „Ja im Prinzip, nur nicht jetzt."

Der Anlass war ein Befund aus dem Betrieb: Wenn der Entscheider eine Anfrage
wegen eines vollen Kontos einfach stehen laesst, **blockiert sie alle anderen**
- ``pending_approval`` steht in ``ACTIVE_STATUSES``, und ``badges_for`` ist
global. Auf der Kachel stand fuer jeden "Angefragt", und es kam nie etwas.

Der Kern der Loesung:

> Der Grund, warum die Anfrage nicht durchgeht, liegt **an der Person**, nicht
> am Titel. Also darf sie den Titel nicht fuer alle reservieren.

Zwei Regeln tragen das Ganze, und beide sind hier festgenagelt:

1. Zurueckgestellt blockiert niemanden.
2. Sobald **eine** Anfrage freigegeben wird, sind die anderen zurueckgestellten
   zu demselben Titel erledigt - sonst gaebe es zwei zurechenbare Anfragen fuer
   eine Datei, und wem der Platz angerechnet wird, waere Glueckssache.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    RequestStatus,
    User,
)
from app.services import quota, requests_service, storage
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user

GB = 1024**3


def _anfrage(db, user_id: int, *, tmdb: int = 603, titel: str = "Matrix") -> int:
    zeile = MediaRequest(
        user_id=user_id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        title=titel,
        status=RequestStatus.pending_approval,
        quality_profile_id=1,
        root_folder_path="/data/Movies",
    )
    db.add(zeile)
    db.commit()
    return zeile.id


# --- Zuruecksetzen selbst --------------------------------------------------


def test_zuruecksetzen_setzt_den_zustand_und_meldet_es(arr_client) -> None:
    """Ein stiller Zustandswechsel saehe aus wie ein Fehler."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _anfrage(db, konto["id"])

    antwort = arr_client.post(f"/api/admin/requests/{anfrage_id}/defer")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "deferred"

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).status == RequestStatus.deferred
        meldung = db.scalars(
            select(Notification).where(Notification.user_id == konto["id"])
        ).all()
        assert [n.type for n in meldung] == [NotificationType.request_deferred]


def test_nur_wartende_lassen_sich_zuruecksetzen(arr_client) -> None:
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _anfrage(db, konto["id"])
        db.get(MediaRequest, anfrage_id).status = RequestStatus.downloaded
        db.commit()

    assert arr_client.post(f"/api/admin/requests/{anfrage_id}/defer").status_code == 409


# --- Regel 1: blockiert niemanden -----------------------------------------


def test_zurueckgestellt_gibt_den_titel_fuer_andere_frei(arr_client) -> None:
    """**Der eigentliche Zweck.**

    Eine wartende Anfrage reserviert den Titel fuer alle mit. Eine
    zurueckgestellte darf das nicht - sonst waere sie nur ein huebscheres Wort
    fuer denselben Stillstand.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _anfrage(db, konto["id"])

    with SessionLocal() as db:
        # Solange sie wartet, ist der Titel belegt.
        assert requests_service.find_active(db, MediaType.movie, 603) is not None

    arr_client.post(f"/api/admin/requests/{anfrage_id}/defer")

    with SessionLocal() as db:
        assert requests_service.find_active(db, MediaType.movie, 603) is None


def test_zurueckgestellt_taucht_auf_keiner_kachel_auf(arr_client) -> None:
    """``badges_for`` ist global - stuende sie dort, saehe der Titel fuer alle
    weiterhin nach "ist bestellt" aus."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _anfrage(db, konto["id"])
    arr_client.post(f"/api/admin/requests/{anfrage_id}/defer")

    with SessionLocal() as db:
        assert requests_service.badges_for(db, MediaType.movie, [603]) == {}


def test_zweimal_zuruecksetzen_derselben_person_geht_nicht(arr_client) -> None:
    """Blockieren soll sie niemanden - **sich selbst** aber schon.

    Sonst stuenden nach dem dritten Versuch drei zurueckgestellte Anfragen
    desselben Titels in derselben Liste.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    kopf = auth_headers(arr_client, "kim", "passwort-1234")
    # Ein echter Titel: Beim Anlegen holt der Dienst die Angaben von TMDB, und
    # eine frei erfundene Nummer gibt es dort nicht.
    titel = arr_client.get("/api/discover/movie").json()["items"][0]
    with SessionLocal() as db:
        anfrage_id = _anfrage(db, konto["id"], tmdb=titel["tmdb_id"], titel=titel["title"])
    arr_client.post(f"/api/admin/requests/{anfrage_id}/defer")

    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kopf,
    )
    assert antwort.status_code == 409
    assert "steht bereits zurück" in antwort.json()["detail"]


# --- Regel 2: eine Datei, ein Besitzer ------------------------------------


def test_freigabe_schliesst_die_anderen_zurueckgestellten(arr_client, monkeypatch) -> None:
    """⚠️ **Die Regel, ohne die die Speicher-Rechnung mehrdeutig wird.**

    Zwei zurechenbare Anfragen fuer eine Datei - und ``storage._zuordnung``
    nimmt per ``setdefault`` die erste, die die Datenbank zufaellig liefert.
    """
    eins = create_user(arr_client, "kim", "passwort-1234")
    zwei = create_user(arr_client, "eva", "passwort-1234")
    with SessionLocal() as db:
        a = _anfrage(db, eins["id"])
        b = _anfrage(db, zwei["id"])

    arr_client.post(f"/api/admin/requests/{a}/defer")
    arr_client.post(f"/api/admin/requests/{b}/defer")

    # Die Uebergabe an Radarr gelingen lassen: Scheitert sie, landet die
    # Anfrage auf "failed" - und dann sollen die zurueckgestellten
    # ausdruecklich stehen bleiben, weil gar nichts geladen wird.
    async def uebergabe(_db, _settings, _anfrage) -> None:
        return None

    monkeypatch.setattr(requests_service, "push_to_arr", uebergabe)

    with SessionLocal() as db:
        db.get(MediaRequest, a).status = RequestStatus.pending_approval
        db.commit()
    arr_client.post(f"/api/admin/requests/{a}/approve")

    with SessionLocal() as db:
        # Die andere ist erledigt und zaehlt gegen nichts mehr.
        assert db.get(MediaRequest, b).status == RequestStatus.cancelled
        # Und eva erfaehrt davon - sonst verschwaende ihre Anfrage kommentarlos.
        arten = [
            n.type
            for n in db.scalars(
                select(Notification).where(Notification.user_id == zwei["id"])
            )
        ]
        assert NotificationType.request_fulfilled in arten


def test_nur_zurueckgestellte_werden_geschlossen(arr_client) -> None:
    """Eine Anfrage zu einem **anderen** Titel bleibt unberuehrt."""
    eins = create_user(arr_client, "kim", "passwort-1234")
    zwei = create_user(arr_client, "eva", "passwort-1234")
    with SessionLocal() as db:
        a = _anfrage(db, eins["id"], tmdb=603, titel="Matrix")
        fremd = _anfrage(db, zwei["id"], tmdb=604, titel="Matrix Reloaded")
    arr_client.post(f"/api/admin/requests/{fremd}/defer")

    arr_client.post(f"/api/admin/requests/{a}/approve")

    with SessionLocal() as db:
        assert db.get(MediaRequest, fremd).status == RequestStatus.deferred


# --- Kostet nichts, solange sie zurueckgestellt ist ------------------------


def test_zurueckgestellt_zaehlt_gegen_kein_kontingent(arr_client) -> None:
    """Weder Stueckzahl noch Speicher - es liegt ja keine Datei."""
    konto = create_user(arr_client, "kim", "passwort-1234", quota_movies=1)
    with SessionLocal() as db:
        anfrage_id = _anfrage(db, konto["id"])
    arr_client.post(f"/api/admin/requests/{anfrage_id}/defer")

    with SessionLocal() as db:
        person = db.get(User, konto["id"])
        stand = quota.state_for(db, person, MediaType.movie)
        assert stand.used == 0
        assert not stand.exhausted
        assert storage.stand_fuer(db, person, load_settings(db)).used_bytes == 0
