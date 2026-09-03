"""Der Umzug von Seerr: verbinden, lesen, vorlegen, abschliessen.

⚠️ **Dieses Modul hat keinen eigenen Router mehr.** Es gab einmal einen unter
``/api/admin/seerr`` fuer den Umzug in eine **laufende** Anlage; der Weg ist
verworfen worden: In eine eingerichtete Anlage brachte er zu wenig, um die
Verwechslungsgefahr zu rechtfertigen - Nexview kann zwei Konten desselben
Menschen nicht wieder zusammenfuehren. Uebrig sind die drei Funktionen
darunter, und ihr einziger Aufrufer ist ``routers/setup``: Der Umzug findet
ausschliesslich bei einer **Neuinstallation** statt, vor dem ersten Konto.

Es liegt trotzdem hier und nicht unter ``services/``: Was hier steht, spricht
FastAPI und Pydantic (``HTTPException``, Eingabemodelle) - die Dienste tun das
nirgends, und ein Dienst, der Statuscodes vergibt, waere eine neue Sorte
Modul.

⚠️ **Alle Adressen sind POST, obwohl zwei davon nur lesen.** Das ist kein
Versehen und keine Bequemlichkeit: Der API-Schluessel der fremden Installation
kommt hier herein, und in einer Adresszeile stuende er im Zugriffsprotokoll
jedes Vermittlers dazwischen, im Browserverlauf und in Nexviews eigenem
Zugriffsprotokoll. Im Rumpf steht er nur dort, wo er hingehoert.

⚠️ **Der Schluessel wird nicht gespeichert.** Er lebt fuer die Dauer des
Aufrufs. Das kostet den Betreiber, dass er ihn beim naechsten Schritt noch
einmal einfuegt - und erspart Nexview, ein fremdes Generalpasswort zu
verwahren. Seerrs Schluessel ist genau das: Er handelt als Administrator und
kann sich zusaetzlich als jedes Konto ausgeben.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict

import httpx
from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import meldungen
from ..models import Blocked, ChannelKind, ChannelTarget, MediaType, Role, User
from ..schemas import SetupAdminCreate, TokenPair
from ..security import unusable_password
from ..services import (
    avatars,
    channel_targets,
    mail,
    mediaserver_accounts,
    settings_service,
    sitzung,
    tokens,
)
from ..services.mediaserver.base import ExternalAccount
from ..services.seerr import SeerrClient, SeerrFehler, Zugang, vorschau_bauen
from ..services.seerr.texte import Satz, satz
from ..services.seerr.uebernahme import NIE_DABEI, WAEHLBAR, bereiche_bauen
from ..services.seerr.vorschau import ROLLEN_BEI_FRISCHER_INSTALLATION, Kontozeile
from ..services.tmdb import TmdbClient, TmdbError

logger = logging.getLogger("nexview.seerr")


class ZugangEingabe(BaseModel):
    """Wohin und womit. Beides nur fuer diesen einen Aufruf."""

    url: str = Field(min_length=1, max_length=300)
    api_key: str = Field(min_length=1, max_length=500)


class StatusAntwort(BaseModel):
    version: str
    geprueft: bool
    hinweis: str | None = None
    commit: str | None = None


def _zugang(eingabe: ZugangEingabe) -> Zugang:
    try:
        return Zugang(basis=eingabe.url.strip(), schluessel=eingabe.api_key.strip())
    except SeerrFehler as fehler:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, fehler.als_meldung()) from fehler


def _weiterreichen(fehler: SeerrFehler) -> HTTPException:
    """Seerrs Absage als Nexview-Meldung.

    502 und nicht 500: Nicht Nexview ist kaputt, sondern das fremde System
    antwortet nicht so, wie es soll. Der Unterschied steht sonst nirgends, und
    er entscheidet, wo der Betreiber sucht.
    """
    return HTTPException(status.HTTP_502_BAD_GATEWAY, fehler.als_meldung())


async def pruefung(eingabe: ZugangEingabe) -> dict[str, object]:
    """Erreichbar, Schluessel gueltig, Fassung bekannt?

    ⚠️ **Geteilt zwischen zwei Adressen**, weil es zwei Wachen gibt und nur
    eine Wahrheit: Im Admin-Bereich verlangt der Zugang einen angemeldeten
    Administrator, in der Erst-Einrichtung gibt es noch keinen. Zwei Kopien
    liefen unweigerlich auseinander, und die Frage "was heisst geprueft"
    haette dann zwei Antworten.
    """
    zugang = _zugang(eingabe)
    client = SeerrClient(zugang)
    try:
        roh = await client.status()
        # ⚠️ **Der zweite Aufruf ist der eigentliche Test.** Seerrs
        # ``/api/v1/status`` verlangt **keine** Anmeldung: An einer echten
        # Installation gemessen (03.09.2026) antwortet es mit 200, ganz ohne
        # Schluessel und ebenso mit einem falschen. Wer nur den Status abfragt,
        # meldet also "verbunden", ohne den Schluessel je angefasst zu haben -
        # und der Betreiber sieht gruen und scheitert erst beim naechsten
        # Schritt, an einer Stelle, die nichts mehr mit dem Schluessel zu tun
        # zu haben scheint.
        #
        # ``settings/main`` ist dafuer der kleinste Aufruf, der wirklich
        # beglaubigt wird (401 ohne, 403 mit falschem Schluessel). Sein Inhalt
        # wird verworfen: Er traegt Seerrs eigenen Schluessel im Klartext, und
        # der hat weder in einer Antwort noch im Protokoll etwas zu suchen.
        await client.einstellungen()
    except SeerrFehler as fehler:
        raise _weiterreichen(fehler) from fehler

    probe = _nur_fassung(roh)
    return {
        "version": probe.fassung,
        "geprueft": probe.fassung_geprueft,
        "hinweis": probe.fassung_hinweis.als_dict() if probe.fassung_hinweis else None,
        "commit": roh.get("commitTag"),
    }


def _nur_fassung(roh: dict) -> object:
    """Die Fassungspruefung ohne alles andere.

    Dieselbe Rechnung wie in der vollen Vorschau, damit hier nicht eine
    zweite Wahrheit ueber "geprueft" entsteht.
    """
    return vorschau_bauen(
        status=roh,
        einstellungen={},
        konten=[],
        anfragen=[],
        sperrliste=[],
        meldungen=[],
        radarr=[],
        sonarr=[],
        nexview_konten=[],
    )


async def vorlage(eingabe: ZugangEingabe, db) -> dict:
    """Alles lesen und als Entscheidungsvorlage zurueckgeben.

    ⚠️ **Weigert sich bei einer Fassung, die nicht geprueft ist**, und liest
    die fremde Instanz dann gar nicht erst leer. Der Umzug koennte
    weiterlaufen und wuerde vermutlich das Richtige tun. Aber wenn nicht,
    entstehen Konten und Anfragen, die niemand mehr auseinandersortiert - und
    Nexview kann zwei Konten nicht zusammenfuehren.
    """
    zugang = _zugang(eingabe)
    client = SeerrClient(zugang)

    try:
        roh = await client.status()
        probe = _nur_fassung(roh)
        if not probe.fassung_geprueft:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "seerr_version_unknown",
                    "message": (
                        probe.fassung_hinweis.text
                        if probe.fassung_hinweis
                        else "Diese Seerr-Fassung ist nicht geprüft."
                    ),
                    "version": probe.fassung,
                },
            )

        einstellungen = await client.einstellungen()
        konten = await client.konten()
        anfragen = await client.anfragen()
        sperrliste = await client.sperrliste()
        meldungen_ = await client.meldungen()
        radarr = await client.radarr()
        sonarr = await client.sonarr()
        plex = await client.plex()
        jellyfin = await client.jellyfin()
        mailserver = await client.mail()
        agenten = await client.meldewege()
    except SeerrFehler as fehler:
        raise _weiterreichen(fehler) from fehler

    nexview_konten = list(
        db.scalars(
            select(User)
            .options(selectinload(User.mediaserver_accounts))
            .where(User.is_active.is_(True))
            .order_by(User.username)
        )
    )

    # ⚠️ **Immer eine frische Installation, und das ist keine Annahme.** Der
    # einzige Weg hierher fuehrt ueber ``/api/setup/seerr/vorschau``, und die
    # Adresse ist durch ``_nur_vor_der_einrichtung`` gesperrt, sobald **ein**
    # Konto existiert. Der Umzug in eine laufende Anlage war einmal geplant
    # und ist verworfen worden: Er kostete mehr, als er einbrachte.
    #
    # Daran haengt, dass Rollen mitkommen duerfen. Wer gerade erst einrichtet,
    # muesste sonst von Hand nachbauen, was er drueben ueber Jahre vergeben
    # hat. Der Betreiber-Haken bleibt trotzdem draussen - er gehoert dem, der
    # hier gerade sitzt.
    ergebnis = vorschau_bauen(
        frische_installation=True,
        status=roh,
        einstellungen=einstellungen,
        konten=konten,
        anfragen=anfragen,
        sperrliste=sperrliste,
        meldungen=meldungen_,
        radarr=radarr,
        sonarr=sonarr,
        nexview_konten=nexview_konten,
    )
    # ⚠️ **Die Werte der Bereiche bleiben hier.** Sie tragen das SMTP-Passwort
    # und die Schluessel von Radarr und Sonarr im Klartext; nach aussen gehen
    # nur die Anzeigezeilen, in denen ein Geheimnis als "gesetzt (n Zeichen)"
    # steht. Wer sie doch mitschicken will, muss diese Zeilen aendern - und das
    # faellt in einer Durchsicht auf.
    bereiche = bereiche_bauen(
        main=einstellungen,
        plex=plex,
        jellyfin=jellyfin,
        radarr=radarr,
        sonarr=sonarr,
        email=mailserver,
        sperrliste=sperrliste,
        agenten=agenten,
    )

    # ⚠️ ``asdict`` traegt nur Felder, keine Eigenschaften. Die drei
    # abgeleiteten Zahlen muessen deshalb ausdruecklich mit - sie sind der
    # Grund, warum die Vorschau ueberhaupt Zahlen zeigt, und waeren sonst
    # stillschweigend nicht in der Antwort.
    return {
        **asdict(ergebnis),
        "konten_neu": ergebnis.konten_neu,
        "konten_verknuepft": ergebnis.konten_verknuepft,
        "anfragen_uebernehmbar": ergebnis.anfragen_uebernehmbar,
        "bereiche": [
            {
                "kennung": b.kennung,
                "anbieter": b.anbieter,
                "zeilen": [_zeile(w, v) for w, v in b.zeilen],
                "luecken": [l.als_dict() for l in b.luecken],
                "leer": b.leer,
                "posten": [
                    {
                        "kennung": p.kennung,
                        "beschriftung": p.beschriftung.als_dict(),
                        "zeilen": [_zeile(w, v) for w, v in p.zeilen],
                    }
                    for p in b.posten
                ],
                "eintraege": len(b.eintraege),
                # ⚠️ Enthaelt bewusst kein Geheimnis: Art, Name, Adresse und
                # Server-Kennung. Das Verbinden danach fuellt sein Formular
                # daraus vor - aus ``zeilen`` ginge das nicht, dort steht die
                # Kennung abgekuerzt.
                "verbindung": b.verbindung,
            }
            for b in bereiche
        ],
        "nie_dabei": [n.als_dict() for n in NIE_DABEI],
    }


def _zeile(was: Satz, wert: object) -> dict[str, object]:
    """Eine Anzeigezeile nach aussen: Beschriftung als Satz, Wert roh oder als Satz."""
    return {"was": was.als_dict(), "wert": wert.als_dict() if isinstance(wert, Satz) else wert}


# --------------------------------------------------------------------------
# Abschliessen: alles in einem Zug
# --------------------------------------------------------------------------


class KontoWunsch(BaseModel):
    """Ein Seerr-Konto, das mitkommen soll - und als was."""

    seerr_id: int
    #: ⚠️ **Vorgabe Nutzer, und mehr nur auf ausdrueckliche Wahl.** Bei einer
    #: frischen Installation duerfen Administrator und Entscheider mitkommen
    #: (``ROLLEN_BEI_FRISCHER_INSTALLATION``); vorausgewaehlt ist trotzdem
    #: nichts davon. Die Oberflaeche zeigt Seerrs Rolle als Hinweis daneben,
    #: setzt sie aber nicht ein.
    rolle: Role = Role.user

    @field_validator("rolle")
    @classmethod
    def _nur_erlaubte_rollen(cls, wert: Role) -> Role:
        if wert not in ROLLEN_BEI_FRISCHER_INSTALLATION:
            raise ValueError("Diese Rolle kann ein Umzug nicht vergeben.")
        return wert


class BesitzerEingabe(SetupAdminCreate):
    """Der Besitzer: dieselben Felder wie beim ersten Administrator, plus
    die Zeile in Seerr, aus der Anzeigename und Medienserver-Kennung kommen."""

    seerr_id: int


class AbschlussEingabe(ZugangEingabe):
    """Alles, was der Assistent am Ende zusammen abgibt.

    ⚠️ **Von aussen kommen Namen, Nummern und die Eingaben des Betreibers -
    keine Werte aus Seerr.** Die Bereiche sind Namen, die Konten sind Seerrs
    Nummern; was dahintersteht (SMTP-Passwort, Arr-Schluessel, Adressen,
    Kontingente) holt der Server ein zweites Mal bei Seerr. Dieselbe Regel
    wie bei der Vorschau, aus demselben Grund: Nichts davon soll im Browser
    liegen.
    """

    bereiche: list[str] = Field(default_factory=list)
    besitzer: BesitzerEingabe
    konten: list[KontoWunsch] = Field(default_factory=list)
    #: Die zwei Werte, die Seerr nicht hat und der Betreiber selbst tippt.
    tmdb_api_key: str = Field(default="", max_length=200)
    public_url: str = Field(default="", max_length=255)


class AbschlussAntwort(TokenPair):
    """Die Sitzung des frisch angelegten Besitzers, dazu der Bericht.

    ⚠️ **Die Sitzung ist Teil der Antwort, nicht ein zweiter Aufruf.** Nach
    diesem Aufruf ist die Einrichtung zu (``has_any_user``); die Adressen
    darunter antworten mit 409. Der Assistent laeuft aber weiter - der
    Medienserver wird erst jetzt verbunden, mit genau dieser Sitzung.
    """

    bericht: dict


def _benutzername_aus(anzeigename: str) -> str:
    """Aus einem Seerr-Anzeigenamen einen Benutzernamen, den Nexview annimmt.

    ⚠️ **Der Zwilling von ``benutzernameAus`` in ``seerr-umzug-typen.ts``.**
    Dort macht die Oberflaeche den Vorschlag fuer den Besitzer, den der
    Betreiber noch ueberschreiben kann; hier entsteht der Name der uebrigen
    Konten, ohne dass jemand ihn sieht, bevor er dasteht. Beide muessen aus
    "Kim Beispiel" dasselbe machen, sonst heisst der Besitzer anders als sein
    Nachbar mit demselben Muster.

    Aus "Jürgen Müller" wird ``Jurgen.Muller``, aus "🎬" wird nichts - dann
    greift ``_unique_username`` mit seinem Rueckfall.

    Die Zerlegung (NFKD) macht aus "ü" ein "u" plus Akzentzeichen; den Akzent
    nimmt der Zeichenfilter darunter mit. Ohne die Zerlegung filtert er das
    ganze "ü" weg, und aus Juergen wird ``Jrgen``.
    """
    text = unicodedata.normalize("NFKD", anzeigename or "")
    text = text.replace("ß", "ss")
    text = re.sub(r"\s+", ".", text)
    text = re.sub(r"[^A-Za-z0-9._-]", "", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"^[._-]+|[._-]+$", "", text)
    return text[:32] if len(text) >= 3 else ""


def _persoenliches(konto: User, einstellungen: dict) -> None:
    """Region und Sprache, so wie die Person sie drueben selbst gewaehlt hat.

    ⚠️ **Die Feldnamen sind an Seerrs Quelle geprueft** (``server/routes/
    user/usersettings.ts``, ``GET /main``, 03.09.2026): Die Region heisst
    ``discoverRegion``, nicht ``region`` - der erste Entwurf hatte das
    geraten, und die Attrappe im Test hatte denselben Fehler, also fiel es
    nicht auf. Daneben gibt es ``streamingRegion`` (wo jemand streamt) und
    ``originalLanguage``; beides hat in Nexview kein eigenes Feld und bleibt
    draussen.

    ⚠️ **Nur was gesetzt ist, und nur was Nexview kennt.** ``discoverRegion``
    ist ein Laendercode oder leer (dann gilt drueben die Hausvorgabe, hier
    auch); ``locale`` kennt 39 Sprachen, Nexview zwei. Alles andere bleibt
    leer, und Nexview fragt beim ersten Anmelden nach - genau wie bei jedem
    anderen Konto ohne eigene Region.
    """
    region = str(einstellungen.get("discoverRegion") or "").strip().upper()
    if len(region) == 2 and region.isalpha():
        konto.discover_region = region
    sprache = str(einstellungen.get("locale") or "").split("-")[0].lower()
    if sprache in ("de", "en"):
        konto.language = sprache


def _verknuepfen(konto: User, zeile: Kontozeile, *, mit_adresse: bool) -> str:
    """Die Medienserver-Kennung aus Seerr an das neue Konto haengen.

    Rueckgabe ist der Weg, auf dem die Person spaeter hereinkommt - fuer den
    Bericht: ``plex``, ``jellyfin``, ``emby`` oder ``kennwort``.

    ⚠️ **Bei Plex ist die Kennung sicher, bei Jellyfin und Emby nicht.** Seerrs
    ``plexId`` stammt von plex.tv, genau wie Nexviews Verknuepfung. Die
    Jellyfin-Kennung ist serverbezogen, und Seerr notiert nicht, welcher
    Server gemeint war - verbindet der Betreiber danach einen anderen, zeigt
    die Verknuepfung ins Leere. Der Bericht sagt das.

    ⚠️ **Die Adresse geht nur bei Plex mit in die Verknuepfung.** ``link``
    setzt ``email_verified``, sobald eine Adresse dabei ist, mit der
    Begruendung "der Anbieter hat sie geprueft". Das stimmt fuer plex.tv und
    fuer sonst niemanden: Bei einem lokalen Seerr-Konto hat ein Administrator
    die Adresse eingetippt, bei Jellyfin und Emby ebenso. Diese Konten bleiben
    unbestaetigt, und "Kennwort vergessen" bestaetigt sie nebenbei.
    """
    if zeile.herkunft == "lokal" or not zeile.anbieter_kennung:
        return "kennwort"
    mediaserver_accounts.link(
        konto,
        ExternalAccount(
            provider=zeile.herkunft,
            account_id=zeile.anbieter_kennung,
            username=zeile.anzeigename,
            email=zeile.email if (mit_adresse and zeile.herkunft == "plex") else None,
            thumb=None,
        ),
    )
    return zeile.herkunft


async def abschliessen(
    eingabe: AbschlussEingabe,
    request: Request,
    response: Response,
    db,
    *,
    besitzer_bauen: Callable[[SetupAdminCreate], User],
) -> AbschlussAntwort:
    """Einstellungen, Besitzer und Konten in **einer** Transaktion schreiben.

    ⚠️ **Warum ein Aufruf und keine Kette - die Entscheidung, an der dieses
    Feature haengt.** Drei Adressen mit drei verschiedenen Wachen standen zur
    Wahl: ``/api/setup/seerr/*`` und ``/api/setup/admin`` sind offen, solange
    kein Konto existiert, und danach zu; das Verbinden des Medienservers
    verlangt einen angemeldeten Administrator. Eine Kette "Einstellungen,
    dann Besitzer" haette ein Fenster, in dem das SMTP-Passwort und die
    Arr-Schluessel in der Datenbank stehen und niemandem gehoeren - und wenn
    der zweite Schritt scheitert (Netz weg, Browser zu, Tippfehler, den der
    Server anders prueft als die Oberflaeche), bleibt es dabei. Der Assistent
    saesse dann fest: ``uebernehmen`` ginge noch einmal und schriebe doppelte
    Kanaele, ``admin`` ginge, aber niemand wuesste, was schon geschrieben ist.

    Deshalb: **erst alles pruefen, dann alles holen, dann alles schreiben,
    dann ein Commit.** Scheitert irgendetwas davor, steht nichts in der
    Datenbank, und der Betreiber sieht die Meldung im Assistenten, der noch
    genau dort steht. Scheitert der Commit selbst, ebenso. Erst nach dem
    Commit beginnt die Sitzung; geht **die** verloren (Antwort kommt nicht
    an), existiert ein Besitzer mit Kennwort, der sich anmelden kann, und
    der Rest des Weges (Medienserver) steht in den Einstellungen. Es gibt
    keinen Zustand, aus dem es weder vor noch zurueck geht.

    ⚠️ **Der Medienserver ist bewusst nicht dabei.** Plex braucht ein
    Code-Verfahren bei plex.tv, Jellyfin und Emby das Passwort eines
    Server-Administrators - beides kann nur der Mensch liefern, und beides
    braucht die Sitzung, die hier erst entsteht. Der Assistent fragt es als
    naechsten Schritt ab, mit genau dieser Sitzung.

    ⚠️ **Die Werte werden hier zum zweiten Mal geholt und nicht vom Browser
    entgegengenommen.** Der naheliegende Entwurf waere gewesen, die Vorschau
    zurueckschicken zu lassen - dann stuenden das SMTP-Passwort, die
    Arr-Schluessel und die Kontingente aller Leute im Browser, im Verlauf und
    in jedem Zwischenspeicher auf dem Weg. Von aussen kommen Bereichsnamen,
    Seerr-Nummern und das, was der Betreiber selbst getippt hat.
    """
    # ---- 1. Pruefen, was sich ohne Netz pruefen laesst -------------------
    unbekannt = [b for b in eingabe.bereiche if b not in WAEHLBAR]
    if unbekannt:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            meldungen.meldung("seerr_area_unknown", f"Unbekannter Bereich: {unbekannt[0]}"),
        )
    if not mail.valid_address(eingabe.besitzer.email):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            meldungen.meldung("email_invalid", "Das ist keine gültige E-Mail-Adresse."),
        )
    gewuenscht = {k.seerr_id: k for k in eingabe.konten}
    if eingabe.besitzer.seerr_id in gewuenscht:
        # Der Besitzer entsteht aus seiner Zeile; dieselbe Zeile noch einmal
        # als gewoehnliches Konto anzulegen gaebe einen Menschen zweimal.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            meldungen.meldung(
                "seerr_owner_in_list",
                "Der Besitzer steht auch in der Liste der übrigen Konten.",
            ),
        )
    adresse_aussen = eingabe.public_url.strip().rstrip("/")
    if adresse_aussen and not adresse_aussen.startswith(("http://", "https://")):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            meldungen.meldung(
                "public_url_invalid",
                "Die Adresse nach außen muss mit http:// oder https:// beginnen.",
            ),
        )

    # ---- 2. Alles holen, bevor irgendetwas geschrieben wird --------------
    zugang = _zugang(eingabe)
    client = SeerrClient(zugang)
    try:
        roh = await client.status()
        if not _nur_fassung(roh).fassung_geprueft:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "seerr_version_unknown",
                    "message": "Diese Seerr-Fassung ist nicht geprüft.",
                },
            )
        einstellungen = await client.einstellungen()
        radarr = await client.radarr()
        sonarr = await client.sonarr()
        plex = await client.plex()
        jellyfin = await client.jellyfin()
        mailserver = await client.mail()
        sperrliste = await client.sperrliste()
        agenten = await client.meldewege()
        seerr_konten = await client.konten()
        # Region und Sprache je Konto - fuer den Besitzer und jedes gewaehlte.
        # Ein Aufruf je Konto, vor dem Schreiben wie alles andere auch.
        persoenlich = {
            nummer: await client.konto_einstellungen(nummer)
            for nummer in (eingabe.besitzer.seerr_id, *gewuenscht)
        }
    except SeerrFehler as fehler:
        raise _weiterreichen(fehler) from fehler

    zeilen = {
        zeile.seerr_id: zeile
        for zeile in vorschau_bauen(
            frische_installation=True,
            status=roh,
            einstellungen=einstellungen,
            konten=seerr_konten,
            anfragen=[],
            sperrliste=[],
            meldungen=[],
            radarr=[],
            sonarr=[],
            nexview_konten=[],
        ).konten
    }
    fehlend = [nummer for nummer in (eingabe.besitzer.seerr_id, *gewuenscht) if nummer not in zeilen]
    if fehlend:
        # Zwischen Vorschau und Abschluss hat drueben jemand ein Konto
        # geloescht - oder die Nummer ist erfunden. Beides bricht ab, bevor
        # etwas geschrieben ist; der Betreiber liest die Vorschau neu.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            meldungen.meldung(
                "seerr_account_unknown",
                "Dieses Konto gibt es in Seerr nicht (mehr).",
                seerr_id=fehlend[0],
            ),
        )

    bereiche = bereiche_bauen(
        main=einstellungen,
        plex=plex,
        jellyfin=jellyfin,
        radarr=radarr,
        sonarr=sonarr,
        email=mailserver,
        sperrliste=sperrliste,
        agenten=agenten,
    )
    gewaehlt = set(eingabe.bereiche)
    aenderungen: dict[str, object] = {}
    sperren: list[dict] = []
    kanaele_roh: list[dict] = []
    for bereich in bereiche:
        if bereich.kennung in gewaehlt:
            aenderungen.update(bereich.werte)
            sperren.extend(bereich.eintraege)
        # ⚠️ Posten werden **einzeln** gewaehlt, nicht mit ihrem Bereich. Wer
        # Radarr will und Sonarr nicht, haekt genau einen an; ein Bereichshaken
        # waere hier alles oder nichts.
        for posten in bereich.posten:
            if posten.kennung not in gewaehlt:
                continue
            aenderungen.update(posten.werte)
            if posten.kanal:
                kanaele_roh.append(posten.kanal)

    tmdb_schluessel = eingabe.tmdb_api_key.strip()
    if tmdb_schluessel:
        # ⚠️ **Vor dem Schreiben, nicht danach.** Der normale Assistent prueft
        # den Schluessel mit einem eigenen Knopf; dieser hier hat den Knopf
        # nicht, also prueft der Abschluss. Ein falscher Schluessel fiele
        # sonst erst auf, wenn die erste Suche leer bleibt - Wochen spaeter,
        # ohne Zusammenhang zu diesem Formular.
        probe = TmdbClient(
            tmdb_schluessel,
            str(aenderungen.get("default_language") or "de"),
            str(aenderungen.get("default_region") or "DE"),
        )
        try:
            await probe.verify()
        except TmdbError as fehler:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                meldungen.meldung("tmdb_key_rejected", fehler.message),
            ) from fehler
        aenderungen["tmdb_api_key"] = tmdb_schluessel
    if adresse_aussen:
        aenderungen["public_url"] = adresse_aussen

    # ---- 3. Schreiben, in einer Transaktion ------------------------------
    besitzer_zeile = zeilen[eingabe.besitzer.seerr_id]
    sprache = str(aenderungen.get("default_language") or eingabe.besitzer.language or "de")
    #: (Seerr-Nummer, Zeile, Nexview-Nummer, Rolle, Weg) je angelegtem Konto.
    angelegt: list[tuple[int, Kontozeile, int, Role, str]] = []
    abgelehnt: list[dict[str, object]] = []
    try:
        if aenderungen:
            settings_service.save_settings(db, aenderungen, commit=False)
        gesperrt = _sperren_anlegen(db, sperren)
        kanaele = 0
        for nutzlast in kanaele_roh:
            kanaele += _kanal_anlegen(db, nutzlast)

        # Der Besitzer: dieselbe Zeile wie ``/api/setup/admin`` - mit
        # Betreiber-Haken, ohne bestaetigte Adresse. Anzeigename aus Seerr,
        # falls der Betreiber keinen getippt hat.
        besitzer = besitzer_bauen(
            SetupAdminCreate(
                username=eingabe.besitzer.username,
                password=eingabe.besitzer.password,
                email=eingabe.besitzer.email,
                display_name=eingabe.besitzer.display_name or besitzer_zeile.anzeigename,
                language=eingabe.besitzer.language,
            )
        )
        _persoenliches(besitzer, persoenlich.get(eingabe.besitzer.seerr_id) or {})
        db.add(besitzer)
        db.flush()
        besitzer_weg = _verknuepfen(besitzer, besitzer_zeile, mit_adresse=False)

        vergeben_mail = {tokens.normalize_email(eingabe.besitzer.email)}
        for nummer, wunsch in gewuenscht.items():
            zeile = zeilen[nummer]
            adresse = tokens.normalize_email(zeile.email) if zeile.email else None
            if adresse and adresse in vergeben_mail:
                # Zwei Seerr-Konten mit derselben Adresse sind in Seerr
                # unmoeglich; die Kollision ist der Besitzer selbst, der
                # seine Adresse in ein anderes Konto getippt hat. Nexview
                # fuehrt Adressen eindeutig, also bleibt die Zeile draussen -
                # mit Grund, nicht still.
                abgelehnt.append(
                    {
                        "seerr_id": nummer,
                        "anzeigename": zeile.anzeigename,
                        "grund": satz("adresse_vergeben").als_dict(),
                    }
                )
                continue
            konto = User(
                username=mediaserver_accounts._unique_username(
                    db, _benutzername_aus(zeile.anzeigename)
                ),
                # Kein Kennwort: Seerr gibt keines heraus. Plex-Konten kommen
                # ueber Plex herein, alle anderen ueber "Kennwort vergessen"
                # oder ein Kennwort vom Administrator - dieselbe Lage wie bei
                # einem Konto aus dem Medienserver-Import.
                password_hash=unusable_password(),
                email=adresse,
                email_verified=False,
                role=wunsch.rolle,
                display_name=zeile.anzeigename,
                language=sprache,
                auto_approve=False,
                is_active=True,
                # Die Stueckzahlen aus Seerr, schon umgerechnet: die Null ist
                # hier bereits UNBEGRENZT (``kontingent_aus_seerr``). Der
                # Speicher bleibt auf Hausvorgabe - drueben gab es keinen.
                quota_movies_limit=zeile.kontingent_filme,
                quota_series_limit=zeile.kontingent_serien,
            )
            _persoenliches(konto, persoenlich.get(nummer) or {})
            db.add(konto)
            db.flush()
            if adresse:
                vergeben_mail.add(adresse)
            weg = _verknuepfen(konto, zeile, mit_adresse=True)
            angelegt.append((nummer, zeile, konto.id, wunsch.rolle, weg))

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        # ⚠️ Alles oder nichts - auch bei einem Fehler, den niemand
        # vorhergesehen hat. Ohne diese Zeile bliebe die Sitzung in einem
        # halb geschriebenen Zustand haengen, bis sie geschlossen wird.
        db.rollback()
        raise

    db.refresh(besitzer)

    # ---- 4. Nach dem Commit: die Profilbilder ----------------------------
    # Bewusst **hinter** der Transaktion. Jedes Bild ist ein Abruf bei einem
    # fremden Dienst mit eigener Wartezeit; waehrenddessen die Datenbank
    # offenzuhalten hiesse, dass ein langsames plex.tv den ganzen Umzug
    # verzoegert - und ein fehlendes Bild darf ihn ohnehin nicht scheitern
    # lassen. Geht hier etwas schief, fehlt ein Bild, sonst nichts.
    mit_bild = await _bilder_uebernehmen(
        db,
        [(besitzer.id, besitzer_zeile.bild)]
        + [(konto_id, zeile.bild) for _, zeile, konto_id, _, _ in angelegt],
    )

    # ⚠️ Zaehlungen und Bereichsnamen, keine Werte. Siehe Kopf von uebernahme.py.
    logger.info(
        "Seerr migration finished: owner=%r areas=%s fields=%d blocked=%d channels=%d "
        "accounts=%d rejected=%d avatars=%d",
        besitzer.username,
        ",".join(sorted(gewaehlt)) or "-",
        len(aenderungen),
        gesperrt,
        kanaele,
        len(angelegt),
        len(abgelehnt),
        len(mit_bild),
    )

    def bild_stand(konto_id: int, zeile: Kontozeile) -> str:
        # Drei Antworten, weil "kein Bild" zwei Gruende hat: Seerr hatte
        # keines, oder es liess sich nicht holen. Nur das zweite ist eine
        # Nachricht an den Betreiber.
        if konto_id in mit_bild:
            return "uebernommen"
        return "nicht_geladen" if zeile.bild else "keins"

    bericht = {
        "besitzer": {
            "username": besitzer.username,
            "email": besitzer.email,
            "zugang": besitzer_weg,
            "bild": bild_stand(besitzer.id, besitzer_zeile),
        },
        "konten": [
            {
                "seerr_id": nummer,
                "anzeigename": zeile.anzeigename,
                "username": db.get(User, konto_id).username,
                "email": db.get(User, konto_id).email,
                "rolle": rolle.value,
                "zugang": weg,
                "bild": bild_stand(konto_id, zeile),
            }
            for nummer, zeile, konto_id, rolle, weg in angelegt
        ],
        "abgelehnt": abgelehnt,
        "bereiche": sorted(gewaehlt),
        "felder": len(aenderungen),
        "gesperrt": gesperrt,
        "kanaele": kanaele,
        "bilder": len(mit_bild),
        "tmdb": bool(tmdb_schluessel),
        "public_url": bool(adresse_aussen),
        "nie_dabei": [n.als_dict() for n in NIE_DABEI],
    }
    # Erst jetzt, nach dem Commit: Die Sitzung gehoert zu einem Konto, das es
    # wirklich gibt.
    paar = sitzung.starten(response, request, besitzer)
    return AbschlussAntwort(
        access_token=paar.access_token, expires_in=paar.expires_in, bericht=bericht
    )


#: Kurz: Ein Bild, das nach zehn Sekunden nicht da ist, kommt nicht mehr.
BILD_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def _bild_laden(adresse: str) -> bytes | None:
    """Ein Profilbild holen - oder ehrlich ``None``.

    ⚠️ **Das ist der eine Abruf dieses Features bei einem Dritten**, und er
    ist gewollt: Am 03.09.2026 wurde entschieden, dass die Bilder mitkommen,
    nachdem der Bauplan sie zunaechst nur anzeigen wollte. Die
    Adressen stammen aus Seerr und liegen (gemessen) bei plex.tv oder
    gravatar.com; Nexview holt sie einmal, beim Abschluss, und legt sie als
    eigene Datei ab. Danach fragt niemand mehr dort nach.

    Kein Fehler dringt nach oben: Ein Bild, das nicht kommt, ist ein
    fehlendes Bild und kein gescheiterter Umzug.
    """
    try:
        async with httpx.AsyncClient(timeout=BILD_TIMEOUT, follow_redirects=True) as client:
            antwort = await client.get(adresse)
    except httpx.HTTPError:
        return None
    if antwort.status_code != 200 or len(antwort.content) > avatars.MAX_BYTES:
        return None
    return antwort.content


async def _bilder_uebernehmen(db, paare: list[tuple[int, str | None]]) -> set[int]:
    """Bilder holen und als Datei ablegen. Rueckgabe: die Konten, die eines haben.

    Laeuft nach dem Commit des Umzugs und schreibt in einer eigenen, kleinen
    Transaktion nur ``avatar_path``. Ein Bild, das nicht laedt oder kein Bild
    ist (``avatars.save`` prueft den Inhalt, nicht die Endung), wird
    uebersprungen und gezaehlt, nicht gemeldet.
    """
    geschafft: set[int] = set()
    for konto_id, adresse in paare:
        if not adresse:
            continue
        inhalt = await _bild_laden(adresse)
        if inhalt is None:
            continue
        konto = db.get(User, konto_id)
        if konto is None:
            continue
        try:
            konto.avatar_path = avatars.save(inhalt, konto.avatar_path)
        except avatars.AvatarError:
            continue
        geschafft.add(konto_id)
    if geschafft:
        db.commit()
    return geschafft


def _kanal_anlegen(db, nutzlast: dict) -> int:
    """Einen Meldeweg als ``ChannelTarget`` anlegen - ohne Commit.

    ⚠️ **Zwei Ebenen, wo der Dienst sie hat.** Bei Telegram traegt die Instanz
    das Token und das Postfach den Chat, bei ntfy die Instanz die Adresse und
    das Postfach das Topic. ``channel_targets.anwenden`` schreibt je Ebene nur
    deren eigene Felder und verschluesselt dabei die Geheimnisse - deshalb geht
    es durch diese Funktion und nicht ueber ein direktes ``setattr``.

    ⚠️ **Was hier NICHT entsteht, ist ein Abonnement.** Welche Meldung ueber
    welchen Kanal geht, bleibt unbestimmt: Nexview kennt andere Arten als Seerr
    (Rueckmeldungen, Ticketcenter, Speicher), und eine Abbildung waere zur
    Haelfte geraten. Der Betreiber stellt es danach selbst ein.

    ⚠️ **Nur ``flush``, kein ``commit``.** Der Aufrufer schreibt alles in einer
    Transaktion; ein Commit hier wuerde die Zusage "alles oder nichts" genau
    an dieser Stelle brechen.
    """
    art = ChannelKind(str(nutzlast["art"]))
    eltern = ChannelTarget(channel=art, name=f"Aus Seerr ({art.value})")
    channel_targets.anwenden(eltern, dict(nutzlast.get("eltern") or {}))
    db.add(eltern)
    db.flush()
    angelegt = 1
    kind = dict(nutzlast.get("kind") or {})
    if kind:
        unten = ChannelTarget(channel=art, name="Aus Seerr", parent_id=eltern.id)
        channel_targets.anwenden(unten, kind)
        db.add(unten)
        angelegt += 1
    db.flush()
    return angelegt


def _sperren_anlegen(db, eintraege: list[dict]) -> int:
    """Die Sperrliste anlegen, ohne Doppelte - und ohne Commit.

    ⚠️ ``blocked_by`` bleibt leer. Nexview laesst das ausdruecklich zu: Die
    Sperre ist eine Entscheidung ueber den Titel, nicht ueber eine Person - und
    beim Umzug gibt es ohnehin noch kein Konto, auf das man zeigen koennte.
    """
    if not eintraege:
        return 0
    vorhanden = {
        (zeile.media_type.value, zeile.tmdb_id)
        for zeile in db.scalars(select(Blocked))
    }
    angelegt = 0
    for eintrag in eintraege:
        schluessel = (eintrag["media_type"], eintrag["tmdb_id"])
        if schluessel in vorhanden:
            continue
        db.add(
            Blocked(
                media_type=MediaType(eintrag["media_type"]),
                tmdb_id=int(eintrag["tmdb_id"]),
                title=str(eintrag["title"]),
            )
        )
        vorhanden.add(schluessel)
        angelegt += 1
    db.flush()
    return angelegt
