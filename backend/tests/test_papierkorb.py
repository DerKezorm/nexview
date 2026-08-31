"""Der Papierkorb-Abgleich zwischen Nexview und Radarr/Sonarr.

**Nexview fuehrt hier keine eigene Einstellung.** Der Stand steht in Radarr
bzw. Sonarr, und nur dort; er wird bei jedem Aufruf frisch geholt. Wuerde
Nexview ihn zusaetzlich speichern, liefen die beiden auseinander, sobald jemand
ihn drueben aendert - und dann hielte Nexview eine Loeschung fuer umkehrbar,
die es nicht ist.

Zwei Anforderungen stehen dahinter, und beide sind hier festgenagelt:

1. Ist der Papierkorb **ausserhalb** von Nexview eingerichtet, ist der Haken
   trotzdem an. Es gibt keinen zweiten Zustand, der hinterherhinken koennte.
2. Die Logik ueberlebt das **Hinzufuegen** einer Instanz: Wer naechste Woche
   ein zweites Sonarr eintraegt, hat damit eine neue Stelle, an der geloescht
   wird - und ohne Papierkorb faellt der Gesamtzustand von selbst auf "aus".
"""

from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.services import library
from app.services.arr import ArrError
from app.services.settings_service import load_settings, save_settings

# Verschiedene Adressen: Beide Stufen duerfen nicht auf derselben liegen.
RADARR_4K = {"radarr_uhd_url": "http://127.0.0.1:11", "radarr_uhd_api_key": "vier-k"}
SONARR_4K = {"sonarr_uhd_url": "http://127.0.0.1:12", "sonarr_uhd_api_key": "vier-k"}


def _antworten(monkeypatch, koerbe: dict[tuple[str, str], object]) -> None:
    """Je Instanz vorgeben, was ``/config/mediamanagement`` liefert.

    Ein ``ArrError`` als Wert stellt eine eingerichtete, aber stumme Instanz
    dar - der Fall, der sich von "kein Papierkorb" unterscheiden muss.
    """

    async def stand(_settings, media_type: str, tier: str = "standard"):
        wert = koerbe.get((media_type, tier))
        if wert is None:
            return library.Papierkorb(erreichbar=True, path="", cleanup_days=7)
        if isinstance(wert, ArrError):
            return library.Papierkorb(erreichbar=False)
        return library.Papierkorb(erreichbar=True, path=str(wert), cleanup_days=7)

    monkeypatch.setattr(library, "papierkorb", stand)


def _stand(client) -> dict:
    antwort = client.get("/api/settings/recyclebin")
    assert antwort.status_code == 200, antwort.text
    return antwort.json()


# --- Der abgeleitete Haken -------------------------------------------------


def test_ausserhalb_eingerichtet_heisst_haken_an(arr_client, monkeypatch) -> None:
    """**Die Kernanforderung.**

    Wer den Papierkorb direkt in Radarr und Sonarr eintraegt, soll ihn in
    Nexview als eingeschaltet vorfinden - ohne dort irgendetwas zu tun.
    """
    _antworten(
        monkeypatch,
        {
            ("movie", "standard"): "/data/Papierkorb",
            ("tv", "standard"): "/data/Papierkorb",
        },
    )

    daten = _stand(arr_client)
    assert daten["enabled"] is True
    assert daten["complete"] is True
    assert {zeile["name"] for zeile in daten["instances"]} == {"Radarr", "Sonarr"}
    assert all(zeile["protected"] for zeile in daten["instances"])


def test_eine_instanz_ohne_papierkorb_schaltet_alles_aus(arr_client, monkeypatch) -> None:
    """Halb geschuetzt gibt es nicht - geloescht wird an jeder Stelle."""
    _antworten(monkeypatch, {("movie", "standard"): "/data/Papierkorb"})

    daten = _stand(arr_client)
    assert daten["enabled"] is False
    # Und es steht dabei, **welche** fehlt - sonst stuende da nur "aus".
    ohne = [z["name"] for z in daten["instances"] if not z["protected"]]
    assert ohne == ["Sonarr"]


def test_ohne_papierkorb_ueberall_ist_der_haken_aus(arr_client, monkeypatch) -> None:
    _antworten(monkeypatch, {})
    assert _stand(arr_client)["enabled"] is False


# --- Neue Instanzen ---------------------------------------------------------


def test_eine_neue_instanz_kippt_den_haken_von_selbst(arr_client, monkeypatch) -> None:
    """⚠️ **Die Anforderung, die das Speichern verbietet.**

    Heute eine Instanz mit Papierkorb, naechste Woche eine zweite ohne. Waere
    der Haken in Nexview gespeichert, stuende er weiterhin auf "an" - und
    Nexview hielte eine Loeschung fuer umkehrbar, die es nicht ist.
    """
    _antworten(
        monkeypatch,
        {
            ("movie", "standard"): "/data/Papierkorb",
            ("tv", "standard"): "/data/Papierkorb",
        },
    )
    assert _stand(arr_client)["enabled"] is True

    # Naechste Woche: ein 4K-Radarr kommt dazu, ohne Papierkorb.
    with SessionLocal() as db:
        save_settings(db, RADARR_4K)

    daten = _stand(arr_client)
    assert daten["enabled"] is False
    assert [z["name"] for z in daten["instances"] if not z["protected"]] == ["Radarr 4K"]


def test_die_neue_instanz_mit_papierkorb_stoert_nicht(arr_client, monkeypatch) -> None:
    """Kommt sie fertig eingerichtet dazu, bleibt alles wie es war."""
    _antworten(
        monkeypatch,
        {
            ("movie", "standard"): "/data/Papierkorb",
            ("tv", "standard"): "/data/Papierkorb",
            ("tv", "uhd"): "/data/Papierkorb",
        },
    )
    with SessionLocal() as db:
        save_settings(db, SONARR_4K)

    daten = _stand(arr_client)
    assert daten["enabled"] is True
    assert len(daten["instances"]) == 3


# --- Nicht erreichbar ist nicht ungeschuetzt --------------------------------


def test_stumme_instanz_ist_nicht_dasselbe_wie_kein_papierkorb(
    arr_client, monkeypatch
) -> None:
    """Sonst gibt es einen Fehlalarm, sobald Radarr gerade neu startet.

    Und Fehlalarme bringen genau die Warnung um ihre Wirkung, auf die es
    spaeter beim Loeschen ankommt.
    """
    _antworten(
        monkeypatch,
        {
            ("movie", "standard"): ArrError("weg", 502),
            ("tv", "standard"): "/data/Papierkorb",
        },
    )

    daten = _stand(arr_client)
    # Unvollstaendige Auskunft - die Oberflaeche sagt "unbekannt", nicht "aus".
    assert daten["complete"] is False
    stumm = next(z for z in daten["instances"] if z["name"] == "Radarr")
    assert stumm["reachable"] is False
    assert stumm["protected"] is False


# --- Zugang -----------------------------------------------------------------


def test_nur_administratoren(arr_client) -> None:
    """Wo geloescht wird, geht Entscheider nichts an - es ist Serverbetrieb."""
    from .conftest import auth_headers, create_user
    from app.models import Role

    create_user(arr_client, "entscheider2", "test1234", role=Role.approver)
    kopf = auth_headers(arr_client, "entscheider2", "test1234")
    assert arr_client.get("/api/settings/recyclebin", headers=kopf).status_code == 403


@pytest.mark.anyio
async def test_ohne_jede_instanz_ist_nichts_zu_schuetzen(monkeypatch) -> None:
    """"An" waere hier eine Behauptung ueber das Nichts."""
    with SessionLocal() as db:
        save_settings(
            db,
            {
                "radarr_url": "",
                "radarr_api_key": "",
                "sonarr_url": "",
                "sonarr_api_key": "",
            },
        )
        leer = load_settings(db)

    assert await library.papierkoerbe(leer) == []


# --- Schreiben --------------------------------------------------------------


def _schreibt(monkeypatch) -> list[tuple]:
    """Mitschreiben, was an die Instanzen ginge - ohne etwas zu senden."""
    gesendet: list[tuple] = []

    async def setzen(_settings, media_type, tier, *, pfad, tage=None):
        gesendet.append((media_type, tier, pfad, tage))

    monkeypatch.setattr(library, "papierkorb_setzen", setzen)
    return gesendet


def test_speichern_traegt_in_alle_instanzen_ein(arr_client, monkeypatch) -> None:
    gesendet = _schreibt(monkeypatch)
    _antworten(monkeypatch, {("movie", "standard"): "/data/Papierkorb",
                             ("tv", "standard"): "/data/Papierkorb"})

    antwort = arr_client.put(
        "/api/settings/recyclebin",
        json={
            "instances": [
                {"media_type": "movie", "tier": "standard", "path": "/data/Papierkorb"},
                {"media_type": "tv", "tier": "standard", "path": "/data/Papierkorb"},
            ],
            "cleanup_days": 30,
        },
    )

    assert antwort.status_code == 200
    assert gesendet == [
        ("movie", "standard", "/data/Papierkorb", 30),
        ("tv", "standard", "/data/Papierkorb", 30),
    ]
    # Zurueck kommt der **gelesene** Stand, nicht der gewuenschte.
    assert antwort.json()["enabled"] is True


def test_null_tage_werden_abgewiesen(arr_client, monkeypatch) -> None:
    """Ob Radarr die Null als "nie aufraeumen" oder "sofort" versteht, ist
    nicht dokumentiert - und bei einem Papierkorb ist die Verwechslung fatal.
    """
    _schreibt(monkeypatch)
    antwort = arr_client.put(
        "/api/settings/recyclebin",
        json={
            "instances": [{"media_type": "movie", "tier": "standard", "path": "/x"}],
            "cleanup_days": 0,
        },
    )
    assert antwort.status_code == 422


def test_nicht_eingerichtete_instanz_wird_abgewiesen(arr_client, monkeypatch) -> None:
    gesendet = _schreibt(monkeypatch)
    antwort = arr_client.put(
        "/api/settings/recyclebin",
        json={
            "instances": [{"media_type": "movie", "tier": "uhd", "path": "/x"}],
            "cleanup_days": 7,
        },
    )
    assert antwort.status_code == 400
    assert gesendet == []


def test_eine_stumme_instanz_bricht_den_ganzen_vorgang_ab(arr_client, monkeypatch) -> None:
    """Ein halb geschriebener Zustand waere schlimmer als gar keiner.

    Danach wuesste niemand mehr, welche Instanz jetzt schuetzt und welche
    nicht - und das ist die eine Auskunft, auf die es hier ankommt.
    """
    versucht: list[str] = []

    async def setzen(_settings, media_type, tier, *, pfad, tage=None):
        versucht.append(media_type)
        if media_type == "movie":
            raise ArrError("Radarr antwortet nicht.", 502)

    monkeypatch.setattr(library, "papierkorb_setzen", setzen)

    antwort = arr_client.put(
        "/api/settings/recyclebin",
        json={
            "instances": [
                {"media_type": "movie", "tier": "standard", "path": "/data/Papierkorb"},
                {"media_type": "tv", "tier": "standard", "path": "/data/Papierkorb"},
            ],
            "cleanup_days": 7,
        },
    )

    assert antwort.status_code == 502
    detail = antwort.json()["detail"]
    # ⚠️ Der Rahmen traegt seit der Umstellung eine Kennung, die fremde Meldung
    # steht als Zitat daneben: Was Radarr sagt, sagt Radarr in seiner Sprache -
    # das kann Nexview nicht uebersetzen, ohne es zu erfinden.
    assert detail["code"] == "recyclebin_write_failed"
    assert detail["media_type"] == "movie"
    assert "Radarr antwortet nicht" in detail["reason"]
    assert "Radarr antwortet nicht" in detail["message"]
    # Sonarr wurde gar nicht erst angefasst.
    assert versucht == ["movie"]


def test_leerer_pfad_schaltet_ab(arr_client, monkeypatch) -> None:
    """Die gefaehrliche Richtung - ab dann loescht die Instanz endgueltig."""
    gesendet = _schreibt(monkeypatch)
    _antworten(monkeypatch, {})

    antwort = arr_client.put(
        "/api/settings/recyclebin",
        json={
            "instances": [{"media_type": "movie", "tier": "standard", "path": ""}],
            "cleanup_days": 7,
        },
    )
    assert antwort.status_code == 200
    assert gesendet == [("movie", "standard", "", 7)]
    assert antwort.json()["enabled"] is False


def test_ordnerauswahl_fragt_die_richtige_instanz(arr_client, monkeypatch) -> None:
    """Sonarr kann anders eingebunden sein als Radarr - geraten wird nichts."""
    gefragt: list[tuple] = []

    async def ordner(_settings, media_type, tier, pfad):
        gefragt.append((media_type, tier, pfad))
        return ["/data/Papierkorb", "/data/Movies"]

    monkeypatch.setattr(library, "ordner", ordner)

    antwort = arr_client.get(
        "/api/settings/recyclebin/folders?media_type=tv&tier=standard&path=/data"
    )
    assert antwort.status_code == 200
    assert antwort.json()["directories"] == ["/data/Papierkorb", "/data/Movies"]
    assert gefragt == [("tv", "standard", "/data")]


def test_schreiben_ist_nur_fuer_administratoren(arr_client) -> None:
    from .conftest import auth_headers, create_user
    from app.models import Role

    create_user(arr_client, "entscheider3", "test1234", role=Role.approver)
    kopf = auth_headers(arr_client, "entscheider3", "test1234")
    antwort = arr_client.put(
        "/api/settings/recyclebin",
        json={"instances": [], "cleanup_days": 7},
        headers=kopf,
    )
    assert antwort.status_code == 403


@pytest.mark.anyio
async def test_ordnerauswahl_erzwingt_den_schraegstrich(monkeypatch) -> None:
    """⚠️ **Ohne ihn zeigt die Auswahl durchweg die falsche Ebene.**

    ``/filesystem`` ohne abschliessenden Schraegstrich listet den
    **Elternordner** und benutzt den Rest als Praefix-Filter: ``/data`` liefert
    die Wurzel, ``/data/`` den Inhalt von ``/data``. Nachgemessen an einer
    echten Instanz - und aus der Antwort allein ist der Unterschied nicht zu
    erkennen, weil beide Male eine plausible Ordnerliste zurueckkommt.
    """
    gefragt: list[str] = []

    class Attrappe:
        async def get(self, _pfad, params=None):
            gefragt.append((params or {}).get("path", ""))
            return {"directories": [{"path": "/data/Papierkorb"}]}

    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: Attrappe())
    with SessionLocal() as db:
        settings = load_settings(db)

    for eingabe in ("/data", "/data/", "/data//"):
        await library.ordner(settings, "movie", "standard", eingabe)

    assert gefragt == ["/data/", "/data/", "/data/"]


# --- Groesse ----------------------------------------------------------------


@pytest.mark.anyio
async def test_groesse_verlangt_includefiles(monkeypatch) -> None:
    """⚠️ **Ohne den Parameter sieht jeder Papierkorb leer aus.**

    ``/filesystem`` liefert von sich aus **nur Ordner**. Nachgemessen an einer
    echten Instanz: derselbe Ordner meldete ohne ``includeFiles`` null Dateien
    und mit ihm eine mit 5,5 GB. Der Fehler faellt nicht auf - eine Null sieht
    aus wie ein leerer Papierkorb.
    """
    gefragt: list[dict] = []

    class Attrappe:
        async def get(self, _pfad, params=None):
            gefragt.append(params or {})
            return {"directories": [], "files": [{"size": 5 * 1024**3}]}

    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: Attrappe())
    with SessionLocal() as db:
        settings = load_settings(db)

    bytes_, unvollstaendig = await library.papierkorb_groesse(
        settings, "movie", "standard", "/data/Papierkorb"
    )

    assert bytes_ == 5 * 1024**3
    assert unvollstaendig is False
    assert gefragt == [{"path": "/data/Papierkorb/", "includeFiles": "true"}]


@pytest.mark.anyio
async def test_groesse_zaehlt_unterordner_mit(monkeypatch) -> None:
    """Radarr legt je Titel einen Ordner an - die Dateien liegen eine Ebene tiefer."""
    baum = {
        "/data/Papierkorb/": ([{"path": "/data/Papierkorb/Ein Film/"}], []),
        "/data/Papierkorb/Ein Film/": ([], [{"size": 3 * 1024**3}]),
    }

    class Attrappe:
        async def get(self, _pfad, params=None):
            ordner, dateien = baum.get((params or {}).get("path", ""), ([], []))
            return {"directories": ordner, "files": dateien}

    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: Attrappe())
    with SessionLocal() as db:
        settings = load_settings(db)

    bytes_, _ = await library.papierkorb_groesse(
        settings, "movie", "standard", "/data/Papierkorb"
    )
    assert bytes_ == 3 * 1024**3


@pytest.mark.anyio
async def test_groesse_bricht_ab_und_sagt_es(monkeypatch) -> None:
    """Eine zu kleine Zahl **ohne Hinweis** ist schlimmer als keine.

    Jeder Ordner kostet einen Netzwerk-Umlauf. Bei tausend Titeln muss die
    Suche abbrechen - dann steht ein ``≥`` davor, statt eine Untergrenze als
    Ergebnis auszugeben.
    """

    class Attrappe:
        async def get(self, _pfad, params=None):
            # Jeder Ordner enthaelt zwei weitere - der Baum endet nie.
            wurzel = (params or {}).get("path", "")
            return {
                "directories": [{"path": wurzel + "a/"}, {"path": wurzel + "b/"}],
                "files": [{"size": 1024}],
            }

    monkeypatch.setattr(library, "radarr_client", lambda *_a, **_k: Attrappe())
    with SessionLocal() as db:
        settings = load_settings(db)

    bytes_, unvollstaendig = await library.papierkorb_groesse(
        settings, "movie", "standard", "/data/Papierkorb"
    )
    assert unvollstaendig is True
    assert bytes_ > 0


def test_leere_papierkoerbe_stehen_nicht_in_der_uebersicht(arr_client, monkeypatch) -> None:
    """Eine Zeile mit null Bytes beantwortet die Frage nicht, die dort steht."""
    _antworten(monkeypatch, {("movie", "standard"): "/data/Papierkorb"})

    async def groesse(_s, _art, _stufe, _pfad):
        return 0, False

    monkeypatch.setattr(library, "papierkorb_groesse", groesse)
    arr_client.put("/api/settings", json={"storage_enabled": True})

    daten = arr_client.get("/api/storage/recyclebin").json()
    assert daten["total_bytes"] == 0
    assert daten["instances"] == []
