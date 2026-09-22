"""Auftraege an Radarr und Sonarr: anlegen, Folgen schalten, abbrechen.

Umgezogen aus ``requests_service`` (Scheibe 2 des NEX-Umbaus), ohne
Aenderung im Verhalten. Dort bleibt, was fuer jeden Weg gilt: Zustaende,
Meldungen, Protokoll. Hier steht, was nur Radarr und Sonarr brauchen.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....models import MediaRequest, MediaType, QualityTier
from ... import logs, media
from ...settings_service import AppSettings
from . import library
from .client import ArrError

logger = logging.getLogger("nexview.requests")
# Die Heilung meldet sich wie vor dem Umzug unter dem Namen des Status-Takts.
_takt_logger = logging.getLogger("nexview.poller")


def _aktive_zustaende():
    """``ACTIVE_STATUSES`` aus ``requests_service`` - erst beim Aufruf geholt.

    Der Anfragedienst ruft diese Datei ueber die Grenze; umgekehrt
    importiert, liefe das beim Laden im Kreis.
    """
    from ...requests_service import ACTIVE_STATUSES

    return ACTIVE_STATUSES


def _nicht_eingerichtet(dienst: str, tier: QualityTier) -> str:
    """Fehlertext, der die Stufe mitnennt.

    Ohne den Zusatz stuende bei einer 4K-Anfrage "Radarr ist nicht
    eingerichtet", obwohl das normale Radarr laeuft - und niemand kaeme darauf,
    dass die *zweite* Instanz gemeint ist.
    """
    zusatz = " für 4K" if tier == QualityTier.uhd else ""
    return f"{dienst}{zusatz} ist nicht eingerichtet."


def _nicht_eingerichtet_fehler(dienst: str, tier: QualityTier) -> ArrError:
    """Dasselbe als ``ArrError`` - mit Kennung, damit es uebersetzbar bleibt.

    Zwei Kennungen statt einer mit Platzhalter: "Radarr für 4K" laesst sich
    nicht sauber aus Bausteinen zusammensetzen, ohne dass eine Sprache
    irgendwann daran zerbricht.
    """
    return ArrError(
        _nicht_eingerichtet(dienst, tier),
        code="arr_uhd_not_configured" if tier == QualityTier.uhd else "arr_not_configured",
        service=dienst,
    )


def requester_tag(username: str) -> str:
    """Etikett, das in Radarr/Sonarr zeigt, wer den Titel angefordert hat."""
    return f"nexview-{username.lower()}"


async def _radarr_eintrag(settings: AppSettings, request: MediaRequest):
    """Kennt Radarr diesen Film bereits? Sonst ``None``.

    ⚠️ **Das Gegenstueck zu ``_sonarr_eintrag`` - und es hat lange gefehlt.**
    Bei Serien wird seit jeher nachgesehen, bevor etwas angelegt wird; bei
    Filmen ging der Auftrag bedingungslos an Radarr. Liegt der Film dort
    schon, antwortet Radarr mit einem gewoehnlichen 400er, dessen Begruendung
    nur im Protokoll landet - die Anfrage wurde "fehlgeschlagen", und in der
    Freigabeliste blieb sie ohne einen einzigen Knopf stehen.

    Der Fall ist nicht selten: eine zweite Radarr-Instanz, ein von Hand
    hinzugefuegter Film, ein eingespielter Stand aus einer anderen
    Installation. Gemeldet wurde er, nachdem ein Film waehrend der offenen
    Freigabe ueber eine andere Instanz ins Haus kam.

    Liegt er schon da, wird nichts neu angelegt: Die Anfrage uebernimmt seine
    Radarr-Nummer und laeuft ganz gewoehnlich weiter. Hat er bereits eine
    Datei, setzt der naechste Rundgang sie auf "geladen" - dafuer braucht es
    hier keinen Sonderfall.

    Der Bestand kommt aus demselben Zwischenspeicher wie bei Serien. Er kann
    ein paar Minuten alt sein; in diesem Fenster schlaegt weiterhin Radarrs
    400er durch, und dafuer gibt es den Weg aus der Freigabeliste.
    """
    bestand = await library.movie_library(settings, request.tier.value)
    return bestand.get(request.tmdb_id)


async def _sonarr_eintrag(settings: AppSettings, request: MediaRequest):
    """Kennt Sonarr diese Serie bereits? Sonst ``None``.

    Erst ueber die TVDB-Kennung, ersatzweise ueber den normalisierten Titel -
    fuer viele neue Serien kennt TMDB noch keine TVDB-Kennung.
    """
    nach_tvdb, nach_titel = await library.series_library(settings, request.tier.value)
    if request.tvdb_id:
        treffer = nach_tvdb.get(request.tvdb_id)
        if treffer is not None:
            return treffer
    return library.treffer_nach_titel(
        nach_titel, request.title, library.jahr_aus(request.release_date)
    )


def _gewollte_staffeln(db: Session, request: MediaRequest) -> set[int]:
    """Alle Staffeln dieser Serie, zu denen eine Anfrage laeuft.

    ⚠️ **Warum die ganze Menge und nicht nur die neue Staffel.**
    ``addOptions.monitor: "none"`` wirkt bei Sonarr **asynchron**: Es raeumt
    nach dem Anlegen alles ab - auch das, was Nexview unmittelbar danach
    eingeschaltet hat. Nachgemessen an "Baywatch": Staffel 3 wurde freigegeben,
    angelegt und geladen; zwei Minuten spaeter kam die Freigabe fuer Staffel 2,
    las den inzwischen abgeraeumten Stand und schrieb ihn samt abgeschalteter
    Staffel 3 zurueck. In Nexview stand "wird gesucht", in Sonarr war die
    Staffel aus - sie waere nie gekommen.

    Deshalb ist **Nexview** die Quelle der Wahrheit und nicht der Zustand, den
    Sonarr gerade zeigt. Ein abgeraeumter Zustand heilt damit von selbst,
    sobald die naechste Staffel derselben Serie freigegeben wird.
    """
    if request.tvdb_id is None:
        return {request.season} if request.season is not None else set()
    laufend = db.scalars(
        select(MediaRequest).where(
            MediaRequest.media_type == MediaType.tv,
            MediaRequest.tvdb_id == request.tvdb_id,
            MediaRequest.fassung_kennung == request.fassung_kennung,
            MediaRequest.season.is_not(None),
            # ⚠️ Folgen-Pakete bleiben draussen: Ihre Staffel hier mitzunehmen
            # hiesse, die **ganze** Staffel einzuschalten - und Sonarr zoege
            # alles, obwohl nur einzelne Folgen bestellt sind. Pakete heilt
            # der Status-Abgleich folgengenau.
            MediaRequest.episodes.is_(None),
            MediaRequest.status.in_(_aktive_zustaende()),
        )
    )
    staffeln = {zeile.season for zeile in laufend if zeile.season is not None}
    if request.season is not None:
        staffeln.add(request.season)
    return staffeln


async def _folgen_einschalten(client, arr_id: int, request: MediaRequest) -> bool:
    """Die bestellten Folgen eines Pakets ueberwachen und suchen.

    ``True`` heisst erledigt. ``False`` heisst: Sonarr kennt die Folgen dieser
    Staffel **noch** nicht - direkt nach dem Anlegen laedt es die Metadaten
    asynchron nach. Das ist kein Fehler: Die Anfrage bleibt stehen, und der
    Status-Abgleich holt das Einschalten im naechsten Durchgang nach
    (dieselbe Heilung, die auch abgeraeumte Ueberwachung repariert).

    Kennt Sonarr die Staffel zwar, aber eine der bestellten Folgen nicht,
    ist das dagegen ein echter Fehler - die Nummer gibt es dort nicht.
    """
    stand = await client.folgen_stand(arr_id)
    staffel = stand.get(request.season) or {}
    if not staffel:
        if stand:
            raise ArrError(
                f"Sonarr kennt Staffel {request.season} dieser Serie nicht.",
                404,
                code="sonarr_season_unknown",
                season=request.season,
            )
        logger.info(
            "Episodes of %r season %s not listed in Sonarr yet - "
            "the status sync will switch them on",
            request.title,
            request.season,
        )
        return False

    fehlend = sorted(
        nummer for nummer in (request.episodes or []) if nummer not in staffel
    )
    if fehlend:
        raise ArrError(
            f"Sonarr kennt Folge {fehlend[0]} von Staffel {request.season} nicht.",
            404,
            code="sonarr_episode_unknown",
            season=request.season,
            episode=fehlend[0],
        )

    # Serie an (ohne Staffel-Flaggen), dann genau die eigenen Folgen - und
    # gesucht wird nur, was noch keine Datei hat.
    await client.serie_ueberwachen(arr_id)
    eigene = [staffel[nummer] for nummer in (request.episodes or [])]
    await client.folgen_schalten([folge.kennung for folge in eigene], True)
    await client.folgen_suchen(
        [folge.kennung for folge in eigene if not folge.has_file]
    )
    return True


async def _paket_uebergeben(client, request: MediaRequest, vorhanden) -> dict:
    """Ein Folgen-Paket an Sonarr uebergeben.

    Liegt die Serie noch nicht dort, wird sie **stumm** angelegt: keine
    Staffel-Ueberwachung, kein Nachschub, keine Suche - eingeschaltet wird
    danach folgengenau. Liegt sie schon dort, wird nichts neu angelegt,
    sondern nur geschaltet und gesucht.
    """
    if vorhanden is not None:
        arr_id = vorhanden.arr_id
        created: dict = {"id": arr_id}
    else:
        tag_id = await client.ensure_tag(requester_tag(request.user.username))
        created = await client.add(
            request.tvdb_id,
            request.quality_profile_id or 0,
            request.root_folder_path or "",
            tag_ids=[tag_id] if tag_id else None,
            nur_anlegen=True,
        )
        arr_id = created.get("id") if isinstance(created, dict) else None

    if arr_id:
        await _folgen_einschalten(client, arr_id, request)
    return created


async def anfragen(db: Session, settings: AppSettings, request: MediaRequest) -> int | None:
    """Freigegebene Anfrage an Radarr bzw. Sonarr uebergeben; die Kennung dort.

    Wirft ``ArrError``; was der Fehler fuer die Anfrage heisst (fehlgeschlagen
    oder ungewiss), entscheidet ``requests_service.push_to_arr``.
    """
    if request.media_type == MediaType.movie:
        client = library.radarr_client(settings, request.tier.value)
        if client is None:
            raise _nicht_eingerichtet_fehler("Radarr", request.tier)
        # Liegt der Film schon in Radarr, wird er nicht neu angelegt -
        # sonst antwortet Radarr mit einem 400er, und die Anfrage bliebe
        # als "fehlgeschlagen" liegen (siehe ``_radarr_eintrag``).
        vorhanden = await _radarr_eintrag(settings, request)
        if vorhanden is not None:
            logger.info(
                "Radarr already holds %r (tmdb=%s) as #%s - linking the request "
                "to it instead of adding it again",
                request.title,
                request.tmdb_id,
                vorhanden.arr_id,
            )
            created = {"id": vorhanden.arr_id}
        else:
            tag_id = await client.ensure_tag(requester_tag(request.user.username))
            created = await client.add(
                request.tmdb_id,
                request.quality_profile_id or 0,
                request.root_folder_path or "",
                tag_ids=[tag_id] if tag_id else None,
            )
    else:
        client = library.sonarr_client(settings, request.tier.value)
        if client is None:
            raise _nicht_eingerichtet_fehler("Sonarr", request.tier)
        if not request.tvdb_id:
            # ⚠️ **Erst frisch nachfragen, dann aufgeben.** Die Kennung an
            # der Anfrage stammt aus dem Detail-Zwischenspeicher, und der
            # haelt sieben Tage. Neuen Serien fehlt die TVDB-Kennung bei
            # TMDB anfangs regelmaessig und wird spaeter nachgetragen -
            # ohne den Nachschlag scheiterte dieselbe Serie eine Woche
            # lang mit einer Begruendung, die laengst nicht mehr stimmte.
            # Der zusaetzliche TMDB-Aufruf faellt nur in diesem Fehlerfall
            # an. Live nachgemessen am 26./27.08.2026.
            request.tvdb_id = await media.tvdb_kennung_nachschlagen(
                db, settings, request.tmdb_id
            )
            if request.tvdb_id:
                db.commit()
                logger.info(
                    "TVDB id for %r (tmdb=%s) appeared since the cached answer: %s",
                    request.title,
                    request.tmdb_id,
                    request.tvdb_id,
                )
        if not request.tvdb_id:
            raise ArrError(
                "Für diese Serie kennt TMDB noch keine TVDB-Kennung - "
                "Sonarr kann sie deshalb nicht anlegen.",
                code="tvdb_id_missing",
            )

        # Liegt die Serie schon in Sonarr, wird sie nicht neu angelegt -
        # das brächte die vorhandenen Folgen durcheinander. Stattdessen
        # wird nur die gewünschte Staffel aktiviert und gesucht.
        vorhanden = await _sonarr_eintrag(settings, request)
        if request.episodes:
            created = await _paket_uebergeben(client, request, vorhanden)
        elif vorhanden is not None and request.season is not None:
            await client.monitor_seasons(
                vorhanden.arr_id,
                _gewollte_staffeln(db, request),
                such_staffel=request.season,
            )
            created = {"id": vorhanden.arr_id}
        else:
            tag_id = await client.ensure_tag(requester_tag(request.user.username))
            created = await client.add(
                request.tvdb_id,
                request.quality_profile_id or 0,
                request.root_folder_path or "",
                tag_ids=[tag_id] if tag_id else None,
                season=request.season,
                monitor_future=request.monitor_future,
            )
    return created.get("id") if isinstance(created, dict) else None


async def _liegt_noch_dort(client, request: MediaRequest) -> bool:
    """Kennt Radarr/Sonarr diesen Titel unter seiner Kennung noch?

    Wird nur gefragt, wenn das Loeschen fehlgeschlagen ist - und beantwortet
    dann die einzige Frage, die zaehlt: War es ein echter Fehler, oder war der
    Titel ohnehin schon weg?

    ⚠️ **Bewusst ein frischer Aufruf und nicht die zwischengespeicherte
    Bibliothek.** Der Zwischenspeicher kann Minuten alt sein; wer gerade in
    Sonarr geloescht hat, staende darin noch drin - und der Abbruch scheiterte
    ein zweites Mal an derselben Ursache.

    Im Zweifel ``True``: Nur was nachweislich weg ist, gilt als weg. Antwortet
    die Instanz gar nicht mehr oder mit einem weiteren Fehler, bleibt es beim
    urspruenglichen Fehler - lieber eine Anfrage, die stehen bleibt, als die
    Behauptung, Dateien seien geloescht.
    """
    pfad = (
        f"/movie/{request.arr_id}"
        if request.media_type == MediaType.movie
        else f"/series/{request.arr_id}"
    )
    try:
        return await client.get(pfad) is not None
    except ArrError as nachfrage:
        return nachfrage.status_code != 404


def _weitere_aktive(db: Session, request: MediaRequest) -> list[MediaRequest]:
    """Welche anderen laufenden Anfragen wollen noch etwas von diesem Titel?

    Zeilen-, nicht nutzerbasiert: Auch die zweite Staffel desselben Nutzers
    zaehlt. ``pending_approval`` zaehlt bewusst mit - einer wartenden Anfrage
    soll ein fremder Abbruch nicht die Serie unter den Fuessen wegziehen.
    """
    return list(
        db.scalars(
            select(MediaRequest).where(
                MediaRequest.media_type == request.media_type,
                MediaRequest.tmdb_id == request.tmdb_id,
                MediaRequest.fassung_kennung == request.fassung_kennung,
                MediaRequest.status.in_(_aktive_zustaende()),
                MediaRequest.id != request.id,
            )
        )
    )


async def _serie_abbrechen(db: Session, client, request: MediaRequest) -> str:
    """Beim Abbruch einer Serien-Anfrage nur das selbst Bestellte entfernen.

    Frueher loeschte jeder Abbruch die **ganze Serie samt Dateien** - auch
    dann, wenn andere Nutzer andere Staffeln derselben Serie laufen hatten
    oder laengst fertig geladen waren. Jetzt faellt die Serie erst, wenn
    niemand mehr etwas von ihr will; sonst gehen nur die eigenen
    Staffel-Dateien, und die Staffel wird stillgelegt, damit Sonarr sie
    nicht im naechsten Suchlauf gleich wieder laedt.

    Gibt fuer das Protokoll zurueck, was tatsaechlich geschehen ist.
    """
    andere = _weitere_aktive(db, request)
    if not andere:
        await client.remove(request.arr_id, delete_files=True)
        return "removed the series including files"

    gewollte_staffeln = {anfrage.season for anfrage in andere}
    if None in gewollte_staffeln:
        # Jemand will weiterhin die ganze Serie - dann ist hier nichts zu
        # loeschen, jede Datei ist noch gedeckt.
        return "left all files in place - another request covers the whole series"

    if request.episodes:
        # Paket: genau die eigenen Folgen stilllegen und deren Dateien
        # loeschen. Die Folgen gehoeren nachweislich niemandem sonst - je
        # Folge gibt es hoechstens einen laufenden Besitzer.
        stand = await client.folgen_stand(request.arr_id)
        staffel = stand.get(request.season) or {}
        eigene = [
            folge
            for nummer in request.episodes
            if (folge := staffel.get(nummer)) is not None
        ]
        if eigene:
            await client.folgen_schalten([folge.kennung for folge in eigene], False)
        datei_ids = [folge.datei_id for folge in eigene if folge.datei_id]
        if datei_ids:
            await client.delete_episode_files(datei_ids)
        return (
            f"removed only episodes {', '.join(str(n) for n in request.episodes)} "
            f"of season {request.season} ({len(datei_ids)} files) - "
            "the series remains for other requests"
        )

    if request.season is not None:
        kennungen = [
            int(datei["id"])
            for datei in await client.episode_files(request.arr_id, request.season)
            if datei.get("id")
        ]
        await client.unmonitor_season(request.arr_id, request.season)
        if kennungen:
            await client.delete_episode_files(kennungen)
        return (
            f"removed only season {request.season} ({len(kennungen)} files) - "
            "the series remains for other requests"
        )

    # Bestand: eine Anfrage ueber die ganze Serie neben Staffeln anderer.
    # Anlegbar ist das laengst nicht mehr (``find_active`` sperrt es),
    # Altdaten koennen es aber noch enthalten. Dann gilt das Muster der
    # Konto-Aufloesung: stilllegen und nur die Staffeln loeschen, die
    # niemand will - die Ueberwachung der laufenden fremden Staffeln heilt
    # der Status-Abgleich im naechsten Durchgang.
    await client.serie_stilllegen(request.arr_id)
    dateien = await client.get("/episodefile", {"seriesId": request.arr_id}) or []
    kennungen = [
        int(datei["id"])
        for datei in dateien
        if isinstance(datei, dict)
        and datei.get("id")
        and datei.get("seasonNumber") not in gewollte_staffeln
    ]
    if kennungen:
        await client.delete_episode_files(kennungen)
    return (
        f"froze the series and removed {len(kennungen)} files "
        "of seasons nobody else wants"
    )


async def abbrechen(db: Session, settings: AppSettings, request: MediaRequest) -> str:
    """Den Titel einer laufenden Anfrage in Radarr/Sonarr zuruecknehmen.

    Gibt fuers Protokoll zurueck, was geschehen ist. Wirft ``ArrError`` nur,
    wenn der Titel nachweislich noch dort liegt.
    """
    umfang = "removed it including files"
    # Die Stufe der Anfrage entscheidet, aus welcher Instanz geloescht wird -
    # sonst bliebe die 4K-Datei liegen, waehrend Nexview "abgebrochen" meldet.
    client = (
        library.radarr_client(settings, request.tier.value)
        if request.media_type == MediaType.movie
        else library.sonarr_client(settings, request.tier.value)
    )
    if client is not None:
        try:
            if request.media_type == MediaType.movie:
                await client.remove(request.arr_id, delete_files=True)
            else:
                umfang = await _serie_abbrechen(db, client, request)
        except ArrError as error:
            # 404 heisst: dort schon weg - dann ist das Ziel ja erreicht.
            #
            # ⚠️ **Sonarr sagt aber nicht immer 404.** Wer eine Serie in
            # Sonarr von Hand entfernt und danach in Nexview abbricht,
            # bekam gemessen einen **500er** auf dasselbe Loeschen:
            #
            #     DELETE /api/v3/series/213?deleteFiles=true -> 500
            #     POST /api/requests/6/cancel -> 502
            #
            # Damit war die Anfrage nicht mehr loszuwerden: Abbrechen
            # scheiterte immer wieder an einer Serie, die es laengst nicht
            # mehr gab. Ein 500er darf trotzdem nicht einfach als Erfolg
            # gelten - dann behauptete Nexview geloeschte Dateien, die
            # weiter auf der Platte liegen. Also wird **nachgesehen**:
            # Ist der Titel unter dieser Kennung wirklich weg, ist das
            # Ziel erreicht; liegt er noch dort, war es ein echter Fehler.
            if error.status_code != 404 and await _liegt_noch_dort(client, request):
                raise
            umfang = "it was already gone there"
    return umfang


async def heilen(db: Session, settings: AppSettings, request: MediaRequest, arr_id: int) -> None:
    """Abgeschaltete Ueberwachung einer laufenden Serien-Anfrage wieder an.

    Ob es noetig ist, entscheidet ``abgleich_kern.heilung_noetig``; hier steht
    nur, wie. Ein stummes Sonarr kostet eine Protokollzeile, keinen Abbruch.
    """
    client = library.sonarr_client(settings, request.tier.value)
    if client is not None:
        try:
            if request.episodes:
                # Paket: Serie an, genau die eigenen Folgen an,
                # Suche anstossen - dieselbe Strecke wie bei der
                # Uebergabe, dort steht auch das Warum.
                geschafft = await _folgen_einschalten(
                    client, arr_id, request
                )
                if geschafft:
                    _takt_logger.warning(
                        "Monitoring healed: %r season %s episodes %s "
                        "were off or not yet on (arr_id=%s)",
                        request.title,
                        request.season,
                        request.episodes,
                        arr_id,
                    )
            else:
                await client.monitor_seasons(
                    arr_id,
                    _gewollte_staffeln(db, request),
                    such_staffel=request.season,
                )
                _takt_logger.warning(
                    "Monitoring healed: %r season %s was switched off in Sonarr "
                    "(arr_id=%s)",
                    request.title,
                    request.season,
                    arr_id,
                )
        except ArrError as fehler:
            _takt_logger.warning(
                "Monitoring of %r could not be healed: %s",
                request.title,
                logs.kennung(fehler),
            )


def nicht_eingerichtet_text(media_type: str, stufe: str) -> str:
    """Der Satz, wenn fuer Art und Stufe keine Instanz eingerichtet ist."""
    return _nicht_eingerichtet(
        "Radarr" if media_type == MediaType.movie.value else "Sonarr", QualityTier(stufe)
    )
