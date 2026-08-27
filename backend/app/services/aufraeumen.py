"""Was liegt herum? - die Grundlage fuer den Aufraeum-Vorschlag.

Der Abgabe-Weg steht seit 0.15: Wer merkt, dass er etwas nicht mehr braucht,
gibt es ab, und der Betreiber entscheidet. Was fehlte, war der **Anstoss** -
von selbst kommt niemand darauf nachzusehen, welcher Film seit drei Jahren
Platz belegt, den keiner mehr angefasst hat.

Dieser Dienst beantwortet genau eine Frage: *Was liegt da, das lange niemand
mehr angesehen hat, und wie viel Platz kostet es?*

⚠️ **Der Hausbestand gehoert ausdruecklich dazu - er ist sogar der Hauptfall.**
Wer Nexview auf eine bestehende Bibliothek setzt, hat zunaechst **alles** im
Hausbestand: Ein Eigentuemer entsteht nur fuer das, was danach ueber Nexview
bestellt wird. Eine Liste, die nur zugerechnete Posten zeigt, waere auf jeder
gewachsenen Anlage fast leer - und liesse genau die Filme aus, um die es geht:
die, die seit Jahren daliegen und niemanden interessieren.

**Was diese Liste nicht weiss - und offen sagt.** Sie stuetzt sich auf die
Sehdaten der Medienserver, und die gibt es nur fuer Konten, die dort verknuepft
sind. Wer ueber ein nicht verknuepftes Konto schaut, ist fuer Nexview
unsichtbar; sein Lieblingsfilm sieht dann aus wie "nie angesehen". Deshalb
liefert ``liste`` neben den Kandidaten immer mit, **auf wessen Daten sie
beruht** - eine Liste, die ihre eigene Luecke verschweigt, behauptet Dinge, die
nicht stimmen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    MediaType,
    StorageEntry,
    StorageState,
    TitleRating,
    User,
    UserMediaServerAccount,
    UserWatched,
    UserWatchedSeason,
    utcnow,
)

# Ab wann gilt etwas als "liegt herum". Ein halbes Jahr, weil eine Serie mit
# Jahresrhythmus sonst staendig in der Liste stuende.
MONATE_STANDARD = 6

# So viele Zeilen hoechstens. Die Liste soll eine Entscheidungsgrundlage sein,
# kein Datenbankauszug - wer 4000 Zeilen bekommt, raeumt gar nichts auf.
GRENZE_STANDARD = 100


@dataclass(frozen=True)
class Kandidat:
    """Ein Posten, der lange daliegt und den lange niemand angesehen hat.

    ⚠️ **Zwei Uhren, nicht eine.** Der erste Bau prueft nur "zuletzt gesehen
    ist lange her" - und liess damit jeden Film durch, den **nie** jemand
    gesehen hat, auch den von heute Nacht. Der stand dann, weil er der
    groesste war, ganz oben in der Liste der Ladenhueter.

    Ein Kandidat muss deshalb beides erfuellen: lange nicht angesehen **und**
    lange da.
    """

    posten_id: int
    media_type: MediaType
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    tier: str
    title: str
    size_bytes: int
    state: StorageState
    # Wem er zugerechnet ist - ``None`` heisst Hausbestand.
    besitzer: str | None
    # Wann zuletzt jemand hineingesehen hat. ``None`` heisst: **kein Konto,
    # das Nexview kennt, hat es je getan.** Das ist nicht dasselbe wie "nie
    # gesehen" - siehe der Hinweis im Modulkopf.
    zuletzt_gesehen: datetime | None
    gesehen_von: list[str] = field(default_factory=list)
    # Das Urteil des Haushalts ueber die Datei (1-5), falls jemand eines
    # abgegeben hat. Zwei Sterne bei einem Titel, den niemand mehr ansieht,
    # sind ein deutlicheres Zeichen als jede Zahl daneben.
    bewertung: float | None = None
    bewertungen: int = 0
    # Seit wann die Datei da liegt - aus Radarr bzw. Sonarr, nicht geraten.
    liegt_seit: datetime | None = None
    # Laeuft fuer diesen Posten schon eine Schonfrist? Dann bleibt er in der
    # Liste stehen - **sichtbar markiert**. Ihn verschwinden zu lassen waere
    # falsch: Vorgemerkt heisst noch nicht geloescht, und wer es
    # zurueckdrehen will, muss ihn wiederfinden.
    loescht_am: datetime | None = None

    @property
    def nie_gesehen(self) -> bool:
        return self.zuletzt_gesehen is None

    @property
    def gesehen_ohne_datum(self) -> bool:
        """Jemand hat es gesehen - wann, weiss Nexview nicht.

        ⚠️ **Kein Randfall, sondern ein Widerspruch in derselben Zeile.**
        Medienserver fuehren "ob" und "wann" getrennt: Plex liefert
        ``viewCount`` und ``lastViewedAt`` als zwei Felder, Jellyfin
        ``Played`` und ``LastPlayedDate``. Wurde der Verlauf gekuerzt, von
        Hand auf "gesehen" gesetzt oder eine Bibliothek zusammengefuehrt,
        ueberlebt der Zaehler und der Zeitpunkt nicht.

        Fuer die Liste heisst das: Der Filter oben prueft nur den Zeitpunkt,
        also faellt so ein Posten **nicht** heraus - er steht hier mit der
        Begruendung "hat im Zeitraum niemand angesehen", obwohl jemand es
        getan hat. Die Oberflaeche schreibt deshalb nicht "nie", sondern
        "gesehen - wann, ist unbekannt", und die Zusammenfassung zaehlt sie.
        """
        return self.zuletzt_gesehen is None and bool(self.gesehen_von)


@dataclass(frozen=True)
class Grundlage:
    """Worauf die Liste beruht - damit sie sich einordnen laesst.

    ⚠️ **Gehoert zwingend zur Antwort, nicht als Beiwerk.** Ohne diese Angabe
    liest sich "seit einem halben Jahr niemand angesehen" als Tatsache, obwohl
    es nur "keines der verknuepften Konten" heisst. Bei zwei von vier Konten
    ohne Verknuepfung ist das ein erheblicher Unterschied.
    """

    konten_gesamt: int
    konten_verknuepft: int
    #: Namen der Konten, deren Sehen Nexview nicht kennt.
    ohne_verknuepfung: list[str] = field(default_factory=list)

    @property
    def vollstaendig(self) -> bool:
        return not self.ohne_verknuepfung


@dataclass(frozen=True)
class Liste:
    kandidaten: list[Kandidat]
    #: Wie viele es insgesamt gaebe - die Liste selbst ist gedeckelt.
    gesamt_anzahl: int
    #: Wie viel Platz alle zusammen belegen, auch die abgeschnittenen.
    gesamt_bytes: int
    grundlage: Grundlage
    monate: int
    #: Posten, deren Alter Nexview (noch) nicht kennt und die deshalb
    #: uebergangen wurden. Direkt nach einem Update ist das alles, bis der
    #: naechste Abgleich die Daten aus Radarr/Sonarr nachtraegt.
    ohne_datum: int = 0
    #: Posten, die jemand **gesehen hat**, ohne dass ein Zeitpunkt dazu
    #: bekannt waere - siehe ``Kandidat.gesehen_ohne_datum``.
    #:
    #: ⚠️ **Gehoert in die Zusammenfassung ueber der Tabelle.** Diese Posten
    #: stehen in der Liste, obwohl die Begruendung "hat im Zeitraum niemand
    #: angesehen" fuer sie nicht traegt: Ohne Zeitpunkt greift der Filter
    #: nicht. Sie herauszunehmen waere schlimmer - es sind gerade die alten,
    #: dicken Posten, bei denen der Medienserver den Verlauf vergessen hat,
    #: also genau die, wegen denen es diese Ansicht gibt. Also: drin lassen,
    #: benennen, zaehlen.
    gesehen_ohne_datum: int = 0


def _ohne_zeitzone(wert: datetime) -> datetime:
    """SQLite kennt keine Zeitzonen - Vergleiche muessen naiv laufen."""
    return wert.replace(tzinfo=None) if wert.tzinfo else wert


def grundlage(db: Session) -> Grundlage:
    """Wessen Sehen kennt Nexview ueberhaupt?

    Kinderkonten zaehlen bewusst **nicht** mit: Sie sind Unterprofile ihrer
    Eltern und haben auf dem Medienserver gar kein Gegenstueck. Sie als
    "nicht verknuepft" zu melden waere ein Mangel, den niemand beheben kann.
    """
    erwachsene = list(
        db.scalars(select(User).where(User.role != "child", User.is_active.is_(True)))
    )
    verknuepft = set(
        db.scalars(select(UserMediaServerAccount.user_id).distinct())
    )
    fehlen = [u.display_name or u.username for u in erwachsene if u.id not in verknuepft]
    return Grundlage(
        konten_gesamt=len(erwachsene),
        konten_verknuepft=len(erwachsene) - len(fehlen),
        ohne_verknuepfung=sorted(fehlen),
    )


def _sehstand(db: Session) -> tuple[dict, dict]:
    """Wann wurde was zuletzt gesehen, und von wem.

    Zwei Tabellen, weil es zwei Koernungen gibt: ``user_watched`` fuehrt den
    **Titel**, ``user_watched_seasons`` die vollstaendig gesehene **Staffel**.
    Die Speicher-Posten liegen staffelweise - fuer sie ist die Staffelzeile die
    genauere Auskunft, und die Serienzeile der Rueckfall.

    ⚠️ Der Rueckfall ist keine Formalie: Die Staffeltabelle wird nur gefuellt,
    wenn der Medienserver vollstaendig gesehene Staffeln meldet. Auf einer
    gemessenen Anlage stand dort **nichts**, waehrend die Serientabelle 56
    Zeilen hatte. Ohne Rueckfall waere die Spalte bei Serien durchgehend leer -
    also genau dort, wo der meiste Platz liegt.
    """
    titel: dict[tuple[MediaType, int], tuple[datetime | None, set[str]]] = {}
    for zeile, name in db.execute(
        select(UserWatched, User.username).join(User, UserWatched.user_id == User.id)
    ):
        schluessel = (zeile.media_type, zeile.tmdb_id)
        wann, wer = titel.get(schluessel, (None, set()))
        wer.add(name)
        if zeile.watched_at and (wann is None or zeile.watched_at > wann):
            wann = zeile.watched_at
        titel[schluessel] = (wann, wer)

    staffel: dict[tuple[int, int], tuple[datetime | None, set[str]]] = {}
    for zeile, name in db.execute(
        select(UserWatchedSeason, User.username).join(
            User, UserWatchedSeason.user_id == User.id
        )
    ):
        schluessel = (zeile.tmdb_id, zeile.season)
        wann, wer = staffel.get(schluessel, (None, set()))
        wer.add(name)
        if zeile.watched_at and (wann is None or zeile.watched_at > wann):
            wann = zeile.watched_at
        staffel[schluessel] = (wann, wer)

    return titel, staffel


def _bewertungen(db: Session) -> dict[tuple[MediaType, int, int | None], tuple[float, int]]:
    """Das Urteil des Haushalts je Titel bzw. Staffel - Mittelwert und Anzahl.

    Veraltete Urteile bleiben draussen: ``outdated`` heisst, die Datei ist
    seither gewachsen, das Urteil galt also einer anderen Fassung.
    """
    gesammelt: dict[tuple[MediaType, int, int | None], list[int]] = {}
    for zeile in db.scalars(select(TitleRating).where(TitleRating.outdated.is_(False))):
        gesammelt.setdefault((zeile.media_type, zeile.tmdb_id, zeile.season), []).append(
            zeile.rating
        )
    return {k: (sum(v) / len(v), len(v)) for k, v in gesammelt.items()}


def liste(
    db: Session,
    *,
    monate: int = MONATE_STANDARD,
    nutzer: User | None = None,
    grenze: int = GRENZE_STANDARD,
    suche: str = "",
    art: MediaType | None = None,
    nur_vorgemerkt: bool = False,
) -> Liste:
    """Die Kandidaten, groesster Brocken zuerst.

    ``nutzer`` schneidet auf das zu, was diesem Konto zugerechnet ist - das
    ist die Sicht im eigenen Profil. Ohne ihn kommt alles, **einschliesslich
    Hausbestand**; das ist die Sicht des Betreibers.

    Sortiert wird nach **Groesse**, nicht nach Alter. Beim Aufraeumen
    entscheidet der Platz: Zwanzig vergessene Folgen zu je 300 MB wiegen
    weniger als eine einzige 4K-Staffel, auch wenn sie laenger daliegen.
    """
    stichtag = _ohne_zeitzone(utcnow() - timedelta(days=monate * 30))

    abfrage = select(StorageEntry)
    if nutzer is not None:
        abfrage = abfrage.where(StorageEntry.user_id == nutzer.id)
    if art is not None:
        abfrage = abfrage.where(StorageEntry.media_type == art)
    if nur_vorgemerkt:
        # ⚠️ Und dann **ohne** die beiden Uhren: Was schon vorgemerkt ist,
        # gehoert in diese Ansicht, auch wenn es inzwischen jemand angesehen
        # hat oder der gewaehlte Zeitraum nicht mehr passt. Sonst
        # verschwaende ausgerechnet der Titel aus der Liste, den man
        # aufhalten will.
        abfrage = abfrage.where(StorageEntry.delete_after.is_not(None))
    if suche.strip():
        # Ohne Ruecksicht auf Gross- und Kleinschreibung: Wer "trek" tippt,
        # meint "Star Trek". ``ilike`` kann SQLite nicht, ``lower`` schon.
        begriff = f"%{suche.strip().lower()}%"
        abfrage = abfrage.where(func.lower(StorageEntry.title).like(begriff))
    posten = list(db.scalars(abfrage))
    if not posten:
        return Liste([], 0, 0, grundlage(db), monate)

    titel_stand, staffel_stand = _sehstand(db)
    urteile = _bewertungen(db)
    namen = {
        u.id: (u.display_name or u.username)
        for u in db.scalars(select(User))
    }

    kandidaten: list[Kandidat] = []
    unbekannt = 0
    for eintrag in posten:
        if eintrag.tmdb_id is None:
            # Ohne TMDB-Nummer laesst sich kein Sehstand zuordnen. Die Zeile
            # dann als "nie gesehen" zu fuehren waere geraten, nicht gewusst.
            continue

        if eintrag.season is not None:
            wann, wer = staffel_stand.get(
                (eintrag.tmdb_id, eintrag.season),
                titel_stand.get((eintrag.media_type, eintrag.tmdb_id), (None, set())),
            )
        else:
            wann, wer = titel_stand.get((eintrag.media_type, eintrag.tmdb_id), (None, set()))

        if not nur_vorgemerkt and wann is not None and wann > stichtag:
            continue  # kuerzlich angesehen - kein Kandidat

        # Die zweite Uhr. Ohne sie stuende ein Film, der heute Nacht fertig
        # wurde, wegen seiner Groesse ganz oben - "noch nie angesehen" ist bei
        # ihm ja richtig und trotzdem kein Grund, ihn wegzuwerfen.
        if not nur_vorgemerkt:
            if eintrag.added_at is None:
                # Alter unbekannt. Nicht raten - lieber uebergehen und sagen,
                # wie viele es waren.
                unbekannt += 1
                continue
            if eintrag.added_at > stichtag:
                continue  # liegt noch nicht lange genug da

        mittel, anzahl = urteile.get(
            (eintrag.media_type, eintrag.tmdb_id, eintrag.season), (None, 0)
        )
        kandidaten.append(
            Kandidat(
                posten_id=eintrag.id,
                media_type=eintrag.media_type,
                tmdb_id=eintrag.tmdb_id,
                tvdb_id=eintrag.tvdb_id,
                season=eintrag.season,
                tier=eintrag.tier.value,
                title=eintrag.title,
                size_bytes=eintrag.size_bytes,
                state=eintrag.state,
                besitzer=namen.get(eintrag.user_id) if eintrag.user_id else None,
                zuletzt_gesehen=wann,
                gesehen_von=sorted(wer),
                bewertung=round(mittel, 1) if mittel is not None else None,
                bewertungen=anzahl,
                liegt_seit=eintrag.added_at,
                loescht_am=eintrag.delete_after,
            )
        )

    kandidaten.sort(key=lambda k: k.size_bytes, reverse=True)
    return Liste(
        kandidaten=kandidaten[:grenze],
        gesamt_anzahl=len(kandidaten),
        gesamt_bytes=sum(k.size_bytes for k in kandidaten),
        grundlage=grundlage(db),
        monate=monate,
        ohne_datum=unbekannt,
        # Ueber **alle** Kandidaten gezaehlt, nicht nur ueber die angezeigten:
        # Die Liste ist gedeckelt, die Aussage darf es nicht sein.
        gesehen_ohne_datum=sum(1 for k in kandidaten if k.gesehen_ohne_datum),
    )
