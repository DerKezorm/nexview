"""Die Admin-Sicht auf ein einzelnes Konto - und das Zuschlagen an das Haus.

Zwei Dinge werden hier geprueft, und das zweite ist das wichtigere:

1. Ein Administrator sieht, **wo** der Platz eines Nutzers steckt, samt Pfad.
2. Er kann einen Titel dem Haus zuschlagen. **Dabei wird keine Datei
   angefasst** - es wechselt nur, wem sie zugerechnet wird.

Und die Sicherheitsregel, die das Ganze traegt: **Entscheider duerfen das
nicht.** Sie haben selbst ein Kontingent und zugleich dauerhafte Auto-Freigabe.
Duerften sie Posten ins Haus schieben, waere die Kette geschlossen - selbst
anfragen, selbst freigeben, selbst ins Haus - und ihr Kontingent damit
wirkungslos, ohne dass es jemandem auffiele.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    Role,
    StorageEntry,
    StorageState,
    User,
)

from .conftest import auth_headers, create_user

GB = 1024**3


def _posten(
    db, *, user_id: int | None, titel: str, gb: int, pfad: str = "", tmdb: int = 603
) -> StorageEntry:
    eintrag = StorageEntry(
        key=f"movie:standard:tmdb:{tmdb}",
        user_id=user_id,
        media_type=MediaType.movie,
        tier=QualityTier.standard,
        tmdb_id=tmdb,
        title=titel,
        path=pfad,
        size_bytes=gb * GB,
        state=StorageState.house if user_id is None else StorageState.owned,
    )
    db.add(eintrag)
    db.commit()
    return eintrag


def _mit_speicher(client) -> None:
    client.put("/api/settings", json={"storage_enabled": True})


# --- Ansehen ---------------------------------------------------------------


def test_admin_sieht_den_stand_eines_nutzers(admin_client) -> None:
    """Wieviel, wieviele Posten - und wo die Dateien liegen."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")

    with SessionLocal() as db:
        _posten(
            db,
            user_id=konto["id"],
            titel="Ein Klassiker",
            gb=8,
            pfad="/data/Movies/Klassiker/klassiker.mkv",
        )

    antwort = admin_client.get(f"/api/storage/user/{konto['id']}")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["username"] == "kim"
    assert daten["used_bytes"] == 8 * GB
    assert daten["items"] == 1
    assert daten["matches"] == 1
    # Der Pfad geht mit - der Administrator soll beurteilen koennen, worum es
    # geht, bevor er den Titel dem Haus zuschlaegt.
    assert daten["entries"][0]["path"] == "/data/Movies/Klassiker/klassiker.mkv"


def test_suche_und_blaettern_im_fremden_konto(admin_client) -> None:
    """Zweihundert Titel ohne Suche waeren unbrauchbar."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")

    with SessionLocal() as db:
        for nummer in range(25):
            _posten(
                db,
                user_id=konto["id"],
                titel=f"Film {nummer:02d}",
                gb=nummer + 1,
                pfad=f"/data/Movies/F{nummer:02d}/f.mkv",
                tmdb=1000 + nummer,
            )

    erste = admin_client.get(f"/api/storage/user/{konto['id']}").json()
    assert erste["matches"] == 25
    # Diese Liste sitzt eingeklappt in einer Karte - deshalb kuerzere Seiten
    # als beim Hausbestand. Gegen ``per_page`` geprueft und nicht gegen eine
    # feste Zahl: Die Groesse ist eine Gestaltungsfrage und darf sich aendern,
    # ohne dass der Test etwas Falsches behauptet.
    assert erste["per_page"] == 10
    assert len(erste["entries"]) == erste["per_page"]
    # Das Groesste zuerst - das ist der Zweck der Liste.
    assert erste["entries"][0]["title"] == "Film 24"

    letzte = admin_client.get(f"/api/storage/user/{konto['id']}?page=3").json()
    assert len(letzte["entries"]) == 25 - 2 * erste["per_page"]

    gesucht = admin_client.get(f"/api/storage/user/{konto['id']}?q=Film 07").json()
    assert gesucht["matches"] == 1
    # Die Kopfzahlen bleiben der **ganze** Stand, nicht der Suchtreffer -
    # sonst saehe es aus, als haette der Nutzer nur diesen einen Titel.
    assert gesucht["items"] == 25


def test_unbekanntes_konto_gibt_404(admin_client) -> None:
    _mit_speicher(admin_client)
    assert admin_client.get("/api/storage/user/99999").status_code == 404


def test_fremdes_konto_ist_nur_fuer_admins(admin_client) -> None:
    """Auch ein Entscheider sieht fremde Konten nicht.

    Dieselbe Regel wie bei der Uebersicht: Wieviel Platz jemand belegt, ist
    eine Angabe ueber eine Person.
    """
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    create_user(admin_client, "entscheider2", "test1234", role=Role.approver)

    for name in ("kim", "entscheider2"):
        kopf = auth_headers(admin_client, name, "passwort-1234" if name == "kim" else "test1234")
        antwort = admin_client.get(f"/api/storage/user/{konto['id']}", headers=kopf)
        assert antwort.status_code == 403, name


# --- Ins Haus --------------------------------------------------------------


def test_ins_haus_macht_das_kontingent_frei(admin_client) -> None:
    """Der Titel bleibt liegen, das Konto wird leer."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")

    with SessionLocal() as db:
        eintrag = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8)
        posten_id = eintrag.id

    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/haus")
    assert antwort.status_code == 200
    assert antwort.json()["state"] == "house"

    stand = admin_client.get(f"/api/storage/user/{konto['id']}").json()
    assert stand["used_bytes"] == 0
    assert stand["items"] == 0

    with SessionLocal() as db:
        # ⚠️ Der Posten ist **nicht** verschwunden - es wechselt nur der Besitz.
        zeilen = db.scalars(select(StorageEntry)).all()
        assert len(zeilen) == 1
        assert zeilen[0].user_id is None
        assert zeilen[0].state == StorageState.house


def test_der_betroffene_erfaehrt_davon(admin_client) -> None:
    """Ohne Nachricht saenke die Zahl grundlos, und niemand wuesste warum."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")

    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    admin_client.post(f"/api/storage/entries/{posten_id}/haus")

    with SessionLocal() as db:
        nachrichten = db.scalars(select(Notification)).all()
        an_kim = [n for n in nachrichten if n.user_id == konto["id"]]
        assert len(an_kim) == 1
        assert an_kim[0].type == NotificationType.storage_released
        # Der Titel gehoert in message_title, nicht in den Textbaustein -
        # sonst stuenden geschweifte Klammern woertlich in der Glocke.
        assert an_kim[0].message_title == "Ein Klassiker"
        assert "{{" not in (an_kim[0].message_key or "")


def test_zweimal_ins_haus_gibt_409(admin_client) -> None:
    _mit_speicher(admin_client)
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=None, titel="Schon im Haus", gb=8).id

    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/haus")
    assert antwort.status_code == 409


def test_unbekannter_posten_gibt_404(admin_client) -> None:
    _mit_speicher(admin_client)
    assert admin_client.post("/api/storage/entries/99999/haus").status_code == 404


@pytest.mark.parametrize(
    ("name", "passwort", "rolle"),
    [("kim", "passwort-1234", Role.user), ("entscheider2", "test1234", Role.approver)],
)
def test_nur_admins_duerfen_ins_haus_schieben(admin_client, name, passwort, rolle) -> None:
    """Die wichtigste Pruefung des ganzen Features.

    Ein Entscheider hat ein Kontingent **und** dauerhafte Auto-Freigabe.
    Duerfte er Posten ins Haus schieben, koennte er beliebig viel anfragen,
    sich selbst freigeben und sein Konto anschliessend selbst leerraeumen -
    unbegrenzter Speicher, ohne dass es jemandem auffiele.
    """
    _mit_speicher(admin_client)
    konto = create_user(admin_client, name, passwort, role=rolle)

    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    kopf = auth_headers(admin_client, name, passwort)
    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/haus", headers=kopf)
    assert antwort.status_code == 403

    # Und der Posten liegt unveraendert da.
    with SessionLocal() as db:
        assert db.get(StorageEntry, posten_id).user_id == konto["id"]


def test_alles_bleibt_verborgen_wenn_der_schalter_aus_ist(admin_client) -> None:
    """Ausgeschaltet heisst: es gibt diese Funktion nicht. 404, nicht 403."""
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    assert admin_client.get(f"/api/storage/user/{konto['id']}").status_code == 404
    assert admin_client.post(f"/api/storage/entries/{posten_id}/haus").status_code == 404


# --- Die Uebersicht --------------------------------------------------------


def test_admins_ohne_bestand_stehen_nicht_in_der_uebersicht(admin_client) -> None:
    """Eine Zeile "0 GB von unbegrenzt" ist Beschriftung ohne Aussage.

    Was ein Administrator holt, gehoert dem Haus - sein Konto steht per
    Definition auf null. Entscheider dagegen **haben** ein Kontingent und
    muessen dastehen, auch wenn sie gerade nichts belegen.
    """
    _mit_speicher(admin_client)
    create_user(admin_client, "entscheider2", "test1234", role=Role.approver)
    with SessionLocal() as db:
        _posten(db, user_id=None, titel="Hausbestand", gb=5)

    namen = [
        anteil["username"]
        for anteil in admin_client.get("/api/storage/overview").json()["shares"]
        if anteil["user_id"] is not None
    ]
    assert "admin" not in namen
    assert "entscheider2" in namen


def test_ein_admin_mit_bestand_bleibt_sichtbar(admin_client) -> None:
    """Haelt ein Administrator doch etwas, ist das ein Fehlerhinweis.

    Der stuendliche Abgleich raeumt solche Posten von selbst ins Haus - bis
    dahin soll man sie sehen und nicht wegfiltern.
    """
    _mit_speicher(admin_client)
    with SessionLocal() as db:
        admin_id = db.scalar(select(User.id).where(User.username == "admin"))
        _posten(db, user_id=admin_id, titel="Sollte nicht hier sein", gb=3)

    zeilen = admin_client.get("/api/storage/overview").json()["shares"]
    assert any(z["username"] == "admin" and z["used_bytes"] == 3 * GB for z in zeilen)


def test_die_grenze_steht_an_jeder_zeile(admin_client) -> None:
    """Ohne sie liesse sich "91 von 300 GB" nicht anzeigen."""
    _mit_speicher(admin_client)
    admin_client.put("/api/settings", json={"storage_default_limit_gb": 300})
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8)
        _posten(db, user_id=None, titel="Hausbestand", gb=5, tmdb=604)

    zeilen = admin_client.get("/api/storage/overview").json()["shares"]
    kim = next(z for z in zeilen if z["username"] == "kim")
    assert kim["limit_bytes"] == 300 * GB
    # Der Hausbestand hat keine - die Frage stellt sich dort nicht.
    haus = next(z for z in zeilen if z["user_id"] is None)
    assert haus["limit_bytes"] is None
