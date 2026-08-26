"""Statistik-Seite - fuer Administratoren und Entscheider."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ..deps import AdminUser, ApproverUser, DbSession
from ..models import MediaType, utcnow
from ..services import aufraeumen
from ..services import stats

router = APIRouter(prefix="/api/admin/stats", tags=["admin"])


class UserStatsPublic(BaseModel):
    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    role: str
    total: int
    movies: int
    series: int
    downloaded: int
    pending: int
    rejected: int
    cancelled: int
    failed: int
    ratings: int
    average_rating: float | None
    poor_ratings: int
    success_rate: float | None
    quota_movie_used: int
    quota_movie_limit: int | None
    quota_series_used: int
    quota_series_limit: int | None
    # ⚠️ Neue Felder muessen **auch hier** stehen. Pydantic laesst weg, was es
    # nicht kennt - ohne Fehler und ohne Log. Genau daran ist der Speicherstand
    # beim ersten Anlauf gescheitert: berechnet, aber nie ausgeliefert.
    #
    # Nur im GB-Betrieb gefuellt; sonst ``None``, und die Oberflaeche zeigt
    # weiter die Stueck-Kontingente.
    storage_used_bytes: int | None = None
    storage_limit_bytes: int | None = None


class TotalsPublic(BaseModel):
    requests: int
    movies: int
    series: int
    downloaded: int
    downloaded_movies: int
    downloaded_series: int
    pending: int
    rejected: int
    cancelled: int
    failed: int
    active_users: int
    ratings: int
    average_rating: float | None
    poor_ratings: int
    unanswered_feedback: int
    rating_distribution: dict[int, int]
    last_request_at: datetime | None


class MonthPoint(BaseModel):
    month: str
    movies: int
    series: int


class PopularTitle(BaseModel):
    media_type: str
    tmdb_id: int
    title: str
    poster_path: str | None
    count: int


class StatsPublic(BaseModel):
    totals: TotalsPublic
    users: list[UserStatsPublic]
    history: list[MonthPoint]
    most_requested: list[PopularTitle]


@router.get("", response_model=StatsPublic)
def read_stats(entscheider: ApproverUser, db: DbSession) -> dict:
    """Zahlen zu Anfragen, Downloads, Kontingenten und Bewertungen."""
    zahlen = stats.collect(db)
    return {
        "totals": TotalsPublic(
            **{
                feld: getattr(zahlen["totals"], feld)
                for feld in TotalsPublic.model_fields
            }
        ),
        "users": [
            UserStatsPublic(
                **{feld: getattr(eintrag, feld) for feld in UserStatsPublic.model_fields}
            )
            for eintrag in zahlen["users"]
        ],
        "history": zahlen["history"],
        "most_requested": zahlen["most_requested"],
    }


# --------------------------------------------------------------------------
# Was liegt herum - der Aufraeum-Vorschlag
# --------------------------------------------------------------------------


class AufraeumPosten(BaseModel):
    """Ein Titel oder eine Staffel, die lange niemand angesehen hat."""

    posten_id: int
    media_type: str
    tmdb_id: int | None
    tvdb_id: int | None
    season: int | None
    tier: str
    title: str
    size_bytes: int
    state: str
    #: ``None`` heisst Hausbestand - der haeufigste Fall, siehe Dienst.
    besitzer: str | None
    #: ``None`` heisst: kein verknuepftes Konto hat es je angesehen.
    zuletzt_gesehen: datetime | None
    gesehen_von: list[str]
    bewertung: float | None
    bewertungen: int
    #: Seit wann die Datei da liegt - aus Radarr/Sonarr, nicht geraten.
    liegt_seit: datetime | None
    #: Laeuft schon eine Schonfrist? Dann bleibt die Zeile stehen und wird
    #: markiert - verschwinden zu lassen waere falsch, denn vorgemerkt ist
    #: nicht geloescht.
    loescht_am: datetime | None
    #: Wie viele Tage die Schonfrist noch laeuft. ``None`` ohne Vormerkung.
    tage_uebrig: int | None


class AufraeumGrundlage(BaseModel):
    """Worauf die Liste beruht.

    ⚠️ **Wird immer mitgeliefert, nicht auf Anfrage.** Ohne sie liest sich
    "seit einem halben Jahr niemand angesehen" als Tatsache - dabei heisst es
    nur "keines der verknuepften Konten". Die Oberflaeche schreibt das ueber
    die Tabelle.
    """

    konten_gesamt: int
    konten_verknuepft: int
    ohne_verknuepfung: list[str]
    vollstaendig: bool


class AufraeumListe(BaseModel):
    posten: list[AufraeumPosten]
    gesamt_anzahl: int
    gesamt_bytes: int
    monate: int
    grundlage: AufraeumGrundlage
    #: Wie viele Posten uebergangen wurden, weil ihr Alter noch unbekannt ist.
    #: Direkt nach einem Update ist das alles - bis der naechste stuendliche
    #: Abgleich die Datei-Daten aus Radarr/Sonarr nachtraegt.
    ohne_datum: int


def als_liste(ergebnis: aufraeumen.Liste) -> AufraeumListe:
    return AufraeumListe(
        posten=[
            AufraeumPosten(
                posten_id=k.posten_id,
                media_type=k.media_type.value,
                tmdb_id=k.tmdb_id,
                tvdb_id=k.tvdb_id,
                season=k.season,
                tier=k.tier,
                title=k.title,
                size_bytes=k.size_bytes,
                state=k.state.value,
                besitzer=k.besitzer,
                zuletzt_gesehen=k.zuletzt_gesehen,
                gesehen_von=k.gesehen_von,
                bewertung=k.bewertung,
                bewertungen=k.bewertungen,
                liegt_seit=k.liegt_seit,
                loescht_am=k.loescht_am,
                tage_uebrig=(
                    None
                    if k.loescht_am is None
                    else max(0, (k.loescht_am - utcnow().replace(tzinfo=None)).days)
                ),
            )
            for k in ergebnis.kandidaten
        ],
        gesamt_anzahl=ergebnis.gesamt_anzahl,
        gesamt_bytes=ergebnis.gesamt_bytes,
        monate=ergebnis.monate,
        ohne_datum=ergebnis.ohne_datum,
        grundlage=AufraeumGrundlage(
            konten_gesamt=ergebnis.grundlage.konten_gesamt,
            konten_verknuepft=ergebnis.grundlage.konten_verknuepft,
            ohne_verknuepfung=ergebnis.grundlage.ohne_verknuepfung,
            vollstaendig=ergebnis.grundlage.vollstaendig,
        ),
    )


@router.get("/aufraeumen", response_model=AufraeumListe)
def aufraeum_liste(
    admin: AdminUser,
    db: DbSession,
    monate: int = aufraeumen.MONATE_STANDARD,
    grenze: int = aufraeumen.GRENZE_STANDARD,
    suche: str = "",
    art: MediaType | None = None,
    nur_vorgemerkt: bool = False,
) -> AufraeumListe:
    """Was in der ganzen Bibliothek herumliegt - **samt Hausbestand**.

    ⚠️ **Administratoren, nicht Entscheider** - anders als der Rest dieser
    Seite. Zwei Gruende: Handeln kann hier ohnehin nur der Administrator (ueber
    Speicher entscheidet ``storage.in_den_hausbestand``, und das ist
    ``AdminUser``), und die Liste sagt nebenbei, was **alle** im Haushalt
    laengst nicht mehr angesehen haben. Das ist mehr, als ein Entscheider fuer
    seine Aufgabe braucht.
    """
    return als_liste(
        aufraeumen.liste(
            db,
            monate=monate,
            grenze=grenze,
            suche=suche,
            art=art,
            nur_vorgemerkt=nur_vorgemerkt,
        )
    )
