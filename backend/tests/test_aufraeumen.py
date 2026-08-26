"""Der Aufräum-Vorschlag: was liegt herum?

⚠️ **Die halbe Datei handelt von einem Denkfehler des ersten Baus.**

Zuerst prüfte die Liste nur „zuletzt gesehen ist lange her". Das klingt
richtig und ist es nicht: Was **nie** jemand gesehen hat, hat gar kein Datum -
und rutschte damit ungeprüft durch. Ein Film, der heute Nacht fertig wurde,
stand deshalb wegen seiner Größe ganz oben in der Liste der Ladenhüter. Der
Fehler fiel beim Draufschauen auf, nicht beim Testen; deshalb steht er jetzt
hier fest.

Ein Kandidat muss **zwei** Uhren erfüllen: lange nicht angesehen **und** lange
da. Die zweite kommt aus Radarr bzw. Sonarr (``StorageEntry.added_at``), nicht
aus ``measured_at`` - das fällt bei jedem stündlichen Abgleich neu an und
hieße auf einer zehn Jahre alten Bibliothek „alles brandneu".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaType,
    QualityTier,
    StorageEntry,
    StorageState,
    TitleRating,
    User,
    UserWatched,
)
from app.services import aufraeumen

from .conftest import create_user

GB = 1024**3


def _jetzt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _posten(
    *,
    key: str,
    title: str,
    size_gb: int,
    liegt_seit_tagen: int | None,
    user_id: int | None = None,
    tmdb_id: int = 500,
    season: int | None = None,
) -> int:
    with SessionLocal() as db:
        zeile = StorageEntry(
            key=key,
            user_id=user_id,
            media_type=MediaType.tv if season is not None else MediaType.movie,
            tier=QualityTier.standard,
            tmdb_id=tmdb_id,
            season=season,
            title=title,
            size_bytes=size_gb * GB,
            state=StorageState.owned if user_id else StorageState.house,
            added_at=(
                None if liegt_seit_tagen is None else _jetzt() - timedelta(days=liegt_seit_tagen)
            ),
        )
        db.add(zeile)
        db.commit()
        return zeile.id


def _gesehen(user_id: int, tmdb_id: int, vor_tagen: int, art: MediaType = MediaType.movie) -> None:
    with SessionLocal() as db:
        db.add(
            UserWatched(
                user_id=user_id,
                media_type=art,
                tmdb_id=tmdb_id,
                watched_at=_jetzt() - timedelta(days=vor_tagen),
            )
        )
        db.commit()


def _liste(**kwargs) -> aufraeumen.Liste:
    with SessionLocal() as db:
        return aufraeumen.liste(db, **kwargs)


# --------------------------------------------------------------------------
# Die zweite Uhr - der eigentliche Grund für diese Datei
# --------------------------------------------------------------------------


def test_frisch_geladen_ist_kein_ladenhueter(admin_client: TestClient) -> None:
    """⚠️ Der gefundene Fehler.

    Ein großer Film, den noch nie jemand gesehen hat - weil er gestern fertig
    wurde. „Nie angesehen" stimmt, und trotzdem gehört er nicht in die Liste.
    """
    _posten(key="movie:standard:tmdb:1", title="Gestern fertig", size_gb=80, liegt_seit_tagen=1)
    assert _liste().gesamt_anzahl == 0


def test_lange_da_und_nie_angesehen_ist_ein_kandidat(admin_client: TestClient) -> None:
    _posten(key="movie:standard:tmdb:2", title="Liegt ewig", size_gb=80, liegt_seit_tagen=900)
    ergebnis = _liste()
    assert ergebnis.gesamt_anzahl == 1
    assert ergebnis.kandidaten[0].title == "Liegt ewig"
    assert ergebnis.kandidaten[0].nie_gesehen


def test_ohne_bekanntes_alter_wird_nicht_geraten(admin_client: TestClient) -> None:
    """Direkt nach einem Update kennt Nexview kein Alter.

    Die richtige Antwort darauf ist eine **leere** Liste plus die Angabe, wie
    viele übergangen wurden - nicht eine Liste voller Behauptungen.
    """
    _posten(key="movie:standard:tmdb:3", title="Alter unbekannt", size_gb=80, liegt_seit_tagen=None)
    ergebnis = _liste()
    assert ergebnis.gesamt_anzahl == 0
    assert ergebnis.ohne_datum == 1


def test_kuerzlich_angesehen_faellt_raus(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    _posten(tmdb_id=4, key="movie:standard:tmdb:4", title="Neulich geschaut", size_gb=80, liegt_seit_tagen=900)
    _gesehen(kim["id"], 4, vor_tagen=10)
    assert _liste().gesamt_anzahl == 0


def test_lange_nicht_angesehen_bleibt_drin(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    _posten(tmdb_id=5, key="movie:standard:tmdb:5", title="Lange her", size_gb=80, liegt_seit_tagen=900)
    _gesehen(kim["id"], 5, vor_tagen=400)

    ergebnis = _liste()
    assert ergebnis.gesamt_anzahl == 1
    kandidat = ergebnis.kandidaten[0]
    assert not kandidat.nie_gesehen
    assert kandidat.gesehen_von == ["kim"]


def test_der_zeitraum_laesst_sich_stellen(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    _posten(tmdb_id=6, key="movie:standard:tmdb:6", title="Vor acht Monaten", size_gb=80, liegt_seit_tagen=900)
    _gesehen(kim["id"], 6, vor_tagen=240)

    # Acht Monate her: bei einem Jahr Frist noch kein Kandidat, bei sechs schon.
    assert _liste(monate=12).gesamt_anzahl == 0
    assert _liste(monate=6).gesamt_anzahl == 1


# --------------------------------------------------------------------------
# Reihenfolge, Hausbestand, eigene Sicht
# --------------------------------------------------------------------------


def test_groesster_brocken_zuerst(admin_client: TestClient) -> None:
    """Beim Aufräumen entscheidet der Platz, nicht das Alter."""
    _posten(key="movie:standard:tmdb:10", title="Klein", size_gb=2, liegt_seit_tagen=1500)
    _posten(key="movie:standard:tmdb:11", title="Groß", size_gb=200, liegt_seit_tagen=700)
    _posten(key="movie:standard:tmdb:12", title="Mittel", size_gb=40, liegt_seit_tagen=1000)

    assert [k.title for k in _liste().kandidaten] == ["Groß", "Mittel", "Klein"]


def test_hausbestand_gehoert_dazu(admin_client: TestClient) -> None:
    """⚠️ Der Hauptfall, nicht der Sonderfall.

    Wer Nexview auf eine bestehende Bibliothek setzt, hat zunächst **alles**
    im Hausbestand - ein Eigentümer entsteht nur für das, was danach über
    Nexview bestellt wird. Eine Liste ohne Hausbestand wäre dort fast leer.
    """
    _posten(key="movie:standard:tmdb:20", title="Gehört niemandem", size_gb=90, liegt_seit_tagen=800)
    ergebnis = _liste()
    assert ergebnis.gesamt_anzahl == 1
    assert ergebnis.kandidaten[0].besitzer is None


def test_eigene_sicht_zeigt_nur_eigenes(admin_client: TestClient) -> None:
    kim = create_user(admin_client, "kim")
    _posten(key="movie:standard:tmdb:30", title="Von Kim", size_gb=50, liegt_seit_tagen=800,
            user_id=kim["id"], tmdb_id=30)
    _posten(key="movie:standard:tmdb:31", title="Vom Haus", size_gb=90, liegt_seit_tagen=800,
            tmdb_id=31)

    with SessionLocal() as db:
        person = db.get(User, kim["id"])
        eigen = aufraeumen.liste(db, nutzer=person)

    assert [k.title for k in eigen.kandidaten] == ["Von Kim"]
    # Und die Gesamtsicht sieht beide.
    assert _liste().gesamt_anzahl == 2


def test_die_bewertung_kommt_mit(admin_client: TestClient) -> None:
    """Zwei Sterne an etwas, das niemand mehr ansieht, ist ein deutlicheres
    Zeichen als jede Zahl daneben."""
    kim = create_user(admin_client, "kim")
    _posten(key="movie:standard:tmdb:40", title="Schlecht", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=40)
    with SessionLocal() as db:
        db.add(
            TitleRating(
                user_id=kim["id"], media_type=MediaType.movie, tmdb_id=40, rating=2, title="Schlecht"
            )
        )
        db.commit()

    kandidat = _liste().kandidaten[0]
    assert kandidat.bewertung == 2.0
    assert kandidat.bewertungen == 1


# --------------------------------------------------------------------------
# Die Grundlage - was die Liste über sich selbst sagt
# --------------------------------------------------------------------------


def test_die_liste_nennt_ihre_eigene_luecke(admin_client: TestClient) -> None:
    """⚠️ Ohne diese Angabe behauptet die Liste Dinge, die nicht stimmen.

    „Seit einem halben Jahr niemand angesehen" heißt in Wahrheit „keines der
    **verknüpften** Konten". Wer über ein nicht verknüpftes Konto schaut, ist
    für Nexview unsichtbar - sein Lieblingsfilm steht dann hier.
    """
    create_user(admin_client, "ohne-server")
    grundlage = _liste().grundlage

    assert not grundlage.vollstaendig
    assert "ohne-server" in grundlage.ohne_verknuepfung
    assert grundlage.konten_gesamt >= 2


def test_kinderkonten_zaehlen_nicht_als_luecke(admin_client: TestClient) -> None:
    """Sie sind Unterprofile ihrer Eltern und haben auf dem Medienserver gar
    kein Gegenstück. Sie als „nicht verknüpft" zu melden wäre ein Mangel, den
    niemand beheben kann."""
    from app.models import Role

    create_user(admin_client, "kind", role=Role.child)
    assert "kind" not in _liste().grundlage.ohne_verknuepfung


# --------------------------------------------------------------------------
# Über die API
# --------------------------------------------------------------------------


def test_admin_sieht_die_ganze_bibliothek(admin_client: TestClient) -> None:
    _posten(key="movie:standard:tmdb:50", title="Vom Haus", size_gb=90, liegt_seit_tagen=800)
    antwort = admin_client.get("/api/admin/stats/aufraeumen")
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["posten"][0]["title"] == "Vom Haus"
    assert daten["posten"][0]["besitzer"] is None
    assert daten["posten"][0]["liegt_seit"]
    assert "grundlage" in daten


def test_die_eigene_liste_braucht_keine_adminrechte(admin_client: TestClient) -> None:
    from .conftest import auth_headers

    create_user(admin_client, "kim")
    kopf = auth_headers(admin_client, "kim", "passwort-1234")
    antwort = admin_client.get("/api/storage/me/aufraeumen", headers=kopf)
    assert antwort.status_code == 200, antwort.text


def test_die_gesamtliste_ist_admins_vorbehalten(admin_client: TestClient) -> None:
    """Entscheider ausdrücklich nicht - anders als der Rest der Statistik.

    Handeln kann hier ohnehin nur der Administrator, und die Liste sagt
    nebenbei, was **alle** im Haushalt längst nicht mehr angesehen haben.
    """
    from app.models import Role

    from .conftest import auth_headers

    create_user(admin_client, "chef2", role=Role.approver)
    kopf = auth_headers(admin_client, "chef2", "passwort-1234")
    assert admin_client.get("/api/admin/stats/aufraeumen", headers=kopf).status_code == 403


@pytest.mark.parametrize("monate", [3, 6, 12, 24])
def test_alle_zeitraeume_der_oberflaeche_gehen(admin_client: TestClient, monate: int) -> None:
    """Die Knöpfe in der Tabelle bieten genau diese vier an."""
    antwort = admin_client.get("/api/admin/stats/aufraeumen", params={"monate": monate})
    assert antwort.status_code == 200
    assert antwort.json()["monate"] == monate


# --------------------------------------------------------------------------
# Suchen und filtern
# --------------------------------------------------------------------------


def test_die_suche_findet_ohne_ruecksicht_auf_grossschreibung(admin_client: TestClient) -> None:
    _posten(key="movie:standard:tmdb:60", title="Star Trek", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=60)
    _posten(key="movie:standard:tmdb:61", title="Findet Nemo", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=61)

    assert [k.title for k in _liste(suche="trek").kandidaten] == ["Star Trek"]
    assert [k.title for k in _liste(suche="TREK").kandidaten] == ["Star Trek"]
    assert [k.title for k in _liste(suche="nemo").kandidaten] == ["Findet Nemo"]
    assert _liste(suche="gibtesnicht").gesamt_anzahl == 0


def test_nach_medienart_filtern(admin_client: TestClient) -> None:
    _posten(key="movie:standard:tmdb:62", title="Ein Film", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=62)
    _posten(key="tv:standard:tvdb:63:s1", title="Eine Serie", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=63, season=1)

    assert [k.title for k in _liste(art=MediaType.movie).kandidaten] == ["Ein Film"]
    assert [k.title for k in _liste(art=MediaType.tv).kandidaten] == ["Eine Serie"]
    assert _liste().gesamt_anzahl == 2


def test_nur_vorgemerkte_zeigt_auch_was_die_uhren_aussortieren(admin_client: TestClient) -> None:
    """⚠️ Der Grund, warum dieser Filter die beiden Uhren übergeht.

    Ein vorgemerkter Titel darf **nicht** aus der Ansicht fallen, nur weil ihn
    inzwischen jemand angesehen hat oder weil er zu frisch ist. Sonst wäre
    ausgerechnet der Titel unsichtbar, den man noch aufhalten will.
    """
    kim = create_user(admin_client, "kim")
    # Frisch geladen und gerade angesehen - durch beide Uhren gefallen.
    posten_id = _posten(key="movie:standard:tmdb:64", title="Frisch und gesehen", size_gb=50,
                        liegt_seit_tagen=1, tmdb_id=64)
    _gesehen(kim["id"], 64, vor_tagen=0)
    assert _liste().gesamt_anzahl == 0

    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        zeile.delete_after = _jetzt() + timedelta(days=14)
        zeile.delete_marked_at = _jetzt()
        db.commit()

    ergebnis = _liste(nur_vorgemerkt=True)
    assert [k.title for k in ergebnis.kandidaten] == ["Frisch und gesehen"]
    assert ergebnis.kandidaten[0].loescht_am is not None


def test_ohne_filter_bleiben_vorgemerkte_sichtbar(admin_client: TestClient) -> None:
    """In der normalen Liste stehen sie weiter - markiert, nicht versteckt."""
    posten_id = _posten(key="movie:standard:tmdb:65", title="Vorgemerkt", size_gb=50,
                        liegt_seit_tagen=800, tmdb_id=65)
    with SessionLocal() as db:
        zeile = db.get(StorageEntry, posten_id)
        zeile.delete_after = _jetzt() + timedelta(days=14)
        db.commit()

    kandidat = _liste().kandidaten[0]
    assert kandidat.title == "Vorgemerkt"
    assert kandidat.loescht_am is not None


def test_suche_und_medienart_zusammen(admin_client: TestClient) -> None:
    _posten(key="movie:standard:tmdb:66", title="Alien", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=66)
    _posten(key="tv:standard:tvdb:67:s1", title="Alien Serie", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=67, season=1)

    ergebnis = _liste(suche="alien", art=MediaType.movie)
    assert [k.title for k in ergebnis.kandidaten] == ["Alien"]


def test_die_filter_gehen_auch_ueber_die_api(admin_client: TestClient) -> None:
    _posten(key="movie:standard:tmdb:68", title="Gesucht", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=68)
    _posten(key="movie:standard:tmdb:69", title="Anderer", size_gb=50, liegt_seit_tagen=800,
            tmdb_id=69)

    antwort = admin_client.get("/api/admin/stats/aufraeumen", params={"suche": "gesucht"})
    assert antwort.status_code == 200, antwort.text
    assert [p["title"] for p in antwort.json()["posten"]] == ["Gesucht"]

    antwort = admin_client.get("/api/admin/stats/aufraeumen", params={"art": "movie"})
    assert antwort.status_code == 200
    assert len(antwort.json()["posten"]) == 2
