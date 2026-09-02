"""Bestehende Konten eines Medienservers nach Nexview holen.

⚠️ **Warum es das gibt.** Ein Konto entsteht heute erst, wenn sich jemand zum
ersten Mal anmeldet - und bei Jellyfin und Emby entsteht es dabei **gar nicht**:
``mediaserver_accounts.resolve`` legt ohne Mailadresse nichts an, und die beiden
kennen zu einem Konto keine. Wer dreissig Leute aus Jellyfin uebernehmen will,
legt heute dreissig Konten von Hand an, setzt dreissig Passwoerter und teilt sie
mit; erst danach kann jede Person Jellyfin im eigenen Profil verknuepfen.

⚠️ **Und warum die Verknuepfung das eigentliche Stueck Arbeit ist.** Ein Konto
allein waere wertlos. Beim Anmelden fragt ``find_linked`` nach Anbieter und
Konto-Kennung; ohne die Zeile faellt die Anmeldung durch bis zum
``knows_email``-Zweig und wird abgewiesen. Der Import legt deshalb **beides**
an, in einer Transaktion.

Die Kennung muss dabei die der **Anmeldung** sein, nicht irgendeine des Servers.
Bei Plex sind das zwei verschiedene Dinge; die Begruendung steht ausfuehrlich in
``mediaserver/plextv.kontenliste``.

## Was dieser Dienst bewusst *nicht* tut

**Er raet nicht.** Ob hinter einem Server-Konto ein Mensch steckt, der in
Nexview schon eines hat, kann er nicht wissen: Ueber die Anbieter hinweg gibt es
kein verlaessliches Merkmal. Plex kennt eine Mailadresse, Jellyfin und Emby
haben grundsaetzlich keine, und derselbe Mensch heisst auf zwei Servern haeufig
verschieden. Ein Abgleich ueber aehnliche Namen taeuscht Sicherheit vor: Wer die
Zeile "kein Gegenstueck" sieht, hakt sie durch - und legt genau dabei das zweite
Konto an.

Deshalb liefert ``kandidaten`` zu jeder Zeile **alle** infrage kommenden
Nexview-Konten und ueberlaesst die Zuordnung dem Betreiber. Was Nexview sicher
weiss - "diese Kennung ist bereits verknuepft" -, sagt es; alles andere fragt es.

⚠️ **Ein falsch zugeordnetes Konto ist teuer.** Es gibt in Nexview kein
Zusammenfuehren zweier Konten. Ein Duplikat heisst zwei Kontingente, zwei
Anfragelisten, zwei Favoritenlisten, und der Weg zurueck ist Loeschen samt
allem, was daran haengt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..models import MediaServerBlock, Role, User, UserMediaServerAccount
from ..schemas import Kontingentwert, kontingent_aus_wert
from ..security import unusable_password
from . import mediaserver_accounts, tokens
from .mediaserver.base import ExternalAccount, ServerUser

logger = logging.getLogger("nexview.import")


@dataclass(frozen=True)
class Zuordenbar:
    """Ein Nexview-Konto, dem sich dieses Server-Konto zuweisen liesse."""

    user_id: int
    username: str
    #: Woran dieses Konto sonst schon haengt, etwa ``["plex"]``.
    #:
    #: ⚠️ Gehoert in die Oberflaeche, und zwar sichtbar. Beim zweiten Import
    #: ist das der einzige Hinweis darauf, dass eine Person schon da ist:
    #: "jamie (Plex)" statt bloss "jamie".
    verknuepft_mit: tuple[str, ...]


@dataclass(frozen=True)
class Kandidat:
    """Ein Konto des Servers, so wie es zur Entscheidung vorgelegt wird."""

    account_id: str
    username: str
    email: str | None
    #: Gibt es zu dieser Kennung schon eine Verknuepfung?
    schon_verknuepft: bool
    #: Und wenn ja, mit welchem Konto - fuer die Anzeige.
    gehoert_zu: str | None
    #: Steht diese Kennung auf der Sperrliste?
    #:
    #: ⚠️ **Sonst legt der Import ein Konto an, in das niemand hineinkommt.**
    #: Wer ein Konto loescht, setzt dessen Medienserver-Identitaet auf die
    #: Sperrliste, damit sie sich nicht bei der naechsten Anmeldung gleich
    #: wieder selbst anlegt. ``resolve`` fragt die Liste als **erstes** ab -
    #: vor der Verknuepfung. Ein Import, der das uebergeht, baut genau den
    #: Zustand, den dieses Feature verhindern soll: Konto da, Verknuepfung da,
    #: Anmeldung abgewiesen.
    gesperrt: bool = False


@dataclass(frozen=True)
class Vorlage:
    """Was der Betreiber zu sehen bekommt."""

    provider: str
    kandidaten: tuple[Kandidat, ...]
    zuordenbar: tuple[Zuordenbar, ...]


def _konten_mit_verknuepfungen(db: Session) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .options(selectinload(User.mediaserver_accounts))
            .where(User.is_active.is_(True))
            .order_by(User.username)
        )
    )


def zuordenbare_konten(db: Session, provider: str) -> list[Zuordenbar]:
    """Konten, die fuer **diesen** Anbieter noch keine Verknuepfung haben.

    ⚠️ **Je Anbieter, nicht ueberhaupt.** Wer schon eine Plex-Verknuepfung hat,
    muss beim Jellyfin-Import trotzdem zur Auswahl stehen - sonst legt der
    Betreiber fuer denselben Menschen ein zweites Konto an, und genau das ist
    der Fehler, den dieser Dienst verhindern soll.

    Denselben Fehler gab es in Nexview schon einmal an anderer Stelle: Die
    Anmeldung prueft heute ``verknuepfung(user, provider)`` und nicht mehr die
    gespiegelte Einzelspalte, weil sonst jemand mit Jellyfin-Verknuepfung bei
    seiner ersten Plex-Anmeldung abgewiesen wurde.

    Kinderkonten bleiben draussen: Sie sind Unterprofile ihrer Eltern und haben
    beim Anbieter kein Gegenstueck.
    """
    gefunden: list[Zuordenbar] = []
    for konto in _konten_mit_verknuepfungen(db):
        if konto.is_child:
            continue
        anbieter = tuple(sorted(zeile.provider for zeile in konto.mediaserver_accounts))
        if provider in anbieter:
            continue
        gefunden.append(
            Zuordenbar(
                user_id=konto.id,
                username=konto.username,
                verknuepft_mit=anbieter,
            )
        )
    return gefunden


def kandidaten(db: Session, provider: str, vom_server: list[ServerUser]) -> Vorlage:
    """Die Liste des Servers, angereichert um das, was Nexview sicher weiss.

    Sicher weiss Nexview genau eine Sache: ob es zu dieser Konto-Kennung
    bereits eine Verknuepfung gibt. Alles andere ist die Entscheidung des
    Betreibers.
    """
    vorhanden = {
        zeile.account_id: zeile
        for zeile in db.scalars(
            select(UserMediaServerAccount)
            .options(selectinload(UserMediaServerAccount.user))
            .where(UserMediaServerAccount.provider == provider)
        )
    }

    zeilen: list[Kandidat] = []
    for konto in vom_server:
        treffer = vorhanden.get(konto.account_id)
        zeilen.append(
            Kandidat(
                account_id=konto.account_id,
                username=konto.username,
                email=konto.email,
                schon_verknuepft=treffer is not None,
                gehoert_zu=treffer.user.username if treffer is not None else None,
                gesperrt=mediaserver_accounts.is_blocked(db, provider, konto.account_id),
            )
        )

    return Vorlage(
        provider=provider,
        kandidaten=tuple(zeilen),
        zuordenbar=tuple(zuordenbare_konten(db, provider)),
    )


@dataclass(frozen=True)
class Wunsch:
    """Eine Zeile, so wie der Betreiber sie entschieden hat."""

    account_id: str
    username: str
    email: str | None
    #: ``None`` heisst "neues Konto", sonst die Nummer des Zielkontos.
    ziel_user_id: int | None
    #: Ausdrueckliches Ja zu einer gesperrten Kennung.
    #:
    #: ⚠️ **Muss eine eigene Entscheidung sein, nicht der Haken der Zeile.**
    #: Die Sperre steht dort, weil jemand dieses Konto geloescht hat. Sie zu
    #: uebergehen kann richtig sein - der Administrator holt die Person
    #: absichtlich zurueck -, aber es darf nicht nebenbei passieren.
    trotz_sperre: bool = False


@dataclass(frozen=True)
class Vorgaben:
    """Was fuer alle **neu angelegten** Konten gilt.

    ⚠️ **Nur fuer neu angelegte, nie fuer verknuepfte.** Wer ein bestehendes
    Nexview-Konto mit einer Server-Identitaet verbindet, hat dessen Grenzen
    irgendwann bewusst gesetzt; ein Import, der sie nebenbei ueberschreibt,
    waere ein Datenverlust, den niemand angefordert hat. Der Code haelt das
    dadurch ein, dass ``uebernehmen`` diese Werte ausschliesslich im Zweig
    "neues Konto" liest.

    Bewusst fuer den Stapel und nicht je Zeile: Dreissig Zeilen mit je vier
    Auswahlfeldern sind ein Formular, das niemand ausfuellt - und wer es doch
    tut, klickt dreissigmal dasselbe. Was danach anders sein soll, aendert die
    Benutzerverwaltung.

    ⚠️ **Die Rolle steht nicht zur Wahl, und das ist Absicht.** Ein Import
    legt gewoehnliche Nutzer an, nie Entscheider und nie Administratoren. Wer
    dreissig Konten auf einmal anlegt, soll dabei nicht dreissig Leuten Rechte
    geben koennen, die er einzeln sorgfaeltig vergeben wuerde. Dasselbe gilt
    fuer die automatische Freigabe: Zugriff auf die Bibliothek zu haben heisst
    nicht, ungefragt herunterladen zu duerfen.

    Die drei Grenzen sprechen dieselbe Sprache wie ueberall sonst
    (``schemas.Kontingentwert``): ``"standard"`` heisst Hausvorgabe,
    ``"unlimited"`` ausdruecklich ohne Grenze, eine Zahl genau diese - und die
    **0 heisst "darf nichts"**. Genau diese Dreiwertigkeit hat in 0.26.2 einen
    Fehler gekostet, bei dem eine gesetzte 0 sich bei jedem Start in
    "unbegrenzt" zurueckverwandelte.
    """

    filme: Kontingentwert = "standard"
    serien: Kontingentwert = "standard"
    speicher_gb: Kontingentwert = "standard"
    #: Inaktiv anlegen heisst: erst uebernehmen, spaeter freischalten.
    aktiv: bool = True


@dataclass(frozen=True)
class Ergebnis:
    angelegt: int
    verknuepft: int
    #: Zeilen, die nicht gingen, mit Grund - je Kennung ein Satz.
    abgelehnt: dict[str, str]
    #: Wie viele Sperren dabei aufgehoben wurden - gehoert in die Zusammenfassung.
    aufgehoben: int = 0


def uebernehmen(
    db: Session,
    provider: str,
    wuensche: list[Wunsch],
    vorgaben: Vorgaben,
) -> Ergebnis:
    """Konten anlegen und verknuepfen - beides oder keines.

    ⚠️ **Das Betreiberkonto ist als Ziel ausgeschlossen, und der Grund ist
    kein formaler.** Wer eine Medienserver-Identitaet an ein Konto haengt, gibt
    jedem, der diese Identitaet kontrolliert, den Weg in dieses Konto: Die
    Anmeldung ueber den Server fragt kein Passwort. Ein zweiter Administrator
    koennte also seine eigene Jellyfin-Kennung an das Betreiberkonto haengen
    und sich anschliessend als Betreiber anmelden. Das ist eine Uebernahme in
    zwei Klicks.

    ⚠️ **Und diese Pruefung steht hier im Rumpf statt als Wache an der
    Adresse - das ist eine bewusste Ausnahme mit einem Preis.** ``deps.
    betreiberschutz`` liest genau **ein** ``user_id`` aus dem Pfad; hier kommen
    beliebig viele Ziele im Rumpf. Die Wache liesse sich also nicht anhaengen,
    ohne sie umzubauen.

    Der Preis: ``test_betreiber_waechter.py`` laeuft ueber die Routentabelle
    und sieht eine Pruefung im Rumpf nicht. Die Adresse steht deshalb mit
    ausgeschriebenem Grund in ``OHNE_SCHUTZ``, und der Grund verweist auf
    ``test_nutzer_import.py::test_der_betreiber_ist_kein_ziel``. Wer diese
    Zeilen hier entfernt, macht jenen Test rot - das ist der Ersatz fuer die
    Wache, und er ist schwaecher als sie.

    Weiter abgelehnt werden: Kinderkonten (Unterprofile, beim Anbieter gibt es
    sie nicht), inaktive Konten und Ziele, die fuer diesen Anbieter schon eine
    Verknuepfung tragen.
    """
    angelegt = 0
    verknuepft = 0
    aufgehoben = 0
    abgelehnt: dict[str, str] = {}

    vergeben = {
        zeile.account_id
        for zeile in db.scalars(
            select(UserMediaServerAccount).where(
                UserMediaServerAccount.provider == provider
            )
        )
    }

    for wunsch in wuensche:
        if wunsch.account_id in vergeben:
            abgelehnt[wunsch.account_id] = "Diese Kennung ist bereits verknüpft."
            continue

        # ⚠️ **Vor allem anderen, weil ``resolve`` es auch als Erstes fragt.**
        # Eine gesperrte Kennung kommt beim Anmelden nicht durch; ein Konto
        # dafuer anzulegen hiesse, eines zu bauen, das niemand benutzen kann.
        # Die Sperre entsteht beim Loeschen eines Kontos und ist absichtlich
        # nicht stillschweigend aufhebbar - wer sie loswerden will, raeumt sie
        # unter Medienserver > Sperrliste weg.
        if mediaserver_accounts.is_blocked(db, provider, wunsch.account_id):
            if not wunsch.trotz_sperre:
                abgelehnt[wunsch.account_id] = (
                    "Für diese Kennung ist der Zugang gesperrt. Setz den Haken "
                    "für „trotzdem übernehmen“, wenn das Absicht ist."
                )
                continue
            # ⚠️ **Die Sperre wird aufgehoben, nicht umgangen.** Sie stuende
            # sonst weiter da und wuerde die Anmeldung abweisen, waehrend Konto
            # und Verknuepfung existieren - genau der Zustand, den dieses
            # Feature verhindern soll. Und sie verschwindet sichtbar: Der
            # Vorgang steht im Protokoll, weil das Aufheben einer Sperre eine
            # Entscheidung ist, die man spaeter nachvollziehen koennen muss.
            geloescht = db.execute(
                delete(MediaServerBlock).where(
                    MediaServerBlock.provider == provider,
                    MediaServerBlock.account_id == wunsch.account_id,
                )
            ).rowcount
            if geloescht:
                logger.info(
                    "Import lifted the block on %s account %s", provider, wunsch.account_id
                )
            aufgehoben += 1

        konto = ExternalAccount(
            provider=provider,
            account_id=wunsch.account_id,
            username=wunsch.username,
            email=wunsch.email,
            thumb=None,
        )

        if wunsch.ziel_user_id is None:
            benutzer = User(
                # Derselbe Weg wie bei einer gewoehnlichen Anmeldung: Der Name
                # beim Anbieter kann Zeichen tragen, die Nexview nicht vergibt,
                # und er kann laengst vergeben sein.
                username=mediaserver_accounts._unique_username(db, wunsch.username),
                # Kein Passwort: Dieses Konto haengt am Medienserver, genau wie
                # eines aus einer gewoehnlichen Anmeldung. Ein erfundenes
                # Passwort waere eines, das niemand kennt und jeder raten darf.
                password_hash=unusable_password(),
                email=tokens.normalize_email(wunsch.email) if wunsch.email else None,
                # Fest, nicht waehlbar - siehe Vorgaben.
                role=Role.user,
                display_name=wunsch.username,
                auto_approve=False,
                is_active=vorgaben.aktiv,
                quota_movies_limit=kontingent_aus_wert(vorgaben.filme),
                quota_series_limit=kontingent_aus_wert(vorgaben.serien),
                storage_limit_gb=kontingent_aus_wert(vorgaben.speicher_gb),
            )
            db.add(benutzer)
            db.flush()
            angelegt += 1
        else:
            benutzer = db.get(User, wunsch.ziel_user_id)
            if benutzer is None:
                abgelehnt[wunsch.account_id] = "Dieses Nexview-Konto gibt es nicht mehr."
                continue
            if benutzer.is_betreiber:
                abgelehnt[wunsch.account_id] = (
                    "Das Betreiberkonto lässt sich nicht verknüpfen - siehe Docstring."
                )
                continue
            if benutzer.is_child:
                abgelehnt[wunsch.account_id] = "Kinderkonten haben beim Anbieter kein Gegenstück."
                continue
            if not benutzer.is_active:
                abgelehnt[wunsch.account_id] = "Dieses Konto ist deaktiviert."
                continue
            if mediaserver_accounts.verknuepfung(benutzer, provider) is not None:
                abgelehnt[wunsch.account_id] = (
                    f"Dieses Konto hat für {provider} schon eine Verknüpfung."
                )
                continue
            verknuepft += 1

        mediaserver_accounts.link(benutzer, konto)
        vergeben.add(wunsch.account_id)

    db.commit()
    return Ergebnis(
        angelegt=angelegt,
        verknuepft=verknuepft,
        aufgehoben=aufgehoben,
        abgelehnt=abgelehnt,
    )
