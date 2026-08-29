"""Der Unterpfad hinter dem Reverse Proxy (``NEXVIEW_URL_BASE``).

Vier Dinge muessen zusammenpassen, damit Nexview unter
``https://domain.tld/nexview/`` funktioniert:

* die **Normalisierung** der Einstellung (config.py),
* das **Zwischenstueck**, das den Vorbau vom Pfad streift (middleware.py) -
  und dabei die Wurzel weiter bedient (abschneidende Proxys, Healthcheck),
* das **Cookie**, dessen Pfad aus Browsersicht den Vorbau traegt (sitzung.py),
* die beim Start **umgeschriebene** ``index.html`` samt CSP-Pruefsummen
  (main.py + services/csp.py).

Die Integrationstests unten laden ``app.main`` mit gesetzter Umgebung neu -
das ist der einzige Weg, weil die Anwendung ihre Einstellungen beim Import
liest. Der Nachbau mit echtem Proxy davor liegt in den Playwright-Tests
(``frontend/e2e/unterpfad.spec.ts``).
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_modul
from app.config import Settings, get_settings
from app.middleware import BasisPfadMiddleware
from app.models import User
from app.services import csp
from app.services.sitzung import cookie_pfad

from .conftest import ADMIN


# ---------------------------------------------------------------------------
# Normalisierung der Einstellung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roh", "erwartet"),
    [
        ("", ""),
        ("   ", ""),
        ("/", ""),
        ("nexview", "/nexview"),
        ("/nexview", "/nexview"),
        ("/nexview/", "/nexview"),
        ("  /nexview  ", "/nexview"),
        ("nexview/", "/nexview"),
        # Wer die ganze Adresse eintraegt, meint ihren Pfad.
        ("https://beispiel.de/nexview", "/nexview"),
        ("https://beispiel.de", ""),
        # Mehrere Stufen sind erlaubt.
        ("/tools/nexview", "/tools/nexview"),
        # /api wuerde mit der Schnittstelle selbst zusammenfallen.
        ("/api", ""),
        ("/api/unter", ""),
        # Kein URL-Pfad, sondern ein Versehen - etwa ein von der Windows-Shell
        # in einen Laufwerkspfad verwandelter Wert (Git Bash macht aus
        # "/nexview" ein "C:/Program Files/Git/nexview"). Stumm ausgeliefert
        # ergaebe das eine halb kaputte Seite; ignoriert bleibt alles heil.
        ("C:/Program Files/Git/nexview", ""),
        ("/mit leerzeichen", ""),
    ],
)
def test_url_base_wird_normalisiert(roh: str, erwartet: str) -> None:
    assert Settings(url_base=roh).url_base == erwartet


# ---------------------------------------------------------------------------
# Das Zwischenstueck
# ---------------------------------------------------------------------------


async def _durchlauf(basis: str, scope: dict) -> dict:
    """Einen Scope durch das Zwischenstueck schicken und ansehen, was ankommt."""
    angekommen: dict = {}

    async def innen(s: dict, receive, send) -> None:
        angekommen.update(s)

    async def _leer() -> None:  # pragma: no cover - wird nie gerufen
        return None

    await BasisPfadMiddleware(innen, basis)(scope, _leer, _leer)
    return angekommen


async def test_zwischenstueck_streift_den_vorbau() -> None:
    scope = {"type": "http", "path": "/nexview/api/health", "raw_path": b"/nexview/api/health"}
    ergebnis = await _durchlauf("/nexview", scope)
    assert ergebnis["path"] == "/api/health"
    assert ergebnis["raw_path"] == b"/api/health"


async def test_zwischenstueck_macht_die_nackte_basis_zur_wurzel() -> None:
    ergebnis = await _durchlauf("/nexview", {"type": "http", "path": "/nexview", "raw_path": b"/nexview"})
    assert ergebnis["path"] == "/"
    assert ergebnis["raw_path"] == b"/"


async def test_wurzelpfade_bleiben_unangetastet() -> None:
    """Abschneidende Proxys, Healthcheck und Webhooks rufen ohne Vorbau an."""
    ergebnis = await _durchlauf("/nexview", {"type": "http", "path": "/api/health"})
    assert ergebnis["path"] == "/api/health"


async def test_nur_scheinbarer_vorbau_bleibt_stehen() -> None:
    """``/nexviewfoo`` traegt den Vorbau nicht - nur fast."""
    ergebnis = await _durchlauf("/nexview", {"type": "http", "path": "/nexviewfoo", "raw_path": b"/nexviewfoo"})
    assert ergebnis["path"] == "/nexviewfoo"
    assert ergebnis["raw_path"] == b"/nexviewfoo"


# ---------------------------------------------------------------------------
# Cookie-Pfad
# ---------------------------------------------------------------------------


def test_cookie_pfad_ohne_basis() -> None:
    assert cookie_pfad() == "/api/auth"


def test_cookie_pfad_mit_basis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "url_base", "/nexview")
    assert cookie_pfad() == "/nexview/api/auth"


def test_cookie_traegt_den_vorbau_beim_anmelden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Anmeldung setzt das Cookie mit dem Vorbau im Pfad.

    Der Pfad zaehlt aus **Browsersicht**, und die traegt den Vorbau bei beiden
    Proxy-Arten - abgeschnitten wird erst hinter dem Browser.
    """
    monkeypatch.setattr(get_settings(), "url_base", "/nexview")
    response = client.post("/api/setup/admin", json=ADMIN)
    assert response.status_code == 201, response.text
    kopf = response.headers.get("set-cookie", "")
    assert "Path=/nexview/api/auth" in kopf


# ---------------------------------------------------------------------------
# Vom Server erzeugte Adressen
# ---------------------------------------------------------------------------


def test_avatar_adresse_traegt_den_vorbau(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "url_base", "/nexview")
    nutzer = User(username="probe", email="probe@beispiel.de", avatar_path="probe.png")
    assert nutzer.avatar_url == "/nexview/api/users/avatar/probe.png"


def test_demo_poster_traegt_den_vorbau(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mocks import demo_data

    monkeypatch.setattr(get_settings(), "url_base", "/nexview")
    eintrag = demo_data.demo_items("movie")[0]
    assert eintrag.poster_url is not None
    assert eintrag.poster_url.startswith("/nexview/api/demo/poster/")


# ---------------------------------------------------------------------------
# index.html umschreiben + CSP
# ---------------------------------------------------------------------------

_PROBE_INDEX = """<!doctype html>
<html lang="de">
  <head>
    <link rel="icon" href="/logo.svg" />
    <script>
      probe();
    </script>
    <script type="module" crossorigin src="/assets/probe.js"></script>
    <link rel="stylesheet" crossorigin href="/assets/probe.css">
    <img src="//fremd.example/bild.png">
  </head>
  <body><div id="root"></div></body>
</html>
"""


def test_index_wird_umgeschrieben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "url_base", "/nexview")
    datei = tmp_path / "index.html"
    datei.write_text(_PROBE_INDEX, encoding="utf-8")

    inhalt = main_modul._index_mit_basis(datei)
    assert inhalt is not None
    assert 'href="/nexview/logo.svg"' in inhalt
    assert 'src="/nexview/assets/probe.js"' in inhalt
    assert 'href="/nexview/assets/probe.css"' in inhalt
    assert 'window.__NEXVIEW_BASIS__ = "/nexview";' in inhalt
    # Protokoll-relative Adressen (``//fremd...``) sind keine eigenen Pfade
    # und bleiben stehen.
    assert 'src="//fremd.example/bild.png"' in inhalt


def test_index_bleibt_ohne_basis_unangetastet(tmp_path: Path) -> None:
    datei = tmp_path / "index.html"
    datei.write_text(_PROBE_INDEX, encoding="utf-8")
    assert main_modul._index_mit_basis(datei) is None


def test_csp_rechnet_auch_aus_text(tmp_path: Path) -> None:
    """Pfad und fertiger Text muessen dieselben Pruefsummen ergeben.

    Mit Unterpfad kommt die CSP aus der umgeschriebenen Fassung im Speicher -
    waere die Datei die Quelle, fehlte die Summe des eingefuegten Skripts und
    die Seite bliebe beim ersten Laden lautlos weiss.
    """
    datei = tmp_path / "index.html"
    datei.write_text(_PROBE_INDEX, encoding="utf-8")
    assert csp.regeln(_PROBE_INDEX) == csp.regeln(datei)
    # Zwei Inline-Skripte -> zwei Summen.
    umgeschrieben = _PROBE_INDEX.replace(
        "<head>", '<head><script>window.__NEXVIEW_BASIS__ = "/nexview";</script>'
    )
    assert csp.regeln(umgeschrieben).count("sha256-") == 2


# ---------------------------------------------------------------------------
# Die ganze Anwendung mit gesetztem Unterpfad
# ---------------------------------------------------------------------------


@pytest.fixture
def unterpfad_client(tmp_path: Path) -> Iterator[TestClient]:
    """``app.main`` mit ``NEXVIEW_URL_BASE=/nexview`` und Attrappen-Frontend.

    Neu geladen statt angepasst, weil die Anwendung Einstellungen, Frontend
    und CSP **beim Import** verdrahtet. Am Ende wird der Ursprungszustand
    durch einen zweiten Reload wiederhergestellt - die uebrigen Tests halten
    ihre eigene, unveraenderte ``app``-Referenz aus ``conftest``.

    Bewusst ohne ``with``: So bleibt der Lifespan (und damit jede
    Datenbank-Arbeit) aus dem Spiel - die Endpunkte hier brauchen ihn nicht.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_PROBE_INDEX, encoding="utf-8")
    (dist / "assets" / "probe.js").write_text("console.log('ok')", encoding="utf-8")

    os.environ["NEXVIEW_URL_BASE"] = "/nexview"
    os.environ["NEXVIEW_STATIC_DIR"] = str(dist)
    get_settings.cache_clear()
    modul = importlib.reload(main_modul)
    try:
        yield TestClient(modul.app)
    finally:
        os.environ.pop("NEXVIEW_URL_BASE", None)
        os.environ.pop("NEXVIEW_STATIC_DIR", None)
        get_settings.cache_clear()
        importlib.reload(main_modul)


def test_wurzel_und_vorbau_antworten_beide(unterpfad_client: TestClient) -> None:
    mit = unterpfad_client.get("/nexview/api/health")
    ohne = unterpfad_client.get("/api/health")
    assert mit.status_code == 200
    assert ohne.status_code == 200
    assert mit.json() == ohne.json()


def test_seite_kommt_mit_vorbau(unterpfad_client: TestClient) -> None:
    antwort = unterpfad_client.get("/nexview/")
    assert antwort.status_code == 200
    assert 'src="/nexview/assets/probe.js"' in antwort.text
    assert 'window.__NEXVIEW_BASIS__ = "/nexview";' in antwort.text

    # Eine tiefe SPA-Adresse liefert dieselbe Seite (SPA-Rueckfall).
    tief = unterpfad_client.get("/nexview/profil")
    assert tief.status_code == 200
    assert 'window.__NEXVIEW_BASIS__ = "/nexview";' in tief.text

    # Auch an der Wurzel (abschneidender Proxy) kommt die umgeschriebene
    # Fassung - der Browser zeigt dort ohnehin die Adresse mit Vorbau.
    wurzel = unterpfad_client.get("/")
    assert 'window.__NEXVIEW_BASIS__ = "/nexview";' in wurzel.text


def test_dateien_unter_dem_vorbau(unterpfad_client: TestClient) -> None:
    antwort = unterpfad_client.get("/nexview/assets/probe.js")
    assert antwort.status_code == 200
    assert "console.log" in antwort.text


def test_doku_unter_dem_vorbau(unterpfad_client: TestClient) -> None:
    doku = unterpfad_client.get("/nexview/docs")
    assert doku.status_code == 200
    assert 'href="/nexview/docs-dateien/swagger-ui.css"' in doku.text

    alt = unterpfad_client.get("/nexview/redoc", follow_redirects=False)
    assert alt.status_code == 308
    assert alt.headers["location"] == "/nexview/docs"


def test_api_fehler_bleiben_json(unterpfad_client: TestClient) -> None:
    """Ein Tippfehler in der API-Adresse bekommt einen Fehler, keine Webseite."""
    antwort = unterpfad_client.get("/nexview/api/gibt-es-nicht")
    assert antwort.status_code == 404
    assert antwort.headers["content-type"].startswith("application/json")


def test_csp_kennt_das_eingefuegte_skript(unterpfad_client: TestClient) -> None:
    """Die Kopfzeile muss aus der **umgeschriebenen** Seite gerechnet sein."""
    antwort = unterpfad_client.get("/nexview/")
    regeln = antwort.headers.get("content-security-policy", "")
    assert regeln.count("sha256-") == 2
    modul = importlib.import_module("app.main")
    assert modul._index_umgeschrieben is not None
    assert regeln == csp.regeln(
        modul._index_umgeschrieben,
        get_settings().frame_ancestors,
        get_settings().img_sources,
    )
