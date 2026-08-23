"""Stoebern - der Rueckkatalog in Regalen.

Bewusst **neben** ``/api/discover`` und nicht darin: Die Entdecken-Seite bleibt
unveraendert, damit sich beide Ansaetze vergleichen lassen.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, Path, Query

from sqlalchemy import select

from ..deps import CurrentUser, DbSession
from ..models import Favorite, MediaType
from ..schemas_media import MediaItem
from ..schemas_stoebern import (
    FilmabendFrage,
    FilmabendStapel,
    FilterSeite,
    RegalInfo,
    RegalSeite,
)
from ..services import age_rating, filmabend, kids, media, stoebern
from ..services.settings_service import for_user, load_settings
from ..services.tmdb import TmdbError

# ``_status_for`` wird bewusst aus dem Entdecken-Router importiert statt
# nachgebaut. Die Rangfolge der Abzeichen (Sperre schlaegt alles, vorhandene
# Datei schlaegt die eigene Anfrage) ist heikel und existiert im Projekt schon
# zweimal - eine dritte Kopie waere die, die beim naechsten Mal vergessen wird.
from .discover import _http_error, _status_for

router = APIRouter(prefix="/api/stoebern", tags=["stoebern"])

MediaTypePath = Annotated[Literal["movie", "tv"], Path()]

# Wie viele Titel eine Regalreihe auf der Uebersicht zeigt bzw. die volle
# Regalseite laedt.
#
# Bewusst klein: Stoebern heisst Auswahl **verkleinern**. Endloses Scrollen
# durch 500 Seiten ist das Gegenteil einer Entscheidungshilfe.
REIHE_ANZAHL = 12
SEITE_ANZAHL = 24


@router.get("/regale/{media_type}", response_model=list[RegalInfo])
def regale(media_type: MediaTypePath, user: CurrentUser, db: DbSession) -> list[RegalInfo]:
    """Welche Regale es gibt - ohne einen einzigen TMDB-Abruf.

    Die Titel darin holt das Frontend je Regal einzeln nach. So erscheint die
    Uebersicht sofort und fuellt sich, statt als Ganzes zu warten.
    """
    regale = [
        RegalInfo(
            kennung=regal.kennung,
            gruppe=regal.gruppe,
            kategorie=regal.kategorie,
            persoenlich=regal.persoenlich,
        )
        for regal in stoebern.regale_fuer(media_type)
    ]
    # Persoenliches kommt **oben drauf** - und nur, wenn es dafuer auch Daten
    # gibt. Ein angebotenes Regal, das dann leer bleibt, sieht aus wie ein
    # Defekt; genau daran krankt die Startseite beim ersten Start.
    return _persoenliche_regale(db, media_type, user) + regale


def _persoenliche_regale(db, media_type: str, user) -> list[RegalInfo]:
    art = MediaType(media_type)
    gefunden: list[RegalInfo] = []

    # ⚠️ Nicht "hat ueberhaupt einen Verlauf", sondern "hat etwas, das lange
    # genug her ist". Sonst erscheint das Regal bei jemandem, der seinen
    # Media-Server letzte Woche verbunden hat, und schlaegt ihm vor, was er
    # vorgestern gesehen hat - "lang nicht gesehen" waere dann gelogen.
    if filmabend.lange_nicht_gesehen(db, user.id, media_type):
        gefunden.append(
            RegalInfo(
                kennung="wieder", gruppe="reihe", kategorie="persoenlich", persoenlich=True
            )
        )

    # Je Herz eine **eigene** Reihe, nicht alles zu einem Brei vermischt wie
    # in ``home/curated``: Eine gemischte Liste sagt nicht, *warum* etwas
    # vorgeschlagen wird - und genau das ist die nuetzliche Auskunft.
    # Alle holen, dann auswaehlen: Ein ``LIMIT`` an dieser Stelle zeigte immer
    # nur die zuletzt markierten, und bei hundert Favoriten saehe man die
    # uebrigen sechsundneunzig nie.
    alle_herzen = db.execute(
        select(Favorite.tmdb_id, Favorite.title)
        .where(Favorite.user_id == user.id, Favorite.media_type == art)
        .order_by(Favorite.created_at.desc())
    ).all()
    for tmdb_id, titel in stoebern.herzen_fuer_heute(list(alle_herzen)):
        gefunden.append(
            RegalInfo(
                kennung=stoebern.weil_du_kennung(tmdb_id),
                gruppe="reihe",
                kategorie="persoenlich",
                persoenlich=True,
                titel=titel,
            )
        )
    return gefunden


@router.get("/regal/{media_type}/{kennung}", response_model=RegalSeite)
async def regal(
    media_type: MediaTypePath,
    kennung: Annotated[str, Path(max_length=40, pattern=r"^[a-z0-9_]+$")],
    user: CurrentUser,
    db: DbSession,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    bestand: Annotated[Literal["egal", "nur_vorhanden", "nur_neu"], Query()] = "egal",
    anzahl: Annotated[int, Query(ge=1, le=60)] = SEITE_ANZAHL,
) -> RegalSeite:
    """Der Inhalt eines Regals.

    ``bestand`` siebt **serverseitig** nach dem eigenen Bestand. Die
    Entdecken-Seite siebt erst im Browser; bei gefuellter Bibliothek bleiben
    dort von 20 Kacheln zwei uebrig und die Seite wirkt kaputt.
    """
    try:
        stoebern.regal_oder_404(kennung, media_type)
    except stoebern.UnbekanntesRegal as fehler:
        raise HTTPException(status_code=404, detail="Unbekanntes Regal.") from fehler

    settings = for_user(load_settings(db), user)
    warnungen: list[str] = []

    async def mit_zustand_regal(items: list[MediaItem]) -> list[MediaItem]:
        fertig, warnung = await _status_for(db, settings, media_type, items, user)
        if warnung and warnung not in warnungen:
            warnungen.append(warnung)
        return fertig

    # Persoenliche Regale kommen nicht von einer TMDB-Abfrage, sondern aus dem
    # eigenen Verlauf bzw. den Empfehlungen zu einem Herz.
    if kennung == "wieder":
        titel, _, erschoepft = await _aus_verlauf(
            db, settings, media_type, user, mit_zustand_regal,
            modus=bestand, ziel=anzahl, streuung={"regal": kennung}, runde=page - 1,
        )
        return RegalSeite(
            kennung=kennung, items=titel, page=page, total_pages=page,
            seiten_durchsucht=1, erschoepft=erschoepft,
            demo=settings.use_demo_data,
            arr_warning=warnungen[0] if warnungen else None,
        )

    herz = stoebern.weil_du_id(kennung)
    if herz is not None:
        titel, erschoepft = await _weil_du(
            db, settings, media_type, user, herz, mit_zustand_regal,
            modus=bestand, ziel=anzahl, seite=page,
        )
        return RegalSeite(
            kennung=kennung, items=titel, page=page, total_pages=page,
            seiten_durchsucht=1, erschoepft=erschoepft,
            demo=settings.use_demo_data,
            arr_warning=warnungen[0] if warnungen else None,
        )

    # Die Laufzeit muss nachgeprueft werden - TMDBs eigener Filter ist dabei
    # unzuverlaessig (siehe ``stoebern.laufzeit_pruefer``).
    pruefer = stoebern.laufzeit_pruefer(stoebern.filter_fuer(kennung, media_type))

    async def hole_seite(seite: int) -> tuple[list[MediaItem], int]:
        filter_ = stoebern.filter_fuer(kennung, media_type, page=seite)
        ergebnis = await media.discover(db, settings, media_type, filter_)
        return ergebnis.items, ergebnis.total_pages

    try:
        ausbeute = await stoebern.sammle(
            hole_seite,
            mit_zustand_regal,
            modus=bestand,
            ziel=anzahl,
            erste_seite=page,
            zusaetzlich=pruefer,
        )
    except TmdbError as fehler:
        raise _http_error(fehler) from fehler

    return RegalSeite(
        kennung=kennung,
        items=ausbeute.items,
        page=page,
        total_pages=ausbeute.total_pages,
        seiten_durchsucht=ausbeute.seiten_durchsucht,
        erschoepft=ausbeute.erschoepft,
        demo=settings.use_demo_data,
        arr_warning=warnungen[0] if warnungen else None,
    )


def _nummern(text: str | None, grenze: int = 12) -> tuple[int, ...]:
    """Eine Komma-Liste von Genrenummern einlesen.

    Stillschweigend nachsichtig: Was keine Zahl ist, faellt weg. Die Liste
    kommt aus der Adresszeile, und ein Tippfehler darf keine 422 ergeben -
    das Ergebnis waere dann eine Fehlerseite statt einer Titelliste.
    """
    if not text:
        return ()
    gefunden: list[int] = []
    for teil in text.split(","):
        teil = teil.strip()
        if teil.isdigit() and len(gefunden) < grenze:
            gefunden.append(int(teil))
    return tuple(dict.fromkeys(gefunden))


@router.get("/filter/{media_type}", response_model=FilterSeite)
async def filter_seite(
    media_type: MediaTypePath,
    user: CurrentUser,
    db: DbSession,
    zeit: Annotated[Literal["egal", "kurz", "mittel", "lang"], Query()] = "egal",
    genres: Annotated[str | None, Query(max_length=120)] = None,
    ohne_genres: Annotated[str | None, Query(max_length=120)] = None,
    epoche: Annotated[str, Query(max_length=8, pattern=r"^[a-z0-9]+$")] = "egal",
    bekanntheit: Annotated[Literal["egal", "bekannt", "geheimtipp"], Query()] = "egal",
    sortierung: Annotated[Literal["rating", "popular", "newest"], Query()] = "rating",
    bestand: Annotated[Literal["egal", "nur_vorhanden", "nur_neu"], Query()] = "egal",
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    anzahl: Annotated[int, Query(ge=1, le=60)] = SEITE_ANZAHL,
) -> FilterSeite:
    """Die freie Auswahl - sechs menschliche Fragen statt dreizehn Reglern.

    Bewusst **nicht** an ``/api/discover`` angebaut: Die Entdecken-Seite
    bleibt unveraendert, damit sich beide Ansaetze vergleichen lassen.
    """
    settings = for_user(load_settings(db), user)
    wahl = stoebern.Wahl(
        zeit=zeit,
        genres=_nummern(genres),
        ohne_genres=_nummern(ohne_genres),
        epoche=epoche,
        bekanntheit=bekanntheit,
        sortierung=sortierung,
    )
    warnungen: list[str] = []
    pruefer = stoebern.laufzeit_pruefer(stoebern.filter_aus_wahl(wahl, media_type))

    async def hole_seite(seite: int) -> tuple[list[MediaItem], int]:
        filter_ = stoebern.filter_aus_wahl(wahl, media_type, page=seite)
        ergebnis = await media.discover(db, settings, media_type, filter_)
        return ergebnis.items, ergebnis.total_pages

    async def mit_zustand(items: list[MediaItem]) -> list[MediaItem]:
        fertig, warnung = await _status_for(db, settings, media_type, items, user)
        if warnung and warnung not in warnungen:
            warnungen.append(warnung)
        return fertig

    try:
        ausbeute = await stoebern.sammle(
            hole_seite,
            mit_zustand,
            modus=bestand,
            ziel=anzahl,
            erste_seite=page,
            zusaetzlich=pruefer,
        )
    except TmdbError as fehler:
        raise _http_error(fehler) from fehler

    return FilterSeite(
        items=ausbeute.items,
        page=page,
        total_pages=ausbeute.total_pages,
        seiten_durchsucht=ausbeute.seiten_durchsucht,
        erschoepft=ausbeute.erschoepft,
        jahrzehnte=list(stoebern.FILTER_JAHRZEHNTE),
        demo=settings.use_demo_data,
        arr_warning=warnungen[0] if warnungen else None,
    )


# --- Der gefuehrte Filmabend ----------------------------------------------


@router.get("/filmabend/fragen/{media_type}", response_model=list[FilmabendFrage])
def filmabend_fragen(
    media_type: MediaTypePath, user: CurrentUser, db: DbSession
) -> list[FilmabendFrage]:
    """Der Fragebaum auf einmal - zugeschnitten auf diese Person.

    Das Frontend laeuft ihn selbst ab; eine Runde zum Server je Frage waere
    fuer eine Handvoll fester Fragen unnoetig langsam. Die **Bedeutung** der
    Antworten bleibt trotzdem hier: ``filmabend.filter_aus`` ist die einzige
    Stelle, an der aus einer Antwort ein Filter wird.

    ``media_type`` gehoert in den Pfad, weil der Sehverlauf je Medienart
    gefuehrt wird: Wer viele Filme, aber keine Serien gesehen hat, darf bei
    Serien nicht nach "lange nicht gesehen" gefragt werden.
    """
    mit_wiedersehen = bool(filmabend.lange_nicht_gesehen(db, user.id, media_type))
    return [
        FilmabendFrage(
            kennung=frage.kennung,
            antworten=list(frage.antworten),
            entfaellt_wenn={k: list(v) for k, v in frage.entfaellt_wenn.items()},
            antworten_entfallen_wenn={
                antwort: {k: list(v) for k, v in bedingungen.items()}
                for antwort, bedingungen in frage.antworten_entfallen_wenn.items()
            },
        )
        for frage in filmabend.fragen_fuer(mit_wiedersehen)
    ]


@router.post("/filmabend/ergebnis/{media_type}", response_model=FilmabendStapel)
async def filmabend_ergebnis(
    media_type: MediaTypePath,
    user: CurrentUser,
    db: DbSession,
    antworten: Annotated[dict[str, str], Body(embed=True)],
    runde: Annotated[int, Body(ge=0, le=99)] = 0,
) -> FilmabendStapel:
    """Aus den Antworten einen kleinen Stapel machen.

    ``runde`` ist das "Nochmal wuerfeln": gleiche Antworten und gleiche Runde
    ergeben immer denselben Stapel, die naechste Runde einen anderen. Kein
    echter Zufall - sonst waere der Stapel, den man gerade ansah, nach einem
    Neuladen der Seite weg.
    """
    try:
        gepruefte = filmabend.pruefe(antworten)
    except filmabend.UngueltigeAntwort as fehler:
        # Klartext, keine Entwicklersprache. Der haeufigste Grund ist ein
        # Fenster, das noch offen war, als eine neue Fassung ausgeliefert
        # wurde - dann bot es eine Antwort an, die es nicht mehr gibt.
        # "Unbekannte Antwort: stimmung=herz" hilft dabei niemandem.
        raise HTTPException(
            status_code=422,
            detail=(
                "Diese Antwort passt nicht mehr zu den vorherigen. "
                "Fang bitte noch einmal von vorn an."
            ),
        ) from fehler

    settings = for_user(load_settings(db), user)

    # "Mit Kindern" bzw. "mit der Familie" setzt eine Altersfreigabe. Die
    # Uebersetzung von "hoechstens 6 Jahre" in die Bezeichnungen des jeweiligen
    # Landes ("FSK 0", "PG", ...) macht dieselbe Funktion wie in der
    # Kinderansicht - geratene kindgerechte Genres waeren eine schwaechere
    # Zusage.
    grenze = filmabend.hoechstalter(gepruefte)
    freigaben: tuple[str | None, str] = (None, "")
    if grenze is not None:
        freigaben = await kids.freigaben_bis_alter(
            db, settings, settings.rating_region or settings.default_region, grenze
        )

    if gepruefte.get("vertraut") == "wieder":
        stapel = await _wiedersehen(
            db, settings, media_type, user, gepruefte, runde, grenze, freigaben
        )
    else:
        stapel = await _neuer_stapel(
            db, settings, media_type, user, gepruefte, runde, freigaben
        )

    return stapel.model_copy(
        update={"runde": runde, "antworten": gepruefte, "demo": settings.use_demo_data}
    )


async def _neuer_stapel(
    db, settings, media_type, user, antworten, runde, freigaben
) -> FilmabendStapel:
    """Der uebliche Weg: bei TMDB suchen und nach dem Bestand sieben."""
    # Jede Runde faengt eine Seite spaeter an. Drei Seiten reichen: Danach ist
    # die Auswahl so eng gefasst, dass tiefer nur noch Randfaelle kommen.
    erste_seite = 1 + (runde % 3)
    warnungen: list[str] = []
    pruefer = stoebern.laufzeit_pruefer(
        filmabend.filter_aus(antworten, media_type, freigaben=freigaben)
    )

    async def hole_seite(seite: int) -> tuple[list[MediaItem], int]:
        filter_ = filmabend.filter_aus(
            antworten, media_type, page=seite, freigaben=freigaben
        )
        ergebnis = await media.discover(db, settings, media_type, filter_)
        return ergebnis.items, ergebnis.total_pages

    async def mit_zustand(items: list[MediaItem]) -> list[MediaItem]:
        fertig, warnung = await _status_for(db, settings, media_type, items, user)
        if warnung and warnung not in warnungen:
            warnungen.append(warnung)
        return fertig

    try:
        ausbeute = await stoebern.sammle(
            hole_seite,
            mit_zustand,
            modus=filmabend._modus(antworten),
            # Etwas mehr holen als gezeigt wird: Bei "noch nicht gesehen" faellt
            # danach noch etwas weg.
            ziel=filmabend.STAPEL_GROESSE * 2,
            erste_seite=erste_seite,
            zusaetzlich=pruefer,
        )
    except TmdbError as fehler:
        raise _http_error(fehler) from fehler

    titel = ausbeute.items
    if antworten.get("vertraut") == "neu":
        gesehen = filmabend.gesehene_kennungen(db, user.id, media_type)
        titel = [eintrag for eintrag in titel if eintrag.tmdb_id not in gesehen]

    return FilmabendStapel(
        items=titel[: filmabend.STAPEL_GROESSE],
        runde=runde,
        antworten=antworten,
        erschoepft=ausbeute.erschoepft,
    )


# So viele Titel werden auf einmal aufgeloest. Der ganze Verlauf waere bei
# einer grossen Bibliothek ein paar hundert Detailabrufe fuer zwoelf Kacheln.
SCHRITT = 24


async def _aus_verlauf(
    db,
    settings,
    media_type,
    user,
    mit_zustand,
    *,
    modus: str,
    ziel: int,
    streuung: dict,
    runde: int,
    erlaubte_genres: set[int] | None = None,
    laufzeit: tuple[int | None, int | None] = (None, None),
    hoechstalter: int | None = None,
    land: str = "",
) -> tuple[list[MediaItem], bool, bool]:
    """"Etwas, das ich lange nicht gesehen habe" - fuer Assistent **und** Regal.

    Der Vorrat kommt **nicht** von TMDB, sondern aus dem eigenen Sehverlauf.
    Genre, Laufzeit und Altersfreigabe wirken deshalb nachtraeglich: Man kann
    bei TMDB schlecht nach "was diese Person vor drei Jahren sah" fragen.

    Gelesen wird ausschliesslich ``UserWatched`` - anbieter-neutral. Ob Plex,
    Jellyfin oder beide sie gefuellt haben, spielt keine Rolle.

    Liefert (Titel, quelle_leer, erschoepft). ``quelle_leer`` heisst "es gibt
    gar keinen Verlauf" und ist etwas anderes als "nichts hat gepasst" - die
    Oberflaeche muss beides unterscheiden koennen.
    """
    kennungen = filmabend.lange_nicht_gesehen(db, user.id, media_type)
    if not kennungen:
        # ⚠️ Zwei verschiedene Auskuenfte, und sie duerfen nicht verwechselt
        # werden: "du hast noch keinen Media-Server verbunden" gegen "du hast
        # einen Verlauf, aber nichts davon ist lange genug her". Die erste
        # Fassung sagte immer das Erste - jemandem mit 389 Eintraegen.
        return [], not filmabend.hat_verlauf(db, user.id, media_type), True

    # Genrenamen statt Nummern: ``MediaItem`` traegt die Namen, nicht die
    # Kennungen. Der Umweg ueber die Genreliste ist zwischengespeichert.
    erlaubte_namen: set[str] = set()
    if erlaubte_genres:
        alle = await media.genre_list(db, settings, media_type)
        erlaubte_namen = {g.name for g in alle if g.id in erlaubte_genres}

    mindestens, hoechstens = laufzeit
    gemischt = filmabend.mischen(filmabend.vorrat(kennungen), streuung, runde)

    gefunden: list[MediaItem] = []
    erschoepft = True
    for anfang in range(0, len(gemischt), SCHRITT):
        haeppchen = gemischt[anfang : anfang + SCHRITT]
        roh = await asyncio.gather(
            *(media.detail(db, settings, media_type, k) for k in haeppchen),
            return_exceptions=True,
        )
        # Altersgesperrte und bei TMDB verschwundene Titel fallen still weg -
        # ein Fehler darf den ganzen Stapel nicht kippen.
        titel = await mit_zustand([t for t in roh if isinstance(t, MediaItem)])

        for eintrag in titel:
            # Auch der eigene Sehverlauf wird gesiebt: Was man vor Jahren
            # allein gesehen hat, ist deshalb nicht kindgeeignet.
            if hoechstalter is not None:
                stufe = age_rating.stufe(land, eintrag.certification or "")
                # Ohne bekannte Einstufung fliegt der Titel raus. In einer
                # Alterssperre ist "unbekannt" nicht "unbedenklich".
                if stufe is None or stufe > hoechstalter:
                    continue
            if modus == "nur_vorhanden" and eintrag.status not in stoebern.VORHANDEN:
                continue
            if modus == "nur_neu" and eintrag.status in stoebern.ERLEDIGT:
                continue
            if erlaubte_namen and not (set(eintrag.genres) & erlaubte_namen):
                continue
            dauer = eintrag.runtime_minutes
            if media_type == "movie" and dauer:
                if mindestens is not None and dauer < mindestens:
                    continue
                if hoechstens is not None and dauer > hoechstens:
                    continue
            gefunden.append(eintrag)

        if len(gefunden) >= ziel:
            erschoepft = False
            break

    return gefunden[:ziel], False, erschoepft


async def _weil_du(
    db, settings, media_type, user, tmdb_id: int, mit_zustand, *, modus: str, ziel: int, seite: int
) -> tuple[list[MediaItem], bool]:
    """"Weil dir *X* gefaellt" - Empfehlungen zu genau einem Herz.

    Je Herz eine **eigene** Reihe. ``home/curated`` vermischt alle Favoriten zu
    einer Liste; die sagt dann nicht mehr, *warum* etwas vorgeschlagen wird -
    und genau das ist die nuetzliche Auskunft.

    ``empfehlungs_vorrat`` liefert eine feste Reihenfolge (erst
    ``recommendations``, dann ``similar``), aus der hier seitenweise
    geschnitten wird.
    """
    try:
        vorrat = await media.empfehlungs_vorrat(db, settings, media_type, tmdb_id)
    except TmdbError:
        # Eine einzelne Reihe darf die Uebersicht nicht kippen.
        return [], True

    titel = await mit_zustand(vorrat)
    passend = [
        eintrag
        for eintrag in titel
        if not (modus == "nur_vorhanden" and eintrag.status not in stoebern.VORHANDEN)
        and not (modus == "nur_neu" and eintrag.status in stoebern.ERLEDIGT)
    ]
    anfang = (seite - 1) * ziel
    ausschnitt = passend[anfang : anfang + ziel]
    return ausschnitt, anfang + ziel >= len(passend)


async def _wiedersehen(
    db, settings, media_type, user, antworten, runde, grenze, freigaben
) -> FilmabendStapel:
    """Der Wiedersehen-Zweig des Assistenten."""

    async def mit_zustand(items: list[MediaItem]) -> list[MediaItem]:
        fertig, _ = await _status_for(db, settings, media_type, items, user)
        return fertig

    titel, quelle_leer, erschoepft = await _aus_verlauf(
        db,
        settings,
        media_type,
        user,
        mit_zustand,
        # "Muss es heute Abend sofort laufen?" gilt auch hier: Was man vor
        # Jahren gesehen hat, muss nicht mehr in der Bibliothek liegen.
        modus=filmabend._modus(antworten),
        ziel=filmabend.STAPEL_GROESSE,
        streuung=antworten,
        runde=runde,
        erlaubte_genres=set(
            filmabend.STIMMUNGEN[media_type][antworten.get("stimmung", "ueberrasch")]
        ),
        laufzeit=filmabend.ZEITEN[antworten.get("zeit", "egal")],
        hoechstalter=grenze,
        land=freigaben[0] or settings.rating_region or settings.default_region,
    )
    return FilmabendStapel(
        items=titel,
        runde=runde,
        antworten=antworten,
        quelle_leer=quelle_leer,
        erschoepft=erschoepft,
    )

