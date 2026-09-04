"""Die Kachel: eine Antwort, wenige Zahlen, keine Saetze.

⚠️ **Was hier geprueft wird, ist eine Zusage.** Solange ``v1`` in der Adresse
steht, darf sich an dieser Antwort nichts aendern, was Bestehendes bricht -
und eine Kachel haengt an einer Wand und wird nicht mitgepflegt.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaType, QualityTier, Role, StorageEntry, StorageState

from .conftest import auth_headers, create_user


def test_die_kachel_liefert_alle_zugesagten_teile(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/v1/dashboard")
    assert antwort.status_code == 200

    kachel = antwort.json()
    assert set(kachel) == {
        "version",
        "befunde",
        "anfragen",
        "bibliothek",
        "instanzen",
        "tickets_offen",
    }
    assert set(kachel["befunde"]) == {"fehler", "warnung", "hinweis", "dringendste"}
    assert set(kachel["anfragen"]) == {"wartend", "laufend", "fehlgeschlagen_7d"}
    assert set(kachel["bibliothek"]) == {
        "filme",
        "serien",
        "belegt_bytes",
        # ⚠️ Erst mit dieser Zahl laesst sich ``belegt_bytes`` lesen: Sie wirft
        # sonst zusammen, was die Bewohner angefragt haben, und was schon vor
        # Nexview da war. Nur die erste Haelfte zaehlt gegen Kontingente.
        "hausbestand_bytes",
        "frei_bytes",
    }


def test_befunde_kommen_als_kennung_nicht_als_satz(arr_client: TestClient) -> None:
    """⚠️ **Der Kern der Zusage.**

    Ein fertiger Satz waere in der Sprache des Servers - und er wuerde sich
    aendern, sobald jemand eine Formulierung verbessert. Unter einer Zusage
    duerfte das dann nie wieder passieren. Kennungen wie
    ``dienst.nicht_erreichbar`` sind stabil.
    """
    from datetime import timedelta

    from app.models import InstanzStand
    from app.services.befunde import _jetzt

    with SessionLocal() as session:
        session.add(
            InstanzStand(
                kennung="radarr-standard",
                erreichbar=False,
                erreichbar_seit=_jetzt() - timedelta(hours=3),
            )
        )
        session.commit()

    kachel = arr_client.get("/api/v1/dashboard").json()
    assert kachel["befunde"]["fehler"] >= 1
    assert "dienst.nicht_erreichbar" in kachel["befunde"]["dringendste"]
    # Punkt-Kennungen, keine Leerzeichen: ein Satz saehe anders aus.
    for kennung in kachel["befunde"]["dringendste"]:
        assert " " not in kennung and "." in kennung


def test_hoechstens_drei_dringendste(arr_client: TestClient) -> None:
    """Eine Kachel hat wenig Platz - und drei Zeilen liest man noch."""
    kachel = arr_client.get("/api/v1/dashboard").json()
    assert len(kachel["befunde"]["dringendste"]) <= 3


def test_nur_fuer_administratoren(admin_client: TestClient) -> None:
    """Instanz-Zustand und Plattenzahlen sind Betreiber-Sache.

    Ein Schluessel erbt die Rechte seines Besitzers - wer die Kachel an eine
    Wand haengt, braucht also einen Admin-Schluessel. Das steht so in der
    Beschreibung des Endpunkts, und es ist der Grund, warum es dort steht.
    """
    create_user(admin_client, "eva", role=Role.approver)
    eva = auth_headers(admin_client, "eva", "passwort-1234")
    assert admin_client.get("/api/v1/dashboard", headers=eva).status_code == 403

    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")
    assert admin_client.get("/api/v1/dashboard", headers=kim).status_code == 403


def test_ohne_anmeldung_gesperrt(client: TestClient) -> None:
    assert client.get("/api/v1/dashboard").status_code == 401


def test_ein_nur_lese_schluessel_darf(admin_client: TestClient) -> None:
    """Der Fall, fuer den es die Kachel gibt.

    Ein Dashboard fragt im Minutentakt und veraendert nie etwas - genau dafuer
    ist der Schalter "darf nur lesen" gedacht.
    """
    angelegt = admin_client.post(
        "/api/auth/me/schluessel", json={"name": "Wandbrett", "nur_lesen": True}
    )
    assert angelegt.status_code in (200, 201), angelegt.text
    schluessel = angelegt.json()["schluessel"]

    antwort = admin_client.get(
        "/api/v1/dashboard", headers={"Authorization": f"Bearer {schluessel}"}
    )
    assert antwort.status_code == 200
    assert "befunde" in antwort.json()


# ------------------------------------------------- Was "Filme" und "Serien" zaehlen
#
# ⚠️ **Beide Zahlen meinten einmal Verschiedenes.** "Serien" zaehlte Werke,
# "Filme" zaehlte Posten - derselbe Film in 1080p und in 4K stand also zweimal
# darin, waehrend eine Serie mit zehn Staffeln einmal zaehlte. Nebeneinander
# gelesen sahen die beiden Beschriftungen gleich aus und bedeuteten es nicht.


def _posten(
    db,
    *,
    art: MediaType,
    schluessel: str,
    tmdb: int | None = None,
    tvdb: int | None = None,
    season: int | None = None,
    stufe: QualityTier = QualityTier.standard,
) -> None:
    db.add(
        StorageEntry(
            key=schluessel,
            user_id=None,
            media_type=art,
            tier=stufe,
            tmdb_id=tmdb,
            tvdb_id=tvdb,
            season=season,
            title="Ein Titel",
            size_bytes=1024**3,
            state=StorageState.owned,
        )
    )


def test_die_kachel_zaehlt_werke_und_nicht_posten(admin_client: TestClient) -> None:
    """⚠️ Drei Faelle, und der dritte ist der, den ein ``distinct`` allein bricht.

    Ein Film in zwei Stufen ist ein Film. Eine Serie in drei Staffeln ist eine
    Serie. Und ein Posten ohne Nummer ist trotzdem einer - ``count(distinct)``
    laesst NULL weg, ein Hausbestand ohne TMDB-Nummer waere sonst unsichtbar.
    """
    with SessionLocal() as db:
        # Derselbe Film, zwei Qualitaetsstufen: zwei Posten, ein Werk.
        _posten(db, art=MediaType.movie, schluessel="movie:standard:tmdb:603", tmdb=603)
        _posten(
            db,
            art=MediaType.movie,
            schluessel="movie:uhd:tmdb:603",
            tmdb=603,
            stufe=QualityTier.uhd,
        )
        # Ein zweiter Film, nur einmal.
        _posten(db, art=MediaType.movie, schluessel="movie:standard:tmdb:604", tmdb=604)
        # Und einer aus dem Hausbestand, dem niemand eine Nummer geben konnte.
        _posten(db, art=MediaType.movie, schluessel="movie:standard:pfad:alt")

        # Eine Serie mit drei Staffeln: drei Posten, ein Werk.
        for staffel in (1, 2, 3):
            _posten(
                db,
                art=MediaType.tv,
                schluessel=f"tv:standard:tvdb:7:s{staffel}",
                tvdb=7,
                season=staffel,
            )
        # Eine Serie ohne TVDB-Nummer.
        _posten(db, art=MediaType.tv, schluessel="tv:standard:pfad:alt", season=1)
        db.commit()

    bibliothek = admin_client.get("/api/v1/dashboard").json()["bibliothek"]

    assert bibliothek["filme"] == 3, (
        "Zwei Stufen desselben Films sind ein Film, dazu ein zweiter und einer "
        f"ohne Nummer: drei. Gezaehlt wurden {bibliothek['filme']} - bei vier "
        "zaehlt die Abfrage Posten statt Werke, bei zwei faellt der Posten "
        "ohne Nummer heraus."
    )
    assert bibliothek["serien"] == 2, (
        "Drei Staffeln sind eine Serie, dazu eine ohne Nummer: zwei. Gezaehlt "
        f"wurden {bibliothek['serien']}."
    )
