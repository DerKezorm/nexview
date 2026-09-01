"""Der Merker, mit dem eine Anfrage die Einstellungen einmal statt achtmal holt.

``load_settings`` legt sein Ergebnis an der Sitzung ab (``Session.info``). Das
ist billig zu bauen und teuer falsch zu machen: Ein Merker, der nach einem
Schreibvorgang stehen bleibt, gibt genau **einmal** die falsche Antwort - in
derselben Anfrage, die gerade gespeichert hat. Die Oberflaeche zeigt dann den
Stand von vorher, und beim naechsten Laden ist alles wieder in Ordnung. So
etwas findet niemand.

Deshalb steht hier zu jedem Schreibweg ein Schreib-Lese-Paar, dazu die
Gegenprobe (eine fremde Tabelle darf den Merker **nicht** werfen) und die eine
Stelle, die ausdruecklich am Merker vorbei liest.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.db import SessionLocal
from app.models import MediaServerConnection, MediaServerLibraryItem, Setting, User
from app.security import hash_password
from app.services import mediaserver_library
from app.services.mediaserver import LibraryItem
from app.services.mediaserver_accounts import ensure_client_identifier
from app.services.settings_service import (
    MERKER,
    clear_secret,
    load_settings,
    save_settings,
)

from .test_mediaserver_login import FakeMediaServer


# --------------------------------------------------------------------------
# Der Merker selbst
# --------------------------------------------------------------------------


def test_zweimal_in_einer_sitzung_ist_dasselbe_objekt() -> None:
    with SessionLocal() as db:
        assert load_settings(db) is load_settings(db)


def test_zwei_sitzungen_teilen_nichts() -> None:
    """Der Merker haengt an der Sitzung, nicht am Prozess.

    Waere er global, schleppte jede Anfrage den Stand der vorherigen mit - und
    die Testreihe waere ab sofort reihenfolgeabhaengig.
    """
    with SessionLocal() as eine:
        erste = load_settings(eine)
    with SessionLocal() as andere:
        zweite = load_settings(andere)
    assert erste is not zweite
    with SessionLocal() as dritte:
        assert MERKER not in dritte.info


def test_frisch_geht_am_merker_vorbei() -> None:
    with SessionLocal() as db:
        gemerkt = load_settings(db)
        frisch = load_settings(db, frisch=True)
        assert frisch is not gemerkt
        # Und legt sich danach selbst als neuer Merker ab.
        assert load_settings(db) is frisch


# --------------------------------------------------------------------------
# Schreiben und im selben Atemzug wieder lesen
# --------------------------------------------------------------------------


def test_nach_save_settings_kommt_der_neue_wert() -> None:
    """Der Pflichtfall. Ohne den Horcher kaeme hier 'DE' zurueck."""
    with SessionLocal() as db:
        assert load_settings(db).default_region == "DE"
        save_settings(db, {"default_region": "AT"})
        assert load_settings(db).default_region == "AT"


def test_nach_clear_secret_ist_das_geheimnis_leer() -> None:
    with SessionLocal() as db:
        save_settings(db, {"tmdb_api_key": "geheim-123"})
        assert load_settings(db).tmdb_api_key == "geheim-123"
        clear_secret(db, "tmdb_api_key")
        assert load_settings(db).tmdb_api_key == ""


def test_neue_verbindung_ist_sofort_zu_sehen() -> None:
    """Deckt das Kontrolllesen direkt nach dem Verbinden ab.

    Ohne den Horcher schriebe der Endpunkt bei **jedem** Verbinden
    'connection was saved but is incomplete when read back' ins Protokoll, und
    der frisch verbundene Server fehlte im Bibliotheks-Abgleich.
    """
    with SessionLocal() as db:
        assert load_settings(db).mediaserver_provider == ""
        db.add(
            MediaServerConnection(
                provider="plex",
                machine_id="maschine-1",
                name="Wohnzimmer",
                url="http://127.0.0.1:32400",
                token="admin-token",
            )
        )
        db.commit()
        assert load_settings(db).mediaserver_provider == "plex"


def test_getrennte_verbindung_ist_sofort_weg() -> None:
    """Dasselbe rueckwaerts - ``session.deleted`` zaehlt genauso."""
    with SessionLocal() as db:
        db.add(
            MediaServerConnection(
                provider="plex",
                machine_id="maschine-1",
                token="admin-token",
            )
        )
        db.commit()
        assert load_settings(db).mediaserver_provider == "plex"

        db.delete(db.query(MediaServerConnection).one())
        db.commit()
        assert load_settings(db).mediaserver_provider == ""


def test_geraetekennung_entsteht_nur_einmal() -> None:
    """Plex fuehrt angemeldete Geraete ueber diese Kennung.

    ``ensure_client_identifier`` speichert und liest im selben Atemzug zurueck.
    Bliebe der Merker stehen, kaeme sie leer zurueck und wuerde bei **jeder**
    Anmeldung neu erzeugt - der Nutzer saehe in seinen Plex-Einstellungen bald
    Dutzende Nexview-Eintraege.
    """
    with SessionLocal() as db:
        load_settings(db)  # Merker warmlaufen lassen, wie im Betrieb
        erste = ensure_client_identifier(db, load_settings(db))
        assert erste.mediaserver_client_identifier

        zweite = ensure_client_identifier(db, load_settings(db))
        assert (
            zweite.mediaserver_client_identifier == erste.mediaserver_client_identifier
        )


def test_put_settings_antwortet_mit_dem_neuen_wert(admin_client: TestClient) -> None:
    """Ueber die Schnittstelle, weil dort der Fall wirklich auftritt.

    ``test_settings`` prueft heute nur ein **nachfolgendes** GET; die Antwort
    des PUT selbst entsteht in derselben Anfrage wie das Speichern und war
    damit ungedeckt.
    """
    antwort = admin_client.put("/api/settings", json={"default_region": "AT"})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["default_region"] == "AT"


# --------------------------------------------------------------------------
# Gegenprobe: ohne sie koennte der Merker hohl bestehen
# --------------------------------------------------------------------------


def test_fremde_tabelle_wirft_den_merker_nicht() -> None:
    """Ein Konto anzulegen hat mit den Einstellungen nichts zu tun.

    Wuerde der Horcher bei jedem Schreibvorgang werfen, waere der Merker
    wertlos: Die meisten Anfragen schreiben irgendetwas.
    """
    with SessionLocal() as db:
        vorher = load_settings(db)
        db.add(
            User(
                username="jemand",
                password_hash=hash_password("x"),
                email="jemand@example.com",
            )
        )
        db.commit()
        assert load_settings(db) is vorher


# --------------------------------------------------------------------------
# Die eine Stelle, die ausdruecklich frisch liest
# --------------------------------------------------------------------------


class TrennenderServer(FakeMediaServer):
    """Ein Server, dem waehrend des Lesens die Verbindung gekappt wird.

    Genau der Fall aus dem Betrieb: Das Lesen einer grossen Bibliothek dauert
    Sekunden, und der Endpunkt zum Trennen laeuft im Threadpool - also in einer
    **fremden** Sitzung, an der der Horcher nichts ausrichten kann.
    """

    async def library_index(self) -> list[LibraryItem]:
        with SessionLocal() as fremde:
            fremde.delete(fremde.query(MediaServerConnection).one())
            fremde.commit()
        return [
            LibraryItem(media_type="movie", guid="p1", title="Dune", tmdb_id=438631)
        ]


async def test_abgleich_schreibt_nichts_wenn_inzwischen_getrennt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ohne ``frisch=True`` in ``mediaserver_library`` schreibt der Abgleich weiter.

    Er saehe dann den Merker vom Anfang des Lesevorgangs, in dem der Server
    noch verbunden ist, und legte frische Zeilen mit frischem Zeitstempel an -
    Minuten nachdem die Verbindung weg ist. Jede Aufraeumarbeit des Trennens
    waere damit zunichte.
    """
    with SessionLocal() as db:
        db.add(
            MediaServerConnection(
                provider="plex",
                machine_id="maschine-1",
                token="admin-token",
            )
        )
        db.commit()

    server = TrennenderServer()
    monkeypatch.setattr(
        mediaserver_library, "media_server_for_setup", lambda _s, _a: server
    )

    with SessionLocal() as db:
        einstellungen = load_settings(db)
        assert einstellungen.mediaserver_provider == "plex"
        assert await mediaserver_library.refresh(db, einstellungen) == 0

    with SessionLocal() as db:
        assert db.query(MediaServerLibraryItem).count() == 0


# --------------------------------------------------------------------------
# Der zweite Merker: der abgeleitete Verschluesselungsschluessel
# --------------------------------------------------------------------------


def test_fernet_wird_nur_einmal_abgeleitet() -> None:
    """Das Ableiten liest ``data/secret.key`` von der Platte - achtmal je Aufruf.

    Es war der groessere Teil der Kosten von ``load_settings``, nicht die
    Datenbank.
    """
    assert crypto._fernet() is crypto._fernet()


def test_fernet_vergessen_leitet_neu_ab() -> None:
    vorher = crypto._fernet()
    crypto.fernet_vergessen()
    assert crypto._fernet() is not vorher


def test_gemerkter_schluessel_liest_alte_werte_weiter() -> None:
    """Der Merker darf nichts an der Verschluesselung aendern.

    Insbesondere muss ein vor dem Vergessen geschriebener Wert danach immer
    noch lesbar sein - es ist derselbe Schluessel, nur neu abgeleitet.
    """
    verschluesselt = crypto.encrypt("geheim-123")
    crypto.fernet_vergessen()
    assert crypto.decrypt(verschluesselt) == "geheim-123"


def test_der_horcher_sieht_auch_rohe_setting_zeilen() -> None:
    """Nicht jeder Schreibweg geht ueber ``save_settings``.

    ``logs``, ``abgleich`` und die Download-Kollision legen ``Setting``-Zeilen
    von Hand an. Solange das ueber das ORM laeuft, sieht der Horcher sie.
    """
    with SessionLocal() as db:
        assert load_settings(db).default_language == "de"
        db.add(Setting(key="default_language", value="en", is_secret=False))
        db.commit()
        assert load_settings(db).default_language == "en"
