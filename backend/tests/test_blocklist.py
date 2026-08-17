"""Die Sperrliste: wer sie pflegen darf, und was sie verhindert.

Wie in ``test_requests.py`` laufen die Tests mit Demo-Daten und einem
eingerichteten, aber absichtlich nicht erreichbaren Radarr/Sonarr - es geht
garantiert nichts an eine echte Instanz.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Blocked, MediaType, Role, User

from .conftest import auth_headers, create_user


def _erster_film(client: TestClient) -> dict:
    return client.get("/api/discover/movie").json()["items"][0]


def _sperren(client: TestClient, item: dict, reason: str | None = None):
    return client.post(
        "/api/admin/blocklist",
        json={
            "media_type": item["media_type"],
            "tmdb_id": item["tmdb_id"],
            "title": item["title"],
            "poster_url": item.get("poster_url"),
            "reason": reason,
        },
    )


def _anfragen(client: TestClient, item: dict, headers: dict | None = None):
    return client.post(
        "/api/requests",
        json={
            "media_type": item["media_type"],
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=headers,
    )


# --- Wer darf sperren? -------------------------------------------------------


def test_nur_der_admin_darf_die_sperrliste_sehen_und_pflegen(
    client: TestClient, arr_client: TestClient
) -> None:
    """Der Kern der Regel: Entscheider entscheiden ueber Anfragen, nicht
    darueber, was es in dieser Bibliothek grundsaetzlich geben darf."""
    film = _erster_film(arr_client)

    create_user(arr_client, "leser")
    create_user(arr_client, "pruefer", role=Role.approver)

    for name in ("leser", "pruefer"):
        headers = auth_headers(client, name, "passwort-1234")
        assert client.get("/api/admin/blocklist", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/admin/blocklist",
                json={"media_type": "movie", "tmdb_id": film["tmdb_id"]},
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/admin/blocklist/movie/{film['tmdb_id']}", headers=headers
            ).status_code
            == 403
        )


def test_admin_sperrt_und_gibt_wieder_frei(arr_client: TestClient) -> None:
    film = _erster_film(arr_client)

    angelegt = _sperren(arr_client, film, reason="Nichts für diese Runde.")
    assert angelegt.status_code == 201, angelegt.text
    assert angelegt.json()["reason"] == "Nichts für diese Runde."

    liste = arr_client.get("/api/admin/blocklist").json()
    assert [(e["media_type"], e["tmdb_id"]) for e in liste] == [("movie", film["tmdb_id"])]

    frei = arr_client.delete(f"/api/admin/blocklist/movie/{film['tmdb_id']}")
    assert frei.status_code == 204
    assert arr_client.get("/api/admin/blocklist").json() == []


def test_zweimal_sperren_ist_kein_fehler(arr_client: TestClient) -> None:
    """Sonst muesste jede Aufrufstelle vorher selbst nachsehen."""
    film = _erster_film(arr_client)

    assert _sperren(arr_client, film, reason="Zuerst").status_code == 201
    zweiter = _sperren(arr_client, film, reason="Danach")
    assert zweiter.status_code == 201
    # Die erste Entscheidung bleibt die dokumentierte.
    assert zweiter.json()["reason"] == "Zuerst"
    assert len(arr_client.get("/api/admin/blocklist").json()) == 1


def test_freigeben_was_gar_nicht_gesperrt_war(arr_client: TestClient) -> None:
    assert arr_client.delete("/api/admin/blocklist/movie/999999").status_code == 404


# --- Was die Sperre bewirkt --------------------------------------------------


def test_gesperrte_titel_koennen_nicht_angefragt_werden(
    client: TestClient, arr_client: TestClient
) -> None:
    film = _erster_film(arr_client)
    _sperren(arr_client, film, reason="Zu brutal.")

    create_user(arr_client, "anfrager")
    headers = auth_headers(client, "anfrager", "passwort-1234")

    antwort = _anfragen(client, film, headers)
    assert antwort.status_code == 403, antwort.text
    # Anders als bei der Altersbeschraenkung wird hier nichts verschwiegen -
    # der Titel ist ja sichtbar, ein "gibt es nicht" waere gelogen.
    assert "Sperrliste" in antwort.json()["detail"]
    assert "Zu brutal." in antwort.json()["detail"]


def test_der_admin_darf_gesperrte_titel_trotzdem_anfragen(arr_client: TestClient) -> None:
    """Die Liste ist seine eigene Entscheidung - sie bremst die anderen.

    Zuerst galt die Sperre auch fuer ihn, und er musste zum Hinzufuegen erst
    freigeben. Das war ein Umweg ohne Gewinn.
    """
    film = _erster_film(arr_client)
    _sperren(arr_client, film)

    # Radarr ist absichtlich nicht erreichbar - entscheidend ist nur, dass die
    # Sperre ihn nicht aufhaelt.
    assert _anfragen(arr_client, film).status_code != 403

    # Und der Eintrag bleibt bestehen: fuer alle anderen gilt er weiter.
    assert len(arr_client.get("/api/admin/blocklist").json()) == 1


def test_entscheider_kommt_an_der_sperre_nicht_vorbei(
    client: TestClient, arr_client: TestClient
) -> None:
    """Die Ausnahme gilt dem Administrator, nicht jedem mit Freigaberecht."""
    film = _erster_film(arr_client)
    _sperren(arr_client, film)

    create_user(arr_client, "pruefer2", role=Role.approver)
    headers = auth_headers(client, "pruefer2", "passwort-1234")
    assert _anfragen(client, film, headers).status_code == 403


def test_nach_dem_freigeben_geht_es_wieder(client: TestClient, arr_client: TestClient) -> None:
    film = _erster_film(arr_client)
    _sperren(arr_client, film)
    arr_client.delete(f"/api/admin/blocklist/movie/{film['tmdb_id']}")

    create_user(arr_client, "anfrager2")
    headers = auth_headers(client, "anfrager2", "passwort-1234")

    # Radarr ist absichtlich nicht erreichbar - entscheidend ist nur, dass die
    # Sperre nicht mehr greift.
    assert _anfragen(client, film, headers).status_code != 403


def test_gesperrte_titel_bleiben_sichtbar_und_tragen_das_abzeichen(
    arr_client: TestClient,
) -> None:
    """Der Unterschied zur Altersbeschraenkung: hier wird nichts versteckt."""
    film = _erster_film(arr_client)
    _sperren(arr_client, film)

    items = arr_client.get("/api/discover/movie").json()["items"]
    treffer = [e for e in items if e["tmdb_id"] == film["tmdb_id"]]

    assert len(treffer) == 1, "Gesperrte Titel muessen auffindbar bleiben"
    assert treffer[0]["status"] == "blocked"

    # Auch beim Einzelabruf und in der Suche.
    einzeln = arr_client.get(f"/api/media/movie/{film['tmdb_id']}").json()
    assert einzeln["status"] == "blocked"


# --- Ablehnen mit Sperren ----------------------------------------------------


def _wartende_anfrage(client: TestClient, arr_client: TestClient) -> tuple[int, dict]:
    """Eine Anfrage anlegen, die auf Freigabe wartet."""
    film = _erster_film(arr_client)
    create_user(arr_client, "wartender", auto_approve=False)
    headers = auth_headers(client, "wartender", "passwort-1234")
    antwort = _anfragen(client, film, headers)
    assert antwort.status_code == 201, antwort.text
    return antwort.json()["id"], film


def test_admin_kann_beim_ablehnen_gleich_sperren(
    client: TestClient, arr_client: TestClient
) -> None:
    anfrage_id, film = _wartende_anfrage(client, arr_client)

    antwort = arr_client.post(
        f"/api/admin/requests/{anfrage_id}/reject",
        json={"reason": "Kommt mir nicht ins Haus.", "block": True},
    )
    assert antwort.status_code == 200, antwort.text

    liste = arr_client.get("/api/admin/blocklist").json()
    assert len(liste) == 1
    assert liste[0]["tmdb_id"] == film["tmdb_id"]
    # Die Begruendung der Ablehnung ist zugleich die der Sperre.
    assert liste[0]["reason"] == "Kommt mir nicht ins Haus."

    # Das Bild muss unveraendert uebernommen werden.
    #
    # ``MediaRequest.poster_path`` enthaelt trotz des Namens die fertige
    # Adresse. Ein ``image_url()`` darum herum setzte das Praefix ein zweites
    # Mal davor, und in der Datenbank stand
    # ".../w500https://image.tmdb.org/t/p/w500/...". Die Gleichheit faengt das
    # unabhaengig davon, ob echte TMDB-Adressen oder Demo-Pfade im Spiel sind.
    assert liste[0]["poster_url"] == film["poster_url"]


def test_entscheider_darf_ablehnen_aber_nicht_sperren(
    client: TestClient, arr_client: TestClient
) -> None:
    """Deutlich abweisen statt still zu ignorieren - sonst klickt jemand den
    Haken und glaubt, es sei passiert."""
    anfrage_id, _ = _wartende_anfrage(client, arr_client)

    create_user(arr_client, "entscheider2", role=Role.approver)
    headers = auth_headers(client, "entscheider2", "passwort-1234")

    verweigert = client.post(
        f"/api/admin/requests/{anfrage_id}/reject",
        json={"reason": "Nein.", "block": True},
        headers=headers,
    )
    assert verweigert.status_code == 403
    assert arr_client.get("/api/admin/blocklist").json() == []

    # Ohne den Haken geht es sehr wohl.
    ohne = client.post(
        f"/api/admin/requests/{anfrage_id}/reject",
        json={"reason": "Nein."},
        headers=headers,
    )
    assert ohne.status_code == 200, ohne.text
    assert ohne.json()["status"] == "rejected"
    assert arr_client.get("/api/admin/blocklist").json() == []


def test_ablehnen_ohne_haken_sperrt_nicht(client: TestClient, arr_client: TestClient) -> None:
    anfrage_id, _ = _wartende_anfrage(client, arr_client)
    arr_client.post(f"/api/admin/requests/{anfrage_id}/reject", json={"reason": "Diesmal nicht."})
    assert arr_client.get("/api/admin/blocklist").json() == []


def test_sperre_ueberlebt_das_loeschen_des_admins(arr_client: TestClient) -> None:
    """Die Sperre war eine Entscheidung ueber den Titel, nicht ueber die Person."""
    film = _erster_film(arr_client)
    zweiter = create_user(arr_client, "admin2", role=Role.admin)

    with SessionLocal() as session:
        eintrag = Blocked(
            media_type=MediaType.movie,
            tmdb_id=film["tmdb_id"],
            title=film["title"],
            blocked_by=zweiter["id"],
        )
        session.add(eintrag)
        session.commit()

    assert arr_client.delete(f"/api/users/{zweiter['id']}").status_code == 204

    with SessionLocal() as session:
        uebrig = session.query(Blocked).all()
        assert len(uebrig) == 1
        assert uebrig[0].blocked_by is None
        assert session.query(User).filter(User.username == "admin2").one_or_none() is None
