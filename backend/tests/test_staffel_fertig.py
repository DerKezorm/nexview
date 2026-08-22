"""Wann eine **Staffel** geladen ist - und nicht die Serie.

Der Anlass kam aus dem Betrieb und sah harmlos aus: Fuenf Staffeln von
"Baywatch" wurden freigegeben, drei Minuten spaeter standen alle fuenf auf
"Bereits geladen", und fuenf Push-Nachrichten gingen in derselben Sekunde
hinaus. Auf der Platte lagen zu diesem Zeitpunkt **drei Folgen einer einzigen
Staffel**.

Die Ursache steckt in einem Feld, das jahrelang richtig war:

> ``has_file`` heisst bei Sonarr "mindestens eine Folge der **ganzen Serie**
> liegt vor".

Solange nur ganze Serien angefragt werden konnten, war das dieselbe Aussage
wie "das Angefragte ist da". Seit es Staffelanfragen gibt, ist es das nicht
mehr - und der Abgleich hat nie auf die Staffel gesehen.

Zwei Schwellen, mit Absicht verschieden:

* **fertig** heisst *alle* Folgen der Staffel - eine Staffel ist eine
  abzaehlbare, abgeschlossene Menge, "fertig" ist hier beantwortbar.
* **noch da** heisst *irgendeine* Datei. Waeren beide gleich, spraenge eine
  fertige Staffel, der jemand eine einzelne Folge entfernt, zwischen "geladen"
  und "geloescht" hin und her - und jeder Sprung erzeugte eine Meldung.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    RequestStatus,
)
from app.services import library, status_poller
from app.services.settings_service import load_settings
from app.services.sonarr import LibraryEntry as SeriesEntry
from app.services.sonarr import Staffelstand

from .conftest import create_user

TVDB = 77304


def _staffelanfrage(db, user_id: int, season: int | None) -> int:
    zeile = MediaRequest(
        user_id=user_id,
        media_type=MediaType.tv,
        tier=QualityTier.standard,
        tmdb_id=4386,
        tvdb_id=TVDB,
        title="Baywatch",
        release_date="1989-04-23",
        season=season,
        status=RequestStatus.searching,
        arr_id=211,
        quality_profile_id=1,
        root_folder_path="/data/TV Shows",
    )
    db.add(zeile)
    db.commit()
    return zeile.id


def _serie(monkeypatch, staffeln: dict[int, Staffelstand]) -> None:
    """Sonarr so stellen, wie es der gemeldete Fall zeigte."""
    dateien = sum(s.dateien for s in staffeln.values())
    eintrag = SeriesEntry(
        arr_id=211,
        # Genau hier lag der Fehler: Eine einzige Datei irgendwo in der Serie
        # macht dieses Feld wahr.
        has_file=dateien > 0,
        monitored=True,
        episode_file_count=dateien,
        episode_count=sum(s.folgen for s in staffeln.values()),
        title_key="baywatch",
        year=1989,
        title="Baywatch",
        staffeln=staffeln,
    )

    async def bibliothek(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        return {TVDB: eintrag}, {eintrag.title_key: eintrag}

    monkeypatch.setattr(library, "series_library", bibliothek)


# --- Der gemeldete Fall ----------------------------------------------------


@pytest.mark.asyncio
async def test_eine_geladene_staffel_macht_die_anderen_nicht_fertig(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **Der Fehler selbst.**

    Drei Dateien in Staffel 3 - und vier voellig leere Staffeln galten als
    geladen, weil die Frage nie an der Staffel gestellt wurde.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        ids = {n: _staffelanfrage(db, konto["id"], n) for n in (1, 2, 3, 4, 5)}

    _serie(
        monkeypatch,
        {
            1: Staffelstand(dateien=0, folgen=22),
            2: Staffelstand(dateien=0, folgen=22),
            3: Staffelstand(dateien=3, folgen=22),  # laeuft noch
            4: Staffelstand(dateien=0, folgen=22),
            5: Staffelstand(dateien=0, folgen=22),
        },
    )

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 0

    with SessionLocal() as db:
        for nummer, anfrage_id in ids.items():
            zustand = db.get(MediaRequest, anfrage_id).status
            assert zustand == RequestStatus.searching, f"Staffel {nummer} gilt als geladen"
        # Und damit auch keine einzige Fertig-Meldung.
        meldungen = db.scalars(
            select(Notification).where(
                Notification.type == NotificationType.download_complete
            )
        ).all()
        assert list(meldungen) == []


@pytest.mark.asyncio
async def test_vollstaendige_staffel_gilt_als_geladen(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe - sonst waere der Fehler nur gegen "nie fertig" getauscht."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        fertig = _staffelanfrage(db, konto["id"], 1)
        laeuft = _staffelanfrage(db, konto["id"], 2)

    _serie(
        monkeypatch,
        {
            1: Staffelstand(dateien=22, folgen=22),
            2: Staffelstand(dateien=21, folgen=22),  # eine fehlt noch
        },
    )

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1

    with SessionLocal() as db:
        assert db.get(MediaRequest, fertig).status == RequestStatus.downloaded
        assert db.get(MediaRequest, laeuft).status == RequestStatus.searching


@pytest.mark.asyncio
async def test_die_meldung_nennt_die_staffel(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fuenf Meldungen zu einer Serie duerfen nicht fuenfmal derselbe Text sein.

    Gemeldet als "ohne die Info, dass das nur eine Folge ist und welche". Der
    Serientitel allein beantwortet nicht, worum es geht.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _staffelanfrage(db, konto["id"], 3)

    _serie(monkeypatch, {3: Staffelstand(dateien=22, folgen=22)})

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as db:
        meldung = db.scalars(
            select(Notification).where(
                Notification.type == NotificationType.download_complete
            )
        ).one()
        assert meldung.season == 3
        assert meldung.request_id == anfrage_id


@pytest.mark.asyncio
async def test_eine_fehlende_folge_loescht_die_staffel_nicht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die zweite, bewusst niedrigere Schwelle.

    Mit derselben Schwelle in beide Richtungen faellt eine fertige Staffel bei
    der ersten entfernten Folge auf "wieder geloescht" zurueck - und beim
    naechsten Fund wieder hoch. Das waere eine Meldung je Durchgang.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _staffelanfrage(db, konto["id"], 1)
        db.get(MediaRequest, anfrage_id).status = RequestStatus.downloaded
        db.commit()

    _serie(monkeypatch, {1: Staffelstand(dateien=21, folgen=22)})

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).status == RequestStatus.downloaded


@pytest.mark.asyncio
async def test_ganze_serie_bleibt_bei_der_alten_regel(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Staffel gilt weiter "irgendeine Folge liegt vor".

    Bei einer laufenden Serie waere "alle Folgen" keine beantwortbare Frage -
    sie ist nie fertig. Die Aenderung gilt deshalb ausdruecklich nur fuer
    Staffelanfragen.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _staffelanfrage(db, konto["id"], None)

    _serie(monkeypatch, {1: Staffelstand(dateien=3, folgen=22)})

    with SessionLocal() as db:
        assert await status_poller.check_once(db, load_settings(db)) == 1

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).status == RequestStatus.downloaded


# --- Das Abzeichen: "Bereits geladen" nur, wenn es stimmt ------------------


def test_abzeichen_teils_geladen() -> None:
    """Eine Serie mit Luecken traegt "partial", eine vollstaendige "downloaded".

    Der gemeldete Fall: Baywatch mit einer geladenen Staffel von elf trug
    "Bereits geladen" - wer draufklickte, fand ein fast leeres Regal.
    """
    from app.services.library import _status_for

    halb = SeriesEntry(
        arr_id=1, has_file=True, monitored=True,
        episode_file_count=22, episode_count=44, title_key="x",
        staffeln={
            1: Staffelstand(dateien=22, folgen=22),
            2: Staffelstand(dateien=0, folgen=22),
        },
    )
    assert _status_for(halb) == "partial"

    ganz = SeriesEntry(
        arr_id=2, has_file=True, monitored=True,
        episode_file_count=44, episode_count=44, title_key="x",
        staffeln={
            1: Staffelstand(dateien=22, folgen=22),
            2: Staffelstand(dateien=22, folgen=22),
            # Extras zaehlen nicht als Luecke - niemand versteht eine Serie
            # als unvollstaendig, weil das Bonusmaterial fehlt.
            0: Staffelstand(dateien=0, folgen=5),
        },
    )
    assert _status_for(ganz) == "downloaded"

    leer = SeriesEntry(
        arr_id=3, has_file=False, monitored=True,
        episode_file_count=0, episode_count=22, title_key="x",
        staffeln={1: Staffelstand(dateien=0, folgen=22)},
    )
    assert _status_for(leer) == "searching"


# --- Der Media-Server-Rueckfall bei Staffelanfragen ------------------------


def _plex_kennt_die_serie(db) -> None:
    from app.models import MediaServerLibraryItem

    db.add(
        MediaServerLibraryItem(
            provider="plex",
            media_type=MediaType.tv,
            guid="baywatch-guid",
            title="Baywatch",
            title_key="baywatch",
            tvdb_id=TVDB,
            year=1989,
            # Ohne die Stufe filtert ``vorhandene_kennungen`` die Zeile weg,
            # und der Test pruefte nur, dass der Rueckfall gar nicht greift.
            has_standard=True,
        )
    )
    db.commit()


@pytest.mark.asyncio
async def test_plex_treffer_haelt_geloeschte_staffel_nicht_fest(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ Die Media-Server-Tabelle kennt nur Titel, keine Staffeln.

    Ihr Serien-Treffer ("irgendetwas von Baywatch liegt in Plex") hielt jede
    geloeschte Staffel fuer immer auf "geladen" - sie liess sich nie wieder
    anfragen. Steht die Serie in Sonarr und meldet dort null Dateien, ist
    Sonarr die Autoritaet ueber die Platte.
    """
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _staffelanfrage(db, konto["id"], 3)
        db.get(MediaRequest, anfrage_id).status = RequestStatus.downloaded
        db.commit()
        _plex_kennt_die_serie(db)

    # In Sonarr steht die Serie noch - Staffel 3 aber ohne jede Datei.
    _serie(monkeypatch, {3: Staffelstand(dateien=0, folgen=0)})

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).status == RequestStatus.deleted


@pytest.mark.asyncio
async def test_ohne_sonarr_eintrag_zaehlt_der_plex_treffer_weiter(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Gegenprobe: Ist die Serie ganz aus Sonarr verschwunden, bleibt der
    Titel-Treffer das Beste, was es gibt - "geladen" bleibt stehen (der Fall
    "laden, bis die Qualitaet stimmt, dann aus Sonarr werfen")."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        anfrage_id = _staffelanfrage(db, konto["id"], 3)
        db.get(MediaRequest, anfrage_id).status = RequestStatus.downloaded
        db.commit()
        _plex_kennt_die_serie(db)

    async def leere_bibliothek(_settings: object, _tier: str = "standard") -> tuple[dict, dict]:
        return {}, {}

    monkeypatch.setattr(library, "series_library", leere_bibliothek)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    with SessionLocal() as db:
        assert db.get(MediaRequest, anfrage_id).status == RequestStatus.downloaded


# --- Die Ueberwachungs-Heilung ---------------------------------------------


class _Heiler:
    def __init__(self) -> None:
        self.aufrufe: list[tuple[int, list[int], int | None]] = []

    async def monitor_seasons(self, arr_id, seasons, such_staffel=None):
        self.aufrufe.append((arr_id, sorted(seasons), such_staffel))


@pytest.mark.asyncio
async def test_abgeschaltete_staffel_wird_geheilt(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **Live nachgemessen**: Sonarrs ``addOptions.monitor: "none"`` wirkt
    asynchron und raeumt bei einer frisch angelegten Serie auch die Staffel ab,
    die Nexview unmittelbar danach eingeschaltet hat. Ohne Heilung stuende die
    Anfrage fuer immer auf "wird gesucht"."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _staffelanfrage(db, konto["id"], 1)

    _serie(monkeypatch, {1: Staffelstand(dateien=0, folgen=6, monitored=False)})
    heiler = _Heiler()
    monkeypatch.setattr(library, "sonarr_client", lambda *_a, **_k: heiler)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    assert heiler.aufrufe == [(211, [1], 1)]


@pytest.mark.asyncio
async def test_laufende_ueberwachung_bleibt_unangetastet(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Heilung greift nur, wenn wirklich etwas abgeschaltet ist - sonst
    stiesse jeder Durchgang eine neue Suche an."""
    konto = create_user(arr_client, "kim", "passwort-1234")
    with SessionLocal() as db:
        _staffelanfrage(db, konto["id"], 1)

    _serie(monkeypatch, {1: Staffelstand(dateien=2, folgen=6, monitored=True)})
    heiler = _Heiler()
    monkeypatch.setattr(library, "sonarr_client", lambda *_a, **_k: heiler)

    with SessionLocal() as db:
        await status_poller.check_once(db, load_settings(db))

    assert heiler.aufrufe == []
