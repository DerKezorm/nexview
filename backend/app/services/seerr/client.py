"""Lesender Zugriff auf eine fremde Seerr-Installation.

⚠️ **Rueckwirkungsfreiheit ist eine Eigenschaft dieses Clients, nicht der
Schnittstelle.** Seerrs API-Schluessel ist ein Generalschluessel: Er handelt
als Konto 1, also als Administrator, und kann sich ueber die Kopfzeile
``X-API-User`` zusaetzlich als jedes beliebige Konto ausgeben
(``server/middleware/auth.ts`` in Seerr 3.4.1). Einen nur lesenden Schluessel
gibt es dort nicht. Wer diesen Client baut, haelt also einen Schluessel in der
Hand, mit dem sich die fremde Installation leerraeumen liesse.

Daraus folgen drei Regeln, und alle drei stehen im Code und nicht bloss in
einer Absichtserklaerung:

1. **Nur GET, und die Methode ist kein Parameter.** Es gibt hier kein
   ``post``, ``put`` oder ``delete``. Wer schreiben will, muss diese Datei
   aendern, und das faellt in einer Durchsicht auf.
2. **Nur Pfade aus** ``ERLAUBTE_PFADE``. Kein Abklappern, keine Selbstauskunft,
   kein "mal sehen was es gibt".
3. **Eine eigene Bremse.** Seerr hat auf allen Pfaden, die ein Umzug braucht,
   keine (die einzige sitzt auf ``/settings/logs``, 50 je Minute). Wer keine
   mitbringt, hat keine.

⚠️ **Warum Punkt 2 kein Formalismus ist.** In Seerrs Schnittstelle stehen
zerstoerende Vorgaenge hinter GET:

* ``GET /settings/discover/reset`` leert die Tabelle der Startseiten-Regale
  und legt sie neu an (``server/routes/settings/discover.ts``). Ein Werkzeug,
  das die Pfadliste abklappert, um zu sehen was es gibt, wischt dem Betreiber
  seine eingerichtete Startseite weg.
* ``GET /user/{id}/watchlist`` ist kein reines Lesen: Steht in Seerrs eigener
  Tabelle nichts, holt der Aufruf die Merkliste bei Plex und **schreibt sie
  dort hinein**.
* ``GET /avatarproxy/{id}`` und ``GET /imageproxy`` legen Dateien in Seerrs
  Bildzwischenspeicher ab, und der liegt im selben Ordner wie die Datenbank.

Keiner dieser Pfade steht unten. Das ist der ganze Zweck der Liste.

⚠️ **Und Titeldaten holt dieser Client nicht ueber Seerr.** Seerrs Pfade
``/movie/{id}`` und ``/tv/{id}`` reichen an TMDB durch, mit Seerrs Schluessel
und einer Bremse, die je Aufruf neu entsteht. Wer dort achthundert Titel
nachschlaegt, kann den TMDB-Schluessel des fremden Betreibers ins Limit
fahren - danach sehen dessen eigene Nutzer Fehler, ohne dass an Seerr etwas
kaputt waere. Nexview hat einen eigenen TMDB-Zugang; der wird benutzt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .. import http_log

logger = logging.getLogger("nexview.seerr")

#: Kurz gehalten: Ein Umzug ist ein Vorgang, den jemand anstoesst und bei dem
#: er zusieht. Haengt die fremde Instanz, will er das nach Sekunden wissen und
#: nicht nach einer Minute.
TIMEOUT = httpx.Timeout(20.0, connect=6.0)

#: Alle Anfragen laufen nacheinander, mit Mindestabstand. Das ist langsamer als
#: noetig und trotzdem richtig: Die fremde Instanz gehoert jemand anderem, sie
#: bedient waehrenddessen ihre eigenen Nutzer, und wir wissen nicht, auf
#: welchem Blech sie laeuft.
MINDESTABSTAND = 0.05

#: Wie viele Datensaetze je Seite. Gemessen an einer echten Installation:
#: 81 Anfragen in einem Aufruf sind 183 kB und 0,2 Sekunden. Bei 100 bleibt
#: eine Seite handlich, und eine grosse Installation braucht ein paar Runden
#: statt einer Antwort von zwanzig Megabyte.
SEITENGROESSE = 100

#: Notbremse gegen eine Instanz, die immer weiter Seiten liefert.
MAX_SEITEN = 200

#: **Die Erlaubnisliste.** Was hier nicht steht, ruft dieser Client nicht auf.
#:
#: Der Platzhalter ``{id}`` wird beim Bauen ersetzt; die Pruefung vergleicht
#: die Vorlage, nicht die fertige Adresse. Sonst waere die Liste durch einen
#: geschickt gewaehlten Bezeichner zu umgehen.
ERLAUBTE_PFADE: frozenset[str] = frozenset(
    {
        "/api/v1/status",
        "/api/v1/settings/main",
        "/api/v1/settings/radarr",
        "/api/v1/settings/sonarr",
        "/api/v1/settings/plex",
        "/api/v1/settings/jellyfin",
        "/api/v1/settings/notifications/email",
        "/api/v1/settings/notifications/discord",
        "/api/v1/settings/notifications/telegram",
        "/api/v1/settings/notifications/gotify",
        "/api/v1/settings/notifications/ntfy",
        "/api/v1/settings/notifications/webhook",
        "/api/v1/settings/notifications/slack",
        "/api/v1/settings/notifications/pushover",
        "/api/v1/settings/notifications/pushbullet",
        "/api/v1/settings/notifications/webpush",
        "/api/v1/user",
        "/api/v1/user/{id}/quota",
        "/api/v1/user/{id}/settings/main",
        "/api/v1/request",
        "/api/v1/blocklist",
        "/api/v1/issue",
    }
)


class SeerrFehler(Exception):
    """Der Zugriff auf Seerr hat nicht geklappt.

    ``code`` ist die Kennung, aus der die Oberflaeche ihren Satz baut
    (``errors.byCode``); ``message`` ist der deutsche Rueckfall fuer alles,
    was ohne die Nexview-Oberflaeche laeuft. Dieselbe Bauweise wie
    ``ArrError``, damit hier nicht ein zweites Muster entsteht.
    """

    def __init__(self, code: str, message: str, **zahlen: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.zahlen = zahlen

    def als_meldung(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, **self.zahlen}


@dataclass(frozen=True)
class Zugang:
    """Wohin und womit.

    ⚠️ **Der Schluessel wird nirgends gespeichert und nirgends protokolliert.**
    Er kommt im Rumpf einer Anfrage herein, lebt fuer die Dauer des Aufrufs und
    ist danach weg. Das ist auch der Grund, warum die Adressen dieses Features
    POST sind und nicht GET: Ein Schluessel in der Adresszeile stuende im
    Zugriffsprotokoll jedes Vermittlers dazwischen.
    """

    basis: str
    schluessel: str

    def __post_init__(self) -> None:
        if not self.basis.startswith(("http://", "https://")):
            raise SeerrFehler(
                code="seerr_url_invalid",
                message="Die Adresse muss mit http:// oder https:// beginnen.",
            )

    @property
    def wurzel(self) -> str:
        return self.basis.rstrip("/")


class SeerrClient:
    """Liest eine Seerr-Installation aus. Mehr kann diese Klasse nicht."""

    def __init__(self, zugang: Zugang) -> None:
        self._zugang = zugang
        self._zuletzt = 0.0
        self._sperre = asyncio.Lock()

    # ------------------------------------------------------------------ Kern

    async def _hole(
        self,
        vorlage: str,
        *,
        abfrage: dict[str, int] | None = None,
        **werte: object,
    ) -> Any:
        """Einen erlaubten Pfad lesen.

        ``vorlage`` ist der Eintrag aus :data:`ERLAUBTE_PFADE`, nicht die
        fertige Adresse. Die Pruefung geschieht **vor** dem Einsetzen: Ein
        Bezeichner, der einen anderen Pfad nachbaut, kaeme sonst durch.

        ⚠️ **Und die Abfrageparameter stehen bewusst nicht in der Vorlage.**
        Sie hier anzuhaengen war der erste Entwurf, und er fiel durch die
        eigene Pruefung: ``/api/v1/user?take=100`` steht in keiner Liste. Wer
        das repariert, indem er die Liste lockert, hat die Liste abgeschafft.
        Deshalb sind es zwei Dinge - der Pfad wird geprueft, die Zahlen werden
        angehaengt.
        """
        if vorlage not in ERLAUBTE_PFADE:
            # Kein SeerrFehler: Das ist kein Betriebsfehler, den ein Betreiber
            # sehen soll, sondern ein Programmierfehler in Nexview.
            raise AssertionError(f"Pfad steht nicht auf der Erlaubnisliste: {vorlage}")

        pfad = vorlage
        for name, wert in werte.items():
            pfad = pfad.replace("{" + name + "}", str(wert))
        adresse = f"{self._zugang.wurzel}{pfad}"
        if abfrage:
            # Nur Zahlen, und der Typ erzwingt es. Ein Text hier waere die
            # Hintertuer, die die Erlaubnisliste umgeht.
            teile = "&".join(f"{name}={int(wert)}" for name, wert in abfrage.items())
            adresse = f"{adresse}?{teile}"

        async with self._sperre:
            abstand = time.monotonic() - self._zuletzt
            if abstand < MINDESTABSTAND:
                await asyncio.sleep(MINDESTABSTAND - abstand)
            self._zuletzt = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                antwort = await client.get(
                    adresse,
                    headers={"X-Api-Key": self._zugang.schluessel},
                )
        except httpx.HTTPError as fehler:
            http_log.unreachable("seerr", "GET", adresse, fehler)
            raise SeerrFehler(
                code="seerr_unreachable",
                message="Seerr ist unter dieser Adresse nicht erreichbar.",
            ) from fehler

        if antwort.status_code in (401, 403):
            raise SeerrFehler(
                code="seerr_key_rejected",
                message="Seerr hat den API-Schlüssel abgelehnt.",
            )
        if antwort.status_code == 404:
            raise SeerrFehler(
                code="seerr_not_found",
                message="Unter dieser Adresse antwortet kein Seerr.",
            )
        if antwort.status_code >= 400:
            # ⚠️ Der Antwortkoerper geht **nicht** ins Protokoll. Seerrs
            # Antworten tragen Zugangsdaten und interne Adressen mit sich;
            # siehe den Kopf von ``vorschau.py``.
            logger.warning("Seerr answered %s for %s", antwort.status_code, pfad)
            raise SeerrFehler(
                code="seerr_error",
                message="Seerr hat mit einem Fehler geantwortet.",
                status=antwort.status_code,
            )

        try:
            return antwort.json()
        except ValueError as fehler:
            raise SeerrFehler(
                code="seerr_not_json",
                message="Unter dieser Adresse antwortet etwas, das kein Seerr ist.",
            ) from fehler

    async def _alle_seiten(self, vorlage: str) -> list[dict[str, Any]]:
        """Eine seitenweise Liste vollstaendig holen.

        Seerr liefert neben ``results`` ein ``pageInfo`` mit ``pages``. Die
        Schleife glaubt dieser Zahl **nicht** blind: Sie hoert auch auf, wenn
        eine Seite leer zurueckkommt, und bei :data:`MAX_SEITEN` in jedem Fall.
        Eine Endlosschleife gegen eine fremde Instanz waere das Gegenteil von
        rueckwirkungsfrei.
        """
        gesammelt: list[dict[str, Any]] = []
        seite = 1
        while seite <= MAX_SEITEN:
            daten = await self._hole(
                vorlage,
                abfrage={"take": SEITENGROESSE, "skip": (seite - 1) * SEITENGROESSE},
            )
            treffer = daten.get("results") or []
            if not treffer:
                break
            gesammelt.extend(treffer)
            seiten = int((daten.get("pageInfo") or {}).get("pages") or 1)
            if seite >= seiten:
                break
            seite += 1
        else:
            logger.warning("Stopped reading %s after %d pages", vorlage, MAX_SEITEN)
        return gesammelt

    # --------------------------------------------------------------- Abrufe

    async def status(self) -> dict[str, Any]:
        """Fassung und Commit der fremden Installation.

        ⚠️ **Das ist der einzige Weg an die Version.** In Seerrs Datenbank
        steht sie nicht: Dort gibt es nur TypeORMs Wanderungstabelle, und die
        nennt Namen und Zeitstempel der gelaufenen Schritte, nicht die Fassung.
        Wer den Umzug ueber die Datei fahren will, muss den Stand deshalb an
        den vorhandenen Spalten ablesen - hier bekommt er ihn geschenkt.
        """
        return await self._hole("/api/v1/status")

    async def einstellungen(self) -> dict[str, Any]:
        return await self._hole("/api/v1/settings/main")

    async def radarr(self) -> list[dict[str, Any]]:
        return list(await self._hole("/api/v1/settings/radarr"))

    async def sonarr(self) -> list[dict[str, Any]]:
        return list(await self._hole("/api/v1/settings/sonarr"))

    async def plex(self) -> dict[str, Any]:
        """Seerrs Plex-Einstellungen.

        ⚠️ **Ohne Token.** An einer echten Installation gemessen: Name,
        Adresse, Port, Bibliotheken und die Maschinenkennung kommen, das Token
        nicht - es haengt in Seerr am Konto und ist in der Schnittstelle
        ausgeblendet. Die Maschinenkennung allein ist trotzdem wertvoll: Sie
        sagt, **welchen** Server der Betreiber nach seiner eigenen
        Plex-Anmeldung auswaehlen muss.
        """
        return await self._hole("/api/v1/settings/plex")

    async def jellyfin(self) -> dict[str, Any]:
        """Seerrs Jellyfin- beziehungsweise Emby-Einstellungen.

        Hier ist es umgekehrt als bei Plex: ``apiKey`` und ``serverId`` liegen
        bei, die Verbindung liesse sich also vollstaendig uebernehmen.
        """
        return await self._hole("/api/v1/settings/jellyfin")

    async def mail(self) -> dict[str, Any]:
        """Der Mailserver, samt Passwort.

        ⚠️ **Das ist der heikelste Abruf dieses Clients.** Die Antwort traegt
        ``authPass`` im Klartext. Sie darf nirgends ins Protokoll und nirgends
        in eine Antwort nach aussen; sie geht direkt in
        ``settings_service.save_settings``, das sie verschluesselt ablegt.
        """
        return await self._hole("/api/v1/settings/notifications/email")

    #: Die Meldewege des Hauses, die Nexview kennen koennte - und die, die es
    #: nicht kennt, damit der Umzug sie wenigstens benennen kann.
    MELDEWEGE = (
        "discord",
        "telegram",
        "gotify",
        "ntfy",
        "webhook",
        "slack",
        "pushover",
        "pushbullet",
        "webpush",
    )

    async def meldewege(self) -> dict[str, dict[str, Any]]:
        """Seerrs Meldewege des Hauses, alle in einem Rutsch.

        ⚠️ **Nicht zu verwechseln mit den Adressen der Benutzer.** Seerr fuehrt
        beides; nur diese hier gehoeren der Installation, und nur sie haben in
        Nexview ein Gegenstueck.

        ⚠️ ``/test`` wird **nicht** aufgerufen. Der Pfad steht auf keiner
        Erlaubnisliste, und er verschickt eine echte Nachricht - der fremde
        Betreiber bekaeme mitten in der Nacht eine Probemeldung aus einer
        Anwendung, die er noch gar nicht benutzt.
        """
        gefunden: dict[str, dict[str, Any]] = {}
        for name in self.MELDEWEGE:
            try:
                gefunden[name] = await self._hole(f"/api/v1/settings/notifications/{name}")
            except SeerrFehler as fehler:
                # Ein Dienst, den diese Seerr-Fassung nicht kennt, ist keine
                # Stoerung - er faellt still weg. Alles andere reicht durch.
                if fehler.code != "seerr_not_found":
                    raise
        return gefunden

    async def konten(self) -> list[dict[str, Any]]:
        return await self._alle_seiten("/api/v1/user")

    async def anfragen(self) -> list[dict[str, Any]]:
        """Alle Anfragen, jede mit Besteller, Werk und Staffeln daran.

        Seerr liefert das eingebettet, in einem Zug. Deshalb braucht der Umzug
        **keinen** Aufruf je Anfrage: An der Zeile haengen bereits die
        TMDB- und die TVDB-Nummer, beide Verfuegbarkeitsspalten, die Staffeln
        mit eigenem Zustand, sowie Instanz, Profil und Ordner.
        """
        return await self._alle_seiten("/api/v1/request")

    async def sperrliste(self) -> list[dict[str, Any]]:
        """Die Sperrliste.

        ⚠️ **Und hier zahlt sich der Schnittstellenweg aus.** In der Datenbank
        hiess diese Tabelle bis zum 14.02.2026 ``blacklist`` und seitdem
        ``blocklist``; wer sie dort unter dem neuen Namen sucht, findet auf
        einer aelteren Installation nichts und meldet faelschlich "keine
        Sperrliste". Die Schnittstelle hat den alten Pfad als Zweitnamen
        behalten - an einer echten Installation gemessen liefern beide
        byteweise dasselbe.
        """
        return await self._alle_seiten("/api/v1/blocklist")

    async def meldungen(self) -> list[dict[str, Any]]:
        return await self._alle_seiten("/api/v1/issue")
