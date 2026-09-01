"""Der Pruefer fuer die gepinnten Abhaengigkeiten, und der Waechter ueber seine
Ausnahmeliste.

``tools/abhaengigkeiten_pruefen.py`` haelt ``requirements.txt`` bei jedem
CI-Lauf gegen api.osv.dev. Hier wird er **ohne Netz** geprueft: Die
OSV-Antworten kommen aus einer abgelegten echten Antwort
(``tests/daten/osv_pyjwt_2_11_0.json``) und aus kleinen erfundenen Antworten.
Ein Test, der die Quelle wirklich anruft, waere von ihrer Verfuegbarkeit
abhaengig und wuerde genau die Reihe rot faerben, die er schuetzen soll.

⚠️ **Warum die Mechanik der Ausnahmeliste hier geprueft wird, obwohl die Liste
leer ausgeliefert wird.** Leer ist sie mit Absicht: Die Fassungen wurden so
gewaehlt, dass keine einzige Ausnahme noetig ist, denn jede Ausnahme ist eine
Behauptung, die dauerhaft wahr bleiben muss. Eine ungeprueft mitgelieferte
Mechanik waere aber schlimmer als gar keine: Der erste Mensch, der irgendwann
einen Eintrag schreibt, verliesse sich darauf, dass er wirkt und dass er
abstirbt, wenn er nicht mehr passt. Genau das wird deshalb mit **erfundenen**
Eintraegen belegt.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from tools import abhaengigkeiten_pruefen as pruefer

DATEN = Path(__file__).resolve().parent / "daten"
REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def _osv_pyjwt() -> list[dict]:
    """Die echte OSV-Antwort auf ``pyjwt==2.11.0``: zwoelf Datensaetze."""
    inhalt = json.loads((DATEN / "osv_pyjwt_2_11_0.json").read_text(encoding="utf-8"))
    return inhalt["vulns"]


def _meldung(kennung: str, alias: str, behoben: str, schwere: str = "HIGH") -> dict:
    """Ein erfundener OSV-Datensatz in der Form, die ``/v1/query`` liefert."""
    return {
        "id": kennung,
        "aliases": [alias] if alias else [],
        "summary": "Erfunden fuer diesen Test.",
        "database_specific": {"severity": schwere},
        "affected": [{"ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": behoben}]}]}],
    }


# ---------------------------------------------------------------------------
# Einlesen
# ---------------------------------------------------------------------------


def test_zeile_ohne_genaue_fassung_wird_genannt() -> None:
    """Eine ungepinnte Zeile bricht ab, statt still uebersprungen zu werden.

    Das Ueberspringen waere genau das Versagen, das die Pruefung verhindern
    soll: Man saehe einen gruenen Lauf und haette ueber diese Abhaengigkeit
    nichts erfahren. Und die Meldung muss die Zeile nennen, sonst sucht der
    naechste Leser sie von Hand.
    """
    with pytest.raises(pruefer.NichtPruefbar) as fehler:
        pruefer.pins_lesen("fastapi==0.121.2\nhttpx>=0.28.1\n")

    assert "httpx>=0.28.1" in str(fehler.value)
    assert "Zeile 2" in str(fehler.value)


def test_kommentare_und_extras_stoeren_nicht() -> None:
    """Leerzeilen, Kommentarzeilen und nachgestellte Kommentare fallen weg.

    Extras werden fuer die Abfrage abgeschnitten: OSV kennt ``uvicorn``, nicht
    ``uvicorn[standard]``. Die Fassung bleibt davon unberuehrt.
    """
    text = "# ein Kommentar\n\nuvicorn[standard]==0.38.0\nbcrypt==5.0.0  # noch einer\n"

    assert pruefer.pins_lesen(text) == [("uvicorn", "0.38.0"), ("bcrypt", "5.0.0")]


def test_datei_ohne_einen_einzigen_pin_ist_nicht_pruefbar() -> None:
    """Sonst meldete eine leergeraeumte Datei "In Ordnung"."""
    with pytest.raises(pruefer.NichtPruefbar):
        pruefer.pins_lesen("# nur Kommentare\n\n")


# ---------------------------------------------------------------------------
# Gruppierung
# ---------------------------------------------------------------------------


def test_gruppen_nach_sache_statt_nach_datensatz() -> None:
    """Zwoelf Datensaetze, sechs Sachen.

    OSV liefert zu jeder Sache einen GHSA- und einen PYSEC-Datensatz. Ohne die
    Gruppierung meldete das Werkzeug fuer pyjwt 2.11.0 zwoelf Funde, und die
    Ausnahmeliste braeuchte je Sache zwei Eintraege, von denen einer unbemerkt
    verrottet.
    """
    vulns = _osv_pyjwt()
    assert len(vulns) == 12

    gruppen = pruefer.gruppen_bilden(vulns)

    assert len(gruppen) == 6
    assert all(schluessel.startswith("CVE-") for schluessel in gruppen)


def test_hoechste_behebungsangabe_gewinnt() -> None:
    """Die beiden Datensaetze zu CVE-2026-48523 widersprechen sich.

    GHSA-jq35-7prp-9v3f nennt 2.13.0, PYSEC-2026-176 nennt 2.12.1. Wer den
    erstbesten nimmt, pinnt zu niedrig und ist danach immer noch verwundbar.

    ⚠️ Auch **rueckwaerts** geprueft. In der abgelegten Antwort steht die
    hoehere Angabe zufaellig vorn; "nimm die erste" saehe damit genauso gruen
    aus wie "nimm die hoechste", und der Test bewiese nur die Reihenfolge der
    Vorlage.
    """
    vulns = _osv_pyjwt()

    gruppe = pruefer.gruppen_bilden(vulns)["CVE-2026-48523"]
    rueckwaerts = pruefer.gruppen_bilden(list(reversed(vulns)))["CVE-2026-48523"]

    assert gruppe["behoben_ab"] == "2.13.0"
    assert rueckwaerts["behoben_ab"] == "2.13.0"
    assert "GHSA-jq35-7prp-9v3f" in gruppe["kennungen"]
    assert "PYSEC-2026-176" in gruppe["kennungen"]


def test_datensatz_ohne_cve_behaelt_seine_osv_kennung() -> None:
    """Nicht jede Meldung hat eine CVE.

    GHSA-537c-gmf6-5ccf (cryptography, das mitgelieferte OpenSSL) ist so ein
    Fall. Ohne Rueckfall auf die OSV-Kennung fielen alle CVE-losen Meldungen in
    einen gemeinsamen Topf und die Ausnahme fuer eine haette fuer alle gegolten.
    """
    gruppen = pruefer.gruppen_bilden([_meldung("GHSA-537c-gmf6-5ccf", "", "48.0.1")])

    assert list(gruppen) == ["GHSA-537c-gmf6-5ccf"]


def test_mehrere_fixed_angaben_in_einem_datensatz() -> None:
    """Ein Datensatz kann mehrere Zweige tragen, je einen mit eigener Behebung.

    Eine Meldung, die eine 2er- und eine 3er-Reihe betrifft, fuehrt beide
    Bereiche. Wer den erstgenannten nimmt, kann in der falschen Reihe landen.
    """
    zwei_zweige = {
        "id": "GHSA-erfunden",
        "aliases": ["CVE-2026-11111"],
        "summary": "Erfunden fuer diesen Test.",
        "affected": [
            {"ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.9.1"}]}]},
            {"ranges": [{"events": [{"introduced": "3.0.0"}, {"fixed": "3.4.2"}]}]},
        ],
    }

    assert pruefer._behoben_ab(zwei_zweige) == "3.4.2"


def test_zwei_ziffernfolgen_werden_als_zahlen_verglichen() -> None:
    """2.13.0 ist groesser als 2.12.1, obwohl es als Text kleiner ist."""
    assert pruefer._fassung_sortierbar("2.13.0") > pruefer._fassung_sortierbar("2.12.1")
    assert pruefer._fassung_sortierbar("0.0.31") > pruefer._fassung_sortierbar("0.0.9")


# ---------------------------------------------------------------------------
# Bewertung: Fund, Ausnahme, tote Ausnahme
# ---------------------------------------------------------------------------

PINS = [("pyjwt", "2.11.0"), ("httpx", "0.28.1")]


def _antworten(vulns: list[dict]) -> dict[tuple[str, str], list[dict]]:
    return {("pyjwt", "2.11.0"): vulns, ("httpx", "0.28.1"): []}


def test_fund_ohne_ausnahme_bricht_ab(capsys: pytest.CaptureFixture[str]) -> None:
    """Rueckgabecode 1, und die Zeile sagt alles, was zum Entscheiden noetig ist."""
    code = pruefer.bewerten(PINS, _antworten(_osv_pyjwt()), {})

    assert code == 1
    ausgabe = capsys.readouterr().out
    assert "FUNDE" in ausgabe
    assert "pyjwt==2.11.0" in ausgabe
    assert "CVE-2026-32597" in ausgabe
    assert "behoben ab 2.12.0" in ausgabe
    assert "HIGH" in ausgabe


def test_fund_mit_passender_ausnahme_ist_in_ordnung(capsys: pytest.CaptureFixture[str]) -> None:
    """Der erfundene Beweis, dass eine Ausnahme wirklich greift.

    Sechs Sachen meldet OSV zu pyjwt 2.11.0. Fuenf davon bekommen hier eine
    Ausnahme, die sechste nicht: Sie muss uebrig bleiben, sonst deckte ein
    einziger Eintrag die ganze Liste zu.
    """
    alle = sorted(pruefer.gruppen_bilden(_osv_pyjwt()))
    ausnahmen = {
        schluessel: {
            "paket": "pyjwt",
            "gilt_fuer": "2.11.0",
            "grund": "Erfunden fuer diesen Test.",
            "beleg": "grep -rn nichts backend/app",
            "geprueft_am": "2026-09-01",
            "von": "Testlauf",
        }
        for schluessel in alle[:-1]
    }

    assert pruefer.bewerten(PINS, _antworten(_osv_pyjwt()), ausnahmen) == 1
    assert alle[-1] in capsys.readouterr().out

    ausnahmen[alle[-1]] = dict(ausnahmen[alle[0]])
    assert pruefer.bewerten(PINS, _antworten(_osv_pyjwt()), ausnahmen) == 0


def test_ausnahme_fuer_eine_andere_fassung_ist_tot(capsys: pytest.CaptureFixture[str]) -> None:
    """Rueckgabecode 2, nicht 0 und nicht 1.

    Die Begruendung wurde gegen 2.10.0 geschrieben, gepinnt ist 2.11.0. Wuerde
    sie stillschweigend weitergelten, erbte jede kuenftige Fassung ein Urteil,
    das nie ueber sie gefaellt wurde. 2 heisst hier: Die Liste selbst stimmt
    nicht mehr, das ist etwas anderes als ein Fund.
    """
    ausnahmen = {
        "CVE-2026-32597": {
            "paket": "pyjwt",
            "gilt_fuer": "2.10.0",
            "grund": "Erfunden fuer diesen Test.",
            "beleg": "grep -rn nichts backend/app",
            "geprueft_am": "2026-09-01",
            "von": "Testlauf",
        }
    }

    code = pruefer.bewerten(PINS, _antworten(_osv_pyjwt()), ausnahmen)

    assert code == 2
    ausgabe = capsys.readouterr().out
    assert "TOTE AUSNAHMEN" in ausgabe
    assert "2.10.0" in ausgabe


def test_ausnahme_ohne_passende_meldung_ist_tot(capsys: pytest.CaptureFixture[str]) -> None:
    """Meldet OSV die Kennung nicht mehr, deckt der Eintrag nichts mehr ab.

    Er gehoert geloescht und nicht vererbt: Ein Eintrag, der nichts mehr tut,
    laesst die Liste laenger aussehen als die Zahl der wirklich getroffenen
    Entscheidungen.
    """
    ausnahmen = {
        "CVE-2026-99999": {
            "paket": "pyjwt",
            "gilt_fuer": "2.11.0",
            "grund": "Erfunden fuer diesen Test.",
            "beleg": "grep -rn nichts backend/app",
            "geprueft_am": "2026-09-01",
            "von": "Testlauf",
        }
    }

    assert pruefer.bewerten(PINS, _antworten(_osv_pyjwt()), ausnahmen) == 2
    assert "CVE-2026-99999" in capsys.readouterr().out


def test_ausnahme_gilt_nur_fuer_ihr_eigenes_paket(capsys: pytest.CaptureFixture[str]) -> None:
    """Der Schluessel allein reicht nicht.

    Dieselbe CVE kann zwei Pakete treffen - eine Bibliothek und das Paket, das
    sie mitbringt. Waere der Schluessel allein entscheidend, deckte die eine
    getroffene Entscheidung die andere gleich mit zu, und ueber das zweite
    Paket haette nie jemand nachgedacht.

    ⚠️ Der Aufbau ist mit Absicht so gebaut, dass die Ausnahme **lebt**: Ihr
    ``gilt_fuer`` passt, und OSV meldet die Kennung fuer ihr Paket auch
    wirklich. Sonst schluege schon die Pruefung auf tote Ausnahmen an, und
    dieser Test bewiese ueber die Zuordnung zum Paket gar nichts.
    """
    beide = _meldung("GHSA-erfunden", "CVE-2026-11111", "9.9.9")
    pins = [("pyjwt", "2.11.0"), ("libfoo", "1.0.0")]
    antworten = {("pyjwt", "2.11.0"): [beide], ("libfoo", "1.0.0"): [beide]}
    ausnahmen = {
        "CVE-2026-11111": {
            "paket": "libfoo",
            "gilt_fuer": "1.0.0",
            "grund": "Erfunden fuer diesen Test.",
            "beleg": "grep -rn nichts backend/app",
            "geprueft_am": "2026-09-01",
            "von": "Testlauf",
        }
    }

    code = pruefer.bewerten(pins, antworten, ausnahmen)

    assert code == 1
    ausgabe = capsys.readouterr().out
    assert "TOTE AUSNAHMEN" not in ausgabe
    assert "pyjwt==2.11.0  CVE-2026-11111" in ausgabe
    assert "libfoo==1.0.0  CVE-2026-11111" not in ausgabe


def test_ohne_meldung_ist_der_lauf_gruen(capsys: pytest.CaptureFixture[str]) -> None:
    """Der Regelfall, und der einzige Weg zu Rueckgabecode 0."""
    assert pruefer.bewerten(PINS, _antworten([]), {}) == 0
    assert "In Ordnung." in capsys.readouterr().out


def test_die_ausnahmeliste_wird_immer_ausgedruckt(capsys: pytest.CaptureFixture[str]) -> None:
    """Auch bei gruenem Lauf, mit Grund, Beleg und Datum.

    Eine Ausnahme, die niemand sieht, sieht niemand nach. Die Zeilen gehoeren
    ins Protokoll jedes Laufs und nicht nur in den Fehlerfall.
    """
    pruefer.ausnahmen_ausgeben(
        {
            "CVE-2026-58203": {
                "paket": "pydantic-settings",
                "gilt_fuer": "2.13.0",
                "grund": "Erfunden fuer diesen Test.",
                "beleg": 'grep -rn "secrets_dir" backend/app',
                "geprueft_am": "2026-09-01",
                "von": "Testlauf",
            }
        }
    )

    ausgabe = capsys.readouterr().out
    assert "CVE-2026-58203" in ausgabe
    assert "pydantic-settings==2.13.0" in ausgabe
    assert "Erfunden fuer diesen Test." in ausgabe
    assert "secrets_dir" in ausgabe
    assert "2026-09-01" in ausgabe


def test_leere_liste_sagt_das_auch(capsys: pytest.CaptureFixture[str]) -> None:
    """Schweigen liesse offen, ob es keine Ausnahmen gibt oder keine Ausgabe."""
    pruefer.ausnahmen_ausgeben({})

    assert "Ausnahmen: keine" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Der Rueckfall auf den Einzelabruf
# ---------------------------------------------------------------------------


def test_duenner_datensatz_wird_einzeln_nachgeholt() -> None:
    """Kommt ein Datensatz ohne ``affected`` an, fehlt die Behebungsangabe.

    Er wuerde als "keine Angabe" durchgereicht, und dann stuende im Fund keine
    Zielfassung. ``/v1/vulns/<id>`` bringt sie nach.
    """
    duenn = {"id": "GHSA-erfunden", "modified": "2026-09-01T00:00:00Z"}
    gerufen: list[str] = []

    def nachschlagen(kennung: str) -> dict:
        gerufen.append(kennung)
        return _meldung(kennung, "CVE-2026-11111", "9.9.9")

    fertig = pruefer.vervollstaendigen([duenn], nachschlagen=nachschlagen)

    assert gerufen == ["GHSA-erfunden"]
    assert pruefer.gruppen_bilden(fertig)["CVE-2026-11111"]["behoben_ab"] == "9.9.9"


def test_vollstaendiger_datensatz_wird_nicht_nachgeschlagen() -> None:
    """Sonst kostete jeder Lauf 51 Abrufe statt 11."""

    def nachschlagen(kennung: str) -> dict:  # pragma: no cover - darf nicht laufen
        raise AssertionError(f"{kennung} haette nicht nachgeschlagen werden duerfen")

    voll = _meldung("GHSA-erfunden", "CVE-2026-11111", "9.9.9")

    assert pruefer.vervollstaendigen([voll], nachschlagen=nachschlagen) == [voll]


# ---------------------------------------------------------------------------
# Der Waechter ueber die Liste selbst
# ---------------------------------------------------------------------------


def _maengel(schluessel: str, eintrag: dict, gepinnt: dict[str, str]) -> list[str]:
    """Was an einem Eintrag der Ausnahmeliste fehlt oder nicht mehr stimmt."""
    gefunden = []
    for feld in pruefer.AUSNAHME_FELDER:
        if not str(eintrag.get(feld, "")).strip():
            gefunden.append(f"{schluessel}: Feld {feld!r} ist leer")
    paket = eintrag.get("paket", "")
    if paket and paket not in gepinnt:
        gefunden.append(f"{schluessel}: {paket!r} steht nicht in requirements.txt")
    elif paket and gepinnt[paket] != eintrag.get("gilt_fuer"):
        gefunden.append(
            f"{schluessel}: gilt_fuer {eintrag.get('gilt_fuer')!r}, gepinnt ist "
            f"{gepinnt[paket]!r}"
        )
    return gefunden


def test_jede_ausnahme_ist_vollstaendig_und_passt_zum_pin() -> None:
    """Der Waechter ueber die ausgelieferte Liste.

    ⚠️ Wird dieser Test rot, ist die Antwort **nicht**, das fehlende Feld
    nachzutragen. Rot heisst: Jemand hat eine Meldung stehen lassen, ohne die
    Entscheidung aufzuschreiben, die dazu gehoert. Diese Entscheidung ist der
    ganze Zweck der Liste.

    Heute laeuft er durch die leere Liste, denn die Fassungen sind so gewaehlt,
    dass keine Ausnahme noetig ist. Dass er trotzdem greift, zeigt der Test
    darunter.
    """
    gepinnt = dict(pruefer.pins_lesen(REQUIREMENTS.read_text(encoding="utf-8")))

    fehlt = [
        text
        for schluessel, eintrag in pruefer.AUSNAHMEN.items()
        for text in _maengel(schluessel, eintrag, gepinnt)
    ]

    assert fehlt == []


def test_der_waechter_wuerde_einen_luecken_eintrag_finden() -> None:
    """Die Gegenprobe, ohne die der Waechter ueber eine leere Liste nichts sagt.

    Ein Eintrag ohne Beleg ist der haeufigste Fall: Der Grund ist schnell
    geschrieben, der Befehl, der ihn belegt, kostet Arbeit. Genau der fehlt dann.
    """
    gepinnt = dict(pruefer.pins_lesen(REQUIREMENTS.read_text(encoding="utf-8")))
    luecke = {
        "paket": "pyjwt",
        "gilt_fuer": "2.13.0",
        "grund": "Trifft uns nicht.",
        "beleg": "",
        "geprueft_am": "2026-09-01",
        "von": "Testlauf",
    }

    assert _maengel("CVE-2026-11111", luecke, gepinnt) == ["CVE-2026-11111: Feld 'beleg' ist leer"]

    veraltet = dict(luecke, beleg="grep -rn nichts backend/app", gilt_fuer="2.11.0")
    assert _maengel("CVE-2026-11111", veraltet, gepinnt) == [
        "CVE-2026-11111: gilt_fuer '2.11.0', gepinnt ist '2.13.0'"
    ]


# ---------------------------------------------------------------------------
# Der Ausfall draussen, und was der Lauf davon sagt
# ---------------------------------------------------------------------------


def _antwort(nutzlast: dict) -> io.BytesIO:
    """Was ``urlopen`` liefert: ein Kontextmanager mit lesbarem JSON."""
    return io.BytesIO(json.dumps(nutzlast).encode("utf-8"))


def test_ein_geglueckter_zweiter_versuch_steht_in_der_ausgabe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ Sonst hinterlaesst er keine Spur.

    Der Lauf ist gruen und war zwanzig Sekunden laenger unterwegs; niemand
    kann sagen, ob api.osv.dev gerade wackelt. Meldet sich Wochen spaeter ein
    "NICHT GEPRUEFT", sieht das nach einem einmaligen Ausrutscher aus - und
    die Laeufe davor, die es beim zweiten Versuch gerade noch schafften,
    haetten es vorher gesagt.
    """
    versuche: list[int] = []

    def wackelig(_anfrage, timeout=None):
        versuche.append(timeout)
        if len(versuche) == 1:
            raise urllib.error.URLError("Verbindung abgelehnt")
        return _antwort({"vulns": []})

    monkeypatch.setattr(pruefer.urllib.request, "urlopen", wackelig)
    monkeypatch.setattr(pruefer, "PAUSE_SEKUNDEN", 0)

    assert pruefer.osv_abfragen("pyjwt", "2.13.0") == []

    ausgabe = capsys.readouterr().out
    assert "Versuch 1 von 2" in ausgabe
    assert "pyjwt==2.13.0" in ausgabe
    assert "Verbindung abgelehnt" in ausgabe


def test_zwei_fehlversuche_heissen_nicht_geprueft(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ein Ausfall darf nicht wie ein Ergebnis aussehen.

    Er endet mit Rueckgabecode 0 - ein Bau, den ein fremder Ausfall umwirft,
    wird binnen einer Woche abgeschaltet, und dann prueft gar nichts mehr.
    Genau deshalb muss die Ausgabe unverwechselbar sein: drei Worte zum
    Suchen, die ``::warning::``-Zeile fuer die Zusammenfassung, und der Grund
    samt Paket, an dem es haengenblieb.
    """

    def tot(_anfrage, timeout=None):
        raise urllib.error.URLError("Name oder Dienst unbekannt")

    monkeypatch.setattr(pruefer.urllib.request, "urlopen", tot)
    monkeypatch.setattr(pruefer, "PAUSE_SEKUNDEN", 0)

    with pytest.raises(pruefer.NichtErreichbar) as fehler:
        pruefer.osv_abfragen("cryptography", "50.0.1")

    pruefer.nicht_geprueft(str(fehler.value))

    ausgabe = capsys.readouterr().out
    assert "NICHT GEPRUEFT" in ausgabe
    assert "::warning::" in ausgabe
    assert "cryptography==50.0.1" in ausgabe
    assert "Name oder Dienst unbekannt" in ausgabe
    # Und kein Wort, das nach einem Ergebnis klingt.
    assert "In Ordnung." not in ausgabe


def test_eine_falsch_gestellte_frage_wird_nicht_durchgewunken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4xx ist unser Fehler und keine Stoerung draussen.

    Ohne diese Trennung liefe eine dauerhaft falsche Abfrage jahrelang als
    "nicht erreichbar" durch, und der Lauf waere jedes Mal gruen.
    """

    def abgelehnt(_anfrage, timeout=None):
        raise urllib.error.HTTPError("https://api.osv.dev", 400, "Bad Request", {}, None)

    monkeypatch.setattr(pruefer.urllib.request, "urlopen", abgelehnt)
    monkeypatch.setattr(pruefer, "PAUSE_SEKUNDEN", 0)

    with pytest.raises(pruefer.NichtPruefbar):
        pruefer.osv_abfragen("pyjwt", "2.13.0")
