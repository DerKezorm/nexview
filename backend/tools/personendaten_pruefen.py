"""Personenbezogene Angaben im Arbeitsstand finden, bevor sie veroeffentlicht werden.

    python tools/personendaten_pruefen.py            prueft dieses Repository
    python tools/personendaten_pruefen.py <pfad>     prueft ein anderes

Rueckgabecode 0, wenn nichts gefunden wurde, sonst 1.

⚠️ **Warum es das gibt.** Am 02.09.2026 stand im oeffentlichen Repository der
volle Name einer angehoerigen Person in zwoelf Zeilen, dazu zwei echte
Plex-Kontonummern in neunzehn. Beides seit Wochen, beides durch 2.500 Tests und
jede Pruefung gelaufen. Gefunden hat es niemand - es kam beim Nachsehen aus
einem anderen Anlass heraus.

⚠️ **Zwei Haelften, und die zweite darf nicht ins Repository.**

*Formregeln* stehen hier in der Datei: Kennungen, Mailadressen, Token, IP-
Adressen, Heimatpfade. Sie erkennen eine Angabe an ihrer Gestalt und brauchen
kein Wissen darueber, wem sie gehoert. Die stehen offen da, sie verraten nichts.

*Die Sperrliste* enthaelt die echten Werte - Namen, Kennungen, Adressen. Sie
liegt in ``.personendaten-sperrliste.json`` und ist **mit Absicht nicht im
Repository**: eine Liste der Namen, die nicht veroeffentlicht werden duerfen,
waere selbst die Veroeffentlichung. Sie speichert auch keine Klartexte,
sondern SHA-256-Abdruecke, damit ein versehentlich mitgegebener Ordner die
Namen nicht preisgibt.

Fehlt die Sperrliste, laufen nur die Formregeln, und das Werkzeug sagt es.

⚠️ **Der richtige Ort ist vor dem Push, nicht in der CI.** Die CI laeuft
nachdem etwas hochgeladen wurde; danach steht es in der Historie. Deshalb
haengt dieses Werkzeug zusaetzlich in ``.githooks/pre-push``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
STANDARD_WURZEL = os.path.dirname(os.path.dirname(HIER))

#: Endungen, die angesehen werden. Binaerdateien haben hier nichts zu suchen.
ENDUNGEN = (
    ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml",
    ".html", ".css", ".txt", ".sh", ".ps1", ".toml", ".cfg", ".ini", ".env",
    ".sql", ".xml", ".svg",
)

#: Domaenen, die ausdruecklich fuer Beispiele gedacht sind. RFC 2606 und die
#: deutschen Entsprechungen, die das Projekt benutzt.
ERLAUBTE_DOMAENEN = {
    "example.com", "example.org", "example.net", "example.edu",
    "beispiel.de", "beispiel.test", "bsp.de", "test.de", "b.de",
    "localhost", "test", "invalid", "local", "nexview.test",
}

#: Erfundene Kontonummern, die als Testdaten gedacht sind. Wer eine neue
#: braucht, traegt sie hier ein - und merkt dabei, dass er eine erfindet und
#: keine abschreibt.
#:
#: ⚠️ Kuerzer wird die Liste durch ``_einfoermig``: Eine Attrappe aus einer
#: einzigen wiederholten Ziffer braucht hier gar keinen Eintrag mehr.
ERLAUBTE_KENNUNGEN = {
    "700000001", "700000002", "700000101", "700000102",
    "111222333", "123456789", "12345678",
}

#: Ausnahmen von den Formregeln. **Ohne Grund keine Ausnahme** - ein leerer
#: Grund laesst die Pruefung durchfallen. Der Schluessel ist der gefundene
#: Wert, der Wert die Begruendung.
AUSNAHMEN = {
    "pyjwt@2.13.1":
        "gepinnte Version in der Abhaengigkeitsliste, keine Mailadresse",
    # ⚠️ Die Adressregel ist absichtlich streng. Was hier steht, ist einzeln
    # nachgesehen; eine neue Adresse faellt weiterhin auf.
    "4.9.5.0": "Emby-Versionsnummer, an der gemessen wurde",
    "1.2.3.4": "Lehrbuchadresse im Test der Anmeldebremse",
    "9.9.9.9": "Lehrbuchadresse im Test der Anmeldebremse",
    "3.1.3.7": "Zahlenfolge in oidc.py, keine Adresse",
    "5.4.5.8": "Koordinaten in einem SVG-Pfad",
    "12.2.8.8": "Koordinaten in einem SVG-Pfad",
    "2.65.64.7": "Koordinaten in einem SVG-Pfad",
    "4.94.36.31": "Koordinaten in einem SVG-Pfad",
    "27.18.58.69": "Koordinaten in einem SVG-Pfad",
}

#: Ganze Dateien, die von den Formregeln ausgenommen sind. Auch hier gilt:
#: ohne Grund keine Ausnahme.
AUSNAHME_DATEIEN = {
    "backend/app/daten/trash-radarr.json":
        "Pruefsummen der TRaSH-Guides, 2.598 Stueck. Fremde Kennungen von "
        "Qualitaetsprofilen, keine Personendaten - und keine Geheimnisse, sie "
        "stehen oeffentlich in den TRaSH-Guides.",
    "backend/app/daten/trash-sonarr.json":
        "dasselbe fuer Sonarr, 1.878 Stueck.",
}

# --------------------------------------------------------------------------
# Formregeln
# --------------------------------------------------------------------------

_MAIL = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
_KENNUNG = re.compile(r"[\"'](\d{8,})[\"']")
_IP = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
# ⚠️ **Nur echte Dateipfade, keine API-Adressen.** Die erste Fassung meldete
# 134 Stellen, und jede einzelne war eine Route: ``/users/betreiber``,
# ``/home/curated``, ``/Users/AuthenticateByName`` bei Jellyfin. Ein
# Dateipfad hat entweder einen Backslash oder geht hinter dem Namen weiter.
_HEIMATPFAD = re.compile(
    r"[A-Za-z]:\\Users\\([A-Za-z][\w.-]{1,30})"
    r"|(?:/home|/Users)/([A-Za-z][\w.-]{1,30})/")
#: Zusammenhaengende Folgen gleicher Schreibung ab vier Zeichen - das, was ein
#: Mensch als Wort liest.
_WORTLAUF = re.compile(r"[a-z]{4,}|[A-Z]{4,}")


def _ist_lesbar(wert: str) -> bool:
    """Ob eine lange Zeichenkette aus Woertern besteht statt aus Zufall.

    ⚠️ **Base64 und URL-Pfade teilen sich das Alphabet.** Der Schraegstrich
    gehoert in beide, und deshalb meldete die erste Fassung achtzehn Stellen,
    von denen keine ein Schluessel war: ``"/api/settings/qualitaetsprofile/
    benennung"``, ``"/settings/instanzen/downloadkollision/..."``, dazu
    Bezeichner aus dem Swagger-Bundle wie
    ``"JSONSchema202012KeywordAdditionalProperties"``.

    Unterscheiden lassen sie sich am Lesbaren. Gemessen an 50.000 zufaelligen
    Base64-Ketten decken Woerter ab vier gleichen Fallen darin im Mittel 14,8
    Prozent der Laenge ab, in 99,9 Prozent der Faelle unter 46. Bei den vier
    Fehlalarmen oben sind es 79 bis 85. Die Grenze liegt bei der Haelfte, weit
    weg von beiden.

    ⚠️ Sie ist nicht dicht: 12 der 50.000 Zufallsketten kamen darueber, 0,024
    Prozent. Ein Base64-Schluessel entkaeme also mit dieser Wahrscheinlichkeit.
    Das ist der Preis dafuer, dass der Waechter ueberhaupt benutzt wird -
    achtzehn Fehlalarme pro Lauf haetten ihn nach einer Woche abgeschaltet.
    """
    lesbar = sum(len(m.group(0)) for m in _WORTLAUF.finditer(wert))
    return lesbar * 2 > len(wert)


def _base64_verdaechtig(treffer: str) -> bool:
    return not _ist_lesbar(treffer.strip("\"'"))


#: So viele verschiedene Zeichen muss eine Kennung mindestens haben, um als
#: echt durchzugehen.
MINDESTENS_VERSCHIEDENE = 4


def _einfoermig(wert: str) -> bool:
    """Ob eine Kette so wenige verschiedene Zeichen hat, dass sie erfunden ist.

    ⚠️ **Eine Regel statt einer Liste.** Erfundene Testkennungen sehen aus wie
    ``"11111111111111111111111111111111"``; eine echte Kennung eines
    Medienservers hat sechzehn verschiedene Zeichen ueber zweiunddreissig
    Stellen verteilt. (Ein echtes Beispiel steht hier absichtlich nicht - auch
    nicht abgekuerzt. Ein halber Wert ist ein halber Verrat.)

    Ohne diese Frage muesste jede neue Attrappe einzeln in
    ``ERLAUBTE_KENNUNGEN`` eingetragen werden - und eine Liste, die bei jedem
    neuen Testfall waechst, wird irgendwann aus Bequemlichkeit gepflegt statt
    aus Ueberzeugung.

    Der Preis ist klein und bekannt: Bei einer echten neunstelligen Nummer aus
    Zufallsziffern liegt die Wahrscheinlichkeit, hoechstens drei verschiedene
    zu treffen, bei etwa 0,24 Prozent; bei einer 32-stelligen Hexkette ist sie
    verschwindend. Wer eine Attrappe baut, macht sie damit **absichtlich**
    eintoenig - und das sieht man ihr im Test dann auch an.
    """
    return len(set(wert)) < MINDESTENS_VERSCHIEDENE


#: Muster, Beschreibung, und - wo noetig - eine zweite Frage an den Treffer.
_TOKEN = (
    (re.compile(r"\beyJ[A-Za-z0-9_-]{15,}"), "sieht aus wie ein JWT", None),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "sieht aus wie ein GitHub-Token", None),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "sieht aus wie ein GitHub-Token", None),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "sieht aus wie ein API-Schluessel", None),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "sieht aus wie ein Slack-Token", None),
    (re.compile(r"[\"'][0-9a-f]{32,}[\"']"), "sieht aus wie ein Schluessel in Hex",
     lambda treffer: not _einfoermig(treffer.strip("\"'"))),
    (re.compile(r"[\"'][A-Za-z0-9+/]{40,}={0,2}[\"']"), "sieht aus wie Base64",
     _base64_verdaechtig),
)

#: Pfadnamen, die in einem Heimatpfad stehen duerfen: Platzhalter, keine Menschen.
ERLAUBTE_PFADNAMEN = {
    "user", "username", "benutzer", "nutzer", "runner", "node", "root",
    "app", "vscode", "someone", "you", "dein-name", "name", "jemand",
}

#: IP-Bereiche, die niemandem gehoeren: privat, lokal, Dokumentation.
def _ip_ist_harmlos(a: int, b: int, c: int, d: int) -> bool:
    if a == 127 or a == 0 or a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 169 and b == 254:
        return True
    if a >= 224:                       # Multicast und reserviert
        return True
    if (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113)):
        return True                    # RFC 5737, ausdruecklich fuer Doku
    if max(a, b, c, d) > 255:
        return False
    # Alles andere koennte eine echte Adresse sein.
    return False


# --------------------------------------------------------------------------
# Sperrliste
# --------------------------------------------------------------------------

SPERRLISTE_DATEI = os.path.join(HIER, ".personendaten-sperrliste.json")

_WORT = re.compile(r"[A-Za-zÄÖÜäöüßÀ-ÿ]{3,}")
_ZIFFERN = re.compile(r"\d{6,}")
_CAMEL = re.compile(r"[A-ZÄÖÜ][a-zäöüß]+|[a-zäöüß]+")


def abdruck(wert: str) -> str:
    return hashlib.sha256(wert.strip().lower().encode("utf-8")).hexdigest()


#: Umgebungsvariable, die den Ort der Sperrliste ueberschreibt.
#:
#: ⚠️ Sie oeffnet kein Loch: Ohne Sperrliste laeuft das Werkzeug ohnehin, und
#: es sagt es dann. Sie ist dafuer da, dass die Gegenprobe in
#: ``tests/test_personendaten.py`` mit einer **erfundenen** Liste durch
#: denselben Weg laufen kann wie der Ernstfall - die echte Liste ist
#: gitignoriert und steht in der CI nicht zur Verfuegung.
SPERRLISTE_UMGEBUNG = "NEXVIEW_PERSONENDATEN_SPERRLISTE"


def sperrliste_pfad():
    return os.environ.get(SPERRLISTE_UMGEBUNG) or SPERRLISTE_DATEI


def sperrliste_lesen():
    """Die Abdruecke laden. Fehlt die Datei, ist die Liste leer."""
    pfad = sperrliste_pfad()
    if not os.path.exists(pfad):
        return {}
    with open(pfad, encoding="utf-8") as fh:
        roh = json.load(fh)
    return {e["abdruck"]: e.get("art", "ohne Angabe") for e in roh.get("eintraege", [])}


def kandidaten(text: str):
    """Alles, was ein personenbezogener Wert sein koennte, als Einzelstueck.

    Auch die Teile zusammengeschriebener Namen: "MiraBaumgart" faellt sonst
    durch, weil es als ein Wort anders aussieht als sein Vorname.

    ⚠️ Das Beispiel hier ist erfunden, und das ist keine Kosmetik: Bis zum
    02.09.2026 stand an dieser Stelle ein echter voller Name - in dem Werkzeug,
    das genau das verhindern soll. Es fiel nicht auf, weil ``git ls-files``
    eine noch nicht versionierte Datei nicht kennt und die Pruefung sich
    selbst also nicht ansah.
    """
    for m in _WORT.finditer(text):
        wort = m.group(0)
        yield wort
        teile = _CAMEL.findall(wort)
        if len(teile) > 1:
            for teil in teile:
                if len(teil) >= 3:
                    yield teil
    for m in _ZIFFERN.finditer(text):
        yield m.group(0)
    for m in _MAIL.finditer(text):
        yield m.group(0)


# --------------------------------------------------------------------------

#: Bodenschwelle. Eine Pruefung, die nichts angesehen hat, meldet auch nichts,
#: und sieht dabei genauso aus wie ein sauberes Repository.
#:
#: Gemessen am 02.09.2026: 672 Dateien, 1.122.305 Kandidaten mit Sperrliste,
#: 1.214.114 ohne. Die Schwellen liegen weit darunter, damit ein geloeschtes
#: Verzeichnis den Waechter nicht rot macht - aber nicht so weit, dass ein
#: ausgefallenes ``git ls-files`` durchginge.
MINDESTENS_DATEIEN = 200
MINDESTENS_KANDIDATEN = 100000


def _gemeint(rel):
    """Ob eine Datei angesehen wird.

    ⚠️ **Nicht nur nach Endung.** Skripte tragen oft gar keine: ``.githooks/
    pre-push`` waere sonst als einziger Teil dieses Waechters selbst
    ungeprueft. Dateien ohne Punkt im Namen kommen deshalb mit; was sich nicht
    als UTF-8 lesen laesst, faellt beim Oeffnen ohnehin heraus.
    """
    if rel.endswith(ENDUNGEN):
        return True
    return "." not in os.path.basename(rel)


def dateien(wurzel):
    """Die versionierten Dateien - was Git ignoriert, wird nie veroeffentlicht."""
    roh = subprocess.run(
        ["git", "ls-files", "-z"], cwd=wurzel,
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if roh.returncode != 0:
        raise SystemExit("kein Git-Baum: " + wurzel)
    for rel in roh.stdout.split("\0"):
        if rel and _gemeint(rel):
            yield rel


def formregeln(text):
    """Die fuenf Formregeln ueber einen Text, als ``(art, wert)``.

    ⚠️ **Warum das eine eigene Funktion ist.** Ein Waechter, von dem niemand
    gezeigt hat, dass er rot wird, ist keiner. Ueber diese Funktion laesst
    ``tests/test_personendaten.py`` jede Regel einzeln gegen eine erfundene
    Probe laufen - und zwar gegen **denselben** Quelltext, der auch das
    Repository prueft. Eine Kopie der Regeln im Test wuerde gruen bleiben,
    waehrend die echten kaputt sind.
    """
    # 1. Mailadressen
    for m in _MAIL.finditer(text):
        if m.group(1).lower() not in ERLAUBTE_DOMAENEN:
            yield "Mailadresse mit echter Domaene", m.group(0)

    # 2. Kennungen
    for m in _KENNUNG.finditer(text):
        if m.group(1) in ERLAUBTE_KENNUNGEN or _einfoermig(m.group(1)):
            continue
        yield "lange Kennung, nicht als erfunden eingetragen", m.group(1)

    # 3. Token und Schluessel
    for muster, was, weiter in _TOKEN:
        for m in muster.finditer(text):
            if weiter is not None and not weiter(m.group(0)):
                continue
            yield was, m.group(0)[:40]

    # 4. IP-Adressen
    for m in _IP.finditer(text):
        teile = m.groups()
        # Eine fuehrende Null gibt es in keiner Adresse - das ist dann eine
        # Versionsnummer oder eine Koordinate.
        if any(z != str(int(z)) for z in teile):
            continue
        zahlen = [int(z) for z in teile]
        if max(zahlen) <= 255 and not _ip_ist_harmlos(*zahlen):
            yield "IP-Adresse ausserhalb der privaten Bereiche", m.group(0)

    # 5. Heimatpfade
    for m in _HEIMATPFAD.finditer(text):
        name = m.group(1) or m.group(2)
        if name and name.lower() not in ERLAUBTE_PFADNAMEN:
            yield "Pfad mit einem Benutzernamen darin", m.group(0)


def sperrlistenregel(text, sperre):
    """Die sechste Regel: Werte, deren Abdruck auf der Sperrliste steht.

    Liefert ``(art, wert)`` und daneben die Zahl der angesehenen Kandidaten -
    die Bodenschwelle haengt daran.
    """
    funde = []
    gesehen = 0
    for wert in kandidaten(text):
        gesehen += 1
        treffer = sperre.get(abdruck(wert))
        if treffer:
            funde.append((f"Sperrliste ({treffer})", wert))
    return funde, gesehen


def pruefen(wurzel):
    sperre = sperrliste_lesen()
    funde = []
    gesehen_dateien = 0
    gesehen_kandidaten = 0

    for rel in dateien(wurzel):
        if AUSNAHME_DATEIEN.get(rel, "").strip():
            continue
        pfad = os.path.join(wurzel, rel)
        try:
            with open(pfad, encoding="utf-8") as fh:
                t = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        gesehen_dateien += 1

        roh = []
        if sperre:
            treffer, gesehen = sperrlistenregel(t, sperre)
            roh.extend(treffer)
            gesehen_kandidaten += gesehen
        else:
            gesehen_kandidaten += len(t) // 8   # damit die Schwelle stimmt
        roh.extend(formregeln(t))

        for art, wert in roh:
            if wert in AUSNAHMEN and AUSNAHMEN[wert].strip():
                continue
            funde.append((rel, art, wert))

    return funde, gesehen_dateien, gesehen_kandidaten, bool(sperre)


def main():
    wurzel = sys.argv[1] if len(sys.argv) > 1 else STANDARD_WURZEL
    funde, n_dateien, n_kandidaten, mit_sperrliste = pruefen(wurzel)

    leere_gruende = [w for w, g in AUSNAHMEN.items() if not g.strip()]
    if leere_gruende:
        print("Ausnahme ohne Grund: " + ", ".join(leere_gruende))
        print("Eine Ausnahme ohne Begruendung ist keine Ausnahme, sondern ein Loch.")
        return 1

    if n_dateien < MINDESTENS_DATEIEN or n_kandidaten < MINDESTENS_KANDIDATEN:
        print(f"Die Pruefung hat nur {n_dateien} Dateien mit {n_kandidaten} "
              f"Kandidaten gesehen (erwartet mindestens {MINDESTENS_DATEIEN} / "
              f"{MINDESTENS_KANDIDATEN}). Sie hat nicht wirklich nachgesehen.")
        return 1

    if not mit_sperrliste:
        print("⚠️  Ohne Sperrliste - es laufen nur die Formregeln.")
        print(f"    {os.path.basename(sperrliste_pfad())} fehlt.")

    if funde:
        print()
        print(f"PERSONENBEZOGENE ANGABEN ({len(funde)}):")
        for rel, art, wert in sorted(set(funde)):
            print(f"   {rel:<58} {art}")
            print(f"      {wert}")
        print()
        print("Nichts davon darf veroeffentlicht werden. Entweder ersetzen, "
              "oder mit Begruendung in AUSNAHMEN eintragen.")
        return 1

    woher = ", mit Sperrliste" if mit_sperrliste else ", ohne Sperrliste"
    print(f"Personendaten: {n_dateien} Dateien, {n_kandidaten} Kandidaten "
          f"geprueft{woher}. Nichts gefunden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
