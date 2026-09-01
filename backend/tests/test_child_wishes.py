"""Vom Wunsch des Kindes zur Anfrage der Eltern.

Der wichtigste Test ist ``test_freigabe_wird_anfrage_des_elternteils``: Genau
darauf ruht der ganze Aufbau. Gehoert die Anfrage den Eltern, brauchen
Kontingent, Speicher, Kontoaufloesung und Admin-Sicht keinen Sonderfall - und
alle bestehenden Pruefungen greifen von selbst, weil es dieselbe Funktion ist
wie bei einem Klick auf "Anfragen".
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import ChildWish, MediaRequest, RequestStatus, User, WishState
from app.services import requests_service

from .conftest import auth_headers, create_user


def _familie(client: TestClient) -> tuple[dict[str, str], dict[str, str], int]:
    """Elternteil + Kind, beide angemeldet. Gibt (eltern, kind, kind_id)."""
    create_user(client, "elternteil", "eltern-passwort", can_manage_children=True)
    eltern = auth_headers(client, "elternteil", "eltern-passwort")
    kind = client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 16},
        headers=eltern,
    ).json()
    return eltern, auth_headers(client, "kind", "kind-passwort"), kind["id"]


def _erster_titel(client: TestClient, kopf: dict[str, str]) -> dict:
    """Irgendein Titel, den dieses Kind sehen darf."""
    kategorien = client.get("/api/kids/categories?media_type=movie", headers=kopf).json()
    assert kategorien, "Die Kinder-Startseite ist leer - dann testet hier nichts."
    rubrik = kategorien[0]["rubrik"]
    seite = client.get(f"/api/kids/rubrik/{rubrik}?media_type=movie", headers=kopf).json()
    assert seite["wuenschbar"], "Nichts zu wuenschen - dann testet hier nichts."
    return seite["wuenschbar"][0]


def test_startseite_zeigt_nur_freigeschaltete_rubriken(arr_client: TestClient) -> None:
    eltern, kind_kopf, kind_id = _familie(arr_client)

    # Nur Animation: alles andere muss verschwinden.
    arr_client.patch(f"/api/children/{kind_id}", json={"genres": ["animation"]}, headers=eltern)

    kategorien = arr_client.get("/api/kids/categories?media_type=movie", headers=kind_kopf).json()
    assert [eintrag["rubrik"] for eintrag in kategorien] == ["animation"]
    # Jede Kachel traegt Bilder - ohne waere sie fuer ein Kind nur ein Wort.
    assert kategorien[0]["bilder"]


def test_fremde_rubrik_ist_404(arr_client: TestClient) -> None:
    """Ueber die Adresszeile darf keine gesperrte Rubrik aufgehen."""
    eltern, kind_kopf, kind_id = _familie(arr_client)
    arr_client.patch(f"/api/children/{kind_id}", json={"genres": ["animation"]}, headers=eltern)

    antwort = arr_client.get("/api/kids/rubrik/comedy?media_type=movie", headers=kind_kopf)
    assert antwort.status_code == 404


def test_angefragte_titel_fallen_ganz_weg(arr_client: TestClient) -> None:
    """Was laeuft, aber noch nicht da ist, gehoert in keinen der zwei Bereiche.

    Wuenschen waere sinnlos - die Freigabe scheiterte spaeter mit "wurde
    bereits angefragt" - und "das kannst du schon schauen" waere gelogen.
    """
    eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)

    create_user(arr_client, "nachbar", "nachbar-passwort")
    nachbar = auth_headers(arr_client, "nachbar", "nachbar-passwort")
    assert (
        arr_client.post(
            "/api/requests",
            json={
                "media_type": "movie",
                "tmdb_id": titel["tmdb_id"],
                "quality_profile_id": 1,
                "root_folder_path": "/data/Movies",
            },
            headers=nachbar,
        ).status_code
        == 201
    )

    kategorien = arr_client.get("/api/kids/categories?media_type=movie", headers=kind_kopf).json()
    for eintrag in kategorien:
        seite = arr_client.get(
            f"/api/kids/rubrik/{eintrag['rubrik']}?media_type=movie", headers=kind_kopf
        ).json()
        alle = seite["verfuegbar"] + seite["wuenschbar"]
        assert all(t["tmdb_id"] != titel["tmdb_id"] for t in alle), eintrag["rubrik"]


def test_vorhandene_titel_stehen_im_eigenen_bereich(arr_client: TestClient) -> None:
    """Was schon da ist, wird **gezeigt** - nur eben getrennt.

    Es zu verbergen war der erste Bauversuch. Gemessen an einer gut gefuellten
    Mediathek verschwanden damit 90 % der Kinderseite, und zwar genau das, was
    das Kind sofort schauen koennte.
    """
    eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)

    # Die Attrappe in conftest meldet alles als in der Bibliothek, was eine
    # erledigte Anfrage hat.
    create_user(arr_client, "nachbar", "nachbar-passwort")
    nachbar = auth_headers(arr_client, "nachbar", "nachbar-passwort")
    arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": titel["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=nachbar,
    )
    with SessionLocal() as sitzung:
        anfrage = (
            sitzung.query(MediaRequest)
            .filter(MediaRequest.tmdb_id == titel["tmdb_id"])
            .one()
        )
        anfrage.status = RequestStatus.downloaded
        sitzung.commit()

    gefunden = False
    kategorien = arr_client.get("/api/kids/categories?media_type=movie", headers=kind_kopf).json()
    for eintrag in kategorien:
        seite = arr_client.get(
            f"/api/kids/rubrik/{eintrag['rubrik']}?media_type=movie", headers=kind_kopf
        ).json()
        assert all(t["tmdb_id"] != titel["tmdb_id"] for t in seite["wuenschbar"])
        if any(t["tmdb_id"] == titel["tmdb_id"] for t in seite["verfuegbar"]):
            gefunden = True
    assert gefunden, "Der vorhandene Titel steht in keinem Bereich."


def test_trailer_laesst_sich_abschalten(arr_client: TestClient) -> None:
    eltern, kind_kopf, kind_id = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)

    arr_client.patch(
        f"/api/children/{kind_id}", json={"child_trailers": False}, headers=eltern
    )
    seite = arr_client.get(f"/api/kids/title/movie/{titel['tmdb_id']}", headers=kind_kopf)
    assert seite.status_code == 200
    assert seite.json()["trailer"] is None


def test_eltern_sehen_die_ansicht_ihres_kindes(arr_client: TestClient) -> None:
    """"Was wuerde mein Kind sehen" - dieselben Daten, nur vom Elternkonto."""
    eltern, kind_kopf, kind_id = _familie(arr_client)
    arr_client.patch(f"/api/children/{kind_id}", json={"genres": ["animation"]}, headers=eltern)

    vorschau = arr_client.get(
        f"/api/children/{kind_id}/preview/categories?media_type=movie", headers=eltern
    ).json()
    kind_sicht = arr_client.get(
        "/api/kids/categories?media_type=movie", headers=kind_kopf
    ).json()
    assert [e["rubrik"] for e in vorschau] == [e["rubrik"] for e in kind_sicht]

    # Ein fremdes Kind gibt es fuer dieses Konto nicht.
    create_user(arr_client, "fremder", "fremd-passwort", can_manage_children=True)
    fremd = auth_headers(arr_client, "fremder", "fremd-passwort")
    assert (
        arr_client.get(
            f"/api/children/{kind_id}/preview/categories", headers=fremd
        ).status_code
        == 404
    )


def test_suche_bleibt_in_den_rubriken(arr_client: TestClient) -> None:
    """Vom Nutzer so entschieden - und die Sperre sitzt im Server."""
    eltern, kind_kopf, kind_id = _familie(arr_client)
    arr_client.patch(f"/api/children/{kind_id}", json={"genres": ["animation"]}, headers=eltern)

    # Ein Titel, den es gibt, der aber in keiner Animations-Rubrik liegt.
    fremd = arr_client.get("/api/discover/movie", headers=eltern).json()["items"]
    ausserhalb = next(t for t in fremd if "Animation" not in t["genres"])

    treffer = arr_client.get(
        f"/api/kids/search?media_type=movie&q={ausserhalb['title'][:8]}", headers=kind_kopf
    ).json()
    alle = treffer["verfuegbar"] + treffer["wuenschbar"]
    assert all(t["tmdb_id"] != ausserhalb["tmdb_id"] for t in alle)


def test_titelseite_lehnt_fremde_rubrik_ab(arr_client: TestClient) -> None:
    """Sonst liesse sich ueber die Adresszeile jeder Titel oeffnen."""
    eltern, kind_kopf, kind_id = _familie(arr_client)
    arr_client.patch(f"/api/children/{kind_id}", json={"genres": ["animation"]}, headers=eltern)

    fremd = arr_client.get("/api/discover/movie", headers=eltern).json()["items"]
    ausserhalb = next(t for t in fremd if "Animation" not in t["genres"])

    antwort = arr_client.get(
        f"/api/kids/title/movie/{ausserhalb['tmdb_id']}", headers=kind_kopf
    )
    assert antwort.status_code == 404


def test_wuenschen_und_eigene_liste(arr_client: TestClient) -> None:
    _eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)

    antwort = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["state"] == "waiting"

    liste = arr_client.get("/api/kids/wishes", headers=kind_kopf).json()
    assert [w["tmdb_id"] for w in liste] == [titel["tmdb_id"]]

    # Zweimal dasselbe gibt keine zweite Zeile.
    assert (
        arr_client.post(
            "/api/kids/wishes",
            json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
            headers=kind_kopf,
        ).status_code
        == 409
    )


def test_wunsch_landet_beim_elternteil(arr_client: TestClient) -> None:
    eltern, kind_kopf, kind_id = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    )

    offen = arr_client.get("/api/children/wishes", headers=eltern).json()
    assert len(offen) == 1
    assert offen[0]["child_id"] == kind_id
    assert offen[0]["title"] == titel["title"]


def test_freigabe_wird_anfrage_des_elternteils(arr_client: TestClient) -> None:
    """Der Kern des Aufbaus: die Anfrage gehoert dem Elternteil."""
    eltern, kind_kopf, kind_id = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    ).json()

    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch['id']}/release",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=eltern,
    )
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as sitzung:
        elternteil = sitzung.query(User).filter(User.username == "elternteil").one()
        anfrage = sitzung.query(MediaRequest).one()
        assert anfrage.user_id == elternteil.id
        # Nur zur Anzeige - an den Rechten aendert es nichts.
        assert anfrage.for_child_id == kind_id

        gespeichert = sitzung.query(ChildWish).one()
        assert gespeichert.state == WishState.released
        assert gespeichert.request_id == anfrage.id

    # Und das Kind sieht seinen Titel jetzt als unterwegs.
    liste = arr_client.get("/api/kids/wishes", headers=kind_kopf).json()
    assert liste[0]["state"] == "coming"


def test_kontingent_des_elternteils_greift(arr_client: TestClient) -> None:
    """Kein zweiter Regelsatz - es ist dieselbe Pruefung wie sonst auch."""
    eltern, kind_kopf, _ = _familie(arr_client)

    with SessionLocal() as sitzung:
        elternteil = sitzung.query(User).filter(User.username == "elternteil").one()
        elternteil.quota_movies_limit = 0
        sitzung.commit()

    titel = _erster_titel(arr_client, kind_kopf)
    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    ).json()

    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch['id']}/release",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=eltern,
    )
    assert antwort.status_code == 429 or antwort.status_code >= 400

    # Der Wunsch bleibt offen - sonst waere er verloren, ohne dass etwas
    # passiert ist.
    with SessionLocal() as sitzung:
        assert sitzung.query(ChildWish).one().state == WishState.open


def test_ablehnen_mit_begruendung(arr_client: TestClient) -> None:
    eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    ).json()

    assert (
        arr_client.post(
            f"/api/children/wishes/{wunsch['id']}/decline",
            json={"note": "Das ist noch nichts für dich."},
            headers=eltern,
        ).status_code
        == 204
    )

    liste = arr_client.get("/api/kids/wishes", headers=kind_kopf).json()
    assert liste[0]["state"] == "declined"
    assert liste[0]["decline_note"] == "Das ist noch nichts für dich."

    # Zweimal entscheiden geht nicht.
    assert (
        arr_client.post(
            f"/api/children/wishes/{wunsch['id']}/decline", json={}, headers=eltern
        ).status_code
        == 409
    )


def test_fremder_wunsch_ist_404(arr_client: TestClient) -> None:
    eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    ).json()

    create_user(arr_client, "fremder", "fremd-passwort", can_manage_children=True)
    fremd = auth_headers(arr_client, "fremder", "fremd-passwort")

    assert (
        arr_client.post(
            f"/api/children/wishes/{wunsch['id']}/decline", json={}, headers=fremd
        ).status_code
        == 404
    )
    assert arr_client.get("/api/children/wishes", headers=fremd).json() == []


def test_anfrage_eines_anderen_erledigt_den_wunsch(arr_client: TestClient) -> None:
    """Holt jemand den Titel selbst, hat sich der Wunsch erledigt.

    Und zwar als ``obsolete``, **nicht** als Absage - das Kind hat ja bekommen,
    was es wollte.
    """
    eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    )

    # Jemand ganz anderes fragt denselben Titel an. Bewusst kein Administrator:
    # der haette Auto-Freigabe, und die Anfrage liefe sofort in das
    # absichtlich unerreichbare Radarr.
    create_user(arr_client, "nachbar", "nachbar-passwort")
    nachbar = auth_headers(arr_client, "nachbar", "nachbar-passwort")
    assert (
        arr_client.post(
            "/api/requests",
            json={
                "media_type": "movie",
                "tmdb_id": titel["tmdb_id"],
                "quality_profile_id": 1,
                "root_folder_path": "/data/Movies",
            },
            headers=nachbar,
        ).status_code
        == 201
    )

    with SessionLocal() as sitzung:
        assert sitzung.query(ChildWish).one().state == WishState.obsolete

    assert arr_client.get("/api/children/wishes", headers=eltern).json() == []
    # Fuer das Kind ist der Titel damit da - keine Absage.
    assert arr_client.get("/api/kids/wishes", headers=kind_kopf).json()[0]["state"] == "available"


def test_kind_loeschen_nimmt_die_wuensche_mit(arr_client: TestClient) -> None:
    eltern, kind_kopf, kind_id = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    )

    assert arr_client.delete(f"/api/children/{kind_id}", headers=eltern).status_code == 204

    with SessionLocal() as sitzung:
        assert sitzung.query(ChildWish).count() == 0


# --- Eltern dosieren: Staffel und Folgen bei der Freigabe ---------------------


def _erste_serie(client: TestClient, kopf: dict[str, str]) -> dict:
    """Irgendeine Serie, die dieses Kind sehen darf."""
    kategorien = client.get("/api/kids/categories?media_type=tv", headers=kopf).json()
    assert kategorien, "Die Kinder-Serienseite ist leer - dann testet hier nichts."
    rubrik = kategorien[0]["rubrik"]
    seite = client.get(f"/api/kids/rubrik/{rubrik}?media_type=tv", headers=kopf).json()
    assert seite["wuenschbar"], "Keine Serie zu wuenschen - dann testet hier nichts."
    return seite["wuenschbar"][0]


def _serienwunsch(client: TestClient, kind_kopf: dict[str, str], eltern: dict[str, str]) -> tuple[dict, int]:
    """Das Kind wuenscht eine Serie; gibt (serie, wunsch_id aus Elternsicht)."""
    serie = _erste_serie(client, kind_kopf)
    antwort = client.post(
        "/api/kids/wishes",
        json={"media_type": "tv", "tmdb_id": serie["tmdb_id"]},
        headers=kind_kopf,
    )
    assert antwort.status_code in (200, 201), antwort.text
    wuensche = client.get("/api/children/wishes", headers=eltern).json()
    passend = [w for w in wuensche if w["tmdb_id"] == serie["tmdb_id"]]
    assert passend, "Der Wunsch kam nicht bei den Eltern an."
    return serie, passend[-1]["id"]


def test_freigabe_mit_folgen_wird_paket(arr_client: TestClient) -> None:
    """Eltern koennen dosieren: erst zwei Folgen zum Antesten."""
    eltern, kind_kopf, kind_id = _familie(arr_client)
    serie, wunsch_id = _serienwunsch(arr_client, kind_kopf, eltern)

    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch_id}/release",
        json={"quality_profile_id": 1, "season": 2, "episodes": [7, 3]},
        headers=eltern,
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["season"] == 2
    assert antwort.json()["episodes"] == [3, 7]

    with SessionLocal() as sitzung:
        anfrage = sitzung.get(MediaRequest, antwort.json()["id"])
        assert anfrage is not None
        assert anfrage.for_child_id == kind_id
        wunsch = sitzung.get(ChildWish, wunsch_id)
        assert wunsch is not None and wunsch.state == WishState.released


def test_teilfreigabe_laesst_fremde_wuensche_offen(arr_client: TestClient) -> None:
    """Zwei Folgen fuer das eine Kind heisst nicht "ist da" fuer das andere.

    Frueher schloss **jede** Anfrage zum Titel alle offenen Wuensche - beim
    Geschwisterkind stand dann "ist da", weil ein Elternteil zwei Folgen zum
    Antesten geholt hat. Jetzt erledigt nur die volle Abdeckung fremde
    Wuensche.
    """
    eltern, kind_kopf, _ = _familie(arr_client)
    kind2 = arr_client.post(
        "/api/children",
        json={"username": "kind2", "password": "kind2-passwort", "age": 16},
        headers=eltern,
    ).json()
    assert kind2.get("id"), kind2
    kind2_kopf = auth_headers(arr_client, "kind2", "kind2-passwort")

    serie, wunsch_id = _serienwunsch(arr_client, kind_kopf, eltern)
    antwort = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "tv", "tmdb_id": serie["tmdb_id"]},
        headers=kind2_kopf,
    )
    assert antwort.status_code in (200, 201), antwort.text

    freigabe = arr_client.post(
        f"/api/children/wishes/{wunsch_id}/release",
        json={"quality_profile_id": 1, "season": 2, "episodes": [1, 2]},
        headers=eltern,
    )
    assert freigabe.status_code == 200, freigabe.text

    with SessionLocal() as sitzung:
        zustaende = {
            wunsch.child_id: wunsch.state
            for wunsch in sitzung.query(ChildWish).all()
        }
    # Kind 1: entschieden. Kind 2: wartet weiter auf eine echte Entscheidung.
    assert zustaende[kind2["id"]] == WishState.open

    # Und die Eltern koennen den zweiten Wunsch eigenstaendig dosieren -
    # Staffel 3 neben Staffel-2-Folgen ist erlaubt.
    wuensche = arr_client.get("/api/children/wishes", headers=eltern).json()
    zweiter = [w for w in wuensche if w["tmdb_id"] == serie["tmdb_id"]]
    assert len(zweiter) == 1
    zweite_freigabe = arr_client.post(
        f"/api/children/wishes/{zweiter[0]['id']}/release",
        json={"quality_profile_id": 1, "season": 3},
        headers=eltern,
    )
    assert zweite_freigabe.status_code == 200, zweite_freigabe.text
    assert zweite_freigabe.json()["season"] == 3
    assert zweite_freigabe.json()["episodes"] is None


def test_kind_darf_nach_der_freigabe_erneut_wuenschen(arr_client: TestClient) -> None:
    """Timos Nachschub: "Ich moechte mehr davon" ist ein neuer Wunsch."""
    eltern, kind_kopf, _ = _familie(arr_client)
    serie, wunsch_id = _serienwunsch(arr_client, kind_kopf, eltern)

    assert (
        arr_client.post(
            f"/api/children/wishes/{wunsch_id}/release",
            json={"quality_profile_id": 1, "season": 2, "episodes": [1]},
            headers=eltern,
        ).status_code
        == 200
    )

    nochmal = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "tv", "tmdb_id": serie["tmdb_id"]},
        headers=kind_kopf,
    )
    assert nochmal.status_code in (200, 201), nochmal.text

    with SessionLocal() as sitzung:
        offene = (
            sitzung.query(ChildWish)
            .filter(ChildWish.state == WishState.open)
            .count()
        )
    assert offene == 1


def test_kind_bekommt_keine_technik_zu_lesen(
    arr_client: TestClient, monkeypatch
) -> None:
    """⚠️ **Ein Achtjaehriger liest nicht "Der TMDB API-Key wurde nicht akzeptiert".**

    Der Kinder-Router reichte den Fehlertext von TMDB unveraendert durch. Auf
    der Startseite der Kinderansicht stand er dem Kind sofort beim Oeffnen da,
    ohne dass es etwas getan hatte - ein Satz ueber einen Dienst, von dem es
    noch nie gehoert hat, und ueber ein Problem, das es nicht loesen kann.

    Der technische Grund gehoert ins Protokoll, nicht auf seinen Bildschirm.
    """
    from app.routers import kids as kids_router
    from app.services.tmdb import TmdbError

    _eltern, kind, _kennung = _familie(arr_client)

    async def kaputt(*_args, **_kwargs):
        raise TmdbError("Der TMDB API-Key wurde nicht akzeptiert.", status_code=401)

    monkeypatch.setattr(kids_router.kids, "kategorien", kaputt)

    antwort = arr_client.get("/api/kids/categories?media_type=movie", headers=kind)

    assert antwort.status_code == 502
    detail = antwort.json()["detail"]
    assert detail["code"] == "kids_service_down"
    # Kein Dienstname, keine Abkuerzung, kein Fehlercode.
    assert "TMDB" not in detail["message"]
    assert "API" not in detail["message"]


# ---------------------------------------------------------------------------
# Der Titel ist schon da
# ---------------------------------------------------------------------------
#
# Gemeldet aus dem laufenden Betrieb: Ein Film kam ueber eine zweite Instanz
# ins Haus, waehrend der Wunsch noch zur Freigabe lag. Die Freigabe scheiterte
# danach bei jedem Versuch - der Grund konnte sich ja nicht mehr aendern -,
# und uebrig blieb nur "Ablehnen". Also ausgerechnet die Antwort, die dem Kind
# das Gegenteil der Wahrheit sagt: Es hat ja bekommen, was es wollte.


def _offener_filmwunsch(client: TestClient) -> tuple[dict[str, str], int]:
    """Elternteil + ein offener Filmwunsch des Kindes. Gibt (eltern, wunsch_id)."""
    eltern, kind_kopf, _ = _familie(client)
    titel = _erster_titel(client, kind_kopf)
    wunsch = client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    ).json()
    return eltern, wunsch["id"]


def test_wunsch_ist_erledigt_wenn_der_titel_schon_da_ist(
    arr_client: TestClient, monkeypatch
) -> None:
    """Nicht abgelehnt, sondern hinfaellig - und nicht mehr in der Liste."""
    eltern, wunsch_id = _offener_filmwunsch(arr_client)

    async def liegt_schon_da(*_args, **_kwargs):
        raise requests_service.RequestError(
            "„Elio“ ist bereits in deiner Bibliothek.",
            409,
            code="already_in_library",
        )

    monkeypatch.setattr(requests_service, "create_request", liegt_schon_da)

    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch_id}/release",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=eltern,
    )
    assert antwort.status_code == 409, antwort.text
    # Mit Kennung, damit die Oberflaeche den Satz in ihrer Sprache bauen kann -
    # sonst laese ein englischer Nutzer hier Deutsch.
    detail = antwort.json()["detail"]
    assert detail["code"] == "wish_already_available"
    assert detail["titel"]

    with SessionLocal() as sitzung:
        wunsch = sitzung.get(ChildWish, wunsch_id)
        assert wunsch is not None
        # ``obsolete`` und ausdruecklich nicht ``declined``.
        assert wunsch.state == WishState.obsolete
        assert wunsch.decided_at is not None

    # Und damit weg aus der Liste, die auf eine Entscheidung wartet.
    offen = arr_client.get("/api/children/wishes", headers=eltern).json()
    assert offen == []


def test_andere_fehler_lassen_den_wunsch_offen(
    arr_client: TestClient, monkeypatch
) -> None:
    """Ein volles Kontingent geht vorueber - der Wunsch muss das ueberleben.

    Ohne diese Grenze haette die Reparatur oben aus jedem Fehlschlag ein
    "erledigt" gemacht, und ein Kind verloere seinen Wunsch, weil sein
    Elternteil gerade keinen Platz hat.
    """
    eltern, wunsch_id = _offener_filmwunsch(arr_client)

    async def kontingent_voll(*_args, **_kwargs):
        raise requests_service.RequestError("Dein Kontingent ist aufgebraucht.", 409)

    monkeypatch.setattr(requests_service, "create_request", kontingent_voll)

    antwort = arr_client.post(
        f"/api/children/wishes/{wunsch_id}/release",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=eltern,
    )
    assert antwort.status_code == 409, antwort.text

    with SessionLocal() as sitzung:
        wunsch = sitzung.get(ChildWish, wunsch_id)
        assert wunsch is not None
        assert wunsch.state == WishState.open

    offen = arr_client.get("/api/children/wishes", headers=eltern).json()
    assert len(offen) == 1


# ---------------------------------------------------------------------------
# Ein geloeschtes Kind hinterlaesst nichts
# ---------------------------------------------------------------------------


def test_geloeschtes_kinderkonto_nimmt_seine_wuensche_mit(arr_client: TestClient) -> None:
    """Auch ueber die Nutzerverwaltung, nicht nur ueber die Elternansicht.

    ``DELETE /api/users/{id}`` raeumte bisher nur die Kinder *unterhalb* eines
    Kontos ab. War das Konto selbst ein Kind, blieb seine Wunsch-Zeile mit
    einer toten ``child_id`` stehen - und die Wunschliste des Elternteils
    stuerzte daran ab, weil sie den Namen des Kindes liest.
    """
    eltern, kind_kopf, kind_id = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    )
    with SessionLocal() as sitzung:
        assert sitzung.query(ChildWish).count() == 1

    weg = arr_client.delete(f"/api/users/{kind_id}")
    assert weg.status_code == 204, weg.text

    with SessionLocal() as sitzung:
        assert sitzung.query(ChildWish).count() == 0

    # Der eigentliche Beweis: Die Liste des Elternteils antwortet noch.
    offen = arr_client.get("/api/children/wishes", headers=eltern)
    assert offen.status_code == 200, offen.text
    assert offen.json() == []


def test_altlasten_werden_beim_start_abgeraeumt(arr_client: TestClient) -> None:
    """Fuer Datenbanken, die den luecklichen Weg schon genommen haben.

    Auch ein eingespielter Stand aus einer fremden Installation bringt solche
    Zeilen mit - dort hatten die Konten andere Nummern.
    """
    from app.db import _verwaiste_kinderwuensche_aufraeumen, engine

    eltern, kind_kopf, kind_id = _familie(arr_client)
    with SessionLocal() as sitzung:
        elternteil = sitzung.query(User).filter(User.username == "elternteil").one()
        eltern_id = elternteil.id

    # ⚠️ **Die Zeile muss an der Fremdschluessel-Regel vorbei entstehen.** In
    # einer frisch angelegten Datenbank traegt ``child_wishes.child_id`` sie,
    # und dann ist eine Waise gar nicht erst moeglich. Sie entsteht nur dort,
    # wo die Tabelle **nachgetragen** wurde - SQLite kann einer per ALTER TABLE
    # ergaenzten Spalte keine Regel geben. Genau diese Datenbanken sind
    # gemeint, und genau so muss der Test sie nachstellen.
    with engine.connect() as verbindung:
        verbindung.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            verbindung.exec_driver_sql(
                "INSERT INTO child_wishes "
                "(child_id, parent_id, media_type, tmdb_id, title, state, created_at) "
                "VALUES (999999, ?, 'movie', 42, 'Verwaister Wunsch', 'open', "
                "'2026-01-01 00:00:00')",
                (eltern_id,),
            )
            verbindung.commit()
        finally:
            # ⚠️ **Wieder anschalten, bevor die Verbindung in den Vorrat
            # zurueckgeht.** ``PRAGMA foreign_keys`` haengt an der Verbindung,
            # nicht an diesem ``with``, und der Vorrat reicht dieselbe
            # Verbindung an den naechsten Test weiter. Ohne diese Zeile lief
            # hier eine Verbindung mit abgeschalteten Fremdschluesseln davon,
            # und in einem Ausschnitt der Reihe fiel danach
            # ``tests/test_tickets.py`` um, weil das Loeschen eines Kontos
            # seine Tickets nicht mehr mitnahm: eine ganz andere Datei, ohne
            # jeden Bezug, und nur je nach Reihenfolge.
            #
            # Der Rollback davor ist kein Ritual: SQLite **uebergeht** ein
            # ``PRAGMA foreign_keys`` stillschweigend, solange ein Vorgang
            # offen ist. Scheitert das INSERT, waere die Zeile darunter sonst
            # wirkungslos, ohne zu murren.
            #
            # Der zweite Riegel steht in ``app/db.py`` im Ereignis
            # ``checkout``; der faengt auch die naechste solche Stelle ab.
            # Diese hier bleibt trotzdem: Wer ein PRAGMA umlegt, raeumt es
            # selbst wieder weg.
            verbindung.rollback()
            verbindung.exec_driver_sql("PRAGMA foreign_keys=ON")

    _verwaiste_kinderwuensche_aufraeumen()

    with SessionLocal() as sitzung:
        assert sitzung.query(ChildWish).count() == 0

    offen = arr_client.get("/api/children/wishes", headers=eltern)
    assert offen.status_code == 200, offen.text


def test_freigabeliste_nennt_das_kind(arr_client: TestClient) -> None:
    """"Aus einem Wunsch von Lena" - sonst wundert sich der Entscheider.

    Das Feld ``for_child_id`` wurde seit jeher gesetzt und nirgends gelesen;
    die im Modell beschriebene Anzeige gab es nicht.
    """
    eltern, kind_kopf, _ = _familie(arr_client)
    titel = _erster_titel(arr_client, kind_kopf)
    wunsch = arr_client.post(
        "/api/kids/wishes",
        json={"media_type": "movie", "tmdb_id": titel["tmdb_id"]},
        headers=kind_kopf,
    ).json()
    arr_client.post(
        f"/api/children/wishes/{wunsch['id']}/release",
        json={"quality_profile_id": 1, "root_folder_path": "/data/Movies"},
        headers=eltern,
    )

    zeilen = arr_client.get("/api/admin/requests").json()
    assert len(zeilen) == 1
    # Der Anzeigename des Kindes, nicht seine Nummer.
    assert zeilen[0]["for_child_name"] == "kind"
    # Die Anfrage gehoert weiterhin dem Elternteil.
    assert zeilen[0]["username"] == "elternteil"


def test_ohne_kinderwunsch_steht_dort_nichts(arr_client: TestClient) -> None:
    """Eine gewoehnliche Anfrage traegt keinen Kindernamen."""
    create_user(arr_client, "allein", "allein-passwort")
    kopf = auth_headers(arr_client, "allein", "allein-passwort")
    item = arr_client.get("/api/discover/movie", headers=kopf).json()["items"][0]
    arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=kopf,
    )

    zeilen = arr_client.get("/api/admin/requests").json()
    assert len(zeilen) == 1
    assert zeilen[0]["for_child_name"] is None
