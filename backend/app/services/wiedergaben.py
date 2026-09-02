"""Was gerade laeuft - ueber alle verbundenen Medienserver.

⚠️ **Der einzige Teil der Auswertung, der live fragt.** Alles andere kommt aus
dem, was der Rundgang abgelegt hat; "wer schaut gerade" veraltet aber in
Sekunden und waere gemerkt wertlos. Deshalb gilt hier eine andere Regel als
sonst - und deshalb hat der Aufruf einen kurzen Timeout und faengt jeden
Fehler je Anbieter ab: Eine Anzeige "gerade laeuft nichts" ist besser als eine
Seite, die wegen eines haengenden Servers nicht laedt.

⚠️ **Nexview taucht in der Sitzungsliste seiner eigenen Server auf.** Gemessen
am 30.08.2026 an einem echten Emby: Unter den fuenf Sitzungen standen zwei mit
``Client: "Nexview"`` und eine mit ``radarr``. Herausgefiltert werden sie
schon im Adapter (nur Eintraege mit laufendem Titel zaehlen) - aber es bleibt
der Grund, warum diese Datei niemals ungefiltert weiterreicht, was ein
Anbieter herausgibt.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User, UserMediaServerAccount, WiedergabeSpitze
from .mediaserver import media_server_for_setup, verbundene_anbieter
from .mediaserver.base import MediaServerError, Umrechnung, Wiedergabe
from .settings_service import AppSettings

logger = logging.getLogger("nexview.wiedergaben")

#: Kurz: Die Frage ist "was laeuft jetzt", und eine Antwort nach zehn Sekunden
#: beantwortet sie nicht mehr.
FRIST_SEKUNDEN = 6.0


async def _von_einem(settings: AppSettings, anbieter: str) -> list[Wiedergabe]:
    try:
        server = media_server_for_setup(settings, anbieter)
    except Exception:  # noqa: BLE001 - ein Anbieter darf die anderen nicht mitnehmen
        logger.exception("Could not build client for %s", anbieter)
        return []
    try:
        return await asyncio.wait_for(
            server.laufende_wiedergaben(), timeout=FRIST_SEKUNDEN
        )
    except (TimeoutError, MediaServerError):
        # Kein Grund zum Aufregen: Der Server ist gerade nicht da, und das
        # steht ohnehin schon als Befund auf der Diensteseite.
        return []
    except Exception:  # noqa: BLE001 - siehe oben
        logger.exception("Sessions unavailable for %s", anbieter)
        return []


async def laufende(db: Session, settings: AppSettings) -> list[Wiedergabe]:
    """Alle laufenden Wiedergaben, ueber alle verbundenen Server.

    ⚠️ **Parallel, nicht nacheinander.** Bei drei verbundenen Servern waere
    die Wartezeit sonst die Summe statt des Laengsten - und einer, der gerade
    haengt, wuerde die beiden gesunden mit ausbremsen.
    """
    anbieter = verbundene_anbieter(settings)
    if not anbieter:
        return []

    ergebnisse = await asyncio.gather(
        *(_von_einem(settings, a) for a in anbieter), return_exceptions=True
    )
    gefunden: list[Wiedergabe] = []
    for ergebnis in ergebnisse:
        if isinstance(ergebnis, list):
            gefunden.extend(ergebnis)
    return gefunden


def nexview_konto(db: Session, wiedergabe: Wiedergabe) -> User | None:
    """Wem beim Anbieter diese Sitzung gehoert - falls Nexview ihn kennt.

    ⚠️ **Ueber die Konto-Nummer des Anbieters, nicht ueber den Namen.** Die
    Nummer steht so auch an der Verknuepfung; der Name muss nirgends
    uebereinstimmen. Gemessen am 30.08.2026: Dieselbe Person heisst bei
    Jellyfin "Markus" und in Nexview "admin-kezorm" - ein Namensvergleich
    haette sie nicht gefunden, und bei zwei aehnlichen Namen haette er die
    falsche gefunden. Das waere schlimmer: Dann stuende an einer Wiedergabe
    der Name von jemand anderem.

    Der Name bleibt als **Rueckfall** fuer Konten, die nie ueber den
    Medienserver angemeldet waren und deshalb keine Nummer tragen.

    ``None`` heisst nicht "unbekannte Person", sondern "kein Nexview-Konto
    dazu" - die Anzeige nimmt dann den Namen des Anbieters, und das ist die
    ehrliche Auskunft.
    """
    if wiedergabe.konto_id:
        # ⚠️ **In der Liste nachsehen, nicht in den Einzelspalten am Konto.**
        # ``User.mediaserver_provider``/``_account_id`` halten nur **eine**
        # Verknuepfung - seit dem Parallelbetrieb hat eine Person aber je
        # Anbieter eine. Der erste Anlauf fragte die Einzelspalten ab und fand
        # deshalb ausgerechnet die Person nicht, die drei Server verbunden
        # hatte: Dort stand "emby", und die Jellyfin-Sitzung lief ins Leere.
        treffer = db.scalar(
            select(User)
            .join(UserMediaServerAccount)
            .where(
                UserMediaServerAccount.provider == wiedergabe.provider,
                UserMediaServerAccount.account_id == wiedergabe.konto_id,
            )
        )
        if treffer is not None:
            return treffer

    if not wiedergabe.konto:
        return None
    name = wiedergabe.konto.casefold()
    for konto in db.scalars(select(User)):
        if (konto.display_name or "").casefold() == name:
            return konto
        if konto.username.casefold() == name:
            return konto
    return None


# ---------------------------------------------------------------------------
# Der Verlauf
# ---------------------------------------------------------------------------

#: So lange bleibt der Verlauf stehen. Zwei Monate reichen fuer die Frage
#: "reicht meine Anlage" - laenger zurueck liegt eine andere Bibliothek, ein
#: anderer Haushalt und oft andere Hardware.
AUFBEWAHREN_TAGE = 60


def _abschnitt(zeitpunkt: datetime) -> str:
    """Der Beginn der Viertelstunde, in der dieser Zeitpunkt liegt."""
    viertel = (zeitpunkt.minute // 15) * 15
    return zeitpunkt.replace(minute=viertel, second=0, microsecond=0).strftime(
        "%Y-%m-%d %H:%M"
    )


def spitze_merken(db: Session, gefunden: list[Wiedergabe]) -> None:
    """Die laufenden Wiedergaben in den Verlauf eintragen.

    ⚠️ **Es wird die Spitze festgehalten, nicht der letzte Stand.** Innerhalb
    einer Viertelstunde laeuft der Rundgang sieben- oder achtmal; die letzte
    Messung zu speichern hiesse, dass eine Spitze verschwindet, sobald jemand
    zwei Minuten spaeter aufhoert. Gefragt ist aber genau der schlimmste
    Moment - danach bemisst sich, ob die Anlage reicht.

    ⚠️ **Eine leere Messung wird trotzdem geschrieben.** "Um drei Uhr nachts
    lief nichts" ist eine Aussage; eine Luecke in der Kurve sieht dagegen aus
    wie ein Ausfall der Messung.
    """
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    abschnitt = _abschnitt(jetzt)

    gleichzeitig = len(gefunden)
    bild = sum(1 for w in gefunden if w.umrechnung is Umrechnung.bild)
    bandbreite = sum(w.bandbreite or 0 for w in gefunden)

    zeile = db.scalar(
        select(WiedergabeSpitze).where(WiedergabeSpitze.abschnitt == abschnitt)
    )
    if zeile is None:
        # ⚠️ Die Nullen ausdruecklich setzen. ``default=0`` an der Spalte greift
        # erst beim Schreiben; bis dahin stehen die Felder auf ``None``, und
        # ``max(None, 3)`` scheitert. Aufgefallen im Test, nicht im Betrieb -
        # dort waere es beim ersten Abtasten nach dem Start passiert.
        zeile = WiedergabeSpitze(
            abschnitt=abschnitt,
            gleichzeitig=0,
            bild_umrechnungen=0,
            bandbreite_kbit=0,
        )
        db.add(zeile)

    zeile.gleichzeitig = max(zeile.gleichzeitig or 0, gleichzeitig)
    zeile.bild_umrechnungen = max(zeile.bild_umrechnungen or 0, bild)
    zeile.bandbreite_kbit = max(zeile.bandbreite_kbit or 0, bandbreite)
    zeile.gemessen_am = jetzt
    db.commit()


def verlauf_aufraeumen(db: Session) -> int:
    """Alles Aeltere wegwerfen. Gibt zurueck, wie viele Zeilen gingen."""
    grenze = (
        datetime.now(UTC).replace(tzinfo=None)
        - timedelta(days=AUFBEWAHREN_TAGE)
    ).strftime("%Y-%m-%d %H:%M")
    alt = list(
        db.scalars(select(WiedergabeSpitze).where(WiedergabeSpitze.abschnitt < grenze))
    )
    for zeile in alt:
        db.delete(zeile)
    if alt:
        db.commit()
    return len(alt)
