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


def test_der_speicher_steht_ohne_vorbedingung_offen(admin_client) -> None:
    """Es gibt keinen Hauptschalter mehr - gemessen wird immer.

    Bis 0.19 antworteten diese Endpunkte mit 404, solange das Haus nach
    Stueckzahl begrenzte. Seit beide Waehrungen zusammen gelten, gibt es
    nichts mehr auszuschalten: Wer wissen will, was er belegt, soll es sehen,
    auch wenn ihn niemand begrenzt.
    """
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    assert admin_client.get(f"/api/storage/user/{konto['id']}").status_code == 200
    assert admin_client.post(f"/api/storage/entries/{posten_id}/haus").status_code == 200


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


# --- Abgeben: der Weg des Nutzers ------------------------------------------


def _als(client, name: str, passwort: str = "passwort-1234") -> dict:
    return auth_headers(client, name, passwort)


def test_abgeben_stellt_zur_entscheidung_und_meldet_es(admin_client) -> None:
    """Der Nutzer gibt ab, die Administratoren erfahren davon."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", headers=_als(admin_client, "kim")
    )
    assert antwort.status_code == 200
    assert antwort.json()["state"] == "pending"
    assert antwort.json()["released_at"]

    with SessionLocal() as db:
        arten = [n.type for n in db.scalars(select(Notification)).all()]
        assert NotificationType.storage_release_requested in arten


def test_abgegebenes_zaehlt_weiter(admin_client) -> None:
    """⚠️ **Sonst waere Abgeben ein Freifahrtschein.**

    Man gaebe alles ab, waere sofort frei, und niemand muesste je entscheiden.
    """
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", headers=_als(admin_client, "kim")
    )

    stand = admin_client.get(f"/api/storage/user/{konto['id']}").json()
    assert stand["used_bytes"] == 8 * GB
    assert stand["pending_bytes"] == 8 * GB


def test_fremder_posten_ist_nicht_von_einem_fehlenden_zu_unterscheiden(
    admin_client,
) -> None:
    """404 statt 403: Wer Nummern durchprobiert, soll nichts daraus ablesen."""
    _mit_speicher(admin_client)
    eins = create_user(admin_client, "kim", "passwort-1234")
    create_user(admin_client, "eva", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=eins["id"], titel="Ein Klassiker", gb=8).id

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", headers=_als(admin_client, "eva")
    )
    assert antwort.status_code == 404
    assert admin_client.post(
        "/api/storage/entries/99999/abgeben", headers=_als(admin_client, "eva")
    ).status_code == 404


def test_zweimal_abgeben_gibt_409(admin_client) -> None:
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    kopf = _als(admin_client, "kim")
    admin_client.post(f"/api/storage/entries/{posten_id}/abgeben", headers=kopf)
    zweite = admin_client.post(f"/api/storage/entries/{posten_id}/abgeben", headers=kopf)
    assert zweite.status_code == 409


def test_hausbestand_laesst_sich_nicht_abgeben(admin_client) -> None:
    """Er gehoert ja niemandem - es gaebe nichts abzugeben."""
    _mit_speicher(admin_client)
    create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=None, titel="Schon im Haus", gb=8).id

    antwort = admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", headers=_als(admin_client, "kim")
    )
    assert antwort.status_code == 404


def test_zu_viele_offene_abgaben_werden_gebremst(admin_client) -> None:
    """Ohne Deckel reicht jemand alles durch und der Admin ertrinkt."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    kopf = _als(admin_client, "kim")

    with SessionLocal() as db:
        kennungen = [
            _posten(db, user_id=konto["id"], titel=f"Film {n}", gb=1, tmdb=1000 + n).id
            for n in range(12)
        ]

    angenommen = [
        admin_client.post(f"/api/storage/entries/{k}/abgeben", headers=kopf).status_code
        for k in kennungen
    ]
    assert angenommen.count(200) == 10
    assert angenommen.count(409) == 2


def test_abgabe_laesst_sich_zuruecknehmen(admin_client) -> None:
    """Ein versehentlicher Klick soll nicht bis zur nächsten Entscheidung stehen."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    kopf = _als(admin_client, "kim")
    admin_client.post(f"/api/storage/entries/{posten_id}/abgeben", headers=kopf)
    antwort = admin_client.post(f"/api/storage/entries/{posten_id}/behalten", headers=kopf)

    assert antwort.status_code == 200
    assert antwort.json()["state"] == "owned"
    assert antwort.json()["released_at"] is None
    assert admin_client.get("/api/storage/releases").json() == []


# --- Die Warteschlange -----------------------------------------------------


def test_warteschlange_zeigt_das_aelteste_zuerst(admin_client) -> None:
    """Nach Groesse sortiert saehe geschaeftiger aus - und verdeckte, was liegen bleibt."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    kopf = _als(admin_client, "kim")

    with SessionLocal() as db:
        klein = _posten(db, user_id=konto["id"], titel="Zuerst", gb=1, tmdb=1).id
        gross = _posten(db, user_id=konto["id"], titel="Danach", gb=90, tmdb=2).id

    admin_client.post(f"/api/storage/entries/{klein}/abgeben", headers=kopf)
    admin_client.post(f"/api/storage/entries/{gross}/abgeben", headers=kopf)

    schlange = admin_client.get("/api/storage/releases").json()
    assert [zeile["entry"]["title"] for zeile in schlange] == ["Zuerst", "Danach"]
    assert schlange[0]["username"] == "kim"
    # Der Pfad geht mit - der Administrator soll beurteilen koennen, worum es geht.
    assert "path" in schlange[0]["entry"]


def test_warteschlange_ist_nur_fuer_admins(admin_client) -> None:
    """⚠️ **Die Sicherheitsregel des ganzen Features.**

    Entscheider haben ein Kontingent **und** dauerhafte Auto-Freigabe. Duerften
    sie Abgaben sehen und entscheiden, waere die Kette geschlossen: selbst
    anfragen, selbst freigeben, selbst ans Haus.
    """
    _mit_speicher(admin_client)
    create_user(admin_client, "entscheider9", "test1234", role=Role.approver)
    kopf = auth_headers(admin_client, "entscheider9", "test1234")
    assert admin_client.get("/api/storage/releases", headers=kopf).status_code == 403


def test_der_admin_entscheidet_und_der_nutzer_ist_frei(admin_client) -> None:
    """Der ganze Weg: abgeben, entscheiden, Konto leer - **ohne Datei anzufassen**."""
    _mit_speicher(admin_client)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", headers=_als(admin_client, "kim")
    )
    admin_client.post(f"/api/storage/entries/{posten_id}/haus")

    stand = admin_client.get(f"/api/storage/user/{konto['id']}").json()
    assert stand["used_bytes"] == 0
    assert admin_client.get("/api/storage/releases").json() == []

    with SessionLocal() as db:
        zeilen = db.scalars(select(StorageEntry)).all()
        assert len(zeilen) == 1  # der Posten lebt weiter, nur beim Haus
        assert zeilen[0].state == StorageState.house


# --- Serverseitige Kanaele -------------------------------------------------


def test_abgabe_meldet_sich_genau_einmal_im_kanal(admin_client) -> None:
    """⚠️ **Einmal je Ereignis, nicht je Administrator.**

    Ein Topic ist ein Postfach, kein Empfaenger. Eine Schleife ueber die
    Administratoren mit eingeschaltetem Rundruf schickte dieselbe Durchsage
    zweimal ins selbe Topic - und bei fuenf Admins fuenfmal.
    """
    from app.models import ChannelMessage

    _mit_speicher(admin_client)
    # Ein zweiter Administrator - bei nur einem faellt der Fehler nicht auf.
    create_user(admin_client, "chefin", "passwort-1234", role=Role.admin)
    konto = create_user(admin_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        posten_id = _posten(db, user_id=konto["id"], titel="Ein Klassiker", gb=8).id

    admin_client.post(
        f"/api/storage/entries/{posten_id}/abgeben", headers=_als(admin_client, "kim")
    )

    with SessionLocal() as db:
        # Zwei Glocken-Meldungen - je Administrator eine.
        an_admins = [
            n
            for n in db.scalars(select(Notification)).all()
            if n.type == NotificationType.storage_release_requested
        ]
        assert len(an_admins) == 2
        # Aber hoechstens eine Kanal-Durchsage je Ziel. Ohne eingerichtetes
        # Ziel sind es null - entscheidend ist, dass es nicht je Admin eine ist.
        assert len(db.scalars(select(ChannelMessage)).all()) == 0


def test_beide_speicher_ereignisse_sind_anhakbar(admin_client) -> None:
    """Was der Postausgang kennt, muss sich auch einschalten lassen.

    Sonst gibt es ein Ereignis, das nie jemand bekommt - und niemand merkt es,
    weil ein fehlender Haken wie eine bewusste Entscheidung aussieht.
    """
    from app.services import channel_outbox

    for art in (
        NotificationType.storage_release_requested,
        NotificationType.storage_released,
    ):
        assert art in channel_outbox.EVENTS
        gruppe = channel_outbox.EVENTS[art]
        assert gruppe in channel_outbox.GROUPS
        # Und in beiden Sprachen ein Text, sonst steht dort nichts.
        for sprache in ("de", "en"):
            assert channel_outbox.TEXTS[sprache][art]["title"]
