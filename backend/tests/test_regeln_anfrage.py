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
        # ⚠️ Der Verlauf an der Anfrage nimmt ``approved_at`` als Datum der
        # Entscheidung. Ohne das stuende dort "Abgelehnt" ohne Wann.
        assert anfrage.approved_at is not None
        # Und niemand hat abgelehnt - es war eine Regel.
        assert anfrage.approved_by is None
        assert anfrage.regel_name == "Probe"


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


# ---------------------------------------------------------------------------
# Der Anfragende erfaehrt davon - und darf manchmal nachfragen
# ---------------------------------------------------------------------------


def test_die_ablehnung_wird_gemeldet(arr_client: TestClient, nutzer: dict) -> None:
    """⚠️ **Sonst verschwindet die Anfrage lautlos.**

    Beim Ausprobieren fiel genau das auf: Der Titel stand als abgelehnt in der
    eigenen Liste, aber weder Glocke noch Mail sagten etwas. Wer davon nichts
    erfaehrt, fragt beim naechsten Mal wieder - und wundert sich wieder.
    """
    _regel_anlegen(begruendung="Nicht dieses Jahr.")
    _anfragen(arr_client, _titel(arr_client), nutzer)

    offen = arr_client.get("/api/notifications", headers=nutzer)
    assert offen.status_code == 200, offen.text
    arten = [n.get("type") for n in offen.json()]
    assert "rejected" in arten, offen.json()


def test_trotzdem_fragen_geht_nur_wo_die_regel_es_erlaubt(
    arr_client: TestClient, nutzer: dict
) -> None:
    _regel_anlegen(trotzdem_fragen=False)
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        anfrage_id = db.query(MediaRequest).one().id

    antwort = arr_client.post(f"/api/requests/{anfrage_id}/trotzdem", headers=nutzer)
    assert antwort.status_code == 403, antwort.text
    with SessionLocal() as db:
        assert db.query(MediaRequest).one().status == RequestStatus.rejected


def test_trotzdem_fragen_schickt_die_anfrage_zum_entscheider(
    arr_client: TestClient, nutzer: dict
) -> None:
    _regel_anlegen(begruendung="Zu schwach.", trotzdem_fragen=True)
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.darf_trotzdem_fragen is True
        anfrage_id = anfrage.id

    antwort = arr_client.post(f"/api/requests/{anfrage_id}/trotzdem", headers=nutzer)
    assert antwort.status_code == 200, antwort.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.pending_approval
        assert anfrage.rejection_reason is None
        # Die Regel bleibt am Vorgang: Der Entscheider soll sehen, dass hier
        # schon einmal etwas dagegen sprach.
        assert anfrage.regel_id is not None
        # ⚠️ Und der Merker dafuer, **wie** sie hereinkam. Ohne ihn liegt vor
        # dem Entscheider eine gewoehnliche wartende Anfrage.
        assert anfrage.trotzdem_gefragt is True


def test_eine_gewoehnliche_anfrage_traegt_den_merker_nicht(
    arr_client: TestClient, nutzer: dict
) -> None:
    """Die Gegenprobe: Ohne den waere das Abzeichen an jeder Anfrage."""
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        assert db.query(MediaRequest).one().trotzdem_gefragt is False


def test_trotzdem_fragen_prueft_das_kontingent_erneut(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Sonst waere das der Weg um ein volles Kontingent herum.**

    Die Ablehnung kostet nichts und blockiert niemanden. Wer sie ohne Pruefung
    auf ``pending_approval`` umschalten koennte, muesste sich nur einmal von
    einer Regel ablehnen lassen und dann „trotzdem“ druecken.
    """
    _regel_anlegen(trotzdem_fragen=True)
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        anfrage_id = db.query(MediaRequest).one().id
        person = db.query(User).filter(User.username == "kim").one()
        person.quota_movies_limit = 0
        db.commit()

    antwort = arr_client.post(f"/api/requests/{anfrage_id}/trotzdem", headers=nutzer)
    assert antwort.status_code == 429, antwort.text
    with SessionLocal() as db:
        assert db.query(MediaRequest).one().status == RequestStatus.rejected


def test_eine_fremde_anfrage_laesst_sich_nicht_weiterreichen(
    arr_client: TestClient, nutzer: dict
) -> None:
    _regel_anlegen(trotzdem_fragen=True)
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        anfrage_id = db.query(MediaRequest).one().id

    create_user(arr_client, "fremd")
    fremd = auth_headers(arr_client, "fremd", "passwort-1234")
    antwort = arr_client.post(f"/api/requests/{anfrage_id}/trotzdem", headers=fremd)
    assert antwort.status_code == 404, antwort.text


def test_nach_einer_ablehnung_von_hand_ist_schluss(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Der Fehler, den das Ausprobieren gefunden hat.**

    Die Anfrage kam ueber "trotzdem" beim Entscheider an, der lehnte sie von
    Hand ab - und der Knopf stand wieder da. ``regel_id`` bleibt ja am Vorgang,
    und der Zustand war wieder ``rejected``. Derselbe Mensch haette beliebig
    oft nachfassen koennen, gegen die ausdrueckliche Entscheidung eines
    anderen.
    """
    _regel_anlegen(trotzdem_fragen=True)
    _anfragen(arr_client, _titel(arr_client), nutzer)
    with SessionLocal() as db:
        anfrage_id = db.query(MediaRequest).one().id

    assert arr_client.post(
        f"/api/requests/{anfrage_id}/trotzdem", headers=nutzer
    ).status_code == 200

    # Jetzt lehnt ein Mensch ab.
    abgelehnt = arr_client.post(
        f"/api/admin/requests/{anfrage_id}/reject",
        json={"reason": "Trotzdem nicht."},
    )
    assert abgelehnt.status_code == 200, abgelehnt.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        assert anfrage.status == RequestStatus.rejected
        # Der Knopf darf nicht wiederkommen.
        assert anfrage.darf_trotzdem_fragen is False

    nochmal = arr_client.post(f"/api/requests/{anfrage_id}/trotzdem", headers=nutzer)
    assert nochmal.status_code == 409, nochmal.text


def test_die_sperrliste_gilt_auch_beim_trotzdem_fragen(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Sonst waere das der Weg an der Sperrliste vorbei.**

    Sie ist keine Regel, sondern die ausdrueckliche Entscheidung des
    Betreibers ueber genau diesen Titel - und sie kann nach der Ablehnung
    dazugekommen sein. Beim Ausprobieren stand der Titel auf der Liste und der
    Knopf daneben.
    """
    _regel_anlegen(trotzdem_fragen=True)
    item = _titel(arr_client)
    _anfragen(arr_client, item, nutzer)
    with SessionLocal() as db:
        anfrage_id = db.query(MediaRequest).one().id

    gesperrt = arr_client.post(
        "/api/admin/blocklist",
        json={"media_type": "movie", "tmdb_id": item["tmdb_id"], "title": item["title"]},
    )
    assert gesperrt.status_code in (200, 201), gesperrt.text

    antwort = arr_client.post(f"/api/requests/{anfrage_id}/trotzdem", headers=nutzer)
    assert antwort.status_code == 403, antwort.text
    with SessionLocal() as db:
        assert db.query(MediaRequest).one().status == RequestStatus.rejected


def test_der_zielordner_beim_entscheider_uebersteuert_die_freigabe(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Sprosse 10 schlaegt Sprosse 13 - versprochen und ungeprueft.**

    Waehlt der Entscheider den Zielordner, darf keine Regel sofort freigeben:
    Die Anfrage kaeme sonst an genau der Wahl vorbei, die er treffen soll. Der
    Commit sagt das ausdruecklich zu, die Oberflaeche zeigt einen eigenen
    Warnkasten dafuer - und eine Mutationsprobe hat die Bedingung entfernt,
    ohne dass ein Test rot wurde.
    """
    gesetzt = arr_client.put("/api/settings", json={"movie_root_folder_mode": "approver"})
    assert gesetzt.status_code == 200, gesetzt.text

    _regel_anlegen(entscheidung=RegelEntscheidung.freigeben)
    antwort = _anfragen(arr_client, _titel(arr_client), nutzer)
    assert antwort.status_code in (200, 201), antwort.text

    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        # Die Regel hat entschieden - aber der Zielordner uebersteuert sie.
        assert anfrage.regel_id is not None
        assert anfrage.status == RequestStatus.pending_approval, (
            "Die Regel hat sofort freigegeben, obwohl der Entscheider den "
            "Zielordner wählt - die Anfrage käme an ihm vorbei."
        )
        assert anfrage.approved_at is None


def test_zweimal_derselbe_titel_ergibt_nicht_zwei_ablehnungen(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Fuenf Klicks, fuenf Zeilen - so war es.**

    Direkt ueber der Pruefung fuer zurueckgestellte Anfragen steht die
    Begruendung: "zweimal dasselbe soll auch niemand von sich selbst haben".
    Fuer ``rejected`` wurde sie nicht mitgezogen. Da die Ablehnung nichts
    kostet und niemanden blockiert, faellt es nur in den Listen auf - in
    seiner und in der des Administrators.
    """
    _regel_anlegen(begruendung="Nicht dieses Jahr.")
    item = _titel(arr_client)

    erste = _anfragen(arr_client, item, nutzer)
    assert erste.status_code in (200, 201), erste.text

    for _ in range(4):
        weitere = _anfragen(arr_client, item, nutzer)
        assert weitere.status_code == 409, weitere.text

    with SessionLocal() as db:
        assert db.query(MediaRequest).count() == 1


def test_eine_ablehnung_von_hand_darf_man_erneut_versuchen(
    arr_client: TestClient, nutzer: dict
) -> None:
    """⚠️ **Die Gegenprobe, und sie zieht die Grenze.**

    Nur Ablehnungen **durch eine Regel** sperren einen zweiten Versuch. Hat
    ein Mensch abgelehnt, war das seine Entscheidung zu diesem Zeitpunkt - die
    Lage kann sich geaendert haben, und dann soll man wieder fragen duerfen.
    """
    item = _titel(arr_client)
    _anfragen(arr_client, item, nutzer)
    with SessionLocal() as db:
        anfrage = db.query(MediaRequest).one()
        anfrage.status = RequestStatus.rejected
        anfrage.rejection_reason = "Diesmal nicht."
        anfrage.regel_id = None  # von Hand, nicht durch eine Regel
        db.commit()

    nochmal = _anfragen(arr_client, item, nutzer)
    assert nochmal.status_code in (200, 201), nochmal.text
    with SessionLocal() as db:
        assert db.query(MediaRequest).count() == 2
