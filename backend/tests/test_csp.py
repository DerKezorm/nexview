"""Die Inhaltsregeln der Seite (Content-Security-Policy).

⚠️ **Was diese Tests nicht koennen.** Ob eine Regel zu eng ist, sagt kein
Test hier - das sagt nur ein Browser, der jede Seite wirklich laedt. Beim Bau
sind so zwei Bildquellen aufgefallen, die in keinem Quelltext stehen:
``artworks.thetvdb.com`` (Kalender-Poster, kommen von Sonarr) und
``i.ytimg.com`` (Trailer-Vorschau in der Kinderansicht). Dafuer gibt es
``frontend/tools/konsole-pruefen.mjs``.

Hier steht das, was sich ohne Browser festhalten laesst - und das ist genau
das, was beim naechsten Umbau leise kaputtgehen wuerde.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import csp

KOPF = "content-security-policy"


def _regeln(antwort) -> dict[str, str]:
    """Die Kopfzeile als Tabelle: Richtlinie -> erlaubte Quellen."""
    roh = antwort.headers.get(KOPF)
    assert roh, f"Keine {KOPF}-Kopfzeile an {antwort.url}"
    tabelle = {}
    for teil in roh.split("; "):
        name, _, rest = teil.partition(" ")
        tabelle[name] = rest
    return tabelle


def test_jede_antwort_traegt_die_regeln(admin_client: TestClient) -> None:
    """An **jeder** Antwort, nicht nur an der HTML-Seite.

    Eine Regel, die nur an einem von mehreren Auslieferungswegen haengt, ist
    irgendwann keine Regel mehr.
    """
    for pfad in ("/api/health", "/api/auth/me", "/api/users"):
        assert KOPF in {k.lower() for k in admin_client.get(pfad).headers}, pfad


def test_auch_fehlerantworten_tragen_sie(client: TestClient) -> None:
    """Sonst waere ausgerechnet die Fehlerseite ungeschuetzt."""
    antwort = client.get("/api/auth/me")
    assert antwort.status_code == 401
    assert KOPF in {k.lower() for k in antwort.headers}


def test_fremde_ziele_sind_zu(admin_client: TestClient) -> None:
    """⚠️ Der wichtigste Eintrag der ganzen Liste.

    ``connect-src 'self'`` ist das, was das HttpOnly-Cookie **nicht** kann: Es
    verhindert, dass ein Ausweis mitgenommen wird, nicht dass er benutzt wird.
    Ein boesartig gewordenes Paket im Buendel koennte den Zugangs-Token sonst
    an eine fremde Adresse schicken.
    """
    regeln = _regeln(admin_client.get("/api/health"))
    assert regeln["connect-src"] == "'self'"


def test_niemand_darf_nexview_einrahmen(admin_client: TestClient) -> None:
    regeln = _regeln(admin_client.get("/api/health"))
    assert regeln["frame-ancestors"] == "'none'"


def test_der_rahmen_laesst_sich_oeffnen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fuer alle, die Nexview in einem Uebersichts-Brett eingebettet haben.

    Ohne diesen Weg saehen sie einen leeren Rahmen - und zwar ohne jede
    Fehlermeldung, nur einen Eintrag in einer Konsole, in die sie nie sehen.
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "frame_ancestors", "self")
    # Die Kopfzeile entsteht beim Start, nicht je Anfrage - hier also direkt
    # gegen den Erbauer geprueft.
    assert "frame-ancestors 'self'" in csp.regeln(None, "self")
    assert "frame-ancestors https://brett.example.org" in csp.regeln(
        None, "https://brett.example.org"
    )


def test_die_grundregel_ist_zu(admin_client: TestClient) -> None:
    """Was nicht ausdruecklich erlaubt ist, kommt von uns oder gar nicht."""
    regeln = _regeln(admin_client.get("/api/health"))
    assert regeln["default-src"] == "'self'"
    assert regeln["object-src"] == "'none'"
    assert regeln["base-uri"] == "'self'"
    assert regeln["form-action"] == "'self'"


# --------------------------------------------------------------------------
# Der Service Worker - die Zeile, die in 0.31.0 Web Push abgewuergt hat
# --------------------------------------------------------------------------


def test_der_service_worker_darf_starten(admin_client: TestClient) -> None:
    """⚠️ Web Push haengt an dieser einen Richtlinie.

    ``navigator.serviceWorker.register('/sw.js')`` ist der erste Schritt jeder
    Anmeldung, noch vor der ersten Anfrage an den Server. In 0.31.0 stand hier
    ``worker-src 'none'``: Jeder Browser brach mit "Creating a worker violates
    the following Content Security Policy directive" ab, die Oberflaeche sagte
    nur "Etwas ist schiefgelaufen", und der Server wurde nie nach seinem
    Schluessel gefragt. Aufgefallen ist es erst auf einer echten Installation,
    weil der Entwicklungsserver von Vite die Kopfzeile gar nicht setzt.
    """
    regeln = _regeln(admin_client.get("/api/health"))
    assert regeln["worker-src"] == "'self'"


# --------------------------------------------------------------------------
# Die Bildquellen - die Stelle, an der es beim Bau zweimal geklemmt hat
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quelle, warum",
    [
        ("https://image.tmdb.org", "Poster und Hintergruende"),
        ("https://artworks.thetvdb.com", "Kalender-Poster von Sonarr"),
        ("https://i.ytimg.com", "Trailer-Vorschau, auch in der Kinderansicht"),
        ("https://assets.fanart.tv", "Bibliotheken, die fanart.tv benutzen"),
    ],
)
def test_bekannte_bildquellen_sind_erlaubt(quelle: str, warum: str) -> None:
    """Jede einzeln, damit im Fehlerfall dasteht **welche** fehlt.

    Die beiden mittleren sind der Grund, warum es diesen Test gibt: Sie stehen
    in keinem Quelltext, sondern entstehen erst zur Laufzeit - die eine aus
    Sonarrs Bestand, die andere in der Trailer-Vorschau.
    """
    assert quelle in csp.regeln(None), warum


def test_weitere_bildquellen_lassen_sich_nachtragen() -> None:
    """Der Ausweg fuer alle, deren Metadaten-Anbieter woanders liegt.

    Ohne ihn saehen sie leere Poster im Kalender - wieder ohne Fehlermeldung.
    """
    regeln = csp.regeln(None, "none", "https://bilder.example.org https://zweite.example.org")
    assert "https://bilder.example.org" in regeln
    assert "https://zweite.example.org" in regeln
    # Und die eingebauten bleiben trotzdem drin.
    assert "https://image.tmdb.org" in regeln


def test_der_trailer_darf_eingebettet_werden() -> None:
    assert "frame-src https://www.youtube-nocookie.com" in csp.regeln(None)


# --------------------------------------------------------------------------
# Das Inline-Skript
# --------------------------------------------------------------------------


def test_die_pruefsumme_passt_zur_ausgelieferten_seite(tmp_path: Path) -> None:
    """⚠️ Warum die Summe beim Start berechnet wird und nicht im Code steht.

    Eine festgeschriebene Pruefsumme laeuft beim ersten Zeichen auseinander,
    das jemand am Skript in ``index.html`` aendert. Der Fehler zeigt sich dann
    nicht beim Bauen, sondern als **weisse Seite bei einem Fremden**.
    """
    skript = "\n      document.documentElement.dataset.x = '1';\n    "
    seite = tmp_path / "index.html"
    seite.write_text(f"<html><head><script>{skript}</script></head></html>", encoding="utf-8")

    erwartet = base64.b64encode(hashlib.sha256(skript.encode("utf-8")).digest()).decode()
    assert f"'sha256-{erwartet}'" in csp.regeln(seite)


def test_skripte_mit_src_brauchen_keine_summe(tmp_path: Path) -> None:
    """Das gebaute Buendel kommt von ``'self'`` - eine Summe waere falsch."""
    seite = tmp_path / "index.html"
    seite.write_text('<html><head><script src="/assets/x.js"></script></head></html>', "utf-8")
    assert csp.regeln(seite).count("sha256-") == 0


def test_ohne_gebautes_frontend_gibt_es_keine_summe() -> None:
    """In der CI laufen die Tests **vor** dem Frontend-Bau."""
    assert "sha256-" not in csp.regeln(None)


# --------------------------------------------------------------------------
# Die Schalter
# --------------------------------------------------------------------------


def test_nur_melden_statt_durchsetzen() -> None:
    """Der Weg fuer alle, die erst nachsehen wollen."""
    name, regeln = csp.kopfzeile("report-only", None)
    assert name == "Content-Security-Policy-Report-Only"
    # Dieselben Regeln - nur unter anderem Namen.
    assert regeln == csp.regeln(None)


def test_der_notausgang() -> None:
    """⚠️ Kein Zierrat.

    Eine zu enge Regel zeigt keine Fehlermeldung, sondern eine halb geladene
    Seite. Wer davon betroffen ist, schreibt kein Issue - er deinstalliert.
    """
    assert csp.kopfzeile("off", None) is None


def test_unsinn_faellt_auf_die_sichere_seite() -> None:
    """Ein Tippfehler darf die Regeln nicht stillschweigend abschalten."""
    name, _ = csp.kopfzeile("vielleicht", None)
    assert name == "Content-Security-Policy"
