"""Hausordnung: lesen, quittieren, verwalten - ueber die Pfade.

Die drei Zutrittsregeln stehen hier auf dem Pruefstand: Der Entwurf gehoert
dem Betreiber, das Veroeffentlichte allen Erwachsenen, die Verwaltung den
Administratoren. Und die Fassung steigt nur, wenn jemand es ausdruecklich
will.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.models import Role, User

from .conftest import auth_headers, create_user

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def leerer_bilderordner():
    ordner = get_settings().data_dir / "hausordnung"
    shutil.rmtree(ordner, ignore_errors=True)
    yield
    shutil.rmtree(ordner, ignore_errors=True)


def _speichern(client: TestClient, **felder):
    daten = {
        "titel": "Bei uns zu Hause",
        "inhalt": "## Regeln\n\nBitte lesen.",
        "quittierbar": True,
        "veroeffentlicht": True,
        "erneut_lesen": False,
    }
    daten.update(felder)
    return client.put("/api/hausordnung/verwaltung", json=daten)


def _leser(client: TestClient, name: str = "leser") -> dict[str, str]:
    create_user(client, name)
    return auth_headers(client, name, "passwort-1234")


def test_ohne_hausordnung_gibt_es_nichts_zu_lesen(admin_client: TestClient) -> None:
    assert admin_client.get("/api/hausordnung").status_code == 404


def test_der_entwurf_bleibt_beim_betreiber(admin_client: TestClient) -> None:
    """⚠️ Sonst schreibt der Betreiber vor Publikum.

    Eine halbfertige Hausordnung waere ab dem ersten Speichern fuer alle
    sichtbar - und die erste Fassung eines Regeltextes ist selten die, die
    man gelesen haben moechte.
    """
    assert _speichern(admin_client, veroeffentlicht=False).status_code == 200

    leser = _leser(admin_client)
    assert admin_client.get("/api/hausordnung", headers=leser).status_code == 404

    # Der Administrator sieht sie in der Verwaltung sehr wohl.
    stand = admin_client.get("/api/hausordnung/verwaltung").json()
    assert stand["inhalt"].startswith("## Regeln")
    assert stand["veroeffentlicht"] is False


def test_veroeffentlicht_ist_fuer_alle_lesbar(admin_client: TestClient) -> None:
    _speichern(admin_client)
    leser = _leser(admin_client)

    antwort = admin_client.get("/api/hausordnung", headers=leser)
    assert antwort.status_code == 200
    assert antwort.json()["titel"] == "Bei uns zu Hause"
    assert antwort.json()["gelesen"] is None


def test_leere_hausordnung_laesst_sich_nicht_veroeffentlichen(admin_client: TestClient) -> None:
    antwort = _speichern(admin_client, inhalt="   ", veroeffentlicht=True)
    assert antwort.status_code == 400
    assert antwort.json()["detail"]["code"] == "hausordnung_empty"


def test_quittieren_haelt_fassung_und_zeitpunkt_fest(admin_client: TestClient) -> None:
    _speichern(admin_client)
    leser = _leser(admin_client)

    assert admin_client.post("/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=leser).status_code == 204

    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter(User.username == "leser").one()
        assert konto.hausordnung_gelesen == 1
        assert konto.hausordnung_gelesen_am is not None

    assert admin_client.get("/api/hausordnung", headers=leser).json()["gelesen"] == 1


def test_nicht_quittierbare_hausordnung_weist_das_abhaken_ab(admin_client: TestClient) -> None:
    _speichern(admin_client, quittierbar=False)
    leser = _leser(admin_client)

    assert admin_client.post("/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=leser).status_code == 409


def test_die_fassung_steigt_nur_auf_anweisung(admin_client: TestClient) -> None:
    """⚠️ Der Kern der Entscheidung.

    Ein berichtigter Tippfehler darf nicht bei allen den Hinweis erneut
    aufpoppen lassen - eine echte neue Regel schon.
    """
    _speichern(admin_client)
    assert admin_client.get("/api/hausordnung/verwaltung").json()["fassung"] == 1

    # Gewoehnliches Speichern laesst sie in Ruhe.
    _speichern(admin_client, inhalt="## Regeln\n\nBitte lesen!")
    assert admin_client.get("/api/hausordnung/verwaltung").json()["fassung"] == 1

    # Mit Haken steigt sie.
    _speichern(admin_client, erneut_lesen=True)
    assert admin_client.get("/api/hausordnung/verwaltung").json()["fassung"] == 2


def test_die_allererste_speicherung_vertraegt_den_haken(admin_client: TestClient) -> None:
    """⚠️ **Der Test, der gefehlt hat.**

    Alle anderen legten die Zeile erst ohne Haken an und setzten ihn danach.
    Genau der Weg, den ein Betreiber beim ersten Mal nimmt - Text schreiben,
    veroeffentlichen, "alle muessen erneut lesen" - war nie gepruft: Die
    Vorgabe ``fassung=1`` setzt SQLAlchemy erst beim Schreiben, davor steht
    dort ``None``, und ``None + 1`` ist ein 500er. Gespeichert wurde dabei
    gar nichts.
    """
    antwort = _speichern(admin_client, erneut_lesen=True)
    assert antwort.status_code == 200, antwort.text
    # Von 1 auf 2 - die Zeile entsteht mit 1 und der Haken zaehlt hoch.
    assert antwort.json()["fassung"] == 2
    assert admin_client.get("/api/hausordnung").json()["titel"] == "Bei uns zu Hause"


def test_eine_neue_fassung_holt_alle_zurueck(admin_client: TestClient) -> None:
    _speichern(admin_client)
    leser = _leser(admin_client)
    admin_client.post("/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=leser)

    _speichern(admin_client, erneut_lesen=True)

    stand = admin_client.get("/api/hausordnung", headers=leser).json()
    # Quittiert ist Fassung 1, aktuell ist 2 - also wieder ungelesen.
    assert stand["gelesen"] == 1
    assert stand["fassung"] == 2


def test_die_verwaltung_nennt_die_zahl_der_betroffenen(admin_client: TestClient) -> None:
    """Neben dem Haken soll stehen, was er ausloest - in Zahlen."""
    vorher = admin_client.get("/api/hausordnung/verwaltung").json()["betroffene_konten"]
    _leser(admin_client, "einer")
    _leser(admin_client, "zweiter")

    nachher = admin_client.get("/api/hausordnung/verwaltung").json()["betroffene_konten"]
    assert nachher == vorher + 2


def test_nur_administratoren_verwalten(admin_client: TestClient) -> None:
    leser = _leser(admin_client)

    assert admin_client.get("/api/hausordnung/verwaltung", headers=leser).status_code == 403
    assert (
        admin_client.put(
            "/api/hausordnung/verwaltung",
            json={"titel": "x", "inhalt": "y", "quittierbar": True, "veroeffentlicht": True},
            headers=leser,
        ).status_code
        == 403
    )
    assert admin_client.get("/api/hausordnung/bilder", headers=leser).status_code == 403


def test_kinderkonten_kommen_nicht_an_die_hausordnung(admin_client: TestClient) -> None:
    """Sie richtet sich an die, die anfragen und Speicher verbrauchen.

    Die Grenze steht im Server, nicht nur in der Oberflaeche - der
    Kinder-Rahmen hat den Knopf ohnehin nicht.
    """
    _speichern(admin_client)
    create_user(admin_client, "elternteil", can_manage_children=True)
    eltern = auth_headers(admin_client, "elternteil", "passwort-1234")
    admin_client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 12},
        headers=eltern,
    )
    kind = auth_headers(admin_client, "kind", "kind-passwort")

    assert admin_client.get("/api/hausordnung", headers=kind).status_code == 403
    assert admin_client.post("/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=kind).status_code == 403


def test_bilder_hochladen_und_wieder_loeschen(admin_client: TestClient) -> None:
    antwort = admin_client.post(
        "/api/hausordnung/bilder",
        files={"datei": ("regel.png", PNG, "image/png")},
    )
    assert antwort.status_code == 201, antwort.text
    name = antwort.json()["name"]

    assert [b["name"] for b in admin_client.get("/api/hausordnung/bilder").json()] == [name]

    _speichern(admin_client)
    bild = admin_client.get(f"/api/hausordnung/bild/{name}")
    assert bild.status_code == 200
    assert bild.headers["content-type"] == "image/png"
    assert bild.headers["x-content-type-options"] == "nosniff"

    assert admin_client.delete(f"/api/hausordnung/bilder/{name}").status_code == 204
    assert admin_client.get("/api/hausordnung/bilder").json() == []


def test_das_bild_kommt_ohne_anmeldung_heraus(admin_client: TestClient) -> None:
    """⚠️ **Der Test, der gefehlt hat.**

    Ein ``<img>``-Element schickt keinen ``Authorization``-Header, und das
    Sitzungs-Cookie gilt nur unter ``/api/auth``. Ein Endpunkt, der hier eine
    Anmeldung verlangt, liefert im Browser also **nie** ein Bild - gemeldet
    wurde genau das: ein zerbrochenes Bild im Editor und ein Platzhalter in
    der Vorschau.

    Alle bisherigen Tests liefen mit angemeldetem Client und merkten davon
    nichts. Dieser hier stellt die Anfrage so, wie ein Browser sie stellt.
    """
    name = admin_client.post(
        "/api/hausordnung/bilder",
        files={"datei": ("regel.png", PNG, "image/png")},
    ).json()["name"]
    _speichern(admin_client)

    # `headers={}` allein genuegt nicht - der Client traegt den Zugang bei
    # sich. Deshalb ein eigener, der nichts mitbringt.
    from fastapi.testclient import TestClient as FrischerClient

    fremd = FrischerClient(admin_client.app)
    antwort = fremd.get(f"/api/hausordnung/bild/{name}")
    assert antwort.status_code == 200, antwort.text
    assert antwort.headers["content-type"] == "image/png"


def test_der_bildname_ist_nicht_zu_erraten(admin_client: TestClient) -> None:
    """Der einzige Schutz, den das Bild noch hat - also muss er sitzen."""
    namen = {
        admin_client.post(
            "/api/hausordnung/bilder",
            files={"datei": ("regel.png", PNG, "image/png")},
        ).json()["name"]
        for _ in range(3)
    }
    assert len(namen) == 3
    for name in namen:
        # 32 Hexziffern = 128 Bit Zufall, wie bei den Profilbildern.
        assert len(name.split(".")[0]) == 32
        assert all(z in "0123456789abcdef" for z in name.split(".")[0])


def test_loeschen_raeumt_text_und_bilder_ab(admin_client: TestClient) -> None:
    admin_client.post(
        "/api/hausordnung/bilder",
        files={"datei": ("regel.png", PNG, "image/png")},
    )
    _speichern(admin_client)

    assert admin_client.delete("/api/hausordnung/verwaltung").status_code == 204

    assert admin_client.get("/api/hausordnung").status_code == 404
    assert admin_client.get("/api/hausordnung/bilder").json() == []


# ---------------------------------------------------------------------------
# Der Stand in /api/config - daran haengt der Knopf
# ---------------------------------------------------------------------------


def test_config_schweigt_ohne_hausordnung(admin_client: TestClient) -> None:
    """⚠️ Gefragt wird als **gewoehnliches Konto**.

    Als Administrator waere die Antwort immer "nichts vorhanden" - die sind
    seit der Ausnahme ohnehin aussen vor, und der Test bewiese nichts mehr.
    """
    leser = _leser(admin_client)
    stand = admin_client.get("/api/config", headers=leser).json()
    assert stand["hausordnung_vorhanden"] is False
    assert stand["hausordnung_gelesen"] is None


def test_config_traegt_den_stand_des_aufrufenden(admin_client: TestClient) -> None:
    """Der Punkt am Knopf haengt an **diesem** Konto, nicht an der Anlage."""
    _speichern(admin_client)
    leser = _leser(admin_client)

    stand = admin_client.get("/api/config", headers=leser).json()
    assert stand["hausordnung_vorhanden"] is True
    assert stand["hausordnung_titel"] == "Bei uns zu Hause"
    assert stand["hausordnung_fassung"] == 1
    assert stand["hausordnung_gelesen"] is None

    admin_client.post("/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=leser)
    danach = admin_client.get("/api/config", headers=leser).json()
    assert danach["hausordnung_gelesen"] == 1


def test_der_entwurf_taucht_im_config_nicht_auf(admin_client: TestClient) -> None:
    """Auch hier als gewoehnliches Konto - siehe den Test darueber."""
    _speichern(admin_client, veroeffentlicht=False)
    leser = _leser(admin_client)
    assert (
        admin_client.get("/api/config", headers=leser).json()["hausordnung_vorhanden"] is False
    )
    # Und mit Veroeffentlichung sieht dasselbe Konto sie sehr wohl - sonst
    # koennte die Zeile darueber auch aus einem ganz anderen Grund stimmen.
    _speichern(admin_client, veroeffentlicht=True)
    assert (
        admin_client.get("/api/config", headers=leser).json()["hausordnung_vorhanden"] is True
    )


def test_kinderkonten_sehen_im_config_keine_hausordnung(admin_client: TestClient) -> None:
    """⚠️ Die zweite Sperre neben dem Router.

    Der Kinder-Rahmen zeigt den Knopf ohnehin nicht - aber die Grenze soll
    nicht allein an der Oberflaeche haengen.
    """
    _speichern(admin_client)
    create_user(admin_client, "elternteil", can_manage_children=True)
    eltern = auth_headers(admin_client, "elternteil", "passwort-1234")
    admin_client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 12},
        headers=eltern,
    )
    kind = auth_headers(admin_client, "kind", "kind-passwort")

    assert admin_client.get("/api/config", headers=kind).json()["hausordnung_vorhanden"] is False
    # Das Elternteil sieht sie sehr wohl.
    assert admin_client.get("/api/config", headers=eltern).json()["hausordnung_vorhanden"] is True


# ---------------------------------------------------------------------------
# Die Uebersicht des Betreibers
# ---------------------------------------------------------------------------


def test_die_uebersicht_zeigt_beide_entscheidungen(admin_client: TestClient) -> None:
    """Akzeptiert, abgelehnt, offen - drei Zustaende, alle sichtbar."""
    _speichern(admin_client)
    ja = _leser(admin_client, "sagtja")
    nein = _leser(admin_client, "sagtnein")
    _leser(admin_client, "schweigt")

    admin_client.post(
        "/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=ja
    )
    admin_client.post(
        "/api/hausordnung/entscheidung", json={"akzeptiert": False}, headers=nein
    )

    zeilen = {z["username"]: z for z in admin_client.get("/api/hausordnung/uebersicht").json()}
    assert zeilen["sagtja"]["akzeptiert"] is True
    assert zeilen["sagtja"]["entschieden_am"] is not None
    assert zeilen["sagtnein"]["akzeptiert"] is False
    assert zeilen["schweigt"]["akzeptiert"] is None
    assert zeilen["schweigt"]["entschieden_am"] is None


def test_ablehnen_nimmt_niemandem_etwas(arr_client: TestClient) -> None:
    """⚠️ **Eine ausdrueckliche Entscheidung, als Test festgehalten.**

    Ablehnen ist eine Auskunft an den Betreiber, keine Sperre. Wer ablehnt,
    darf weiter anfragen - was daraus folgt, entscheidet ein Mensch.

    Nutzt ``arr_client``, weil eine Anfrage ohne eingerichtetes Radarr schon
    aus einem ganz anderen Grund scheitern wuerde - und dann bewiese der Test
    nichts.
    """
    _speichern(arr_client)
    nein = _leser(arr_client, "sagtnein")
    arr_client.post("/api/hausordnung/entscheidung", json={"akzeptiert": False}, headers=nein)

    item = arr_client.get("/api/discover/movie", headers=nein).json()["items"][0]
    antwort = arr_client.post(
        "/api/requests",
        json={
            "media_type": "movie",
            "tmdb_id": item["tmdb_id"],
            "quality_profile_id": 1,
            "root_folder_path": "/data/Movies",
        },
        headers=nein,
    )
    assert antwort.status_code == 201, antwort.text

    # Und der Knopf ist trotzdem weg - entschieden ist entschieden.
    assert arr_client.get("/api/config", headers=nein).json()["hausordnung_gelesen"] == 1


def test_eine_neue_fassung_setzt_die_uebersicht_zurueck(admin_client: TestClient) -> None:
    """Wer Fassung 1 akzeptiert hat, hat ueber Fassung 2 nicht entschieden."""
    _speichern(admin_client)
    ja = _leser(admin_client, "sagtja")
    admin_client.post(
        "/api/hausordnung/entscheidung", json={"akzeptiert": True}, headers=ja
    )

    _speichern(admin_client, erneut_lesen=True)

    zeile = next(
        z for z in admin_client.get("/api/hausordnung/uebersicht").json()
        if z["username"] == "sagtja"
    )
    assert zeile["akzeptiert"] is None
    # Die alte Fassung steht trotzdem dabei - sonst saehe es aus, als haette
    # nie jemand zugestimmt.
    assert zeile["fassung"] == 1


def test_kinderkonten_stehen_nicht_in_der_uebersicht(admin_client: TestClient) -> None:
    _speichern(admin_client)
    create_user(admin_client, "elternteil", can_manage_children=True)
    eltern = auth_headers(admin_client, "elternteil", "passwort-1234")
    admin_client.post(
        "/api/children",
        json={"username": "kind", "password": "kind-passwort", "age": 12},
        headers=eltern,
    )

    namen = [z["username"] for z in admin_client.get("/api/hausordnung/uebersicht").json()]
    assert "elternteil" in namen
    assert "kind" not in namen


def test_die_uebersicht_bleibt_beim_administrator(admin_client: TestClient) -> None:
    _speichern(admin_client)
    leser = _leser(admin_client)
    assert admin_client.get("/api/hausordnung/uebersicht", headers=leser).status_code == 403


def test_administratoren_werden_nicht_gefragt(admin_client: TestClient) -> None:
    """⚠️ **Sie schreiben die Regeln - sie sollen sie nicht abnicken.**

    Ein Punkt am Knopf, der den Betreiber an seinen eigenen Text erinnert,
    waere Zeremonie, und nach jeder neuen Fassung waere er wieder da.
    """
    _speichern(admin_client)

    # Kein Knopf: Fuer den Administrator gibt es die Hausordnung gar nicht.
    stand = admin_client.get("/api/config").json()
    assert stand["hausordnung_vorhanden"] is False

    # Und er steht nicht in der Liste derer, die noch entscheiden muessen.
    namen = [z["username"] for z in admin_client.get("/api/hausordnung/uebersicht").json()]
    assert "admin" not in namen


def test_auch_ein_zweiter_administrator_ist_aussen_vor(admin_client: TestClient) -> None:
    """Es haengt an der Rolle, nicht daran, wer gespeichert hat."""
    _speichern(admin_client)
    create_user(admin_client, "zweiteradmin", role=Role.admin)
    zweiter = auth_headers(admin_client, "zweiteradmin", "passwort-1234")

    # Der Testname behauptet "Administrator" - also muss er auch einer sein.
    assert admin_client.get("/api/hausordnung/uebersicht", headers=zweiter).status_code == 200
    assert admin_client.get("/api/config", headers=zweiter).json()["hausordnung_vorhanden"] is False


def test_entscheider_muessen_sehr_wohl(admin_client: TestClient) -> None:
    """⚠️ Die Grenze verlaeuft bei "Administrator", nicht bei "hat Rechte".

    Ein Entscheider entscheidet ueber Anfragen, nicht ueber die Regeln - fuer
    ihn gelten sie wie fuer jeden anderen.
    """
    _speichern(admin_client)
    create_user(admin_client, "pruefer", role=Role.approver)
    pruefer = auth_headers(admin_client, "pruefer", "passwort-1234")

    assert admin_client.get("/api/config", headers=pruefer).json()["hausordnung_vorhanden"] is True
    namen = [z["username"] for z in admin_client.get("/api/hausordnung/uebersicht").json()]
    assert "pruefer" in namen


def test_die_zahl_beim_haken_zaehlt_nur_die_betroffenen(admin_client: TestClient) -> None:
    """Sonst verspricht der Hinweis mehr Wirkung, als er hat."""
    _speichern(admin_client)
    vorher = admin_client.get("/api/hausordnung/verwaltung").json()["betroffene_konten"]

    create_user(admin_client, "nocheinadmin", role=Role.admin)
    create_user(admin_client, "gewoehnlich")

    nachher = admin_client.get("/api/hausordnung/verwaltung").json()["betroffene_konten"]
    # Nur das gewoehnliche Konto zaehlt.
    assert nachher == vorher + 1
