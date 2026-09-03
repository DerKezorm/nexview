"""Der Umzug von Seerr: verbinden, lesen, vorlegen, uebernehmen.

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
from dataclasses import asdict

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..models import Blocked, ChannelKind, ChannelTarget, MediaType, User
from ..services import channel_targets, settings_service
from ..services.seerr import SeerrClient, SeerrFehler, Zugang, vorschau_bauen
from ..services.seerr.uebernahme import NIE_DABEI, WAEHLBAR, bereiche_bauen

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
        "hinweis": probe.fassung_hinweis,
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
                    "message": probe.fassung_hinweis
                    or "Diese Seerr-Fassung ist nicht geprüft.",
                    "version": probe.fassung,
                },
            )

        einstellungen = await client.einstellungen()
        konten = await client.konten()
        anfragen = await client.anfragen()
        sperrliste = await client.sperrliste()
        meldungen = await client.meldungen()
        radarr = await client.radarr()
        sonarr = await client.sonarr()
        plex = await client.plex()
        jellyfin = await client.jellyfin()
        mail = await client.mail()
        sperrliste = await client.sperrliste()
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
        meldungen=meldungen,
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
        email=mail,
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
                "zeilen": [{"was": w, "wert": v} for w, v in b.zeilen],
                "luecken": b.luecken,
                "leer": b.leer,
                "posten": [
                    {
                        "kennung": p.kennung,
                        "beschriftung": p.beschriftung,
                        "zeilen": [{"was": w, "wert": v} for w, v in p.zeilen],
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
        "nie_dabei": list(NIE_DABEI),
    }


class UebernahmeEingabe(ZugangEingabe):
    """Welche Bereiche der Betreiber ausgewaehlt hat."""

    bereiche: list[str] = Field(default_factory=list)


async def uebernehmen(eingabe: UebernahmeEingabe, db) -> dict:
    """Die gewaehlten Bereiche wirklich in die Einstellungen schreiben.

    ⚠️ **Nur Einstellungen, keine Konten und keine Anfragen.** Der Schritt, der
    Konten anlegt, ist ein anderer und steht noch nicht.

    ⚠️ **Die Werte werden hier zum zweiten Mal geholt und nicht vom Browser
    entgegengenommen.** Der naheliegende Entwurf waere gewesen, die Vorschau
    zurueckschicken zu lassen - dann stuenden das SMTP-Passwort und die
    Schluessel von Radarr und Sonarr im Browser, im Verlauf und in jedem
    Zwischenspeicher auf dem Weg. Sie bleiben stattdessen auf dem Server; von
    aussen kommt nur die Liste der Bereichsnamen.
    """
    unbekannt = [b for b in eingabe.bereiche if b not in WAEHLBAR]
    if unbekannt:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "seerr_area_unknown", "message": f"Unbekannter Bereich: {unbekannt[0]}"},
        )

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
        mail = await client.mail()
        sperrliste = await client.sperrliste()
        agenten = await client.meldewege()
    except SeerrFehler as fehler:
        raise _weiterreichen(fehler) from fehler

    bereiche = bereiche_bauen(
        main=einstellungen,
        plex=plex,
        jellyfin=jellyfin,
        radarr=radarr,
        sonarr=sonarr,
        email=mail,
        sperrliste=sperrliste,
        agenten=agenten,
    )
    gewaehlt = set(eingabe.bereiche)
    aenderungen: dict[str, object] = {}
    gesperrt = 0
    kanaele = 0
    for bereich in bereiche:
        if bereich.kennung in gewaehlt:
            aenderungen.update(bereich.werte)
            gesperrt += _sperren_anlegen(db, bereich.eintraege)
        # ⚠️ Posten werden **einzeln** gewaehlt, nicht mit ihrem Bereich. Wer
        # Radarr will und Sonarr nicht, haekt genau einen an; ein Bereichshaken
        # waere hier alles oder nichts.
        for posten in bereich.posten:
            if posten.kennung not in gewaehlt:
                continue
            aenderungen.update(posten.werte)
            if posten.kanal:
                kanaele += _kanal_anlegen(db, posten.kanal)

    if aenderungen:
        settings_service.save_settings(db, aenderungen)

    # ⚠️ Zaehlungen und Bereichsnamen, keine Werte. Siehe Kopf von uebernahme.py.
    logger.info(
        "Seerr takeover applied: areas=%s fields=%d blocked=%d channels=%d",
        ",".join(sorted(gewaehlt)) or "-",
        len(aenderungen),
        gesperrt,
        kanaele,
    )
    return {
        "bereiche": sorted(gewaehlt),
        "felder": len(aenderungen),
        "gesperrt": gesperrt,
        "kanaele": kanaele,
    }


def _kanal_anlegen(db, nutzlast: dict) -> int:
    """Einen Meldeweg als ``ChannelTarget`` anlegen.

    ⚠️ **Zwei Ebenen, wo der Dienst sie hat.** Bei Telegram traegt die Instanz
    das Token und das Postfach den Chat, bei ntfy die Instanz die Adresse und
    das Postfach das Topic. ``channel_targets.anwenden`` schreibt je Ebene nur
    deren eigene Felder und verschluesselt dabei die Geheimnisse - deshalb geht
    es durch diese Funktion und nicht ueber ein direktes ``setattr``.

    ⚠️ **Was hier NICHT entsteht, ist ein Abonnement.** Welche Meldung ueber
    welchen Kanal geht, bleibt unbestimmt: Nexview kennt andere Arten als Seerr
    (Rueckmeldungen, Ticketcenter, Speicher), und eine Abbildung waere zur
    Haelfte geraten. Der Betreiber stellt es danach selbst ein.
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
    db.commit()
    return angelegt


def _sperren_anlegen(db, eintraege: list[dict]) -> int:
    """Die Sperrliste anlegen, ohne Doppelte.

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
    db.commit()
    return angelegt
