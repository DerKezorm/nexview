"""Misst, ob die Kennungen von plex.tv zu denen der Anmeldung passen.

⚠️ **Warum es dieses Werkzeug gibt, bevor es das Feature gibt.** Ein Import
bestehender Nutzer legt Konto **und** Verknuepfung in einem Zug an. Die
Verknuepfung traegt eine Konto-Kennung, und beim spaeteren Anmelden sucht
``mediaserver_accounts.find_linked`` genau danach. Passen die beiden Kennungen
nicht zusammen, entstehen Konten, in die niemand hineinkommt.

Bei Jellyfin und Emby ist das geklaert: Die Anmeldung baut ihre Kennung aus
``Id``, und ``GET /Users`` liefert dasselbe ``Id``. Bei Plex ist es offen, und
zwar mit einem konkreten Verdacht: Die Liste, die Nexview heute kennt, kommt
vom **Server** (``/accounts``), und dessen Nummern sind nach Auskunft des
Adapters nicht die von plex.tv - der Eigentuemer steht dort auf der 1.

Dieses Werkzeug fragt deshalb beide Quellen und legt sie nebeneinander. Es
aendert nichts. Es schreibt nichts. Es liest den hinterlegten Zugang aus dem
Datenverzeichnis und stellt drei Fragen an plex.tv.

**Aufruf** (dort, wo Nexview mit Plex verbunden ist):

    cd backend
    python tools/plex_kennungen_pruefen.py

Wer ein anderes Datenverzeichnis benutzt, setzt ``NEXVIEW_DATA_DIR`` davor.

⚠️ **Was ausgegeben wird und was nicht.** Kennungen ja, Namen und Mailadressen
nein. Von einem Namen steht nur der erste Buchstabe und die Laenge da - genug,
um zwei Zeilen auseinanderzuhalten, zu wenig, um jemanden zu benennen. Die
Ausgabe soll sich gefahrlos in ein Gespraech kopieren lassen.
"""

from __future__ import annotations

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import crypto  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import MediaServerConnection, UserMediaServerAccount  # noqa: E402
from app.services.settings_service import load_settings  # noqa: E402

PLEX_TV = "https://plex.tv"


def verdeckt(name: str | None) -> str:
    """Ein Name, an dem man Zeilen unterscheiden kann, ohne ihn zu lesen."""
    text = (name or "").strip()
    if not text:
        return "(ohne Namen)"
    return f"{text[0]}… ({len(text)} Zeichen)"


def kopf(client_identifier: str, token: str) -> dict[str, str]:
    return {
        "X-Plex-Token": token,
        "X-Plex-Client-Identifier": client_identifier,
        "X-Plex-Product": "Nexview",
        "Accept": "application/xml",
    }


async def hole(pfad: str, kopfzeilen: dict[str, str], als_json: bool) -> object:
    async with httpx.AsyncClient(timeout=20.0) as client:
        zeilen = dict(kopfzeilen)
        if als_json:
            zeilen["Accept"] = "application/json"
        antwort = await client.get(f"{PLEX_TV}{pfad}", headers=zeilen)
        antwort.raise_for_status()
        return antwort.json() if als_json else antwort.text


async def main() -> int:
    with SessionLocal() as db:
        einstellungen = load_settings(db)
        kennung = einstellungen.mediaserver_client_identifier
        verbindung = (
            db.query(MediaServerConnection)
            .filter(MediaServerConnection.provider == "plex")
            .first()
        )
        if verbindung is None:
            print("Keine Plex-Verbindung hinterlegt. Nichts zu messen.")
            return 2
        token = crypto.decrypt(verbindung.token) if verbindung.token else ""
        if not token:
            print("Die Plex-Verbindung hat keinen Zugang hinterlegt.")
            return 2

        maschine = verbindung.machine_id
        verknuepft = {
            zeile.account_id: zeile
            for zeile in db.query(UserMediaServerAccount)
            .filter(UserMediaServerAccount.provider == "plex")
            .all()
        }

    kopfzeilen = kopf(kennung, token)

    print(f"Maschinenkennung des Servers : {maschine}")
    print(f"In Nexview verknüpfte Plex-Kennungen: {sorted(verknuepft)}")
    print()

    # 1. Der Eigentümer. Er steht in seiner eigenen Freundesliste nicht drin.
    try:
        konto = await hole("/users/account.json", kopfzeilen, als_json=True)
        eigner = (konto or {}).get("user", {})
        print("EIGENTÜMER (/users/account.json)")
        print(f"  Kennung : {eigner.get('id')}")
        print(f"  Name    : {verdeckt(eigner.get('username'))}")
        print(f"  bekannt : {'JA' if str(eigner.get('id')) in verknuepft else 'nein'}")
    except Exception as fehler:  # noqa: BLE001 - Messwerkzeug, jeder Ausfall ist ein Ergebnis
        print(f"EIGENTÜMER: Abfrage gescheitert: {type(fehler).__name__}: {fehler}")
    print()

    # 2. Die geteilten Nutzer, so wie Seerr sie holt.
    try:
        roh = await hole("/api/users", kopfzeilen, als_json=False)
        baum = ET.fromstring(roh)  # noqa: S314 - Antwort von plex.tv, kein fremder Upload
        print("GETEILTE NUTZER (/api/users)")
        gefunden = 0
        for nutzer in baum.findall("User"):
            gefunden += 1
            nummer = nutzer.get("id") or ""
            server = [s.get("machineIdentifier") for s in nutzer.findall("Server")]
            print(f"  Kennung : {nummer}")
            print(f"    Name        : {verdeckt(nutzer.get('title'))}")
            print(f"    Adresse da  : {'ja' if nutzer.get('email') else 'nein'}")
            print(f"    dieser Server: {'ja' if maschine in server else 'NEIN'}")
            print(f"    bekannt     : {'JA' if nummer in verknuepft else 'nein'}")
        if not gefunden:
            print("  (keine geteilten Nutzer)")
    except Exception as fehler:  # noqa: BLE001
        print(f"GETEILTE NUTZER: Abfrage gescheitert: {type(fehler).__name__}: {fehler}")
    print()

    # 3. Und dieselbe Frage auf dem neueren Weg, den Nexview sonst benutzt.
    try:
        freunde = await hole("/api/v2/friends", kopfzeilen, als_json=True)
        print("ZUM VERGLEICH (/api/v2/friends)")
        for eintrag in freunde or []:
            nummer = str(eintrag.get("id") or "")
            print(f"  Kennung : {nummer}  Name: {verdeckt(eintrag.get('title'))}")
    except Exception as fehler:  # noqa: BLE001
        print(f"ZUM VERGLEICH: Abfrage gescheitert: {type(fehler).__name__}: {fehler}")

    print()
    print("Was hier zu sehen sein muss, damit der Import tragfähig ist:")
    print("  1. Der Eigentümer steht bei 'bekannt' auf JA, wenn du dich in")
    print("     Nexview schon einmal mit Plex angemeldet hast.")
    print("  2. Die geteilten Nutzer tragen 'dieser Server: ja'.")
    print("  3. Die Kennungen sind lange Zahlen, nicht 1, 2, 3.")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("NEXVIEW_DISABLE_POLLER", "true")
    raise SystemExit(asyncio.run(main()))
