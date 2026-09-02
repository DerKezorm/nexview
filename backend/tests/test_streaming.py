"""Die eigenen Streaming-Abos und der Hinweis „laeuft schon in deinem Abo".

Geprueft wird das, was schiefgehen kann, ohne dass es jemand merkt: dass die
Markenliste eine Kennung doppelt vergibt, dass der Abgleich an *fremden* Abos
gemessen wird, und dass die Auswahl einen Regionswechsel ueberlebt.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, StreamingService, User
from app.services import streaming

from .conftest import auth_headers, create_user

# --- Die Markenliste selbst -------------------------------------------------


def test_keine_kennung_zweimal_vergeben() -> None:
    """Eine TMDB-Kennung gehoert zu genau einer Marke.

    Stuende 8 bei Netflix *und* bei einer anderen Marke, waere der Abgleich
    nicht mehr eindeutig - und welche Marke gewinnt, haenge an der
    Reihenfolge im Quelltext.
    """
    gesehen: dict[int, str] = {}
    for marke in streaming.MARKEN:
        for kennung in marke.kennungen:
            assert kennung not in gesehen, (
                f"Kennung {kennung} steht bei '{marke.name}' und bei "
                f"'{gesehen[kennung]}'"
            )
            gesehen[kennung] = marke.name


def test_slugs_sind_eindeutig() -> None:
    slugs = [marke.slug for marke in streaming.MARKEN]
    assert len(slugs) == len(set(slugs))


def test_keine_untermieter_und_keine_kaufhaeuser() -> None:
    """Die Kennungen der Kaufhaeuser und Untermieter duerfen nicht drinstehen.

    2 = Apple TV Store, 3 = Google Play Movies, 35 = Rakuten, 130 = Sky Store
    sind Leih- und Kaufhaeuser: Dort „hat" man nichts. 1825 und 582 sind
    Amazon-Kanaele - wer Prime bezahlt, hat damit nicht HBO Max ueber Prime.
    """
    verboten = {2, 3, 35, 130, 1825, 582, 2243, 1853}
    assert not (verboten & set(streaming.NACH_KENNUNG))


# --- Der Abgleich -----------------------------------------------------------


def test_treffer_nennt_nur_eigene_dienste() -> None:
    # 8 = Netflix, 337 = Disney+, 9 = Amazon. Angehakt sind nur die ersten
    # beiden - Amazon darf nicht auftauchen.
    assert streaming.treffer({"netflix", "disney-plus"}, [8, 337, 9]) == [
        "Netflix",
        "Disney+",
    ]


def test_treffer_kennt_die_tarif_varianten() -> None:
    """1796 ist „Netflix Standard with Ads" - fuer den Zuschauer ist das Netflix.

    Wer den Haken bei Netflix setzt, meint alle Tarife. Ein Titel, der nur im
    Werbe-Tarif liegt, ist fuer ihn trotzdem da.
    """
    assert streaming.treffer({"netflix"}, [1796]) == ["Netflix"]
    assert streaming.treffer({"netflix"}, [175]) == ["Netflix"]


def test_treffer_kennt_amazon_in_der_schweiz() -> None:
    """Amazon ist 9 in Deutschland und 119 in der Schweiz - eine Marke."""
    assert streaming.treffer({"amazon-prime-video"}, [9]) == ["Amazon Prime Video"]
    assert streaming.treffer({"amazon-prime-video"}, [119]) == ["Amazon Prime Video"]


def test_treffer_ist_immer_gleich_herum() -> None:
    """Zwei Treffer stehen heute und morgen in derselben Reihenfolge.

    Die Reihenfolge folgt ``MARKEN``, nicht der Reihenfolge, in der TMDB die
    Anbieter liefert - sonst hiesse es mal „Netflix und Disney+" und mal
    umgekehrt.
    """
    a = streaming.treffer({"netflix", "disney-plus"}, [337, 8])
    b = streaming.treffer({"netflix", "disney-plus"}, [8, 337])
    assert a == b


def test_ohne_angaben_kein_hinweis() -> None:
    assert streaming.treffer(set(), [8, 337]) == []
    assert streaming.treffer({"netflix"}, []) == []


# --- Die Auswahl ueber die API ---------------------------------------------


@pytest.fixture()
def nutzer(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "abonnent")
    return auth_headers(admin_client, "abonnent", "passwort-1234")


def test_auswahl_speichern_und_lesen(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    antwort = admin_client.put(
        "/api/streaming", json={"slugs": ["netflix", "wow"]}, headers=nutzer
    )
    assert antwort.status_code == 200, antwort.text
    assert sorted(antwort.json()["meine"]) == ["netflix", "wow"]

    erneut = admin_client.get("/api/streaming", headers=nutzer)
    assert sorted(erneut.json()["meine"]) == ["netflix", "wow"]


def test_auswahl_ersetzt_statt_zu_ergaenzen(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    admin_client.put("/api/streaming", json={"slugs": ["netflix", "wow"]}, headers=nutzer)
    antwort = admin_client.put("/api/streaming", json={"slugs": ["netflix"]}, headers=nutzer)
    assert antwort.json()["meine"] == ["netflix"]


def test_unbekannter_dienst_wird_abgelehnt(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    antwort = admin_client.put(
        "/api/streaming", json={"slugs": ["gibtsnicht"]}, headers=nutzer
    )
    assert antwort.status_code == 422
    assert "gibtsnicht" in antwort.text


def test_dienst_ausserhalb_der_region_bleibt_erlaubt(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Hulu gibt es in Deutschland nicht - der Haken bleibt trotzdem stehen.

    Wer die Region wechselt, soll seine Haken wiederfinden, statt sie beim
    Wechsel stillschweigend zu verlieren.
    """
    antwort = admin_client.put("/api/streaming", json={"slugs": ["hulu"]}, headers=nutzer)
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["meine"] == ["hulu"]


def test_region_wird_als_geerbt_gemeldet(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    """Wer nie eine Region gewaehlt hat, soll das erfahren.

    Der Einrichtungsassistent fragt nicht danach - ohne diesen Hinweis liefe
    die Mehrheit auf der Vorgabe des Betreibers, ohne davon zu wissen.
    """
    antwort = admin_client.get("/api/streaming", headers=nutzer)
    assert antwort.json()["region_selbst_gewaehlt"] is False

    admin_client.patch("/api/auth/me", json={"discover_region": "AT"}, headers=nutzer)
    antwort = admin_client.get("/api/streaming", headers=nutzer)
    assert antwort.json()["region_selbst_gewaehlt"] is True
    assert antwort.json()["region"] == "AT"


def test_kind_kommt_nicht_an_die_dienste(admin_client: TestClient) -> None:
    """Kinderkonten haben keine eigenen Abos - sie gucken ueber die der Eltern."""
    create_user(admin_client, "kindkonto", role=Role.child)
    kopf = auth_headers(admin_client, "kindkonto", "passwort-1234")
    assert admin_client.get("/api/streaming", headers=kopf).status_code == 403


def test_auswahl_verschwindet_mit_dem_konto(
    admin_client: TestClient, nutzer: dict[str, str]
) -> None:
    admin_client.put("/api/streaming", json={"slugs": ["netflix"]}, headers=nutzer)

    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "abonnent").one()
        db.delete(konto)
        db.commit()

        assert db.query(StreamingService).count() == 0
