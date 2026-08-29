"""Der ausfuehrliche Zweig des Assistenten.

⚠️ **Warum diese Fragen und keine anderen.** Beim Entwurf wurde jede
TRaSH-Gruppe nachgerechnet statt nach ihrem Namen ausgewaehlt - und drei
verworfen: ``audio-channels`` und ``streaming-services-uk`` tragen durchgehend
**null** Punkte. Eine Frage danach haette nichts bewirkt und trotzdem eine
Wirkung versprochen. Diese Tests halten fest, dass die uebrigen Fragen wirklich
etwas aendern.

⚠️ **Der einfache Weg ist der ausfuehrliche ohne Antworten.** Es gibt keine
zwei Bauwege, die auseinander driften koennten - fehlt eine Antwort oder steht
sie auf "egal", passiert schlicht nichts.
"""

from __future__ import annotations

import pytest

from app.services import trash

SPRACHEN = {code: nummer for nummer, code in enumerate(trash.SPRACHNAMEN, 1)}
QUALITAETEN_HD = [
    "Remux-1080p", "Bluray-1080p", "WEBDL-1080p", "WEBRip-1080p",
    "Bluray-720p", "WEBDL-720p", "WEBRip-720p",
]
QUALITAETEN_UHD = ["Remux-2160p", "Bluray-2160p", "WEBDL-2160p", "WEBRip-2160p", *QUALITAETEN_HD]


def rezept(**anders):
    grund = {
        "name": "P", "typ": "radarr", "aufloesung": "1080p", "sofortNehmen": True,
        "quelle": "remux", "sprachen": ["de"], "sprachRollen": {"de": "pflicht"},
        "mehrerePflicht": "alle", "hdr": "netz", "schlusspunkt": "trash",
    }
    grund.update(anders)
    return grund


def namen(dienst="radarr", qualitaeten=None, **anders):
    plan = trash.bauplan(
        rezept(**anders), dienst, SPRACHEN, qualitaeten or QUALITAETEN_HD
    )
    return {w.name: w.punkte for w in plan.formate}


def test_ohne_antworten_aendert_sich_nichts():
    """⚠️ Der einfache Weg muss unberuehrt bleiben.

    Sonst haetten alle bestehenden Profile plötzlich einen anderen Inhalt -
    und der Abgleich meldete auf jeder Instanz Unterschiede.
    """
    schlicht = namen()
    mit_egal = namen(
        modus="einfach", ton="egal", x265="egal", sdr="egal", fassungen="egal",
        barrierefrei="egal", regionale_gruppen="egal", asiatische_dienste="egal",
    )
    assert schlicht == mit_egal


def test_ton_bringt_die_tonspuren_mit():
    """TrueHD ATMOS an der Spitze - die Werte stammen aus den Guides."""
    ohne, mit = namen(), namen(ton="bevorzugen")
    dazu = set(mit) - set(ohne)
    assert "TrueHD ATMOS" in dazu and "DTS-HD MA" in dazu
    assert mit["TrueHD ATMOS"] > mit["DTS-HD MA"] > 0


@pytest.mark.parametrize(
    "sprache,rollen",
    [("de", {"de": "pflicht"}), ("en", {"en": "pflicht"})],
)
def test_x265_wird_wirklich_ausgeschlossen(sprache, rollen):
    """⚠️ "Meiden" muss Minuspunkte bedeuten, nicht bloss "weniger Punkte".

    ⚠️ **Geprueft wird die Wirkung, nicht jeder einzelne Wert.** Die Guides
    gewichten je Sprachfamilie verschieden: Im deutschen Satz steht
    ``x265 (HD)`` bewusst auf 0, dafuer ``x265 (no HDR/DV)`` auf -35000; im
    Standardsatz liegen beide bei -10000. Wer auf beide Werte prueft, prueft
    eine Fassung der Guides statt die Zusage an den Betreiber - und faellt beim
    naechsten Stand um.
    """
    ohne = namen(sprachen=[sprache], sprachRollen=rollen)
    mit = namen(sprachen=[sprache], sprachRollen=rollen, x265="meiden")
    treffer = {n: p for n, p in mit.items() if n.startswith("x265")}
    assert treffer, "die goldene Regel muss x265-Muster mitbringen"
    assert not [n for n in ohne if n.startswith("x265")], (
        "ohne die Frage darf kein x265-Muster im Plan stehen"
    )
    assert min(treffer.values()) <= -10000, (
        f"mindestens ein x265-Muster muss deutlich ausschliessen: {treffer}"
    )


def test_die_goldene_regel_kennt_beide_aufloesungen():
    """Es gibt eine Gruppe fuer HD und eine fuer UHD - beide muessen greifen."""
    hd = [n for n in namen(x265="meiden") if n.startswith("x265")]
    uhd = [
        n
        for n in namen(x265="meiden", aufloesung="2160p", qualitaeten=QUALITAETEN_UHD)
        if n.startswith("x265")
    ]
    assert hd and uhd


def test_sdr_nur_bei_4k():
    """⚠️ Bei 1080p gibt es kein HDR - die Frage waere dort gegenstandslos.

    Sie trotzdem wirken zu lassen, schloesse ohne Grund Fassungen aus.
    """
    bei_hd = namen(sdr="meiden")
    bei_uhd = namen(sdr="meiden", aufloesung="2160p", qualitaeten=QUALITAETEN_UHD)
    assert "SDR" not in bei_hd
    assert bei_uhd.get("SDR", 0) <= -10000


def test_schnittfassungen_nur_bei_filmen():
    """Serien haben keine Schnittfassungen - die Muster gehoeren nicht hinein."""
    filme = namen(fassungen="bevorzugen")
    serien = namen(dienst="sonarr", typ="sonarr", fassungen="bevorzugen")
    assert filme.get("IMAX", 0) > 0
    assert "IMAX" not in serien


def test_barrierefreie_fassungen_werden_nur_auf_wunsch_gemieden():
    """⚠️ Die Voreinstellung ist **egal**, nicht "meiden".

    Wer Audiodeskription oder Gebaerdensprache braucht, darf sie nicht deshalb
    verlieren, weil eine Voreinstellung es so wollte.
    """
    standard = namen()
    gemieden = namen(barrierefrei="meiden")
    assert not [n for n in standard if n.startswith("WiTH ")]
    assert all(p <= -10000 for n, p in gemieden.items() if n.startswith("WiTH "))


def test_regionale_gruppen_nur_bei_passender_sprache():
    """Deutsche Release-Gruppen ergeben ohne deutsche Familie keinen Sinn."""
    deutsch = namen(regionale_gruppen="bevorzugen")
    englisch = namen(
        regionale_gruppen="bevorzugen", sprachen=["en"], sprachRollen={"en": "pflicht"}
    )
    assert deutsch.get("German Remux Tier 01", 0) > 0
    assert "German Remux Tier 01" not in englisch


def test_jede_frage_aendert_wirklich_etwas():
    """⚠️ Eine Frage, die nichts bewirkt, ist eine Luege.

    Genau daran sind ``audio-channels`` und ``streaming-services-uk``
    gescheitert: durchgehend null Punkte. Was hier steht, muss den Bauplan
    nachweislich veraendern.
    """
    grund = namen(aufloesung="2160p", qualitaeten=QUALITAETEN_UHD)
    faelle = {
        "ton": "bevorzugen",
        "x265": "meiden",
        "sdr": "meiden",
        "fassungen": "bevorzugen",
        "barrierefrei": "meiden",
        "regionale_gruppen": "bevorzugen",
        "asiatische_dienste": "dazu",
    }
    for feld, antwort in faelle.items():
        mit = namen(aufloesung="2160p", qualitaeten=QUALITAETEN_UHD, **{feld: antwort})
        assert mit != grund, f"Die Antwort {feld}={antwort} bewirkt nichts"


@pytest.mark.parametrize("dienst", ["radarr", "sonarr"])
def test_beide_dienste_bauen_ausfuehrlich_durch(dienst):
    """Alle Antworten gleichzeitig - und es muss ein Bauplan herauskommen."""
    plan = trash.bauplan(
        rezept(
            typ=dienst, ton="bevorzugen", x265="meiden", fassungen="bevorzugen",
            barrierefrei="meiden", regionale_gruppen="bevorzugen",
            asiatische_dienste="dazu",
        ),
        dienst,
        SPRACHEN,
        QUALITAETEN_HD,
    )
    assert plan.formate and plan.profilname == "P"
