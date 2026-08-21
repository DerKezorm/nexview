"""Datenformate rund um Anfragen."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import MediaType, QualityTier, QuotaPeriod, RequestStatus


class RequestCreate(BaseModel):
    """Was die Oberflaeche zum Anlegen schickt.

    Titel, Poster und TVDB-Kennung holt der Server selbst von TMDB - so kann
    niemand ueber den Browser falsche Angaben unterschieben.
    """

    media_type: MediaType
    # Welche Instanz? Fehlt die Angabe, ist die Standard-Stufe gemeint - so
    # bleiben aeltere Aufrufe und die Oberflaeche ohne 4K unveraendert gueltig.
    tier: QualityTier = QualityTier.standard
    tmdb_id: int = Field(ge=1)
    # Darf nur fehlen, wenn der Entscheider das Ziel erst bei der Freigabe
    # waehlt (``approver_picks_target``). Sonst lehnt der Dienst die Anfrage
    # ab - ohne Profil koennte Radarr nichts damit anfangen.
    quality_profile_id: int | None = Field(default=None, ge=1)
    # Darf fehlen: welcher Ordner tatsaechlich gilt, entscheidet der Server.
    # Hat der Administrator die Auswahl abgeschaltet, wird ein hier
    # mitgeschickter Wert bewusst ignoriert - sonst waere die Einstellung
    # blosse Kosmetik, die sich mit einem selbstgebauten Aufruf umgehen liesse.
    root_folder_path: str | None = Field(default=None, max_length=500)
    # Nur bei Serien: welche Staffel? Fehlt sie, ist die ganze Serie gemeint.
    # Staffel 0 sind bei TMDB die Specials - die schliessen wir nicht aus.
    season: int | None = Field(default=None, ge=0, le=200)
    # Kam der Klick von der Merklisten-Seite? Reine Herkunftsangabe: Am Ablauf
    # aendert sie nichts, sie macht die Anfrage nur nachtraeglich zuordenbar.
    from_watchlist: bool = False


class FeedbackCreate(BaseModel):
    """Rueckmeldung des Anfragenden zur Qualitaet des Downloads."""

    rating: int = Field(ge=0, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackReply(BaseModel):
    """Antwort eines Administrators auf eine Rueckmeldung."""

    reply: str = Field(min_length=1, max_length=1000)


class RequestPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_type: MediaType
    # Welche Instanz? Steht an der Anfrage selbst, damit eine laufende
    # 4K-Anfrage auch dann noch als solche erkennbar bleibt, wenn der
    # Administrator die zweite Instanz wieder herausnimmt.
    tier: QualityTier
    tmdb_id: int
    title: str
    poster_path: str | None
    release_date: str | None
    status: RequestStatus
    quality_profile_id: int | None
    root_folder_path: str | None
    season: int | None
    # Kam die Anfrage von der Merkliste? Der Entscheider soll sehen, dass
    # niemand diesen Titel im Einzelnen ausgesucht hat.
    from_watchlist: bool
    requested_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None
    rejection_reason: str | None
    error_message: str | None
    rating: int | None
    feedback: str | None
    rated_at: datetime | None
    feedback_reply: str | None
    replied_at: datetime | None


class AnfragerSpeicher(BaseModel):
    """Wo der Anfragende beim Speicher steht - fuer die Freigabe-Entscheidung.

    **Warum ein Entscheider das sehen darf, obwohl ``/storage/overview``
    admin-only ist:** Die Uebersicht ist eine *Rangliste ueber alle* - eine
    Vergleichsauskunft ueber Personen, die niemanden etwas angeht, der sie
    nicht braucht. Hier steht **eine** Zahl ueber **die eine** Person, deren
    Anfrage gerade vor dem Entscheider liegt, im Augenblick der Entscheidung.
    Dieselbe Kategorie wie "3 von 5 Anfragen verbraucht".

    Ohne sie gaebe es die Warnung dort nicht, wo entschieden wird - und
    Sammelfreigaben sind genau der Weg, auf dem ein Konto unbemerkt weit ins
    Minus rutscht.
    """

    used_bytes: int
    # None heisst unbegrenzt.
    limit_bytes: int | None
    # Liegt das Konto **schon jetzt** auf oder ueber der Grenze? Dieselbe
    # Bedeutung wie bei ``QuotaInfo.exhausted``.
    exhausted: bool


class RequestWithUser(RequestPublic):
    """Fuer die Freigabe-Uebersicht: wer hat was angefragt.

    Die Benutzer-Kennung wird mitgeliefert, damit auch Entscheider gruppieren
    und sammelfreigeben koennen - sie duerfen die Benutzerliste nicht abrufen.
    """

    user_id: int
    username: str
    display_name: str | None
    avatar_url: str | None
    # Nur gesetzt, wenn Speicher-Kontingente eingeschaltet sind. ``None`` heisst
    # "diese Waehrung gilt hier nicht" - es gilt immer nur eine.
    storage: AnfragerSpeicher | None = None


class QuotaInfo(BaseModel):
    limit: int | None
    used: int
    remaining: int | None
    unlimited: bool
    exhausted: bool
    period: QuotaPeriod
    resets_at: datetime | None


class QuotaOverview(BaseModel):
    movie: QuotaInfo
    tv: QuotaInfo
    auto_approve: bool
