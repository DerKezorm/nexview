"""Jeder Endpunkt bei weggebrochener Instanz.

⚠️ **Die Frage, die im Betrieb wirklich zaehlt.** Dass etwas funktioniert, wenn
alles laeuft, ist der leichte Teil. Interessant ist, was passiert, wenn Radarr
gerade neu startet, der Container weg ist oder jemand den Stecker gezogen hat.

Zwei Antworten sind falsch und beide kommen vor:
* **Ein 500er.** Dann sieht der Betreiber "Interner Fehler" und sucht bei
  Nexview, obwohl das Problem eine Tuer weiter liegt.
* **Stillschweigen.** Eine leere Liste, die aussieht wie "nichts zu tun",
  waehrend in Wahrheit niemand gefragt werden konnte - das ist die
  gefaehrlichere Variante.

Richtig ist: **benennen**, dass diese Instanz gerade nicht antwortet, und den
Rest trotzdem ausliefern.

Die Instanzen zeigen hier auf Port 9 - der lehnt Verbindungen sofort ab, der
Fehlerfall tritt also verlaesslich und schnell ein.
"""

from __future__ import annotations

import pytest

STAMM = "/api/settings/qualitaetsprofile"

REZEPT = {
    "name": "Pruefprofil", "typ": "radarr", "aufloesung": "1080p",
    "sofortNehmen": True, "quelle": "remux", "sprachen": ["de"],
    "sprachRollen": {"de": "pflicht"}, "mehrerePflicht": "alle",
    "hdr": "netz", "schlusspunkt": "trash",
}


# ---------------------------------------------------------------------------
# Lesende Wege: melden, statt zu schweigen oder abzustuerzen
# ---------------------------------------------------------------------------


def test_liste_kommt_auch_ohne_instanz(arr_client) -> None:
    """Die Ablage liegt in Nexview - sie haengt nicht an Radarr.

    ⚠️ Das ist der Sinn der Trennung: Wer seine Profile ansehen will, soll das
    koennen, auch wenn gerade keine Instanz erreichbar ist.
    """
    antwort = arr_client.get(STAMM)
    assert antwort.status_code == 200
    assert antwort.json() == []


def test_benennung_meldet_unerreichbar_statt_zu_scheitern(arr_client) -> None:
    antwort = arr_client.get(f"{STAMM}/benennung")
    assert antwort.status_code == 200, antwort.text
    eintraege = antwort.json()
    assert eintraege, "Eingerichtete Instanzen muessen aufgefuehrt werden"
    for eintrag in eintraege:
        assert eintrag["erreichbar"] is False
        # ⚠️ Und nichts behaupten, das nicht geprueft werden konnte.
        assert eintrag["altnamen"]["im_dateinamen"] == 0
        assert eintrag["lauf_offen"] is False


def test_medienserver_lage_meldet_unerreichbar(arr_client) -> None:
    antwort = arr_client.get(f"{STAMM}/medienserver")
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["instanzen"], "Instanzen gehoeren aufgefuehrt"
    for eintrag in daten["instanzen"]:
        assert eintrag["erreichbar"] is False
    # ⚠️ Keine Warnung erfinden: Nicht geprueft ist nicht dasselbe wie kaputt.
    assert daten["warnungen"] == []


def test_abgleich_meldet_unerreichbar_je_installation(arr_client) -> None:
    """Ein Profil, das auf einer stummen Instanz liegt, gilt nicht als "aktuell"."""
    profil_id = arr_client.post(
        STAMM, json={"name": "P", "dienst": "radarr", "rezept": REZEPT}
    ).json()["id"]
    # Eine Installation vortaeuschen, ohne sie schreiben zu koennen.
    from app.db import SessionLocal
    from app.models import QualitaetsprofilInstallation

    with SessionLocal() as db:
        db.add(
            QualitaetsprofilInstallation(
                profil_id=profil_id, kennung="radarr-standard",
                profil_id_extern=7, fingerabdruck="x", trash_stand="2026-01-01",
            )
        )
        db.commit()

    antwort = arr_client.get(f"{STAMM}/abgleich")
    assert antwort.status_code == 200, antwort.text
    staende = {e["stand"] for e in antwort.json()}
    assert staende == {"unerreichbar"}, f"unerwartet: {staende}"


def test_quelle_kommt_ohne_netz(arr_client) -> None:
    """Der mitgelieferte Schnappschuss braucht weder Instanz noch Internet."""
    antwort = arr_client.get(f"{STAMM}/quelle")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["stand"] and daten["lizenz"] == "MIT"


def test_fortschritt_ohne_lauf_ist_ruhig(arr_client) -> None:
    antwort = arr_client.get(f"{STAMM}/benennung/radarr-standard/fortschritt")
    assert antwort.status_code == 200
    assert antwort.json()["laeuft"] is False


# ---------------------------------------------------------------------------
# Schreibende Wege: eine nennbare Kennung, kein 500er
# ---------------------------------------------------------------------------


def test_verteilen_auf_stumme_instanz_nennt_den_grund(arr_client) -> None:
    profil_id = arr_client.post(
        STAMM, json={"name": "P", "dienst": "radarr", "rezept": REZEPT}
    ).json()["id"]
    antwort = arr_client.put(
        f"{STAMM}/{profil_id}/instanzen", json={"kennungen": ["radarr-standard"]}
    )
    assert antwort.status_code == 502, antwort.text
    kennung = antwort.json()["detail"]["code"]
    assert kennung.startswith("arr_"), f"unerwartete Kennung: {kennung}"


def test_benennung_uebernehmen_ohne_instanz(arr_client) -> None:
    antwort = arr_client.put(
        f"{STAMM}/benennung",
        json={"kennung": "radarr-standard", "datei": True, "ordner": False,
              "bestand": False},
    )
    assert antwort.status_code == 502, antwort.text
    assert antwort.json()["detail"]["code"].startswith("arr_")


def test_altnamen_aufraeumen_ohne_instanz(arr_client) -> None:
    antwort = arr_client.post(f"{STAMM}/benennung/radarr-standard/altnamen", json={})
    assert antwort.status_code == 502, antwort.text
    assert antwort.json()["detail"]["code"] == "quality_instance_unreachable"


def test_verbinden_ohne_instanz_meldet_statt_abzustuerzen(arr_client) -> None:
    """⚠️ Hier darf **kein** Fehlercode kommen, sondern eine Liste.

    Der Aufruf gilt fuer alle Instanzen. Waere eine stumme Instanz ein
    Abbruch, koennte man bei drei Instanzen nie verbinden, sobald eine davon
    gerade neu startet. Also: was ging, wird eingetragen; was nicht ging,
    steht namentlich in ``gescheitert``.
    """
    antwort = arr_client.post(f"{STAMM}/medienserver/verbinden", json={"kennungen": []})
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert daten["hergestellt"] == 0
    assert daten["gescheitert"], "Eine stumme Instanz muss benannt werden"


def test_unbekannte_kennungen_werden_sauber_abgelehnt(arr_client) -> None:
    """Tippfehler in der Kennung: 400 mit Kennung, nicht 500."""
    for pfad, methode, koerper in (
        (f"{STAMM}/benennung", "put",
         {"kennung": "gibtsnicht", "datei": True, "ordner": False, "bestand": False}),
        (f"{STAMM}/benennung/gibtsnicht/altnamen", "post", {}),
    ):
        antwort = getattr(arr_client, methode)(pfad, json=koerper)
        assert antwort.status_code == 400, f"{pfad}: {antwort.text}"
        assert antwort.json()["detail"]["code"] == "quality_instance_unknown"


def test_schluessel_fuer_unbekannten_server(arr_client) -> None:
    antwort = arr_client.put(
        f"{STAMM}/medienserver/schluessel", json={"server_id": 9999, "schluessel": "x"}
    )
    assert antwort.status_code == 404
    assert antwort.json()["detail"]["code"] == "mediaserver_unknown"


# ---------------------------------------------------------------------------
# Nur Admins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "methode,pfad",
    [
        ("get", STAMM),
        ("get", f"{STAMM}/abgleich"),
        ("get", f"{STAMM}/benennung"),
        ("get", f"{STAMM}/medienserver"),
        ("get", f"{STAMM}/quelle"),
        ("post", f"{STAMM}/quelle/aktualisieren"),
        ("post", f"{STAMM}/medienserver/verbinden"),
        ("put", f"{STAMM}/medienserver/schluessel"),
        ("post", f"{STAMM}/benennung/radarr-standard/altnamen"),
    ],
)
def test_kein_zugriff_ohne_adminrechte(client, methode, pfad) -> None:
    """⚠️ Jeder Endpunkt einzeln - der Schutz haengt hier an jedem Weg.

    Er steht bewusst nicht einmal am Router, damit ein vergessener Endpunkt
    beim Lesen auffaellt. Damit er auch beim *Testen* auffaellt, wird jeder
    Weg aufgezaehlt.
    """
    # GET nimmt keinen Koerper - sonst wirft der Testclient, bevor er fragt.
    antwort = (
        client.get(pfad) if methode == "get" else getattr(client, methode)(pfad, json={})
    )
    assert antwort.status_code in (401, 403), f"{pfad} ist ungeschuetzt!"


# ---------------------------------------------------------------------------
# Wege, die keine erreichbare Instanz brauchen
# ---------------------------------------------------------------------------


def test_schreibfortschritt_ohne_laufenden_vorgang(arr_client) -> None:
    """Kein Vorgang heisst ``laeuft: false`` - nicht 404.

    ⚠️ Die Oberflaeche fragt das im Sekundentakt. Ein Fehler waere dort keine
    Auskunft, sondern eine rote Meldung, die nichts bedeutet.
    """
    profil_id = arr_client.post(
        STAMM, json={"name": "P", "dienst": "radarr", "rezept": REZEPT}
    ).json()["id"]
    antwort = arr_client.get(f"{STAMM}/{profil_id}/fortschritt")
    assert antwort.status_code == 200
    assert antwort.json()["laeuft"] is False


def test_ein_unbaubares_rezept_wird_benannt(arr_client) -> None:
    """⚠️ Eine Antwortkombination ohne TRaSH-Vorlage gibt eine eigene Kennung.

    Sonst laege der Fehler bei "Radarr meldet einen Fehler" - und der Betreiber
    suchte an der falschen Stelle. Franzoesisch ist so ein Fall: TRaSH fuehrt
    drei Fassungen, und Nexview stellt die Frage nicht, die dazwischen
    entscheidet.
    """
    kaputt = {**REZEPT, "aufloesung": "480p", "quelle": "gibtsnicht"}
    profil_id = arr_client.post(
        STAMM, json={"name": "Unbaubar", "dienst": "radarr", "rezept": kaputt}
    ).json()["id"]
    antwort = arr_client.put(
        f"{STAMM}/{profil_id}/instanzen", json={"kennungen": ["radarr-standard"]}
    )
    assert antwort.status_code in (409, 502), antwort.text
    kennung = antwort.json()["detail"]["code"]
    assert kennung in ("quality_recipe_unsupported", "arr_unreachable", "arr_http_error")


def test_medienserver_schluessel_speichern_und_loeschen(arr_client) -> None:
    """Der Schluessel wird hinterlegt - und ein leerer Wert nimmt ihn zurueck.

    ⚠️ Das Zuruecknehmen gehoert dazu: Ein toter Schluessel ist schlechter als
    keiner, weil die Oberflaeche ihn als "liegt vor" zeigt.
    """
    from app.db import SessionLocal
    from app.models import MediaServerConnection

    with SessionLocal() as db:
        server = MediaServerConnection(
            provider="jellyfin", machine_id="m1", name="Jellyfin",
            url="http://127.0.0.1:8096", token="", account_id="",
        )
        db.add(server)
        db.commit()
        server_id = server.id

    gesetzt = arr_client.put(
        f"{STAMM}/medienserver/schluessel",
        json={"server_id": server_id, "schluessel": "geheim"},
    )
    assert gesetzt.status_code == 204, gesetzt.text
    with SessionLocal() as db:
        assert db.get(MediaServerConnection, server_id).arr_api_key, "nichts gespeichert"

    geleert = arr_client.put(
        f"{STAMM}/medienserver/schluessel",
        json={"server_id": server_id, "schluessel": ""},
    )
    assert geleert.status_code == 204
    with SessionLocal() as db:
        assert db.get(MediaServerConnection, server_id).arr_api_key == ""
