"""Jellyfin als Media-Server.

Der Gegenentwurf zu Plex, und der Unterschied faengt bei der Anmeldung an:

* **Plex** hat mit plex.tv einen Vermittler. Nexview kennt eine Adresse und ein
  Token, aber wer jemand *ist*, sagt plex.tv - und ueber welche Server jemand
  verfuegt, auch.
* **Jellyfin** hat keinen. Es gibt nur diesen einen Server, er kennt seine
  Konten selbst, und angemeldet wird sich mit Benutzername und Passwort.

Daraus folgt fast alles Weitere. ``list_servers`` kann nur den einen Server
zurueckgeben, unter dem Nexview gerade angemeldet ist - eine Auswahl gibt es
nicht. Die Token sind server-eigen, weshalb ein gueltiges Token bereits die
Zugriffspruefung *ist*. Und es gibt keine Merkliste, also auch keinen Haken
dafuer in der Oberflaeche (``supports_watchlist`` bleibt falsch).

⚠️ **Jellyfin-Konten haben keine E-Mail-Adresse.** Das Feld gibt es schlicht
nicht. Wer ueber Jellyfin hereinkommt, hat in Nexview also keine hinterlegte
Adresse - und ohne die greift "Passwort vergessen" nicht. Das ist der Grund,
warum das Trennen einer Verbindung vorher nachfragt, wen es aussperren wuerde.

Gemessen an Jellyfin 10.11.11.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from hashlib import sha1
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from ... import __version__
from .. import http_log
from .base import (
    Umrechnung,
    Wiedergabe,
    ExternalAccount,
    LibraryItem,
    LoginChallenge,
    MediaServer,
    MediaServerError,
    ServerCandidate,
    ServerUser,
    SeasonWatchedRecord,
    WatchedRecord,
    http_client,
    kleineres_haeppchen,
    seiten_timeout,
    SEITE_HOECHSTENS,
)

if TYPE_CHECKING:  # nur fuer die Typangabe - vermeidet einen Ringschluss
    from ..settings_service import AppSettings, Verbindung

logger = logging.getLogger("nexview.mediaserver")

# Wie viele Titel je Abfrage. Die Obergrenze und die Leiter darunter stehen in
# ``base.py`` (``SEITE_HOECHSTENS``, ``kleineres_haeppchen``) - sie gelten fuer
# jeden Anbieter, der seitenweise liest.
#
# ⚠️ **Die Film-Abfrage faengt kleiner an, und das ist kein Feinschliff.**
# Sie ist die einzige, die ``MediaSources`` mitliest - die Dateiangaben, aus
# denen Groesse und Aufloesung kommen. Jellyfin baut die je Titel zusammen,
# und damit ist diese eine Abfrage um ein Vielfaches teurer als alle anderen.
# Bei 200 laeuft ein langsamer Server in jedem Durchlauf erst zweimal in die
# Zeitgrenze, bevor er unten ankommt; bei 100 faengt er dort an, wo er ohnehin
# landet. Serien tragen keine Dateien (die haengen an den Folgen), der
# Gesehen-Stand ist gefiltert und klein - beide bleiben bei der Obergrenze.
SEITE_FILME = 100

# Ab dieser Breite gilt eine Datei als 4K.
#
# Nach der Breite und nicht nach der Hoehe: Ein Film im Kinoformat ist
# 3840x1600 - nach der Hoehe gemessen laege er unter 1080p, obwohl er in jeder
# Hinsicht 4K ist. Die Breite ist bei allen Seitenverhaeltnissen dieselbe.
UHD_AB_BREITE = 3000

# Wie viele Staffel-Abfragen gleichzeitig. Jede Serie kostet eine eigene -
# nacheinander dauert das schon bei einer Handvoll Serien spuerbar lange.
PARALLELE_STAFFELN = 5


def _kennung(werte: dict[str, Any], name: str) -> int | None:
    """Eine Zahl aus ``ProviderIds`` holen.

    Jellyfin schreibt die Schluessel mal ``Tmdb``, mal ``TMDB`` - je nachdem,
    welches Plugin den Titel erkannt hat. Deshalb wird ohne Ruecksicht auf
    Gross- und Kleinschreibung gesucht.
    """
    for schluessel, wert in werte.items():
        if schluessel.lower() == name.lower():
            roh = str(wert or "").strip()
            return int(roh) if roh.isdigit() else None
    return None


def _text_kennung(werte: dict[str, Any], name: str) -> str | None:
    for schluessel, wert in werte.items():
        if schluessel.lower() == name.lower():
            return str(wert or "").strip() or None
    return None


def _zeitpunkt(roh: Any) -> datetime | None:
    """Aus Jellyfins ISO-Zeit einen naiven UTC-Zeitpunkt machen.

    Naiv, weil die ganze Anwendung so rechnet (siehe ``utcnow``). Jellyfin
    haengt an die Zeit mehr Nachkommastellen, als ``fromisoformat`` verdaut,
    und manchmal ein ``Z`` - beides wird hier geglaettet.
    """
    if not isinstance(roh, str) or not roh:
        return None
    text = roh.replace("Z", "+00:00")
    # "2026-08-14T19:03:12.1234567+00:00" -> auf sechs Stellen kuerzen
    if "." in text:
        kopf, _, rest = text.partition(".")
        ziffern = ""
        for zeichen in rest:
            if not zeichen.isdigit():
                break
            ziffern += zeichen
        text = f"{kopf}.{ziffern[:6]}{rest[len(ziffern):]}"
    try:
        wert = datetime.fromisoformat(text)
    except ValueError:
        return None
    if wert.tzinfo is None:
        return wert
    return wert.astimezone(UTC).replace(tzinfo=None)


def _als_werk(
    eintrag: dict[str, Any],
    media_type: str,
    angesehene_serien: set[str] | None = None,
    anbieter: str = "jellyfin",
) -> LibraryItem | None:
    """Einen Eintrag in die anbieter-neutrale Form uebersetzen.

    ``anbieter`` steckt im ``guid`` und entscheidet ueber die Eindeutigkeit:
    Die Bibliothekstabelle schluesselt ueber ``(provider, guid)``, und
    derselbe Film auf zwei Servern muss zwei Zeilen ergeben. Emby benutzt
    dieselbe Uebersetzung - siehe ``emby.py``.
    """
    titel = (eintrag.get("Name") or "").strip()
    kennung = str(eintrag.get("Id") or "").strip()
    if not titel or not kennung:
        return None

    anbieter_ids = eintrag.get("ProviderIds") or {}
    nutzerdaten = eintrag.get("UserData") or {}

    # Gesehen: Bei Filmen sagt ``Played`` alles.
    #
    # Bei Serien nicht: Dort steht ``Played`` nur auf wahr, wenn *jede* Folge
    # gesehen wurde - fuer das Auge an der Kachel zaehlt aber schon eine
    # einzige, genau wie bei Plex. Am Serien-Eintrag laesst sich das nicht
    # ablesen: Jellyfin nennt dort zwar die Zahl der offenen Folgen, aber nicht
    # die Gesamtzahl (nachgemessen an 10.11.11 - ``RecursiveItemCount`` kommt
    # schlicht nicht mit). Ohne beide Zahlen sagt die eine nichts.
    #
    # Deshalb wird die Antwort woanders geholt und hier nur eingesetzt: eine
    # einzige Abfrage nach gesehenen *Folgen* nennt genau die Serien, um die es
    # geht. Siehe ``_angesehene_serien``.
    gesehen = bool(nutzerdaten.get("Played"))
    if not gesehen and media_type == "tv" and angesehene_serien is not None:
        gesehen = kennung in angesehene_serien

    # Aufloesung und Groesse je hinterlegter Datei.
    #
    # Nur bei Filmen: Bei Serien haengen die Dateien an den Folgen, der
    # Serien-Eintrag traegt keine ``MediaSources``. Fehlt die Angabe, bleibt es
    # bei "Standard vorhanden" - dem Verhalten, das ``LibraryItem`` vorgibt.
    hat_uhd = False
    hat_standard = False
    groesse_standard = 0
    groesse_uhd = 0
    quellen = eintrag.get("MediaSources") or []
    for quelle in quellen:
        breite = 0
        for spur in quelle.get("MediaStreams") or []:
            if (spur.get("Type") or "") == "Video" and isinstance(spur.get("Width"), int):
                breite = max(breite, spur["Width"])
        # Manche Sammlungen fuehren die Breite am Titel statt an der Spur.
        if not breite and isinstance(eintrag.get("Width"), int):
            breite = eintrag["Width"]
        roh_groesse = quelle.get("Size")
        groesse = roh_groesse if isinstance(roh_groesse, int) and roh_groesse > 0 else 0
        if breite >= UHD_AB_BREITE:
            hat_uhd = True
            groesse_uhd += groesse
        else:
            hat_standard = True
            groesse_standard += groesse

    jahr = eintrag.get("ProductionYear")

    return LibraryItem(
        media_type=media_type,
        guid=f"{anbieter}://{kennung}",
        title=titel,
        tmdb_id=_kennung(anbieter_ids, "tmdb"),
        tvdb_id=_kennung(anbieter_ids, "tvdb"),
        imdb_id=_text_kennung(anbieter_ids, "imdb"),
        year=jahr if isinstance(jahr, int) else None,
        rating_key=kennung,
        owner_watched=gesehen,
        watched_at=_zeitpunkt(nutzerdaten.get("LastPlayedDate")),
        # Ohne Dateiangaben bleibt es bei "vorhanden" - siehe ``LibraryItem``.
        has_standard=hat_standard or not quellen,
        has_uhd=hat_uhd,
        size_standard=groesse_standard,
        size_uhd=groesse_uhd,
    )


class JellyfinServer(MediaServer):
    provider = "jellyfin"
    label = "Jellyfin"
    login_kind = "password"
    knows_email = False

    def __init__(
        self, settings: "AppSettings", verbindung: "Verbindung | None" = None
    ) -> None:
        # Anders als bei Plex ist die Adresse hier lebenswichtig: Es gibt
        # keinen Vermittler, ueber den sich der Server sonst finden liesse.
        self.base_url = (verbindung.url if verbindung else settings.mediaserver_url).rstrip("/")
        self.token = verbindung.token if verbindung else settings.mediaserver_token
        self.machine_id = (
            verbindung.machine_id if verbindung else settings.mediaserver_machine_id
        )
        self.client_identifier = settings.mediaserver_client_identifier
        # Die Konto-Nummer des hinterlegten Zugangs. Jellyfin verlangt sie bei
        # fast jeder Abfrage und nennt sie nur unter ``/Users/Me`` - deshalb
        # wird sie beim ersten Bedarf geholt und dann behalten.
        # Beim Verbinden gemerkt (seit 0.19). Damit entfaellt die Rueckfrage
        # beim Server - und fuer Emby ist sie die einzige Quelle, weil es dort
        # kein ``/Users/Me`` gibt.
        gemerkt = (
            verbindung.account_id
            if verbindung is not None
            else getattr(settings, "mediaserver_account_id", "")
        )
        self._eigene_id: str | None = gemerkt or None
        # Wie viele Titel dieser Server auf einmal vertraegt. Faengt bei der
        # Obergrenze an und sinkt, sobald er eine Zeitgrenze reisst - siehe
        # ``_seiten``. Bewusst am Server-Objekt und nicht global: Ein Abgleich
        # baut sich seinen eigenen, und was ein lahmes Jellyfin gelernt hat,
        # soll das flotte Emby daneben nicht ausbaden.
        self._seitengroesse = SEITE_HOECHSTENS

    # --- Werkzeug ----------------------------------------------------------

    def geraet(self, zweck: str = "") -> str:
        """Die Geraete-Kennung fuer einen bestimmten Zweck.

        ⚠️ **Jellyfin fuehrt Zugaenge je Geraet, nicht je Konto.** Meldet sich
        jemand erneut unter derselben Geraete-Kennung an, verfaellt der
        vorherige Zugang - auch der eines *anderen* Kontos.

        Genau das ist passiert: Nexview benutzte eine einzige Kennung fuer
        alles. Die persoenliche Anmeldung des Administrators knipste damit den
        **Server-Zugang** aus, den derselbe Mensch beim Verbinden hinterlegt
        hatte. Der Bibliotheks-Abgleich stand still, und in der Oberflaeche
        stand nur "Zugang abgelaufen".

        Deshalb bekommt jeder Zweck seine eigene Kennung: eine fuer die
        Server-Verbindung, je eine fuer jedes persoenliche Konto. Gehasht,
        damit dort keine Benutzernamen im Klartext auftauchen - Jellyfin zeigt
        die Kennung in seiner Geraeteliste.
        """
        basis = self.client_identifier or "nexview"
        if not zweck:
            return basis
        return sha1(f"{basis}:{zweck}".encode()).hexdigest()[:24]

    def _kopfzeilen(self, token: str = "", zweck: str = "") -> dict[str, str]:
        """Die Ausweiszeile, die Jellyfin erwartet.

        Der ganze Aufbau ist Vorschrift: Ohne ``DeviceId`` legt Jellyfin bei
        jeder Anmeldung ein neues Geraet an, ohne ``Client`` weist es die
        Anmeldung ganz zurueck.
        """
        teile = [
            'Client="Nexview"',
            'Device="Nexview"',
            f'DeviceId="{self.geraet(zweck)}"',
            f'Version="{__version__}"',
        ]
        if token:
            teile.append(f'Token="{token}"')
        return {"Authorization": f"MediaBrowser {', '.join(teile)}"}

    async def _anfrage(
        self,
        methode: str,
        pfad: str,
        *,
        token: str | None = None,
        basis: str | None = None,
        zweck: str = "",
        timeout: httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> Any:
        """Eine Abfrage an den Server - mit lesbaren Fehlern statt Ausnahmen.

        ``token=None`` heisst "der hinterlegte Zugang", ``token=""`` heisst
        ausdruecklich *ohne* Anmeldung - das braucht ``/System/Info/Public``,
        das ja gerade dann gefragt wird, wenn es noch kein Token gibt.
        """
        url = (basis or self.base_url).rstrip("/")
        if not url:
            raise MediaServerError(f"Es ist keine {self.label}-Adresse hinterlegt.")

        client = await http_client()
        kopfzeilen = self._kopfzeilen(self.token if token is None else token, zweck)
        kopfzeilen.update(kwargs.pop("headers", {}))
        # ⚠️ Nur setzen, wenn es eine gibt: ``timeout=None`` heisst bei httpx
        # **gar keine** Grenze, nicht "die vom Client".
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            response = await client.request(
                methode, f"{url}{pfad}", headers=kopfzeilen, **kwargs
            )
        except httpx.TimeoutException as exc:
            # ⚠️ Die Haken am Client laufen hier **nicht** - sie haengen an
            # einer Antwort, und die gibt es nicht. Ohne diese Zeile steht von
            # einer Zeitueberschreitung nichts im Protokoll, und niemand kann
            # hinterher sagen, welche Abfrage gestorben ist. Genau daran war
            # Issue #7 nicht nachzuvollziehen; arr.py und tmdb.py machen es
            # laengst so.
            http_log.unreachable("mediaserver", methode, f"{url}{pfad}", exc)
            raise MediaServerError(
                f"Der {self.label}-Server antwortet nicht (Zeitüberschreitung).",
                code="mediaserver_timeout",
                service=self.label,
            ) from exc
        except httpx.HTTPError as exc:
            http_log.unreachable("mediaserver", methode, f"{url}{pfad}", exc)
            raise MediaServerError(
                f"Der {self.label}-Server ist unter {url} nicht erreichbar.",
                code="mediaserver_offline",
                service=self.label,
                url=url,
            ) from exc

        if response.status_code in (401, 403):
            raise MediaServerError(
                f"Der {self.label}-Server hat die Anmeldung nicht akzeptiert.", 401
            )
        if response.status_code >= 400:
            raise MediaServerError(
                f"Der {self.label}-Server meldet einen Fehler (HTTP {response.status_code}).",
                response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise MediaServerError(
                f"Die Antwort von {url} ist unerwartet. "
                "Zeigt die Adresse wirklich auf einen Jellyfin-Server?"
            ) from exc

    async def _eigene_konto_id(self, token: str | None = None) -> str:
        """Die Konto-Nummer zum Token - gemerkt, weil sie staendig gebraucht wird."""
        if token is None and self._eigene_id:
            return self._eigene_id
        try:
            daten = await self._anfrage("GET", "/Users/Me", token=token) or {}
        except MediaServerError as exc:
            # ⚠️ Ein Jellyfin-*API-Schluessel* gehoert keinem Konto, und
            # ``/Users/Me`` beantwortet das mit einem blanken HTTP 400 - eine
            # Meldung, aus der niemand schliessen kann, was zu tun waere.
            # Nexview meldet sich mit Benutzername und Passwort an und bekommt
            # damit ein Konto-Token; ein von Hand eingetragener Schluessel
            # taete es nicht.
            if exc.status_code in (400, 401):
                raise MediaServerError(
                    f"Dieser {self.label}-Zugang gehört zu keinem Benutzerkonto. "
                    "Bitte den Server neu verbinden - mit Benutzername und Passwort.",
                    401,
                ) from exc
            raise
        nummer = str(daten.get("Id") or "")
        if not nummer:
            raise MediaServerError(f"{self.label} nennt zu diesem Zugang kein Konto.")
        if token is None:
            self._eigene_id = nummer
        return nummer

    # --- Einrichtung -------------------------------------------------------

    async def verify(self) -> dict[str, Any]:
        daten = await self._anfrage("GET", "/System/Info") or {}
        return {
            "name": daten.get("ServerName") or "Jellyfin",
            "version": daten.get("Version") or "",
            "machine_id": daten.get("Id") or "",
        }

    async def list_servers(self, provider_token: str) -> list[ServerCandidate]:
        """Der eine Server, mit dem gerade gesprochen wird.

        Eine Liste, weil die Schnittstelle eine verlangt - aber nie mit mehr
        als einem Eintrag. Jellyfin kennt kein Verzeichnis, ueber das sich
        weitere Server eines Kontos finden liessen; es *gibt* nur diesen einen.
        """
        daten = await self._anfrage("GET", "/System/Info/Public", token="") or {}
        kennung = str(daten.get("Id") or "")
        if not kennung:
            raise MediaServerError(f"Unter dieser Adresse meldet sich kein {self.label}-Server.")
        return [
            ServerCandidate(
                machine_id=kennung,
                name=daten.get("ServerName") or "Jellyfin",
                url=self.base_url,
                # Wer sich hier anmelden darf, benutzt diesen Server als seinen.
                # Ein "geteilt bekommen" wie bei Plex gibt es nicht.
                owned=True,
                urls=(self.base_url,),
            )
        ]

    async def probe(self, url: str, provider_token: str) -> bool:
        """Antwortet unter dieser Adresse wirklich ein Jellyfin-Server?

        ``/System/Info/Public`` braucht keine Anmeldung - der Test taugt also
        auch, bevor es ein Token gibt.
        """
        try:
            daten = await self._anfrage("GET", "/System/Info/Public", token="", basis=url)
        except MediaServerError:
            return False
        return bool(isinstance(daten, dict) and daten.get("Id"))

    # --- Anmeldung ---------------------------------------------------------

    async def begin_login(self) -> LoginChallenge:
        """Gibt es bei Jellyfin nicht.

        Der PIN-Ablauf ist auf plex.tv zugeschnitten: Jemand bestaetigt in
        seinem Browser bei einem Dritten, und Nexview fragt dort nach. Jellyfin
        hat keinen solchen Dritten - hier gehen Benutzername und Passwort
        direkt an den Server (``login_with_password``).

        Denkbar waere spaeter Jellyfins "Quick Connect": Der Server zeigt einen
        Code, den man in einer bereits angemeldeten App bestaetigt. Das passt
        genau auf diese beiden Methoden und waere die Stelle dafuer - gebaut
        ist es nicht.
        """
        raise MediaServerError(
            "Jellyfin wird mit Benutzername und Passwort verbunden, nicht über einen Code."
        )

    async def poll_login(self, ref: str, code: str = "") -> str | None:
        raise MediaServerError(
            "Jellyfin wird mit Benutzername und Passwort verbunden, nicht über einen Code."
        )

    async def login_with_password(
        self, username: str, password: str, url: str | None = None, zweck: str = ""
    ) -> tuple[str, ExternalAccount, bool]:
        """Anmelden - liefert Token, Konto und ob es ein Administrator ist.

        Das Administrator-Merkmal kommt in derselben Antwort mit, und genau
        hier wird es gebraucht: Beim Verbinden muss Nexview wissen, ob dieser
        Zugang die Bibliothek und die Konten des Servers ueberhaupt lesen darf.
        Wuerde es erst spaeter auffallen, stuende schon eine halbe Verbindung
        in der Datenbank.
        """
        if not username or not password:
            raise MediaServerError("Benutzername und Passwort werden beide gebraucht.")

        daten = await self._anfrage(
            "POST",
            "/Users/AuthenticateByName",
            token="",
            basis=url,
            zweck=zweck,
            json={"Username": username, "Pw": password},
        ) or {}

        token = str(daten.get("AccessToken") or "")
        konto = daten.get("User") or {}
        nummer = str(konto.get("Id") or "")
        if not token or not nummer:
            raise MediaServerError(f"{self.label} hat die Anmeldung nicht akzeptiert.", 401)

        richtlinie = konto.get("Policy") or {}
        return (
            token,
            ExternalAccount(
                provider=self.provider,
                account_id=nummer,
                username=konto.get("Name") or username,
                # Jellyfin kennt zu einem Konto keine E-Mail-Adresse. Siehe den
                # Hinweis am Kopf dieser Datei - das hat Folgen.
                email=None,
                thumb=None,
            ),
            bool(richtlinie.get("IsAdministrator")),
        )

    async def account_for_token(self, provider_token: str) -> ExternalAccount:
        daten = await self._anfrage("GET", "/Users/Me", token=provider_token) or {}
        nummer = str(daten.get("Id") or "")
        if not nummer:
            raise MediaServerError(f"{self.label} nennt zu diesem Zugang kein Konto.", 401)
        return ExternalAccount(
            provider=self.provider,
            account_id=nummer,
            username=daten.get("Name") or "",
            email=None,
            thumb=None,
        )

    async def user_has_server_access(self, provider_token: str) -> bool:
        """Bei Jellyfin beantwortet sich das von selbst.

        Ein Token gilt nur auf dem Server, der es ausgestellt hat. Wer eines
        hat, das hier funktioniert, hat damit auch Zugriff - anders als bei
        Plex, wo ein plex.tv-Token fuer *irgendeinen* Server gilt und deshalb
        eigens geprueft werden muss, ob es fuer diesen gilt.
        """
        try:
            await self._anfrage("GET", "/Users/Me", token=provider_token)
        except MediaServerError as exc:
            if exc.status_code == 401:
                return False
            raise
        return True

    # --- Konten und Bibliothek ---------------------------------------------

    async def list_server_users(self) -> list[ServerUser]:
        daten = await self._anfrage("GET", "/Users") or []
        return [
            ServerUser(
                account_id=str(zeile.get("Id") or ""),
                username=zeile.get("Name") or "",
                email=None,
            )
            for zeile in daten
            if zeile.get("Id")
        ]

    async def _seiten(
        self,
        params: dict[str, Any],
        token: str | None,
        hoechstens: int = SEITE_HOECHSTENS,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """``/Items`` seitenweise - mit einer Groesse, die der Server bestimmt.

        Reisst ein Haeppchen die Zeitgrenze, ist das keine Panne, sondern eine
        Auskunft: zu viel auf einmal. Also wird halbiert und **dieselbe**
        Stelle erneut gefragt - ``start`` bleibt stehen, es geht kein Titel
        verloren und keiner kommt doppelt. Die kleinere Groesse gilt danach
        fuer den Rest des Durchlaufs; der Server hat gerade gesagt, wie schnell
        er ist.

        ⚠️ **Ein Erzeuger, keine Liste.** Der Aufrufer bekommt Seite fuer Seite
        und verarbeitet jede sofort. Wer hier eine fertige Liste zurueckgibt,
        haelt bei einer grossen Bibliothek die Rohdaten *und* das Ergebnis
        gleichzeitig im Speicher - und tauscht genau das ein, was diese
        Aenderung gewinnen soll.

        ⚠️ **Nur eine Zeitueberschreitung fuehrt zum Halbieren.** Ein HTTP 500
        oder ein abgelehnter Zugang wird durchgereicht: Die Antwort kaeme bei
        25 Titeln genauso, nur viermal spaeter.
        """
        start = 0
        while True:
            groesse = min(hoechstens, self._seitengroesse)
            try:
                daten = await self._anfrage(
                    "GET",
                    "/Items",
                    token=token,
                    params={**params, "StartIndex": start, "Limit": groesse},
                    timeout=seiten_timeout(groesse),
                ) or {}
            except MediaServerError as fehler:
                if fehler.code != "mediaserver_timeout":
                    raise
                kleiner = kleineres_haeppchen(groesse)
                if kleiner is None:
                    raise MediaServerError(
                        f"Der {self.label}-Server hat auch auf kleine Abfragen "
                        f"({groesse} Titel) nicht rechtzeitig geantwortet.",
                        code="mediaserver_pages_too_slow",
                        service=self.label,
                        size=groesse,
                    ) from fehler
                logger.info(
                    "Media server %r: a page of %d titles timed out, retrying with %d",
                    self.provider,
                    groesse,
                    kleiner,
                )
                self._seitengroesse = kleiner
                continue
            seite = daten.get("Items") or []
            if seite:
                yield seite
            start += len(seite)
            gesamt = daten.get("TotalRecordCount")
            if not seite or not isinstance(gesamt, int) or start >= gesamt:
                return

    async def _titel_lesen(
        self,
        konto_id: str,
        token: str | None,
        media_type: str,
        angesehene_serien: set[str] | None = None,
    ) -> list[LibraryItem]:
        """Alle Filme oder alle Serien - seitenweise."""
        art = "Movie" if media_type == "movie" else "Series"
        # Fuer Filme die Dateiangaben (Groesse, Aufloesung), fuer Serien die
        # Zaehler - die Serie selbst hat keine Datei, aber Folgenzahlen.
        # ⚠️ ``ProductionYear`` gehoert dazu, auch wenn es nach Beiwerk aussieht.
        #
        # Ohne das Jahr steht in jedem Bibliothekseintrag ``year=None``, und
        # damit faellt der Titel-Rueckfall in ``vorhandene_kennungen`` in sich
        # zusammen: Er vergleicht Titel **und Jahr**, damit "The Lion King"
        # von 1994 nicht dasselbe ist wie das Remake von 2019. Fehlt das Jahr,
        # greift er gar nicht mehr - und Titel ohne fremde Kennung gelten als
        # nicht vorhanden. Aufgefallen beim Messen gegen Emby 4.9.5.0, wo das
        # Feld ohne ausdrueckliche Anforderung nicht mitkommt.
        felder = (
            "ProviderIds,ProductionYear,MediaSources,Width"
            if media_type == "movie"
            else "ProviderIds,ProductionYear"
        )
        werke: list[LibraryItem] = []
        async for seite in self._seiten(
            {
                "userId": konto_id,
                "Recursive": "true",
                "IncludeItemTypes": art,
                "Fields": felder,
                "EnableUserData": "true",
            },
            token,
            SEITE_FILME if media_type == "movie" else SEITE_HOECHSTENS,
        ):
            for eintrag in seite:
                werk = _als_werk(eintrag, media_type, angesehene_serien, self.provider)
                if werk is not None:
                    werke.append(werk)
        return werke

    async def _gesehenes_lesen(
        self, konto_id: str, token: str | None, art: str, felder: str
    ) -> list[dict[str, Any]]:
        """Nur das Gesehene - seitenweise, mit ``Filters=IsPlayed``.

        Der Unterschied zum Einlesen der ganzen Bibliothek ist kein feiner:
        gemessen 17 bis 20 Sekunden fuer 3529 Filme gegen den Bruchteil einer
        Sekunde fuer die zwei, die jemand gesehen hat. Und diese Abfrage faellt
        **je Konto** an - bei zwanzig Leuten waere der Unterschied der zwischen
        einer Minute und einer Viertelstunde.
        """
        treffer: list[dict[str, Any]] = []
        async for seite in self._seiten(
            {
                "userId": konto_id,
                "Recursive": "true",
                "IncludeItemTypes": art,
                "Filters": "IsPlayed",
                "Fields": felder,
                "EnableUserData": "true",
            },
            token,
        ):
            treffer.extend(seite)
        return treffer

    async def _angesehene_serien(
        self, konto_id: str, token: str | None
    ) -> dict[str, datetime | None]:
        """Serien, von denen dieses Konto **mindestens eine Folge** gesehen hat.

        Gefragt wird nach Folgen, nicht nach Serien - der Serien-Eintrag selbst
        verraet es nicht (siehe ``_als_werk``). Zurueck kommt je Serie der
        spaeteste Zeitpunkt: Wer Folge 1 im Januar und Folge 5 im Maerz gesehen
        hat, hat die Serie zuletzt im Maerz gesehen.
        """
        gesehen: dict[str, datetime | None] = {}
        for folge in await self._gesehenes_lesen(konto_id, token, "Episode", "SeriesId"):
            serie = str(folge.get("SeriesId") or "")
            if not serie:
                continue
            wann = _zeitpunkt((folge.get("UserData") or {}).get("LastPlayedDate"))
            bisher = gesehen.get(serie)
            if serie not in gesehen or (wann and (bisher is None or wann > bisher)):
                gesehen[serie] = wann
        return gesehen

    async def library_index(self) -> list[LibraryItem]:
        konto = await self._eigene_konto_id()
        angesehen = set(await self._angesehene_serien(konto, None))
        filme = await self._titel_lesen(konto, None, "movie")
        serien = await self._titel_lesen(konto, None, "tv", angesehen)
        return filme + serien

    async def watched_index(
        self, provider_token: str, account_id: str = ""
    ) -> list[WatchedRecord]:
        """Der Gesehen-Stand des Kontos hinter diesem Token.

        Dieselbe Abfrage wie fuer die Bibliothek, nur mit dem persoenlichen
        Token: Jellyfin haengt ``UserData`` immer an das Konto an, mit dessen
        Zugang gefragt wurde.
        """
        konto = account_id or await self._eigene_konto_id(provider_token)
        # ``account_id`` bleibt leer: Wessen Stand das ist, weiss der Aufrufer -
        # ihm gehoert das Token. Siehe ``watched_index`` in base.py.
        stand: list[WatchedRecord] = [
            WatchedRecord(
                account_id="",
                item_key=str(film.get("Id") or ""),
                media_type="movie",
                watched_at=_zeitpunkt((film.get("UserData") or {}).get("LastPlayedDate")),
            )
            for film in await self._gesehenes_lesen(konto, provider_token, "Movie", "")
            if film.get("Id")
        ]
        stand.extend(
            WatchedRecord(
                account_id="",
                item_key=serie,
                media_type="tv",
                watched_at=wann,
            )
            for serie, wann in (
                await self._angesehene_serien(konto, provider_token)
            ).items()
        )
        return stand

    async def watched_seasons(
        self, provider_token: str, series_keys: list[str], account_id: str = ""
    ) -> list[SeasonWatchedRecord]:
        if not series_keys:
            return []
        konto = account_id or await self._eigene_konto_id(provider_token)
        sperre = asyncio.Semaphore(PARALLELE_STAFFELN)

        async def fuer_serie(serie: str) -> list[SeasonWatchedRecord]:
            async with sperre:
                try:
                    daten = await self._anfrage(
                        "GET",
                        f"/Shows/{serie}/Seasons",
                        token=provider_token,
                        params={"userId": konto, "EnableUserData": "true"},
                    ) or {}
                except MediaServerError as exc:
                    # Eine inzwischen geloeschte Serie darf nicht den ganzen
                    # Abgleich kippen - dann fehlen eben ihre Staffel-Augen.
                    logger.info(
                        "Jellyfin: seasons of series %s could not be read (%s)",
                        serie,
                        exc.message,
                    )
                    return []
            treffer: list[SeasonWatchedRecord] = []
            for staffel in daten.get("Items") or []:
                nummer = staffel.get("IndexNumber")
                nutzerdaten = staffel.get("UserData") or {}
                if isinstance(nummer, int) and nutzerdaten.get("Played"):
                    treffer.append(SeasonWatchedRecord(item_key=serie, season=nummer))
            return treffer

        ergebnisse = await asyncio.gather(*(fuer_serie(s) for s in series_keys))
        return [eintrag for liste in ergebnisse for eintrag in liste]

    async def laufende_wiedergaben(self) -> list[Wiedergabe]:
        """Was gerade laeuft - aus ``/Sessions``.

        ⚠️ **``/Sessions`` sind Geraete, keine Wiedergaben.** Gemessen am
        30.08.2026: Jellyfin meldete vier Sitzungen und Emby fuenf, davon
        **null** mit Wiedergabe - darunter Eintraege von vor zwei Tagen, ein
        Radarr und **Nexview selbst**. Gefiltert wird deshalb auf
        ``NowPlayingItem``; nur wer eines hat, schaut wirklich.

        ⚠️ **``IsActive`` taugt dafuer nicht.** Jellyfin setzt es auch bei
        einer zwei Tage alten Sitzung auf ``true``, und Emby kennt das Feld
        gar nicht.
        """
        try:
            antwort = await self._anfrage("GET", "/Sessions")
        except MediaServerError:
            # Eine Anzeige "gerade laeuft nichts" ist besser als eine Seite,
            # die wegen einer Nebensache nicht laedt.
            return []
        if not isinstance(antwort, list):
            return []

        gefunden: list[Wiedergabe] = []
        for sitzung in antwort:
            if not isinstance(sitzung, dict):
                continue
            titel = sitzung.get("NowPlayingItem")
            if not isinstance(titel, dict):
                continue
            gefunden.append(self._als_wiedergabe(sitzung, titel))
        return gefunden

    def _als_wiedergabe(self, sitzung: dict, titel: dict) -> Wiedergabe:
        zustand = sitzung.get("PlayState") or {}
        umrechnen = sitzung.get("TranscodingInfo") or {}

        # ⚠️ **``PlayMethod`` allein reicht nicht.** Emby meldete
        # ``PlayMethod: "Transcode"`` **und** ``IsVideoDirect: true`` - Ton
        # wurde umgerechnet, das Bild durchgereicht. Als "Umrechnung" waere
        # das ein falscher Alarm ueber die CPU-Last.
        art = Umrechnung.direkt
        if str(zustand.get("PlayMethod") or "").lower() != "directplay":
            art = (
                Umrechnung.ton
                if umrechnen.get("IsVideoDirect") is True
                else Umrechnung.bild
            )

        laenge = titel.get("RunTimeTicks")
        stand = zustand.get("PositionTicks")
        fortschritt = None
        if isinstance(laenge, int) and laenge > 0 and isinstance(stand, int):
            fortschritt = max(0.0, min(1.0, stand / laenge))

        kennungen = titel.get("ProviderIds") or {}
        tmdb = kennungen.get("Tmdb") or kennungen.get("TMDB")
        try:
            tmdb_id = int(tmdb) if tmdb else None
        except (TypeError, ValueError):
            tmdb_id = None

        gruende = umrechnen.get("TranscodeReasons")
        bandbreite = umrechnen.get("Bitrate")

        return Wiedergabe(
            provider=self.provider,
            konto=str(sitzung.get("UserName") or ""),
            konto_id=str(sitzung.get("UserId") or ""),
            titel=str(titel.get("Name") or ""),
            media_type="tv" if titel.get("Type") == "Episode" else "movie",
            serie=str(titel.get("SeriesName") or ""),
            fortschritt=fortschritt,
            geraet=str(sitzung.get("DeviceName") or ""),
            anwendung=str(sitzung.get("Client") or ""),
            pausiert=bool(zustand.get("IsPaused")),
            umrechnung=art,
            grund=", ".join(str(g) for g in gruende) if isinstance(gruende, list) else "",
            beschleunigung=str(umrechnen.get("HardwareAccelerationType") or ""),
            # Der Anbieter meldet Bit je Sekunde; hier zaehlen kBit.
            bandbreite=int(bandbreite / 1000) if isinstance(bandbreite, int) else None,
            tmdb_id=tmdb_id,
        )
