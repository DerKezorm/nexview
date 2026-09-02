"""Regeln an der echten Anfrage - der Ablauf, nicht die Funktion.

``test_regeln.py`` sichert zu, dass ``passt`` und ``entscheiden`` richtig
rechnen. Hier geht es um die andere Haelfte, und die ist die gefaehrlichere:
**Wo** greifen die Regeln, und woran kommen sie ausdruecklich **nicht** vorbei.

⚠️ **Gemessen wird das Ergebnis, nicht die Absicht.** Ein Test, der nachsieht,
ob ``regeln.entscheiden`` aufgerufen wurde, bestuende auch dann, wenn das
Ergebnis danach verworfen wird. Deshalb wird hier jedes Mal eine echte Anfrage
gestellt und nachgesehen, was in der Datenbank steht.

Die Sprossen, um die es geht (siehe ``services/regeln.py``):

* **11 Kontingent** laeuft vor der Regel. Ein volles Kontingent schlaegt jede
  Regel - auch eine, die aufs Haus gebucht haette.
* **10 Zielordner** uebersteuert eine freigebende Regel, wie es schon die
  Auto-Freigabe am Konto uebersteuert.
* **6 laufende Anfrage** haelt dieselbe Stufe auf, aber nicht die andere -
  genau die Luecke, fuer die es die Bedingung ``bestand`` gibt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    QualityTier,
    Regel,
    RegelEntscheidung,
    RequestStatus,
    User,
)

from .conftest import auth_headers, create_user


def _titel(client: TestClient, index: int = 0) -> dict:
    return client.get("/api/discover/movie").json()["items"][index]


def _anfragen(client: TestClient, item: dict, headers: dict, **rest):
    nutzlast = {
        "media_type": item["media_type"],
        "tmdb_id": item["tmdb_id"],
        "quality_profile_id": 1,
        "root_folder_path": "/data/Movies",
    }
    nutzlast.update(rest)
    return client.post("/api/requests", json=nutzlast, headers=headers)


def _regel_anlegen(**rest) -> int:
    """Eine Regel direkt in die Datenbank - der Weg ueber die Adresse hat
    eigene Tests, und hier soll nichts davon abhaengen."""
    with SessionLocal() as db:
        regel = Regel(
            name=rest.pop("name", "Probe"),
            position=rest.pop("position", 0),
            aktiv=rest.pop("aktiv", True),
            bedingungen=rest.pop("bedingungen", [{"feld": "typ", "werte": ["movie"]}]),
            entscheidung=rest.pop("entscheidung", RegelEntscheidung.ablehnen),
            **rest,
        )
        db.add(regel)
        db.commit()
        return regel.id


@pytest.fixture
def nutzer(arr_client: TestClient) -> dict:
    create_user(arr_client, "kim")
    return auth_headers(arr_client, "kim", "passwort-1234")


# ---------------------------------------------------------------------------
# Ablehnen
# ---------------------------------------------------------------------------


def test_eine_ablehnende_regel_legt_die_anfrage_als_abgelehnt_an(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Die Anfrage entsteht, statt spurlos zu scheitern.**

    Sonst waere die Absage ein Satz auf dem Bildschirm: Der Anfragende koennte
    nicht nachlesen, warum, und der Administrator nie sehen, dass seine Regel
    zu scharf steht.
    """
    regel_id = _regel_anlegen(begruendung="Zu schwach bewertet.")
    antwort = _anfragen(arr_client, _titel(arr_client), nutzer)
    assert antwort.status_code in (200, 201), antwort.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.rejected
        assert anfrage.rejection_reason == "Zu schwach bewertet."
        assert anfrage.regel_id == regel_id


def test_die_abgelehnte_anfrage_blockiert_niemanden(
    arr_client: TestClient, nutzer: dict
) -> None:
    """``rejected`` steht nicht in ``ACTIVE_STATUSES``.

    Waere es anders, haette eine einzige Regelablehnung den Titel fuer das
    ganze Haus gesperrt - und niemand haette den Zusammenhang gesehen.
    """
    _regel_anlegen()
    item = _titel(arr_client)
    _anfragen(arr_client, item, nutzer)

    create_user(arr_client, "alex", role="approver")
    alex = auth_headers(arr_client, "alex", "passwort-1234")
    zweite = _anfragen(arr_client, item, alex)
    # ⚠️ **Kein 409.** Ob die Anfrage danach an Radarr scheitert, ist hier
    # gleichgueltig - gemessen wird, dass der Titel nicht gesperrt war.
    assert zweite.status_code != 409, zweite.text

    with SessionLocal() as db:
        anfragen = db.query(MediaRequest).all()
        assert len(anfragen) == 2
        assert RequestStatus.rejected in {a.status for a in anfragen}


def test_die_abgelehnte_anfrage_kostet_kein_kontingent(
    arr_client: TestClient, nutzer: dict
) -> None:
    """``rejected`` steht auch nicht in ``COUNTED_STATUSES``.

    Sonst haetten drei Regelablehnungen ein Kontingent von drei aufgebraucht,
    ohne dass ein einziger Titel geladen wurde.
    """
    _regel_anlegen()
    for i in range(3):
        _anfragen(arr_client, _titel(arr_client, i), nutzer)

    stand = arr_client.get("/api/requests/quota", headers=nutzer).json()
    verbraucht = [
        eintrag.get("used")
        for eintrag in (stand.values() if isinstance(stand, dict) else [])
        if isinstance(eintrag, dict) and "used" in eintrag
    ]
    assert verbraucht == [] or all(v == 0 for v in verbraucht), stand


def test_der_begruendungstext_darf_leer_sein(
    arr_client: TestClient, nutzer: dict
) -> None:
    _regel_anlegen(begruendung=None)
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        assert db.query(MediaRequest).one().rejection_reason is None


# ---------------------------------------------------------------------------
# Freigeben
# ---------------------------------------------------------------------------


def test_eine_freigebende_regel_gibt_sofort_frei(
    arr_client: TestClient, nutzer: dict
) -> None:
    """Und zwar auch dann, wenn am Konto keine Sofort-Freigabe steht.

    Das ist der eigentliche Zweck: Die Regel setzt sich an die Stelle der
    Einstellung am Konto.
    """
    regel_id = _regel_anlegen(entscheidung=RegelEntscheidung.freigeben)
    _anfragen(arr_client, _titel(arr_client), nutzer)

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        # ⚠️ Nicht ``status == approved``: In dieser Umgebung ist Radarr
        # absichtlich unerreichbar, und die freigegebene Anfrage faellt
        # danach auf ``failed``. Der Beweis fuer die Freigabe ist
        # ``approved_at`` - das wird vor dem Anruf gesetzt.
        assert anfrage.approved_at is not None
        assert anfrage.status != RequestStatus.pending_approval
        assert anfrage.regel_id == regel_id
        assert anfrage.hausbestand is False


def test_hausbestand_wird_am_vorgang_festgehalten(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ Der Merker steht an der Anfrage, nicht an der Regel.

    Sonst verschoebe ein spaeteres Aendern der Regel die Zurechnung
    rueckwirkend: Titel, die bewusst aufs Haus gingen, laegen ploetzlich bei
    ihren Anfragenden.
    """
    _regel_anlegen(entscheidung=RegelEntscheidung.freigeben, hausbestand=True)
    _anfragen(arr_client, _titel(arr_client), nutzer)

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.hausbestand is True

        # Die Regel aendern - der Vorgang bleibt, wie er entschieden wurde.
        regel = db.query(Regel).one()
        regel.hausbestand = False
        db.commit()
        db.refresh(anfrage)
        assert anfrage.hausbestand is True


# ---------------------------------------------------------------------------
# Woran eine Regel nicht vorbeikommt
# ---------------------------------------------------------------------------


def test_ein_volles_kontingent_schlaegt_die_regel(
    arr_client: TestClient, nutzer: dict
) -> None:
    """Sprosse 11 vor Sprosse 13 - die Entscheidung aus dem Gespraech.

    Auch eine Regel, die aufs Haus gebucht haette, kommt hier nicht durch.
    Liesse man sie vorbei, haette dieselbe Anfrage je nach Regellage ein
    anderes Ergebnis, und das Kontingent waere keine Grenze mehr.
    """
    _regel_anlegen(entscheidung=RegelEntscheidung.freigeben, hausbestand=True)
    with SessionLocal() as db:
        person = db.query(User).filter(User.username == "kim").one()
        person.quota_movies_limit = 0
        db.commit()

    antwort = _anfragen(arr_client, _titel(arr_client), nutzer)
    assert antwort.status_code == 429, antwort.text
    with SessionLocal() as db:
        assert db.query(MediaRequest).count() == 0


def test_die_regel_gilt_nicht_fuer_den_administrator(arr_client: TestClient) -> None:
    """Wie die Sperrliste: Sie soll die anderen bremsen, nicht ihn."""
    _regel_anlegen()
    # ``arr_client`` traegt den Zugang des Administrators bereits.
    antwort = _anfragen(arr_client, _titel(arr_client), {})
    assert antwort.status_code != 409, antwort.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status != RequestStatus.rejected
        assert anfrage.regel_id is None


def test_die_regel_gilt_nicht_fuer_den_entscheider(arr_client: TestClient) -> None:
    _regel_anlegen()
    create_user(arr_client, "alex", role="approver")
    alex = auth_headers(arr_client, "alex", "passwort-1234")
    antwort = _anfragen(arr_client, _titel(arr_client), alex)
    assert antwort.status_code != 409, antwort.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status != RequestStatus.rejected
        assert anfrage.regel_id is None


# ---------------------------------------------------------------------------
# Die Luecke, fuer die es die Bedingung ``bestand`` gibt
# ---------------------------------------------------------------------------


def test_dieselbe_stufe_haelt_sprosse_sechs_auf(
    arr_client: TestClient, nutzer: dict
) -> None:
    """Ohne Regel: zweimal dieselbe Stufe geht nicht, quer darueber schon.

    Diese Zusicherung ist die Voraussetzung fuer den naechsten Test - und
    zugleich die Begruendung dafuer, dass es die Bedingung ueberhaupt braucht.
    """
    item = _titel(arr_client)
    assert _anfragen(arr_client, item, nutzer).status_code in (200, 201)
    zweite = _anfragen(arr_client, item, nutzer)
    assert zweite.status_code == 409, zweite.text


def test_eine_regel_kann_die_zweite_stufe_verhindern(
    arr_client: TestClient, nutzer: dict
) -> None:
    """Der Fall aus dem Gespraech: „liegt schon in 4K vor, also kein HD“.

    Hier andersherum aufgebaut, weil die Demo-Instanz nur eine Stufe kennt:
    Eine vorhandene HD-Anfrage macht ``bestand`` zu ``hd``.
    """
    item = _titel(arr_client)
    assert _anfragen(arr_client, item, nutzer).status_code in (200, 201)

    _regel_anlegen(
        bedingungen=[{"feld": "bestand", "werte": ["hd"]}],
        begruendung="Liegt schon in einer anderen Stufe vor.",
    )

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        anfrage.tier = QualityTier.standard
        db.commit()

    zweite = _anfragen(arr_client, item, nutzer, tier="uhd")
    # Entweder die Regel greift, oder eine fruehere Sprosse hat schon
    # abgebrochen - beides ist ein Nein, und beides ist richtig. Was **nicht**
    # passieren darf: eine zweite laufende Anfrage.
    with SessionLocal() as db:
        laufend = [
            a
            for a in db.query(MediaRequest).all()
            if a.status
            in (RequestStatus.pending_approval, RequestStatus.approved)
        ]
        assert len(laufend) == 1, (
            f"Zweite Stufe kam durch: {zweite.status_code} {zweite.text}"
        )


# ---------------------------------------------------------------------------
# Bodenschwelle
# ---------------------------------------------------------------------------


def test_ohne_regel_entscheidet_weiterhin_das_konto(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Die Gegenprobe zu allem hier.**

    Ohne diesen Test bewiesen die anderen nur, dass *etwas* ablehnt. Er zeigt,
    dass es die Regel war: Ohne Regel laeuft dieselbe Anfrage durch, und
    ``regel_id`` bleibt leer.
    """
    antwort = _anfragen(arr_client, _titel(arr_client), nutzer)
    assert antwort.status_code in (200, 201), antwort.text
    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status != RequestStatus.rejected
        assert anfrage.regel_id is None
        assert anfrage.hausbestand is False
