"""Zielordner und Qualitätsprofil erst bei der Freigabe wählen.

Für Betreiber, die ihre Mediathek in mehrere Ordner sortieren (etwa nach
Genre): Der Anfragende kennt die Struktur nicht, also entscheidet erst der
Entscheider. Geschaltet wird das global über ``approver_picks_target``.

Der wichtigste Test steht ganz oben: Ist der Schalter aus, verhält sich alles
wie vorher. Alles Weitere darf nur greifen, wenn er an ist.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus, Role

from .conftest import auth_headers, create_user


def _demo(client: TestClient, media_type: str = "movie", index: int = 0) -> dict:
    return client.get(f"/api/discover/{media_type}").json()["items"][index]


def _anfrage(client: TestClient, item: dict, headers: dict | None = None, **extra: object):
    rumpf: dict = {
        "media_type": item["media_type"],
        "tmdb_id": item["tmdb_id"],
        "quality_profile_id": 1,
        "root_folder_path": "/data/Movies",
    }
    rumpf.update(extra)
    return client.post("/api/requests", json=rumpf, headers=headers)


def _schalter(client: TestClient, an: bool) -> None:
    """Zielordner-Regel je Dienst setzen: der Entscheider waehlt, oder nicht."""
    modus = "approver" if an else "user"
    antwort = client.put(
        "/api/settings",
        json={"movie_root_folder_mode": modus, "series_root_folder_mode": modus},
    )
    assert antwort.status_code == 200, antwort.text


def _anfrage_aus_db(kennung: int) -> MediaRequest:
    with SessionLocal() as session:
        return session.get(MediaRequest, kennung)


def _einzige_anfrage() -> MediaRequest:
    """Die eine Anfrage in der Datenbank.

    Gebraucht, wo die Antwort keine Kennung liefert: Bei Auto-Freigabe geht die
    Anfrage sofort an Radarr, das im Test absichtlich nicht erreichbar ist - der
    Aufruf endet also mit 502. Angelegt wurde sie trotzdem, und genau ihr
    Zielordner ist hier die Frage.
    """
    with SessionLocal() as session:
        return session.query(MediaRequest).one()


# --- Ausgeschaltet: nichts darf sich ändern --------------------------------


def test_schalter_ist_standardmaessig_aus(arr_client: TestClient) -> None:
    """Wer nichts einstellt, merkt von der Funktion nichts."""
    config = arr_client.get("/api/config").json()
    assert config["approver_picks_target_movie"] is False
    assert config["approver_picks_target_tv"] is False
    # Bestandsverhalten: der Anfragende waehlt selbst.
    assert arr_client.get("/api/settings").json()["movie_root_folder_mode"] == "user"


def test_ausgeschaltet_bleibt_alles_wie_bisher(arr_client: TestClient) -> None:
    create_user(arr_client, "kim", auto_approve=True)
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    # Auto-Freigabe greift, also geht es sofort an das (unerreichbare) Radarr.
    assert _anfrage(arr_client, _demo(arr_client), headers).status_code == 502

    gespeichert = _einzige_anfrage()
    assert gespeichert.status != RequestStatus.pending_approval
    assert gespeichert.root_folder_path == "/data/Movies"
    assert gespeichert.quality_profile_id == 1


# --- Eingeschaltet: gewöhnliche Benutzer ----------------------------------


def test_anfrage_bleibt_ohne_ziel_und_wartet(arr_client: TestClient) -> None:
    """Auch mit Auto-Freigabe landet die Anfrage in der Warteschlange.

    Sonst käme sie an keinem Entscheider vorbei - und genau der soll wählen.
    """
    _schalter(arr_client, True)
    create_user(arr_client, "kim", auto_approve=True)
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = _anfrage(arr_client, _demo(arr_client), headers)
    assert antwort.status_code == 201
    assert antwort.json()["status"] == "pending_approval"

    gespeichert = _anfrage_aus_db(antwort.json()["id"])
    assert gespeichert.root_folder_path is None
    assert gespeichert.quality_profile_id is None


def test_mitgeschickter_ordner_wird_ignoriert(arr_client: TestClient) -> None:
    """Ein selbstgebauter Aufruf darf sich den Ordner nicht doch aussuchen."""
    _schalter(arr_client, True)
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    antwort = _anfrage(
        arr_client, _demo(arr_client), headers, root_folder_path="/data/TV-Shows"
    )
    assert antwort.status_code == 201
    assert _anfrage_aus_db(antwort.json()["id"]).root_folder_path is None


def test_gesperrtes_profil_blockiert_nicht_mehr(arr_client: TestClient) -> None:
    """Es wird ja gar kein Profil gewählt - also gibt es nichts zu sperren."""
    _schalter(arr_client, True)
    create_user(arr_client, "kim", blocked_movie_profiles="1")
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    assert _anfrage(arr_client, _demo(arr_client), headers).status_code == 201


# --- Eingeschaltet: Entscheider und Admin ---------------------------------


def test_admin_waehlt_direkt_beim_anfragen(arr_client: TestClient) -> None:
    """Wer freigeben darf, entscheidet gleich jetzt statt im Kreis zu laufen."""
    _schalter(arr_client, True)

    assert _anfrage(arr_client, _demo(arr_client)).status_code == 502  # als Admin

    gespeichert = _einzige_anfrage()
    assert gespeichert.status != RequestStatus.pending_approval
    assert gespeichert.root_folder_path == "/data/Movies"
    assert gespeichert.quality_profile_id == 1


def test_entscheider_waehlt_ebenfalls_direkt(arr_client: TestClient) -> None:
    _schalter(arr_client, True)
    create_user(arr_client, "eva", role=Role.approver)
    headers = auth_headers(arr_client, "eva", "passwort-1234")

    assert _anfrage(arr_client, _demo(arr_client), headers).status_code == 502

    gespeichert = _einzige_anfrage()
    assert gespeichert.status != RequestStatus.pending_approval
    assert gespeichert.root_folder_path == "/data/Movies"


# --- Die Freigabe selbst ---------------------------------------------------


def _wartende_anfrage(client: TestClient) -> int:
    _schalter(client, True)
    create_user(client, "kim")
    headers = auth_headers(client, "kim", "passwort-1234")
    return _anfrage(client, _demo(client), headers).json()["id"]


def test_freigabe_ohne_wahl_wird_abgelehnt(arr_client: TestClient) -> None:
    """Lieber abbrechen als raten - sonst läge der Titel im falschen Ordner."""
    kennung = _wartende_anfrage(arr_client)

    antwort = arr_client.post(f"/api/admin/requests/{kennung}/approve")
    assert antwort.status_code == 422
    # Die Anfrage wartet unverändert weiter.
    assert _anfrage_aus_db(kennung).status == RequestStatus.pending_approval


def test_freigabe_mit_unbekanntem_ordner_wird_abgelehnt(arr_client: TestClient) -> None:
    kennung = _wartende_anfrage(arr_client)

    antwort = arr_client.post(
        f"/api/admin/requests/{kennung}/approve",
        json={"root_folder_path": "/etc", "quality_profile_id": 1},
    )
    assert antwort.status_code == 422
    assert _anfrage_aus_db(kennung).status == RequestStatus.pending_approval


def test_freigabe_mit_unbekanntem_profil_wird_abgelehnt(arr_client: TestClient) -> None:
    kennung = _wartende_anfrage(arr_client)

    antwort = arr_client.post(
        f"/api/admin/requests/{kennung}/approve",
        json={"root_folder_path": "/data/Movies", "quality_profile_id": 999},
    )
    assert antwort.status_code == 422


def test_freigabe_setzt_das_gewaehlte_ziel(arr_client: TestClient) -> None:
    """Radarr ist nicht erreichbar - die Übergabe scheitert also mit 502.

    Entscheidend ist, dass das Ziel davor gesetzt wurde: die Wahl des
    Entscheiders darf nicht am Verbindungsfehler verlorengehen.
    """
    kennung = _wartende_anfrage(arr_client)

    arr_client.post(
        f"/api/admin/requests/{kennung}/approve",
        json={"root_folder_path": "/data/TV-Shows", "quality_profile_id": 1},
    )

    gespeichert = _anfrage_aus_db(kennung)
    assert gespeichert.root_folder_path == "/data/TV-Shows"
    assert gespeichert.quality_profile_id == 1


# --- Sammelfreigabe --------------------------------------------------------


def test_sammelfreigabe_setzt_das_ziel_auf_allen(arr_client: TestClient) -> None:
    _schalter(arr_client, True)
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    erste = _anfrage(arr_client, _demo(arr_client, index=0), headers).json()["id"]
    zweite = _anfrage(arr_client, _demo(arr_client, index=1), headers).json()["id"]

    benutzer = arr_client.get("/api/admin/requests").json()[0]["user_id"]
    arr_client.post(
        f"/api/admin/requests/approve-all/{benutzer}",
        json={"movie": {"root_folder_path": "/data/Movies", "quality_profile_id": 1}},
    )

    for kennung in (erste, zweite):
        assert _anfrage_aus_db(kennung).root_folder_path == "/data/Movies"


def test_sammelfreigabe_ueberspringt_ohne_wahl(arr_client: TestClient) -> None:
    """Ohne Wahl bleibt die Anfrage wartend - der Stapel scheitert nicht."""
    _schalter(arr_client, True)
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    kennung = _anfrage(arr_client, _demo(arr_client), headers).json()["id"]

    benutzer = arr_client.get("/api/admin/requests").json()[0]["user_id"]
    antwort = arr_client.post(f"/api/admin/requests/approve-all/{benutzer}")

    assert antwort.status_code == 200
    assert antwort.json() == []
    assert _anfrage_aus_db(kennung).status == RequestStatus.pending_approval


def test_filme_und_serien_bekommen_eigene_ziele(arr_client: TestClient) -> None:
    """Radarr und Sonarr haben verschiedene Ordner - eine Wahl je Art."""
    _schalter(arr_client, True)
    create_user(arr_client, "kim")
    headers = auth_headers(arr_client, "kim", "passwort-1234")
    film = _anfrage(arr_client, _demo(arr_client, "movie"), headers).json()["id"]
    serie = _anfrage(arr_client, _demo(arr_client, "tv"), headers).json()["id"]

    benutzer = arr_client.get("/api/admin/requests").json()[0]["user_id"]
    arr_client.post(
        f"/api/admin/requests/approve-all/{benutzer}",
        json={
            "movie": {"root_folder_path": "/data/Movies", "quality_profile_id": 1},
            "tv": {"root_folder_path": "/data/TV-Shows", "quality_profile_id": 1},
        },
    )

    assert _anfrage_aus_db(film).root_folder_path == "/data/Movies"
    assert _anfrage_aus_db(serie).root_folder_path == "/data/TV-Shows"


def test_regel_gilt_je_dienst(arr_client: TestClient) -> None:
    """Filme warten, Serien laufen durch - obwohl derselbe Benutzer
    Auto-Freigabe hat.

    Genau die Frage, die beim Prüfen aufkam: Wenn erst der Entscheider den
    Ordner wählt, kann es dort keine Auto-Freigabe geben - sie käme ja an ihm
    vorbei. Für den *anderen* Dienst gilt sie aber unverändert weiter. Der
    gespeicherte Haken bleibt dabei erhalten.
    """
    arr_client.put(
        "/api/settings",
        json={"movie_root_folder_mode": "approver", "series_root_folder_mode": "user"},
    )
    create_user(arr_client, "kim", auto_approve=True)
    headers = auth_headers(arr_client, "kim", "passwort-1234")

    film = _anfrage(arr_client, _demo(arr_client, "movie"), headers)
    assert film.status_code == 201
    assert film.json()["status"] == "pending_approval"

    # Bei Serien greift die Auto-Freigabe weiter - also sofort an das
    # (unerreichbare) Sonarr, was mit 502 endet.
    serie = _anfrage(arr_client, _demo(arr_client, "tv"), headers)
    assert serie.status_code == 502

    with SessionLocal() as session:
        zustaende = {
            r.media_type.value: r.status
            for r in session.query(MediaRequest).all()
        }
    assert zustaende["movie"] == RequestStatus.pending_approval
    assert zustaende["tv"] != RequestStatus.pending_approval
