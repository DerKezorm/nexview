"""Die Abfragen-Waage: Waechst die Abfragezahl mit den Daten mit?

⚠️ **Warum es das gibt.** /api/admin/requests stellte 157 Abfragen fuer 144
Zeilen - gefunden am 01.09.2026, und niemandem war es aufgefallen, weil nichts
danach fragte. Am 02.09.2026 gegen die Kopie der echten Datenbank nachgemessen:
158 Abfragen fuer 145 Zeilen, nach Verdopplung der Zeilen 303 fuer 290 - exakt
eine Abfrage je Zeile bei konstantem Sockel von 13. Im Browser sieht man diese
Klasse Fehler nie: Die Seite wird nur langsamer, mit jedem Nutzer ein wenig
mehr, und kein Test schlaegt an.

Gewogen wird deshalb das **Wachstum**, nicht eine feste Zahl: Jede Adresse
wird einmal bei einfacher und einmal bei verdoppelter Saat gemessen, und die
Abfragezahl darf dabei um hoechstens WACHSTUM_ERLAUBT steigen - also gar
nicht. Feste Obergrenzen waeren datenabhaengig: mit Luft wertlos, ohne Luft
bei jeder legitimen Konstantabfrage kaputt. (Die in Punkt 5 umgebauten
Adressen halten ihre Zielzahlen zusaetzlich in test_abfragezahl.py fest.)

⚠️ **UND DIE GRENZE WIRD BEIM ANSCHLAGEN NICHT HOCHGESETZT.** Das ist der
ganze Sinn der Sache - dieselbe Regel wie bei der Gewichts-Waage der
Oberflaeche (frontend/tools/gewicht-pruefen.mjs). Eine Waage, an der man das
Gewicht verstellt, sobald sie anschlaegt, misst nichts mehr - sie bestaetigt
nur noch jeden Zustand. Schlaegt sie an, gehoeren die Abfragen gebuendelt:
``selectinload`` fuer Beziehungen oder **eine** Abfrage ueber alle Kennungen
(Muster: ``requests.my_requests``, ``ratings.fuer_anfragen``). Soll
WACHSTUM_ERLAUBT wirklich steigen, ist das eine Entscheidung von Markus und
keine Zeile nebenbei.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.db import SessionLocal, engine
from app.models import (
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    RequestStatus,
    StorageEntry,
    StorageState,
    User,
)
from app.schemas_media import MediaItem

from .conftest import auth_headers, create_user

# Um so viele Abfragen darf ein Aufruf bei verdoppelter Saat wachsen: um gar
# keine. Alle flachen Adressen wurden mit einem Delta von exakt 0 gemessen;
# jede Abweichung nach oben ist eine Abfrage je Zeile oder je Nutzer.
WACHSTUM_ERLAUBT = 0

GB = 1024**3

# Laufende Titelnummer fuer die Saat - jede Zeile bekommt ihren eigenen Titel,
# damit weder Eindeutigkeits-Indizes noch die Titel-Buendelung der Startseite
# zwei Zeilen zusammenfallen lassen.
_TITELNUMMER = itertools.count(700_001)


# ---------------------------------------------------------------------------
# Horcher und Messung
# ---------------------------------------------------------------------------


@contextmanager
def _gezaehlt() -> Iterator[list[str]]:
    """Jede Abfrage an der Engine mitschreiben - ueber alle Sitzungen.

    Die PRAGMA-Befehle der Verbindungs-Horcher aus ``db.py`` laufen als rohe
    Cursor-Aufrufe an diesem Ereignis vorbei und verschmutzen die Zahl nicht;
    der Sockel war in allen Messungen exakt konstant.
    """
    saetze: list[str] = []

    def horcher(conn, cursor, statement, parameters, context, executemany) -> None:
        saetze.append(statement)

    event.listen(engine, "before_cursor_execute", horcher)
    try:
        yield saetze
    finally:
        # Der Horcher haengt am prozessweiten Engine. Die Zahlen der anderen
        # Tests verfaelscht ein haengengebliebener nicht (jede Messung fuehrt
        # ihre eigene Liste und liest sie am Ende des with-Blocks), aber er
        # schriebe bis zum Prozessende jede Abfrage der Testreihe mit.
        event.remove(engine, "before_cursor_execute", horcher)


def _gemessen(
    client: TestClient, pfad: str, kopf: dict[str, str] | None = None
) -> tuple[int, list[str]]:
    """Ein aufwaermender Aufruf, dann der gezaehlte - beide muessen 200 sein."""
    aufwaermer = client.get(pfad, headers=kopf)
    assert aufwaermer.status_code == 200, aufwaermer.text
    with _gezaehlt() as saetze:
        antwort = client.get(pfad, headers=kopf)
    assert antwort.status_code == 200, antwort.text
    return len(saetze), saetze


def _darf_nicht_wachsen(pfad: str, vorher: int, nachher: int, saetze: list[str]) -> None:
    """Die Waage selbst: schlaegt sie an, steht der Taeter in der Meldung."""
    if nachher - vorher <= WACHSTUM_ERLAUBT:
        return
    haeufigste, wie_oft = Counter(saetze).most_common(1)[0]
    pytest.fail(
        f"{pfad}: {vorher} Abfragen vor und {nachher} nach der Verdopplung der "
        f"Saat, Wachstum {nachher - vorher} (erlaubt {WACHSTUM_ERLAUBT}). "
        f"Eine Abfrage je Zeile oder je Nutzer gehoert gebuendelt, nicht die "
        f"Grenze hochgesetzt. Haeufigste Abfrage ({wie_oft}x):\n{haeufigste}"
    )


# ---------------------------------------------------------------------------
# Saat
# ---------------------------------------------------------------------------


def _satz_fuer(session, kennung: int, entscheider: int, mit_geladenem: bool) -> None:
    """Ein Satz Zeilen fuer ein Konto: 5 Anfragen, 5 Meldungen, 2 Posten."""
    jetzt = datetime.now(timezone.utc).replace(tzinfo=None)
    stati = [
        RequestStatus.pending_approval,
        RequestStatus.approved,
        RequestStatus.downloaded if mit_geladenem else RequestStatus.searching,
        RequestStatus.rejected,
        RequestStatus.failed,
    ]
    for status in stati:
        nummer = next(_TITELNUMMER)
        session.add(
            MediaRequest(
                user_id=kennung,
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=nummer,
                title=f"Waage-Titel {nummer}",
                status=status,
                requested_at=jetzt,
                # Mit Entscheider, sobald entschieden wurde: Nur eine Zeile,
                # deren ``approver`` wirklich jemand ist, kann ein entferntes
                # ``selectinload`` als Nachladen je Zeile entlarven.
                approved_by=(
                    None if status == RequestStatus.pending_approval else entscheider
                ),
                approved_at=(
                    None if status == RequestStatus.pending_approval else jetzt
                ),
                completed_at=jetzt if status == RequestStatus.downloaded else None,
            )
        )
    for _ in range(5):
        session.add(
            Notification(
                user_id=kennung,
                type=NotificationType.approved,
                message_key="request_approved",
                message_title="Waage",
            )
        )
    for _ in range(2):
        nummer = next(_TITELNUMMER)
        session.add(
            StorageEntry(
                key=f"movie:standard:tmdb:{nummer}",
                user_id=kennung,
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=nummer,
                title=f"Waage-Posten {nummer}",
                size_bytes=GB,
                state=StorageState.owned,
            )
        )


def _saetze_fuer_runde(session, kennungen: list[int], verschiebung: int) -> None:
    """Je Konto ein Satz; der Entscheider ist ein **anderes** Konto der Runde.

    Die Verschiebung wechselt je Runde, damit dasselbe Konto in Runde 2 einen
    anderen Entscheider bekommt - erst dadurch waechst die Zahl der zu
    ladenden Entscheider mit, und ein Nachladen je Zeile wird messbar.
    """
    for stelle, kennung in enumerate(kennungen):
        _satz_fuer(
            session,
            kennung,
            entscheider=kennungen[(stelle + verschiebung) % len(kennungen)],
            # Nur die ersten beiden Konten je Runde bekommen einen geladenen
            # Titel - siehe die LIMIT-Rechnung in ``_haushalt``.
            mit_geladenem=stelle < 2,
        )


def _haushalt(client: TestClient, runde: int) -> None:
    """Die Saat: Runde 1 legt vier Konten mit je einem Satz an, Runde 2
    verdoppelt **beide** Achsen - vier neue Konten mit je einem Satz UND ein
    zweiter Satz fuer jedes Konto aus Runde 1. So schlagen beide N+1-Klassen
    in derselben Probe an: die Je-Nutzer-Klasse (mehr Konten) und die
    Je-Zeile-Klasse (mehr Zeilen je Konto).

    ⚠️ Achse unter jedem LIMIT halten, sonst ist die Probe blind: Eine Abfrage
    je Zeile auf einer Liste, die immer gleich viele Zeilen liefert, waechst
    nicht mit. Deshalb geladene Titel nur bei zwei Konten je Satz (2 nach
    Runde 1, 6 nach Runde 2, Startseite home.py LIMIT = 12), Meldungen 5 je
    Konto und Runde (10 unter ?limit=100), Posten 2 je Konto und Runde
    (4 unter storage.JE_SEITE = 20).
    """
    namen = [f"waage-r{runde}-u{stelle}" for stelle in range(1, 5)]
    for name in namen:
        create_user(client, name)

    with SessionLocal() as session:
        def _kennungen(liste: list[str]) -> list[int]:
            gefunden = {
                zeile.username: zeile.id
                for zeile in session.scalars(
                    select(User).where(User.username.in_(liste))
                )
            }
            return [gefunden[name] for name in liste]

        _saetze_fuer_runde(session, _kennungen(namen), verschiebung=1)
        if runde == 2:
            alte = [f"waage-r1-u{stelle}" for stelle in range(1, 5)]
            _saetze_fuer_runde(session, _kennungen(alte), verschiebung=2)
        session.commit()


def _wachstumsprobe(
    client: TestClient, pfad: str, benutzer: str | None = None
) -> None:
    """Der immer gleiche Gang: Saat, Messung, doppelte Saat, Messung."""
    _haushalt(client, 1)
    kopf = (
        auth_headers(client, benutzer, "passwort-1234") if benutzer else None
    )
    vorher, _ = _gemessen(client, pfad, kopf)
    _haushalt(client, 2)
    nachher, saetze = _gemessen(client, pfad, kopf)
    _darf_nicht_wachsen(pfad, vorher, nachher, saetze)


# ---------------------------------------------------------------------------
# Die Adressen auf der Waage
# ---------------------------------------------------------------------------


def test_freigabeliste_waechst_nicht(arr_client: TestClient) -> None:
    """/api/admin/requests - der Anlass fuer die Waage (157 Abfragen, s. oben)."""
    _wachstumsprobe(arr_client, "/api/admin/requests")


def test_benutzerliste_waechst_nicht(arr_client: TestClient) -> None:
    """/api/users - zaehlte vor Punkt 5 zweimal je Konto."""
    _wachstumsprobe(arr_client, "/api/users")


def test_speicher_uebersicht_waechst_nicht(arr_client: TestClient) -> None:
    """/api/storage/overview - die Je-Nutzer-Klasse in Reinform."""
    _wachstumsprobe(arr_client, "/api/storage/overview")


def test_eigene_anfragen_wachsen_nicht(arr_client: TestClient) -> None:
    """/api/requests/mine - v1-Zusage; buendelt Entscheider und Bewertungen."""
    _wachstumsprobe(arr_client, "/api/requests/mine", benutzer="waage-r1-u1")


def test_startseite_waechst_nicht(arr_client: TestClient) -> None:
    """/api/v1/home/recent - v1-Zusage; die Achse bleibt unter LIMIT = 12."""
    _wachstumsprobe(arr_client, "/api/v1/home/recent")


def test_eigener_speicher_waechst_nicht(arr_client: TestClient) -> None:
    """/api/storage/me - v1-Zusage; an der echten Datenbank flach bei 8584 Posten."""
    _wachstumsprobe(arr_client, "/api/storage/me", benutzer="waage-r1-u1")


def test_meldungen_wachsen_nicht(arr_client: TestClient) -> None:
    """/api/notifications - der Handler hat kein Je-Zeile-Recht auf die Bank."""
    _wachstumsprobe(
        arr_client, "/api/notifications?limit=100", benutzer="waage-r1-u1"
    )


def test_merkliste_waechst_nicht(
    arr_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/watchlist/plex - bewacht die Batch-Natur der Abzeichen-Maschine.

    ``badges_for``, ``gesperrte_kennungen``, ``vorhandene_kennungen`` und
    ``gesehene_kennungen`` bedienen auch den ganzen Katalog - aber ``discover``
    haengt an TMDB und laesst sich nicht ehrlich wiegen. Diese eine Adresse
    kostet dafuer einen Mock: ``watchlist.lesen`` liefert erfundene Titel, die
    Achse ist deren Anzahl.
    """
    assert (
        arr_client.put("/api/settings", json={"watchlist_enabled": True}).status_code
        == 200
    )

    umfang = {"titel": 6}

    async def merkliste(_db, _settings, _user):
        filme = [
            MediaItem(
                media_type=MediaType.movie,
                tmdb_id=800_000 + stelle,
                title=f"Waage-Film {stelle}",
            )
            for stelle in range(umfang["titel"])
        ]
        serien = [
            MediaItem(
                media_type=MediaType.tv,
                tmdb_id=900_000 + stelle,
                title=f"Waage-Serie {stelle}",
            )
            for stelle in range(umfang["titel"])
        ]
        return filme, serien, 0

    monkeypatch.setattr("app.services.watchlist.lesen", merkliste)

    # Beide Achsen verdoppeln: die Merklisten-Titel selbst und der Haushalt
    # dahinter, gegen den die Abzeichen abgeglichen werden.
    _haushalt(arr_client, 1)
    vorher, _ = _gemessen(arr_client, "/api/watchlist/plex")
    _haushalt(arr_client, 2)
    umfang["titel"] = 12
    nachher, saetze = _gemessen(arr_client, "/api/watchlist/plex")
    _darf_nicht_wachsen("/api/watchlist/plex", vorher, nachher, saetze)
