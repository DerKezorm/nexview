"""Zahlen fuer „Statistik & Analyse" - alles, was nicht die Anfragen betrifft.

⚠️ **Warum ein Aufruf und nicht fuenf.** Die Seite hat fuenf Reiter, und beim
Wechseln soll nichts nachladen: Wer zwischen „Dienste" und „Betrieb" hin und
her springt, um zwei Zahlen zu vergleichen, wartet sonst jedes Mal. Die
Antwort ist klein - es sind Zahlen, keine Listen.

⚠️ **Hier wird nur gelesen, nie gemessen.** Alles kommt aus dem, was der
Rundgang stuendlich abgelegt hat (``instanz_stand``, ``abgleich``,
``speicher_verlauf``). Dieselbe Regel wie beim Befund-Register, und aus
demselben Grund: Ein Seitenaufruf darf keine zwanzig Radarr-Anfragen ausloesen.

⚠️ **Durchgehend Administratoren** - wie das Dashboard. Auf dieser Seite stehen
Instanz-Zustand, Plattenfuellstand und Sicherungen; das sind Betriebsdaten.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from ..deps import AdminUser, DbSession
from ..models import (
    ArrWebhook,
    MediaServerLibraryItem,
    Notification,
    Role,
    SpeicherVerlauf,
    StorageEntry,
    StorageState,
    User,
    UserWatched,
    WiedergabeSpitze,
)
from ..services import abgleich as abgleich_dienst
from ..services import beschaffung, instanz_stand, logs, mail_outbox, sicherung
from ..services import server_vergleich as vergleich_dienst
from ..services import updates as updates_dienst
from ..services import wiedergaben as wiedergaben_dienst
from ..services.beschaffung import get_beschaffung, normalize_title
from ..services.mediaserver import MediaServerError, media_server_for_setup
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/admin/analyse", tags=["admin"])

logger = logging.getLogger("nexview.mediaserver")


class GesundheitsMeldung(BaseModel):
    typ: str
    #: Wortlaut von Radarr/Sonarr - bleibt englisch, es ist ihre Aussage.
    #: Leer, wenn ``code`` steht: dann uebersetzt die Oberflaeche.
    text: str
    code: str | None = None
    params: dict[str, Any] = {}


class InstanzZeile(BaseModel):
    """Eine Radarr-/Sonarr-Instanz, wie sie auf dem Dienste-Reiter steht."""

    kennung: str
    name: str
    media_type: str
    tier: str
    erreichbar: bool
    #: Seit wann in diesem Zustand - **nicht** wann zuletzt nachgesehen wurde.
    erreichbar_seit: datetime | None
    gemessen_am: datetime | None
    version: str
    neuere_version: str | None
    warteschlange: int | None
    warteschlange_haengt: int | None
    #: Ueberwacht, aber nicht da. Zahl und Einheit ("titel" oder "folgen") -
    #: die beiden werden bewusst nicht addiert.
    luecken: int | None
    luecken_einheit: str | None
    meldungen: list[GesundheitsMeldung]
    rueckkanal_aktiv: bool
    rueckkanal_fehler: str


class TraegerZeile(BaseModel):
    gesamt_bytes: int
    frei_bytes: int
    belegt_anteil: float
    ordner: list[str]


class BibliothekZahlen(BaseModel):
    """Was in der Bibliothek liegt - aus Nexviews eigener Buchhaltung."""

    posten: int
    medien_bytes: int
    hausbestand_bytes: int
    zugerechnet_bytes: int
    #: Belastet, aber von Radarr/Sonarr nicht mehr gefuehrt.
    geisterposten: int
    geisterposten_bytes: int


class AbgleichZahlen(BaseModel):
    moeglich: bool
    arr_ohne_server: int
    server_ohne_arr: int
    nicht_erkannt: int
    doppelt: int
    jahr_widerspruch: int
    anbieter_luecke: int
    je_anbieter: dict[str, int]
    beispiele: dict[str, list[str]]


class BetriebZahlen(BaseModel):
    sicherungen: int
    sicherung_letzte: str | None
    sicherung_takt: str
    mail_offen: int
    mail_aufgegeben: int
    protokoll_fehler_24h: int
    protokoll_stufe: str
    version: str
    neueste_version: str | None


class AnalyseStand(BaseModel):
    instanzen: list[InstanzZeile]
    traeger: list[TraegerZeile]
    verlauf_tage: int
    bibliothek: BibliothekZahlen
    abgleich: AbgleichZahlen
    betrieb: BetriebZahlen


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _instanzen(db, settings) -> list[InstanzZeile]:
    # Alle drei Tabellen je einmal, nicht je Instanz - dasselbe Muster wie im
    # Vorrat von ``befunde.sammeln``. Wer je Instanz fragt, stellt bei drei
    # Instanzen sechs Abfragen fuer zwei Tabellen mit je drei Zeilen.
    staende = instanz_stand.alle(db)
    gesundheiten = beschaffung.gesundheit_je_instanz(db)
    webhooks = {zeile.kennung: zeile for zeile in db.scalars(select(ArrWebhook))}
    haenger = beschaffung.haenger_je_instanz(db)
    zeilen: list[InstanzZeile] = []
    for instanz in get_beschaffung(settings).instanzen():
        stand = staende.get(instanz.kennung)
        messwerte = (stand.messwerte or {}) if stand else {}
        warteschlange = messwerte.get("warteschlange") or {}
        luecken = messwerte.get("luecken") or {}
        neuer = messwerte.get("aktualisierung") or {}
        gesundheit = gesundheiten.get(instanz.kennung)
        webhook = webhooks.get(instanz.kennung)
        zeilen.append(
            InstanzZeile(
                kennung=instanz.kennung,
                name=instanz.name,
                media_type=instanz.media_type,
                tier=instanz.tier,
                erreichbar=stand.erreichbar if stand else True,
                erreichbar_seit=stand.erreichbar_seit if stand else None,
                gemessen_am=stand.gemessen_am if stand else None,
                version=stand.version if stand else "",
                neuere_version=(neuer.get("version") if isinstance(neuer, dict) else None),
                warteschlange=warteschlange.get("gesamt"),
                # Aus den haengenden Downloads, nicht aus der stuendlichen
                # Messung: Dort zaehlte bis zum 12.09.2026 jede Warnung mit.
                warteschlange_haengt=haenger.get(instanz.kennung, 0),
                luecken=luecken.get("fehlend"),
                luecken_einheit=luecken.get("einheit"),
                meldungen=[
                    GesundheitsMeldung(**beschaffung.gesundheit_nach_aussen(p))
                    for p in (gesundheit.stand if gesundheit else None) or []
                ],
                rueckkanal_aktiv=bool(webhook.aktiv) if webhook else False,
                rueckkanal_fehler=(webhook.fehler if webhook else "") or "",
            )
        )
    return zeilen


def _traeger(db) -> list[TraegerZeile]:
    """Die Datentraeger aus der zuletzt gemessenen Instanz-Zeile.

    Aus **einer** Zeile: Der Wert ist haus-weit derselbe, und alle Instanzen
    tragen dieselbe Liste. Sie zu summieren hiesse, jede Platte so oft zu
    zaehlen, wie es Instanzen gibt.
    """
    for stand in instanz_stand.alle(db).values():
        gefunden = (stand.messwerte or {}).get("traeger")
        if not isinstance(gefunden, list) or not gefunden:
            continue
        return [
            TraegerZeile(
                gesamt_bytes=int(t.get("gesamt") or 0),
                frei_bytes=int(t.get("frei") or 0),
                belegt_anteil=float(t.get("belegt_anteil") or 0),
                ordner=[str(o) for o in (t.get("ordner") or [])],
            )
            for t in gefunden
            if isinstance(t, dict)
        ]
    return []


def _bibliothek(db) -> BibliothekZahlen:
    posten = db.scalar(select(func.count(StorageEntry.id))) or 0
    gesamt = db.scalar(select(func.sum(StorageEntry.size_bytes))) or 0
    haus = (
        db.scalar(
            select(func.sum(StorageEntry.size_bytes)).where(
                StorageEntry.state == StorageState.house
            )
        )
        or 0
    )
    geister = db.execute(
        select(func.count(StorageEntry.id), func.sum(StorageEntry.size_bytes)).where(
            StorageEntry.arr_managed.is_(False),
            StorageEntry.state == StorageState.owned,
        )
    ).one()
    return BibliothekZahlen(
        posten=posten,
        medien_bytes=int(gesamt),
        hausbestand_bytes=int(haus),
        zugerechnet_bytes=int(gesamt) - int(haus),
        geisterposten=geister[0] or 0,
        geisterposten_bytes=int(geister[1] or 0),
    )


def _betrieb(db, settings) -> BetriebZahlen:
    try:
        gesichert = sicherung.liste()
    except OSError:
        gesichert = []
    automatisch = [e for e in gesichert if e.art == sicherung.AUTOMATISCH]

    grenze = _jetzt() - timedelta(days=7)
    offen = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.mail_pending.is_(True)
            )
        )
        or 0
    )
    aufgegeben = (
        db.scalar(
            select(func.count(Notification.id)).where(
                Notification.mail_pending.is_(False),
                Notification.mail_sent_at.is_(None),
                Notification.mail_attempts >= mail_outbox.MAX_ATTEMPTS,
                Notification.created_at >= grenze,
            )
        )
        or 0
    )

    seit = (_jetzt() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        fehler = sum(1 for z in logs.read(limit=2000, level="ERROR") if z.time >= seit)
        stufe = logs.state().mode
    except OSError:
        fehler, stufe = 0, logs.DEFAULT_MODE

    stand = updates_dienst.gemerkt()
    from .. import __version__

    return BetriebZahlen(
        sicherungen=len(gesichert),
        sicherung_letzte=automatisch[0].erstellt if automatisch else None,
        sicherung_takt=settings.backup_schedule,
        mail_offen=offen,
        mail_aufgegeben=aufgegeben,
        protokoll_fehler_24h=fehler,
        protokoll_stufe=stufe,
        version=__version__,
        neueste_version=(
            stand.latest if stand is not None and stand.update_available else None
        ),
    )


class MonatsPunkt(BaseModel):
    monat: str
    anzahl: int


class BestandsPunkt(BaseModel):
    monat: str
    #: Aufsummiert bis zu diesem Monat - der Bestand, nicht der Zuwachs.
    posten: int
    bytes: int


class SeherZeile(BaseModel):
    user_id: int | None
    name: str
    avatar_url: str | None
    anzahl: int
    zuletzt: datetime | None


class GesehenerTitel(BaseModel):
    """Ein Titel und wie viele Leute ihn gesehen haben.

    ⚠️ **``anzahl`` sind Personen, keine Abspielvorgaenge.** Nexview merkt sich
    je Person einen Gesehen-Marker, nicht wie oft jemand etwas geschaut hat -
    das ist alles, was der Bibliotheks-Abgleich hergibt. In einem Haushalt mit
    zwei Konten ist die groesstmoegliche Zahl also zwei. Echte Abspielzaehler
    braeuchten den laufenden Wiedergabe-Verlauf der Anbieter.
    """

    tmdb_id: int
    media_type: str
    titel: str
    anzahl: int


class SpitzenTag(BaseModel):
    """Die Spitze eines Tages - der schlimmste Moment, nicht der Schnitt."""

    tag: str
    gleichzeitig: int
    bild_umrechnungen: int


class WiedergabeStand(BaseModel):
    """Was auf den Medienservern tatsaechlich angesehen wurde.

    ⚠️ **Mit Namen.** Bis 0.24 sah auch der Administrator nur seine eigenen
    Marker - eine bewusste Entscheidung aus Datensparsamkeit. Der Betreiber hat
    sie am 30.08.2026 ausdruecklich aufgehoben: Ein Werkzeug, das beim
    Verwalten helfen soll, muss sagen duerfen, wer zieht und wer nicht.
    Dokumentiert, damit es niemand fuer ein Versehen haelt und zurueckbaut.
    """

    #: Wiedergaben je Monat, aeltester Punkt zuerst.
    monate: list[MonatsPunkt]
    personen: list[SeherZeile]
    #: Leer, solange kein Titel von mehr als einer Person gesehen wurde -
    #: eine Bestenliste aus lauter Einsen ist keine Bestenliste.
    beliebteste: list[GesehenerTitel]
    #: Wie die Bibliothek gewachsen ist - aufsummiert.
    bestand: list[BestandsPunkt]
    #: Wie viele Titel ueberhaupt schon jemand angesehen hat.
    angesehen: int
    bestand_gesamt: int
    #: Auf wie vielen Konten ueberhaupt Sehdaten liegen. Ohne diese Zahl liest
    #: sich "niemand schaut Serien" als Tatsache, obwohl es "kein verknuepftes
    #: Konto meldet Serien" heisst.
    konten_mit_daten: int
    #: Die Tagesspitzen der letzten Wochen. Leer heisst: noch nicht lange
    #: genug gemessen - der Abtaster laeuft erst seit diesem Update.
    spitzen: list[SpitzenTag]
    #: Die hoechste je gemessene Gleichzeitigkeit. ``0``, solange nichts lief.
    spitze_gesamt: int


#: So weit reichen die Kurven zurueck.
MONATE = 18

#: So viele Titel stehen in der Bestenliste. Mehr liest niemand.
BESTENLISTE = 10


def _monatsreihe(anzahl: int) -> list[str]:
    heute = _jetzt().replace(day=1)
    monate: list[str] = []
    zeiger = heute
    for _ in range(anzahl):
        monate.append(zeiger.strftime("%Y-%m"))
        zeiger = (zeiger - timedelta(days=1)).replace(day=1)
    return list(reversed(monate))


class LaufendeZeile(BaseModel):
    """Eine Wiedergabe, die gerade laeuft."""

    provider: str
    konto: str
    #: Das Nexview-Konto dahinter, falls der Name zugeordnet werden konnte.
    #: ``None`` heisst nicht "unbekannte Person", sondern "kein Nexview-Konto
    #: mit diesem Namen" - die Anzeige nimmt dann den Anbieter-Namen.
    user_id: int | None
    avatar_url: str | None
    titel: str
    serie: str
    media_type: str
    fortschritt: float | None
    geraet: str
    anwendung: str
    pausiert: bool
    #: "direkt" | "ton" | "bild" - siehe ``mediaserver.base.Umrechnung``.
    umrechnung: str
    grund: str
    beschleunigung: str
    bandbreite: int | None


class LaufendStand(BaseModel):
    wiedergaben: list[LaufendeZeile]
    #: Wie viele davon das Bild neu berechnen - die Zahl, die CPU kostet.
    bild_umrechnungen: int


@router.get("/laufend", response_model=LaufendStand)
async def laufend(admin: AdminUser, db: DbSession) -> LaufendStand:
    """Wer schaut gerade was.

    ⚠️ **Der einzige Teil dieser Seite, der live fragt.** "Gerade" veraltet in
    Sekunden; gemerkt waere es wertlos. Deshalb kurzer Timeout je Anbieter und
    parallel - ein haengender Server darf die gesunden nicht ausbremsen.
    """
    settings = load_settings(db)
    gefunden = await wiedergaben_dienst.laufende(db, settings)

    zeilen: list[LaufendeZeile] = []
    for eintrag in gefunden:
        konto = wiedergaben_dienst.nexview_konto(db, eintrag)
        zeilen.append(
            LaufendeZeile(
                provider=eintrag.provider,
                konto=(konto.display_name or konto.username) if konto else eintrag.konto,
                user_id=konto.id if konto else None,
                avatar_url=konto.avatar_url if konto else None,
                titel=eintrag.titel,
                serie=eintrag.serie,
                media_type=eintrag.media_type,
                fortschritt=eintrag.fortschritt,
                geraet=eintrag.geraet,
                anwendung=eintrag.anwendung,
                pausiert=eintrag.pausiert,
                umrechnung=eintrag.umrechnung.value,
                grund=eintrag.grund,
                beschleunigung=eintrag.beschleunigung,
                bandbreite=eintrag.bandbreite,
            )
        )

    return LaufendStand(
        wiedergaben=zeilen,
        bild_umrechnungen=sum(1 for z in zeilen if z.umrechnung == "bild"),
    )


@router.get("/wiedergabe", response_model=WiedergabeStand)
def wiedergabe(admin: AdminUser, db: DbSession) -> WiedergabeStand:
    """Wer hat was gesehen, und wie ist die Bibliothek gewachsen.

    ⚠️ **Alles aus der eigenen Datenbank, kein Anbieter wird gefragt.** Die
    Sehdaten sammelt der stuendliche Abgleich ohnehin ein; hier wird nur
    gerechnet.
    """
    monate = _monatsreihe(MONATE)
    leer = dict.fromkeys(monate, 0)

    # --- Wiedergaben je Monat ---------------------------------------------
    je_monat = dict(leer)
    # ⚠️ Nach dem **Ausdruck** gruppieren, nicht nach der Spaltennummer:
    # SQLAlchemy kennt das ``GROUP BY 1`` aus rohem SQL nicht und wirft dort
    # einen ArgumentError - der erst beim Aufruf auffiel, nicht beim Import.
    monatsspalte = func.strftime("%Y-%m", UserWatched.watched_at)
    for monat, anzahl in db.execute(
        select(monatsspalte, func.count(UserWatched.id))
        .where(UserWatched.watched_at.is_not(None))
        .group_by(monatsspalte)
    ):
        if monat in je_monat:
            je_monat[monat] = anzahl

    # --- Wer schaut --------------------------------------------------------
    #
    # ⚠️ Kinderkonten sind Unterprofile ihrer Eltern und haben am Medienserver
    # kein Gegenstueck - sie stuenden hier dauerhaft mit einer Null.
    personen: list[SeherZeile] = []
    for user, anzahl, zuletzt in db.execute(
        select(User, func.count(UserWatched.id), func.max(UserWatched.watched_at))
        .join(UserWatched, UserWatched.user_id == User.id)
        .where(User.role != Role.child)
        .group_by(User.id)
        .order_by(func.count(UserWatched.id).desc())
    ):
        personen.append(
            SeherZeile(
                user_id=user.id,
                name=user.display_name or user.username,
                avatar_url=user.avatar_url,
                anzahl=anzahl,
                zuletzt=zuletzt,
            )
        )

    # --- Meistgesehene Titel ----------------------------------------------
    #
    # ⚠️ Der Titel steht nicht an der Wiedergabe, nur die Nummer. Geholt wird
    # er aus dem Bibliotheks-Abbild; findet sich keiner, faellt der Eintrag
    # heraus statt als "Titel 603" dazustehen.
    namen: dict[tuple[str, int], str] = {}
    for art, nummer, titel in db.execute(
        select(
            MediaServerLibraryItem.media_type,
            MediaServerLibraryItem.tmdb_id,
            MediaServerLibraryItem.title,
        ).where(MediaServerLibraryItem.tmdb_id.is_not(None))
    ):
        namen.setdefault((art.value, nummer), titel or "")

    beliebteste: list[GesehenerTitel] = []
    for art, nummer, anzahl in db.execute(
        select(
            UserWatched.media_type, UserWatched.tmdb_id, func.count(UserWatched.id)
        )
        .group_by(UserWatched.media_type, UserWatched.tmdb_id)
        .order_by(func.count(UserWatched.id).desc())
        .limit(BESTENLISTE * 3)
    ):
        titel = namen.get((art.value, nummer))
        if not titel:
            continue
        beliebteste.append(
            GesehenerTitel(
                tmdb_id=nummer, media_type=art.value, titel=titel, anzahl=anzahl
            )
        )
        if len(beliebteste) >= BESTENLISTE:
            break

    # ⚠️ **Eine Liste aus lauter Einsen ist keine Bestenliste.** Solange
    # niemand denselben Titel gesehen hat wie jemand anderes, sagt die
    # Reihenfolge nur, in welcher Reihenfolge die Datenbank antwortet - und
    # das saehe nach einer Aussage aus, wo keine ist. Wird erst interessant,
    # wenn mehrere Konten Sehdaten liefern.
    if not any(t.anzahl > 1 for t in beliebteste):
        beliebteste = []

    # --- Wie die Bibliothek gewachsen ist ----------------------------------
    #
    # ⚠️ ``added_at`` ist das Datum der **Datei**, nicht der Zeitpunkt, an dem
    # Nexview sie kennengelernt hat. Sonst waere jede gewachsene Anlage am Tag
    # der Einrichtung schlagartig entstanden.
    zuwachs: dict[str, tuple[int, int]] = {}
    zuwachsspalte = func.strftime("%Y-%m", StorageEntry.added_at)
    for monat, anzahl, bytes_ in db.execute(
        select(
            zuwachsspalte,
            func.count(StorageEntry.id),
            func.sum(StorageEntry.size_bytes),
        )
        .where(StorageEntry.added_at.is_not(None))
        .group_by(zuwachsspalte)
    ):
        zuwachs[monat] = (anzahl or 0, int(bytes_ or 0))

    bestand: list[BestandsPunkt] = []
    posten_summe = 0
    bytes_summe = 0
    # Alles, was **vor** dem Fenster liegt, ist der Startwert - sonst begaenne
    # die Kurve bei null und behauptete, die Bibliothek sei neu.
    for monat, (anzahl, bytes_) in sorted(zuwachs.items()):
        if monat < monate[0]:
            posten_summe += anzahl
            bytes_summe += bytes_
    for monat in monate:
        anzahl, bytes_ = zuwachs.get(monat, (0, 0))
        posten_summe += anzahl
        bytes_summe += bytes_
        bestand.append(
            BestandsPunkt(monat=monat, posten=posten_summe, bytes=bytes_summe)
        )

    angesehen = (
        db.scalar(
            select(func.count(func.distinct(UserWatched.tmdb_id)))
        )
        or 0
    )
    gesamt = (
        db.scalar(
            select(func.count(func.distinct(MediaServerLibraryItem.tmdb_id))).where(
                MediaServerLibraryItem.tmdb_id.is_not(None)
            )
        )
        or 0
    )

    # --- Spitzenlast ------------------------------------------------------
    #
    # ⚠️ **Je Tag die hoechste Viertelstunde, nicht die Summe.** Vier
    # Wiedergaben um acht und vier um zehn sind nicht acht gleichzeitige - und
    # die Frage lautet, wie viele die Anlage auf einmal aushalten muss.
    je_tag: dict[str, tuple[int, int]] = {}
    for zeile in db.scalars(
        select(WiedergabeSpitze).order_by(WiedergabeSpitze.abschnitt)
    ):
        tag = zeile.abschnitt[:10]
        bisher = je_tag.get(tag, (0, 0))
        je_tag[tag] = (
            max(bisher[0], zeile.gleichzeitig),
            max(bisher[1], zeile.bild_umrechnungen),
        )
    spitzen = [
        SpitzenTag(tag=tag, gleichzeitig=werte[0], bild_umrechnungen=werte[1])
        for tag, werte in sorted(je_tag.items())
    ]

    return WiedergabeStand(
        spitzen=spitzen,
        spitze_gesamt=max((s.gleichzeitig for s in spitzen), default=0),
        monate=[MonatsPunkt(monat=m, anzahl=je_monat[m]) for m in monate],
        personen=personen,
        beliebteste=beliebteste,
        bestand=bestand,
        angesehen=angesehen,
        bestand_gesamt=gesamt,
        konten_mit_daten=len(personen),
    )


@router.get("", response_model=AnalyseStand)
def analyse(admin: AdminUser, db: DbSession) -> AnalyseStand:
    """Alles fuer die Reiter Dienste, Bibliothek, Abgleich und Betrieb."""
    settings = load_settings(db)
    roh = abgleich_dienst.lesen(db)
    return AnalyseStand(
        instanzen=_instanzen(db, settings),
        traeger=_traeger(db),
        verlauf_tage=db.scalar(select(func.count(SpeicherVerlauf.id))) or 0,
        bibliothek=_bibliothek(db),
        abgleich=AbgleichZahlen(
            moeglich=roh.moeglich,
            arr_ohne_server=roh.arr_ohne_server,
            server_ohne_arr=roh.server_ohne_arr,
            nicht_erkannt=roh.nicht_erkannt,
            doppelt=roh.doppelt,
            jahr_widerspruch=roh.jahr_widerspruch,
            anbieter_luecke=roh.anbieter_luecke,
            je_anbieter=roh.je_anbieter,
            beispiele=roh.beispiele,
        ),
        betrieb=_betrieb(db, settings),
    )


# ---------------------------------------------------------------------------
# Server-Vergleich: welcher Titel liegt wo
# ---------------------------------------------------------------------------


class VergleichZelle(BaseModel):
    zustand: str
    tmdb: list[int]
    tvdb: list[int]
    imdb: list[str]
    jahr: int | None
    titel: str | None
    pfade: list[str]
    schluessel: str | None


class VergleichZeile(BaseModel):
    kennung: str
    titel: str
    jahr: int | None
    art: str
    zuordnung: str
    zellen: dict[str, VergleichZelle]
    jahr_uneinig: bool
    ohne_kennung: bool


class VergleichServer(BaseModel):
    anbieter: str
    filme: int
    serien: int
    #: Titel, die mindestens ein anderer Server kennt, dieser nicht.
    fehlen: int


class ServerVergleichStand(BaseModel):
    moeglich: bool
    server: list[VergleichServer]
    #: Wie viele Titel jede Ansicht zeigt - fuer die Beschriftung der Knoepfe.
    anzahl: dict[str, int]
    zeilen: list[VergleichZeile]
    gesamt: int
    seite: int
    seiten: int


@router.get("/server-vergleich", response_model=ServerVergleichStand)
def server_vergleich(
    admin: AdminUser,
    db: DbSession,
    ansicht: Annotated[
        Literal["unterschiede", "andere_nummer", "jahr", "ohne_kennung", "nur_arr", "alle"],
        Query(),
    ] = "unterschiede",
    art: Annotated[Literal["alle", "movie", "tv"], Query()] = "alle",
    fehlt_auf: Annotated[str | None, Query(max_length=20)] = None,
    suche: Annotated[str | None, Query(max_length=200)] = None,
    seite: Annotated[int, Query(ge=1)] = 1,
    pro_seite: Annotated[int, Query(ge=10, le=200)] = 50,
) -> ServerVergleichStand:
    """Eine Zeile je Titel, eine Spalte je Server - gefiltert und seitenweise.

    ⚠️ **Gerechnet bei jedem Aufruf, nicht abgelegt.** Anders als der Abgleich
    fragt das keinen Dienst im Netz, es liest nur ``media_server_library``.
    An einer Anlage mit drei Servern und 11.000 Zeilen gemessen: 0,3 Sekunden.
    Abgelegt waere die Tabelle nach jeder neuen Einlesung veraltet.
    """
    server, alle_zeilen = vergleich_dienst.zeilen_bauen(
        db, vergleich_dienst.verbundene(load_settings(db))
    )
    if not server:
        return ServerVergleichStand(
            moeglich=False, server=[], anzahl={}, zeilen=[], gesamt=0, seite=1, seiten=1
        )
    arr = vergleich_dienst.arr_zeilen(abgleich_dienst.lesen(db), server)

    zusammenfassung = [
        VergleichServer(
            anbieter=s,
            filme=sum(
                1
                for z in alle_zeilen
                if z.art == "movie" and z.zellen[s].zustand not in vergleich_dienst.FEHLT
            ),
            serien=sum(
                1
                for z in alle_zeilen
                if z.art == "tv" and z.zellen[s].zustand not in vergleich_dienst.FEHLT
            ),
            fehlen=(
                sum(1 for z in alle_zeilen if z.zellen[s].zustand in vergleich_dienst.FEHLT)
                if len(server) > 1
                else 0
            ),
        )
        for s in server
    ]
    anzahl = {
        a: (len(arr) if a == "nur_arr" else sum(1 for z in alle_zeilen if vergleich_dienst.passt(z, a)))
        for a in vergleich_dienst.ANSICHTEN
    }

    quelle = arr if ansicht == "nur_arr" else alle_zeilen
    gesucht = vergleich_dienst.titel_schluessel(suche or "")
    # Gesucht wird im Titel **und** im Pfad: Wer auf dem Datentraeger einen
    # Ordner vor sich hat, will ihn in der Tabelle wiederfinden.
    pfad_gesucht = (suche or "").strip().casefold()
    treffer = [
        z
        for z in quelle
        if vergleich_dienst.passt(z, ansicht)
        and (art == "alle" or z.art == art)
        and (
            not fehlt_auf
            or (fehlt_auf in z.zellen and z.zellen[fehlt_auf].zustand in vergleich_dienst.FEHLT)
        )
        and (
            not pfad_gesucht
            or (gesucht and gesucht in vergleich_dienst.titel_schluessel(z.titel))
            or any(pfad_gesucht in p.casefold() for c in z.zellen.values() for p in c.pfade)
        )
    ]
    seiten = max(1, -(-len(treffer) // pro_seite))
    seite = min(seite, seiten)
    ausschnitt = treffer[(seite - 1) * pro_seite : seite * pro_seite]
    return ServerVergleichStand(
        moeglich=True,
        server=zusammenfassung,
        anzahl=anzahl,
        zeilen=[VergleichZeile.model_validate(asdict(z)) for z in ausschnitt],
        gesamt=len(treffer),
        seite=seite,
        seiten=seiten,
    )


class TitelPfade(BaseModel):
    pfade: list[str]


@router.get("/server-vergleich/pfade", response_model=TitelPfade)
async def server_vergleich_pfade(
    admin: AdminUser,
    db: DbSession,
    anbieter: Annotated[str, Query(max_length=20)],
    schluessel: Annotated[str, Query(min_length=1, max_length=40)],
) -> TitelPfade:
    """Die Pfade eines Titels nachschlagen, den die Bibliotheksliste ohne liefert.

    Beim Aufklappen einer Zeile gefragt, nicht beim Einlesen - siehe
    ``PlexServer.titel_pfade``. Das Ergebnis wird an die Bibliothekszeile
    geschrieben, damit der zweite Blick keine Anfrage mehr kostet. Das
    naechste Einlesen ersetzt die Zeile und vergisst es wieder; dann fragt
    eben der naechste Blick.
    """
    zeilen = db.scalars(
        select(MediaServerLibraryItem).where(
            MediaServerLibraryItem.provider == anbieter,
            MediaServerLibraryItem.rating_key == schluessel,
        )
    ).all()
    if not zeilen:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "library_item_not_found",
                "message": "Diesen Titel kennt die Bibliothek des Servers nicht.",
            },
        )
    gespeichert = [p for z in zeilen for p in (z.file_paths or "").splitlines() if p.strip()]
    if gespeichert:
        return TitelPfade(pfade=list(dict.fromkeys(gespeichert)))

    try:
        server = media_server_for_setup(load_settings(db), anbieter)
        pfade = await server.titel_pfade(schluessel)
    except MediaServerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.als_meldung()) from exc
    if pfade:
        for zeile in zeilen:
            zeile.file_paths = "\n".join(pfade)[:4000]
        db.commit()
    return TitelPfade(pfade=pfade)


# ---------------------------------------------------------------------------
# Neu zuordnen: schreibt in den Medienserver, nur auf Klick
# ---------------------------------------------------------------------------


class NeuZuordnen(BaseModel):
    anbieter: str
    schluessel: str
    art: Literal["movie", "tv"]
    tmdb: int | None = None
    tvdb: int | None = None


class ZuordnungErgebnis(BaseModel):
    #: ``korrigiert``, ``zurueckgesprungen`` (der Server hat die Korrektur
    #: selbst wieder ueberschrieben) oder ``nicht_bestaetigt`` (in der
    #: Wartezeit nicht sichtbar geworden).
    ergebnis: str
    titel: str | None
    jahr: int | None


#: Wie lange nach dem Anwenden gefragt wird, ob es da ist. Emby brauchte in der
#: Messung knapp fuenf Sekunden.
NACHFRAGEN = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
#: Und wie lange danach noch einmal - bei einer Serie sprang Emby in dieser
#: Zeit von selbst zurueck.
NOCHMAL_NACH = 6.0

#: Austauschbar fuer die Tests, die nicht wirklich warten sollen.
_warten = asyncio.sleep


@router.post("/server-vergleich/zuordnen", response_model=ZuordnungErgebnis)
async def server_vergleich_zuordnen(
    payload: NeuZuordnen, admin: AdminUser, db: DbSession
) -> ZuordnungErgebnis:
    """Einen Titel auf **einem** Medienserver neu zuordnen und nachpruefen.

    ⚠️ **Das ist der einzige Weg, auf dem Nexview Metadaten in einem
    Medienserver aendert** - und er laeuft nur auf ausdruecklichen Klick eines
    Administrators. Kein Hintergrundlauf, keine Automatik.

    ⚠️ **"Angenommen" heisst nicht "erledigt".** Gemessen am 17.09.2026: Emby
    zeigte die neue Zuordnung erst nach fuenf Sekunden, und bei einer Serie
    stand sie zwei Sekunden da und war dann weg - Emby hatte sie ueber IMDb und
    TVDB selbst wieder aufgeloest. Deshalb wird nachgefragt, und zwar zweimal.
    Wer "korrigiert" meldet, ohne nachzusehen, luegt in genau diesem Fall.
    """
    if payload.tmdb is None and payload.tvdb is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "mediaserver_rematch_no_id",
                "message": "Ohne Nummer lässt sich nicht neu zuordnen.",
            },
        )
    zeilen = db.scalars(
        select(MediaServerLibraryItem).where(
            MediaServerLibraryItem.provider == payload.anbieter,
            MediaServerLibraryItem.rating_key == payload.schluessel,
        )
    ).all()
    if not zeilen:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "library_item_not_found",
                "message": "Diesen Titel kennt die Bibliothek des Servers nicht.",
            },
        )

    def passt(werk) -> bool:
        if werk is None:
            return False
        if payload.tmdb is not None and payload.tmdb not in (werk.tmdb_id, *werk.tmdb_ids):
            return False
        return not (payload.art == "tv" and payload.tvdb is not None and werk.tvdb_id != payload.tvdb)

    try:
        server = media_server_for_setup(load_settings(db), payload.anbieter)
        await server.zuordnung_anwenden(payload.schluessel, payload.art, payload.tmdb, payload.tvdb)
        logger.warning(
            "Media server %r: title %s rematched to tmdb=%s tvdb=%s by %s",
            payload.anbieter,
            payload.schluessel,
            payload.tmdb,
            payload.tvdb,
            admin.username,
        )
        werk = None
        for pause in NACHFRAGEN:
            await _warten(pause)
            werk = await server.titel_nummern(payload.schluessel)
            if passt(werk):
                break
        if not passt(werk):
            ergebnis = "nicht_bestaetigt"
        else:
            await _warten(NOCHMAL_NACH)
            werk = await server.titel_nummern(payload.schluessel)
            ergebnis = "korrigiert" if passt(werk) else "zurueckgesprungen"
    except MediaServerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.als_meldung()) from exc

    if ergebnis == "korrigiert" and werk is not None:
        # Die Tabelle soll es sofort zeigen, nicht erst nach dem naechsten
        # Einlesen. Nur die Zuordnung - Pfade und Groessen bleiben, wie sie sind.
        for zeile in zeilen:
            zeile.tmdb_id = payload.tmdb if payload.tmdb is not None else werk.tmdb_id
            zeile.tvdb_id = werk.tvdb_id
            zeile.imdb_id = werk.imdb_id
            zeile.title = werk.title[:500]
            zeile.title_key = normalize_title(werk.title)[:500]
            zeile.year = werk.year
        db.commit()
    logger.info("Media server %r: rematch of %s %s", payload.anbieter, payload.schluessel, ergebnis)
    return ZuordnungErgebnis(
        ergebnis=ergebnis,
        titel=werk.title if werk else None,
        jahr=werk.year if werk else None,
    )
