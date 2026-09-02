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
    # Nur bei Serien von Belang. Standard aus - siehe ``MediaRequest``.
    monitor_future: bool = False
    # Die Antwort auf "welche Serie meinst du?" (Issue #5).
    #
    # ⚠️ **Die einzige Kennung, die von aussen kommen darf - und auch die nur
    # scheinbar.** Der Server faehrt dieselbe Sonarr-Suche noch einmal und
    # nimmt die Zahl nur an, wenn sie darin vorkam
    # (``serien_zuordnung.erlaubt``). Ungeprueft waere sie der Weg, an TMDB
    # und damit an der Altersbeschraenkung vorbei eine beliebige Serie
    # anlegen zu lassen.
    tvdb_id: int | None = Field(default=None, ge=1)
    # Nur bei Serien: welche Staffel? Fehlt sie, ist die ganze Serie gemeint.
    # Staffel 0 sind bei TMDB die Specials - die schliessen wir nicht aus.
    season: int | None = Field(default=None, ge=0, le=200)
    # Nur zusammen mit ``season``: einzelne Folgen statt der ganzen Staffel
    # ("Folgen-Paket"). Fehlt die Liste, ist die ganze Staffel gemeint. Der
    # Dienst sortiert, entfernt Doppelte und prueft die Grenzen.
    episodes: list[int] | None = Field(default=None, max_length=400)
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
    # Das Folgen-Paket, falls die Anfrage einzelne Folgen meint - fuer die
    # Karten-Pille ("S2 · F 3, 7") und den Verlauf.
    episodes: list[int] | None = None
    # Kam die Anfrage von der Merkliste? Der Entscheider soll sehen, dass
    # niemand diesen Titel im Einzelnen ausgesucht hat.
    from_watchlist: bool
    #: Haengt an dieser Anfrage wirklich ein Eintrag in Radarr/Sonarr?
    #:
    #: ⚠️ **Damit das Abbrechen die Wahrheit sagen kann.** Der Rueckfrage-Dialog
    #: kuendigte bisher **immer** an, der Titel werde "aus Radarr bzw. Sonarr
    #: entfernt", das Kontingent werde frei und "bereits heruntergeladene
    #: Dateien werden dabei mitgeloescht". Bei einer **fehlgeschlagenen**
    #: Anfrage stimmt davon nichts: Sie kam nie bis zum Anlegen, ``arr_id``
    #: blieb leer, sie zaehlt auch nicht aufs Kontingent (siehe
    #: ``quota.COUNTED_STATUSES``) - abgebrochen wird nur die Zeile selbst.
    #:
    #: Gemeldet, weil genau diese Warnung Angst gemacht hat: Es standen
    #: Serien in der Liste, die der Betreiber laengst geladen hatte, und der
    #: Satz drohte mit dem Loeschen seiner Dateien.
    #:
    #: Bewusst ein Wahrheitswert und nicht ``arr_id``: Die Nummer ist eine
    #: Interna von Radarr/Sonarr, und die Oberflaeche braucht nur die Antwort.
    arr_linked: bool = False
    requested_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None
    # --- Fuer den Verlauf ---------------------------------------------------
    #
    # Nexview kannte diese Angaben immer und zeigte davon ein einziges
    # Zustandswort. "Warum dauert das?" ist aber die haeufigste Frage, und die
    # Antwort steht hier vollstaendig: wer freigegeben hat und wann zuletzt
    # bei Radarr/Sonarr nachgesehen wurde.
    #
    # ``approved_by_name`` statt der Kennung: Der Anfragende darf die
    # Benutzerliste nicht abrufen, koennte eine Nummer also nicht aufloesen.
    approved_by_name: str | None = None
    last_checked_at: datetime | None = None
    # "Laedt gerade": Prozent aus der Warteschlange (0-100) oder None - eine
    # Momentaufnahme fuer die Pille, kein eigener Status. Siehe MediaRequest.
    laedt_fortschritt: int | None = None
    laedt_seit: datetime | None = None
    rejection_reason: str | None
    # Welche Regel entschieden hat. ``None`` heisst: keine - es galt, was am
    # Konto steht.
    regel_name: str | None = None
    error_message: str | None
    # Kennung samt Werten - das Frontend baut daraus den Satz in der
    # eingestellten Sprache und faellt sonst auf ``error_message`` zurueck.
    error_detail: dict | None = None
    rating: int | None
    feedback: str | None
    rated_at: datetime | None
    # Gilt die Bewertung noch der Datei, die jetzt dort liegt? Siehe
    # ``MediaRequest.rating_outdated``.
    rating_outdated: bool = False
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

    # Laeuft dieser Titel in einem Abo, das **der Anfragende** hat? Namen der
    # Dienste, leer heisst "nein" oder "hat nichts angegeben".
    #
    # ⚠️ Gemessen an *seinen* Abos, nicht an denen des Entscheiders. Der
    # Entscheider hat vielleicht kein Netflix - die Frage ist aber, ob der
    # Anfragende den Film ohne diesen Download sehen kann.
    requester_subscriptions: list[str] = []

    # Steckt der Wunsch eines Kindes dahinter? Dann steht hier sein Name.
    #
    # ⚠️ **Reine Auskunft.** Die Anfrage gehoert dem Elternteil, mit seinem
    # Kontingent und seinem Freigabeweg - daran aendert dieser Name nichts.
    # Er beantwortet nur die Frage, die sich der Entscheider sonst selbst
    # stellt: warum ein Erwachsener einen Kinderfilm bestellt.
    #
    # ``None`` heisst "kein Kinderwunsch" **oder** "das Kind gibt es nicht
    # mehr" - beim Loeschen wird der Verweis auf NULL gesetzt. Beide Faelle
    # sehen gleich aus, und das ist richtig so: Der Name eines geloeschten
    # Kontos gehoert nirgends mehr hin.
    for_child_name: str | None = None


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
