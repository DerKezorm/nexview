"""Der Waechter: Keine personenbezogene Angabe darf ins oeffentliche Repository.

⚠️ **Warum es diese Datei gibt.** Am 02.09.2026 stand im oeffentlichen
Repository der volle Name einer angehoerigen Person in zwoelf Zeilen, dazu zwei
echte Konto-Kennungen eines Medienservers in neunzehn. Seit Wochen. Durch 2.500
Tests und jede Pruefung hindurch, weil keiner dieser Tests danach gesucht hat.
Aufgefallen ist es beim Nachsehen aus einem ganz anderen Anlass.

Der Test ruft ``tools/personendaten_pruefen.py`` und faellt durch, sobald etwas
gefunden wird. **Rot heisst nicht: eine Ausnahme nachtragen.** Rot heisst: eine
Angabe ersetzen. Eine Ausnahme kommt nur infrage, wenn der Fund gar keine
Angabe ist - und dann mit einem Satz, der sagt warum. Der Pruefer faellt von
selbst durch, wenn eine Ausnahme keinen Grund hat.

⚠️ **Der Test ist die zweite Reihe, nicht die erste.** Die CI laeuft nach dem
Push; was sie meldet, steht schon in der Historie und ist ohne Umschreiben nicht
mehr wegzubekommen. Die erste Reihe ist ``.githooks/pre-push``.

⚠️ **Drei Dinge muessen zusaetzlich belegt sein, sonst ist der Test hohl.**

1. *Die Bodenschwelle.* Eine Pruefung, die keine Datei angesehen hat, meldet
   auch nichts und ist gruen. ``test_die_pruefung_hat_wirklich_nachgesehen``
   verlangt deshalb eine Mindestzahl an Dateien und Kandidaten.
2. *Jede einzelne Regel.* Waere ein Muster durch einen Tippfehler kaputt,
   bliebe alles gruen. ``test_jede_regel_schlaegt_bei_einer_probe_an`` schickt
   deshalb je eine **erfundene** Probe durch **denselben** Quelltext, den auch
   der Repository-Lauf benutzt.
3. *Die Sperrliste, gerade weil sie hier fehlt.* Sie ist gitignoriert und liegt
   in der CI nicht vor - dort laufen nur die Formregeln. Das darf nicht
   stillschweigend passieren: Der Lauf sagt es, und
   ``test_die_sperrliste_wirkt_mit_einem_erfundenen_eintrag`` belegt trotzdem,
   dass die Mechanik traegt, wenn sie da ist.

⚠️ **Die Proben hier sind stueckweise zusammengesetzt** - ``"@".join(...)``
statt einer fertigen Mailadresse. Nicht aus Verspieltheit: Der Pruefer sieht
auch diese Datei an, und eine ausgeschriebene Probe wuerde ihn dauerhaft rot
faerben. Nichts davon gehoert in die Sperrliste; erfundene Werte dort
einzutragen wuerde sie nur verwaessern.
"""

from __future__ import annotations

import base64
import hashlib
import json
import warnings
from pathlib import Path

import pytest

from tools import personendaten_pruefen as pruefer

WURZEL = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lauf() -> tuple[list, int, int, bool]:
    """Ein Durchlauf ueber das ganze Repository, einmal fuer alle Tests.

    Dauert rund sechs Sekunden mit Sperrliste, drei ohne.
    """
    funde, n_dateien, n_kandidaten, mit_sperrliste = pruefer.pruefen(str(WURZEL))
    if not mit_sperrliste:
        warnings.warn(
            "Ohne Sperrliste geprüft - es liefen nur die Formregeln. Das ist in "
            "der CI der Normalfall (die Liste ist gitignoriert), auf einem "
            "Arbeitsplatz aber ein Hinweis, dass "
            f"{Path(pruefer.sperrliste_pfad()).name} fehlt.",
            stacklevel=2,
        )
    return funde, n_dateien, n_kandidaten, mit_sperrliste


# --------------------------------------------------------------------------
# Der eigentliche Waechter
# --------------------------------------------------------------------------


def test_im_repository_steht_keine_personenbezogene_angabe(lauf) -> None:
    """Nichts, was einen Menschen bezeichnet, darf im versionierten Stand sein."""
    funde = lauf[0]
    zeilen = sorted({f"{rel}: {art} -> {wert}" for rel, art, wert in funde})
    assert not zeilen, (
        "Personenbezogene Angaben im versionierten Stand:\n  "
        + "\n  ".join(zeilen)
        + "\n\nErsetze sie durch erfundene Werte. Eine Ausnahme in AUSNAHMEN "
        "kommt nur infrage, wenn der Fund gar keine Angabe ist - mit "
        "ausgeschriebenem Grund."
    )


def test_die_pruefung_hat_wirklich_nachgesehen(lauf) -> None:
    """⚠️ Die Bodenschwelle: eine Pruefung, die nichts ansieht, meldet nichts.

    Faellt ``git ls-files`` aus, greift ein Ausnahmeeintrag zu weit oder
    scheitert das Lesen still an der Kodierung, waere der Test oben gruen und
    saehe genauso aus wie ein sauberes Repository. Am 02.09.2026 gemessen: 672
    Dateien, 1.122.305 Kandidaten mit Sperrliste, 1.214.114 ohne.
    """
    _, n_dateien, n_kandidaten, _ = lauf
    assert n_dateien >= pruefer.MINDESTENS_DATEIEN, (
        f"Nur {n_dateien} Dateien angesehen, erwartet mindestens "
        f"{pruefer.MINDESTENS_DATEIEN}. Die Prüfung hat nicht wirklich "
        "nachgesehen."
    )
    assert n_kandidaten >= pruefer.MINDESTENS_KANDIDATEN, (
        f"Nur {n_kandidaten} Kandidaten angesehen, erwartet mindestens "
        f"{pruefer.MINDESTENS_KANDIDATEN}. Die Prüfung hat nicht wirklich "
        "nachgesehen."
    )


def test_die_bodenschwelle_haelt_einen_leeren_lauf_auf(monkeypatch) -> None:
    """Und die Schwelle muss auch wirklich verdrahtet sein.

    ⚠️ Ohne diesen Test waere ``MINDESTENS_DATEIEN`` eine Zahl, die dasteht.
    Hier wird ein leerer Lauf vorgegaukelt; ``main`` muss 1 liefern.
    """
    monkeypatch.setattr(pruefer, "pruefen", lambda _: ([], 3, 12, True))
    monkeypatch.setattr(pruefer.sys, "argv", ["personendaten_pruefen.py", str(WURZEL)])
    assert pruefer.main() == 1


# --------------------------------------------------------------------------
# Jede Regel einzeln
# --------------------------------------------------------------------------

#: Je Regel eine erfundene Probe. Der Schluessel ist die Art, die der Pruefer
#: melden muss.
#:
#: ⚠️ Kommt eine Formregel dazu, gehoert hier eine Probe dazu -
#: ``test_zu_jeder_formregel_gibt_es_eine_probe`` macht das Vergessen rot.
PROBEN = {
    # ⚠️ noqa: Ruff will hier eine fertige Zeichenkette sehen. Genau die
    # darf hier nicht stehen - der Pruefer liest auch diese Datei und waere
    # dann dauerhaft rot. Dasselbe gilt fuer die Adresse weiter unten.
    "Mailadresse mit echter Domaene":
        "@".join(("erfundener.name", "gmail.com")),  # noqa: FLY002
    "lange Kennung, nicht als erfunden eingetragen": '"' + "4711" + "08150" + '"',
    "sieht aus wie ein JWT": "ey" + "J" + "hbGciOiJIUzI1NiJ9.e30.Xy" + "Z" * 8,
    "sieht aus wie ein GitHub-Token": "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "sieht aus wie ein API-Schluessel": "sk" + "-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "sieht aus wie ein Slack-Token": "xox" + "b" + "-" + "1234567890-abcdef",
    # ⚠️ Nicht "9f3c"*9: vier verschiedene Zeichen liegen genau auf der Kante
    # von ``_einfoermig``, und die Probe pruefte dann die Ausnahme statt die
    # Regel. Ein echter Abdruck hat sechzehn.
    "sieht aus wie ein Schluessel in Hex":
        '"' + hashlib.sha256(b"erfundener schluessel").hexdigest() + '"',
    "sieht aus wie Base64": '"' + base64.b64encode(
        hashlib.sha256(b"eine erfundene probe").digest()
    ).decode() + '"',
    "IP-Adresse ausserhalb der privaten Bereiche":
        ".".join(("81", "17", "22", "9")),  # noqa: FLY002
    "Pfad mit einem Benutzernamen darin":
        "C:" + "\\" + "Users" + "\\" + "erfundener.name" + "\\" + "Desktop",
}


@pytest.mark.parametrize("art", sorted(PROBEN))
def test_jede_regel_schlaegt_bei_einer_probe_an(art: str) -> None:
    """⚠️ Ein Waechter, von dem niemand gezeigt hat, dass er rot wird, ist keiner.

    Geprueft wird gegen ``pruefer.formregeln`` - dieselbe Funktion, die auch
    ueber das Repository laeuft. Eine Kopie der Muster hier waere gruen,
    waehrend die echten kaputt sind.
    """
    gemeldet = {a for a, _ in pruefer.formregeln(PROBEN[art])}
    assert art in gemeldet, (
        f"Die Probe für »{art}« hat nichts ausgelöst. Entweder ist die Regel "
        f"kaputt, oder die Probe passt nicht mehr zu ihr. Gemeldet wurde: "
        f"{sorted(gemeldet) or 'nichts'}."
    )


def test_zu_jeder_formregel_gibt_es_eine_probe() -> None:
    """Neue Regel ohne Probe: dann ist sie ungeprueft mitgeliefert.

    ⚠️ Ohne diesen Test waechst die Regelliste, und die Gegenprobe bleibt bei
    den alten stehen - man sieht es an nichts.
    """
    aus_dem_pruefer = {was for _, was, _ in pruefer._TOKEN}
    fehlend = sorted(aus_dem_pruefer - set(PROBEN))
    assert not fehlend, (
        "Zu diesen Token-Regeln gibt es keine Probe in PROBEN: "
        + ", ".join(fehlend)
        + ". Eine ungeprüft mitgelieferte Regel ist schlimmer als keine."
    )
    # ⚠️ Die Token-Muster stehen in einer Liste und lassen sich abzählen; die
    # vier anderen Formregeln (Mailadresse, Kennung, IP, Heimatpfad) stehen als
    # Code in ``formregeln`` und nicht in einer Tabelle. Diese Schwelle hält
    # fest, dass es auch zu ihnen Proben gibt - fällt eine weg, wird es rot.
    assert len(PROBEN) >= len(aus_dem_pruefer) + 4, (
        f"Nur {len(PROBEN)} Proben für {len(aus_dem_pruefer)} Token-Regeln plus "
        "vier weitere Formregeln. Es fehlt mindestens eine."
    )


def test_harmloser_text_loest_nichts_aus() -> None:
    """Die Gegenrichtung: ein Waechter, der alles meldet, wird abgeschaltet.

    ⚠️ Genau hier war die erste Fassung kaputt - sie hielt jede zweite
    API-Adresse fuer Base64, weil der Schraegstrich in beiden Alphabeten steht.

    ⚠️ Eine Vierergruppe wie ``4.9.5.0`` steht hier bewusst **nicht**. Eine
    Versionsnummer ist von einer Adresse an der Gestalt nicht zu unterscheiden;
    die Regel meldet sie zu Recht, und der Einzelfall gehoert nach AUSNAHMEN -
    nicht in eine Regel, die dann auch echte Adressen durchliesse.
    """
    harmlos = "\n".join((
        '  const pfad = "/api/settings/qualitaetsprofile/benennungsschema";',
        '  await fetch("/api/settings/instanzen/downloadkollision/pruefen");',
        '  "JSONSchema202012KeywordAdditionalPropertiesUnevaluated"',
        '  hinweis = "schreib an support@example.com"',
        "  server = '127.0.0.1' und '192.168.1.50' und '203.0.113.7'",
        "  pfad = '/home/runner/work/nexview'",
        '  kennung = "700000001"',
        # ⚠️ Attrappen aus einer einzigen wiederholten Ziffer. Sie treffen
        # zwei Muster auf einmal - die Kennung und den Hex-Schluessel - und
        # sind trotzdem offensichtlich erfunden. ``_einfoermig`` faengt beide,
        # damit die erlaubten Werte nicht zu einer Pflegeliste werden.
        '  konto_id = "' + "1" * 32 + '"',
        '  konto_id = "' + "2" * 32 + '"',
    ))
    gemeldet = sorted({a for a, _ in pruefer.formregeln(harmlos)})
    assert not gemeldet, (
        "Harmloser Text hat Alarm ausgelöst: " + ", ".join(gemeldet)
    )


# --------------------------------------------------------------------------
# Sperrliste
# --------------------------------------------------------------------------


def test_die_sperrliste_wirkt_mit_einem_erfundenen_eintrag(tmp_path, monkeypatch) -> None:
    """Die Mechanik der Sperrliste, belegt ohne die echte Liste.

    ⚠️ **Warum das sein muss.** Die echte Liste ist gitignoriert; in der CI gibt
    es sie nicht, und dort liefe dieser halbe Waechter ungeprueft mit. Der erste
    Mensch, der spaeter einen Namen eintraegt, verliesse sich darauf, dass der
    Abdruck wirklich verglichen wird.

    Der Name hier ist frei erfunden und gehoert **nicht** in die echte Liste.
    """
    name = "Quirinbold"
    liste = tmp_path / "sperrliste.json"
    liste.write_text(json.dumps({"eintraege": [
        {"abdruck": pruefer.abdruck(name), "art": "erfundener Test-Name"},
    ]}), encoding="utf-8")
    monkeypatch.setenv(pruefer.SPERRLISTE_UMGEBUNG, str(liste))

    sperre = pruefer.sperrliste_lesen()
    assert sperre, "Die Sperrliste wurde nicht gelesen."

    funde, gesehen = pruefer.sperrlistenregel(f"konto = '{name.lower()}Schmitt'", sperre)
    assert gesehen > 0, "Die Sperrlistenregel hat keinen Kandidaten angesehen."
    assert [w for _, w in funde] == [name.lower()], (
        f"Der Name im zusammengeschriebenen Wort wurde nicht gefunden: {funde}"
    )

    sauber, _ = pruefer.sperrlistenregel("konto = 'jemand anderes'", sperre)
    assert not sauber, f"Falscher Alarm auf harmlosem Text: {sauber}"


# --------------------------------------------------------------------------
# Ausnahmen
# --------------------------------------------------------------------------


def test_jede_ausnahme_hat_einen_ausgeschriebenen_grund() -> None:
    """⚠️ Ohne Grund keine Ausnahme - sonst ist die Liste ein stilles Loch.

    Verlangt wird ein Satz, kein Haken. Wer nichts hinschreiben kann, hat
    gerade gemerkt, dass es keine Ausnahme ist.
    """
    zu_duenn = sorted(
        f"{wert!r} ({grund.strip()!r})"
        for wert, grund in list(pruefer.AUSNAHMEN.items())
        + list(pruefer.AUSNAHME_DATEIEN.items())
        if len(grund.strip()) < 20
    )
    assert not zu_duenn, (
        "Diese Ausnahmen haben keinen brauchbaren Grund: "
        + ", ".join(zu_duenn)
        + ". Schreib hin, warum der Fund keine personenbezogene Angabe ist."
    )


def test_eine_ausnahme_ohne_grund_laesst_die_pruefung_durchfallen(monkeypatch) -> None:
    """Und dieses Tor muss verdrahtet sein, nicht bloss beschrieben."""
    monkeypatch.setitem(pruefer.AUSNAHMEN, "erfundener-eintrag-ohne-grund", "   ")
    monkeypatch.setattr(pruefer, "pruefen", lambda _: ([], 10**6, 10**7, True))
    monkeypatch.setattr(pruefer.sys, "argv", ["personendaten_pruefen.py", str(WURZEL)])
    assert pruefer.main() == 1


def test_ausgenommene_dateien_gibt_es_noch() -> None:
    """Eine Ausnahme auf eine Datei, die es nicht mehr gibt, ist eine Leiche.

    ⚠️ Sie schadet nicht sofort - aber wenn der Pfad spaeter wieder auftaucht,
    ist er ohne Entscheidung ausgenommen.
    """
    verschwunden = sorted(p for p in pruefer.AUSNAHME_DATEIEN if not (WURZEL / p).exists())
    assert not verschwunden, (
        "Diese ausgenommenen Dateien gibt es nicht mehr: "
        + ", ".join(verschwunden)
        + ". Nimm den Eintrag heraus."
    )


# --------------------------------------------------------------------------
# Der Haken vor dem Push
# --------------------------------------------------------------------------


def test_der_pre_push_haken_liegt_vor_und_ruft_die_pruefung() -> None:
    """⚠️ Der Haken ist der wichtigere Teil - er laeuft **vor** dem Push.

    Was die CI meldet, steht schon in der Historie. Deshalb wird hier nicht nur
    geprueft, dass die Datei existiert, sondern auch, dass sie den Pruefer
    wirklich aufruft und auf seinen Rueckgabecode reagiert.
    """
    haken = WURZEL / ".githooks" / "pre-push"
    assert haken.exists(), (
        ".githooks/pre-push fehlt. Ohne ihn greift die Prüfung erst nach dem "
        "Push, und dann steht die Angabe schon in der Historie."
    )
    text = haken.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh"), (
        "Der Haken braucht #!/bin/sh in der ersten Zeile - unter Windows läuft "
        "er über Git Bash."
    )
    assert "personendaten_pruefen.py" in text, "Der Haken ruft den Prüfer nicht auf."
    assert "exit 1" in text, "Der Haken bricht den Push nicht ab."
