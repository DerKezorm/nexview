"""Welche Rechte ein Konto nach der Einrichtung des Hauses ueberhaupt haben kann.

⚠️ **Eine Stelle fuer alle, die das wissen muessen.** Der Einladungsassistent
fragt hier, was er anbieten darf, das Einloesen einer Einladung, was davon noch
gilt, und der Kontodialog, welche Haken er frei gibt und warum nicht. Stuende
die Regel an zwei Stellen, liefe sie auseinander, sobald jemand eine davon
anfasst, und eine Einladung vergaebe dann mehr, als das Haus hergibt.

Neu sind die Regeln nicht. Sie stehen verstreut im Code und werden dort beim
Anfragen weiterhin durchgesetzt; das bleibt das zweite Netz:

* Administratoren und Entscheider geben sich selbst frei und duerfen 4K
  anfragen (``User.auto_approve_for``, ``User.may_request_uhd``).
* Administratoren haben weder Stueck- noch Speicherkontingent
  (``quota._limit_for``, ``storage.grenze_in_bytes``).
* Waehlt der Entscheider Ordner oder Profil, wartet jede Anfrage trotz
  Auto-Freigabe (``requests_service``, ``ziel_erst_bei_freigabe``).
* 4K braucht das Recht **und** eine eingerichtete 4K-Instanz (ebenda).
* Administratoren legt niemand die Hausordnung vor, Entscheidern schon
  (``UNBETEILIGT`` in ``routers/hausordnung.py``).

⚠️ **Der Grund ist eine Kennung, kein Satz.** Die Oberflaeche zeigt die
eingestellte Sprache; ein deutscher Satz von hier stuende in einer englischen
Oberflaeche auf Deutsch da.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import models
from ..models import Hausordnung, Role
from . import fassungen
from .mediaserver import PROVIDERS, verbindung_fuer
from .settings_service import AppSettings

# Warum ein Recht nicht frei ist. Die Oberflaeche uebersetzt die Kennungen.
ROLLE_ADMIN = "role_admin"
ROLLE_ENTSCHEIDER = "role_approver"
# Waehlt der Entscheider, dann Zielordner und Profil zusammen: Die Einstellungen
# lassen nur beides oder keines zu (``target_and_profile_together``).
ENTSCHEIDER_WAEHLT = "approver_picks_target"
ENTSCHEIDER_WAEHLT_4K = "approver_picks_uhd_target"
KEINE_4K_INSTANZ = "no_uhd_instance"
KEINE_4K_INSTANZ_FILME = "no_uhd_instance_movie"
KEINE_4K_INSTANZ_SERIEN = "no_uhd_instance_tv"
UHD_ERST_ERLAUBEN = "uhd_needs_permission"
# Dieselben zwei Gruende je Fassung, fuer die Schalter ``fassung:<kennung>:*``.
FASSUNG_ERST_ERLAUBEN = "fassung_needs_permission"
# Eine Fassung, die jeder anfragen darf, braucht keinen Haken am Konto.
FASSUNG_OFFEN = "fassung_open_to_all"
KEIN_KONTINGENT = "admin_no_quota"
KEINE_HAUSORDNUNG = "no_house_rules"
ADMIN_NICHT_GEFRAGT = "admin_not_asked"
# Zugang zu einem Medienserver vergibt eine Einladung nur, wenn er verbunden ist.
SERVER_NICHT_VERBUNDEN = "server_not_connected"

#: Die Schalter, die am Konto landen - in der Reihenfolge der Oberflaeche.
SCHALTER = (
    "auto_approve_movies",
    "auto_approve_series",
    "can_request_uhd_movies",
    "can_request_uhd_series",
    "auto_approve_uhd",
)


@dataclass(frozen=True)
class Stand:
    """Ein Recht, wie es nach den Regeln des Hauses dasteht."""

    #: Darf der Administrator es umlegen?
    frei: bool
    #: Was nach dem Einloesen tatsaechlich gilt.
    wirkt: bool
    #: Warum es nicht frei ist. Ein gesperrter Haken ohne Grund wirkt wie ein Fehler.
    grund: str | None = None


@dataclass(frozen=True)
class Wunsch:
    """Was der Administrator ankreuzt - Wuensche, noch keine Rechte."""

    rolle: Role
    auto_approve_movies: bool = False
    auto_approve_series: bool = False
    can_request_uhd_movies: bool = False
    can_request_uhd_series: bool = False
    auto_approve_uhd: bool = False
    hausordnung: bool = False
    #: Wuensche je Fassung als ``(kennung, anfragen, auto_freigabe)``. Fuer die
    #: beiden 4K-Fassungen des ARR-Betriebs gelten ohne Eintrag die alten
    #: Felder darueber - so sprechen Assistent und Kontodialog bis zum Umbau
    #: der Oberflaeche weiter ihre Namen.
    fassungen: tuple[tuple[str, bool, bool], ...] = ()

    def fassung_wunsch(self, kennung: str) -> tuple[bool, bool]:
        """Was fuer diese Fassung gewuenscht ist: ``(anfragen, auto_freigabe)``."""
        for eigene, anfragen, auto in self.fassungen:
            if eigene == kennung:
                return anfragen, auto
        if kennung == models.UHD_FILME:
            return self.can_request_uhd_movies, self.auto_approve_uhd
        if kennung == models.UHD_SERIEN:
            return self.can_request_uhd_series, self.auto_approve_uhd
        return False, False


@dataclass(frozen=True)
class FassungStand:
    """Die beiden Schalter einer Fassung, die nicht offen fuer alle ist."""

    anfragen: Stand
    auto: Stand


def schalter_name(kennung: str, feld: str) -> str:
    """Der Name eines Schalters je Fassung: ``fassung:<kennung>:anfragen|auto``."""
    return f"fassung:{kennung}:{feld}"


@dataclass(frozen=True)
class Bewertung:
    kontingent: Stand
    auto_approve_movies: Stand
    auto_approve_series: Stand
    can_request_uhd_movies: Stand
    can_request_uhd_series: Stand
    auto_approve_uhd: Stand
    hausordnung: Stand
    #: Je Fassung, die nicht offen fuer alle ist - die alten 4K-Felder oben
    #: sind daraus abgeleitet.
    fassungen: dict[str, FassungStand] = field(default_factory=dict)

    def entfallen(self, wunsch: Wunsch) -> list[str]:
        """Gewuenschte Schalter, die nicht wirken - fuer die Meldung an den Admin.

        Was aus der Rolle folgt, entfaellt nicht: Es wirkt ja, nur ohne Haken.
        """
        return [
            name
            for name in (*SCHALTER, "hausordnung")
            if getattr(wunsch, name) and not getattr(self, name).wirkt
        ]

    def werte_fuers_konto(self, wunsch: Wunsch) -> dict[str, bool]:
        """Was am neuen Konto gespeichert wird.

        Nur, was frei ist **und** gewuenscht wurde. Was aus der Rolle folgt,
        bleibt ``False``: Die Rolle gibt es ohnehin, und ein gespeicherter Haken
        kaeme nach einem spaeteren Herabstufen als Ueberraschung zum Vorschein.
        """
        return {
            name: bool(getattr(wunsch, name)) and getattr(self, name).frei
            for name in SCHALTER
        }


def _fassung_bewerten(
    settings: AppSettings, fassung: fassungen.FassungInfo, aus_rolle: Stand | None, wunsch: Wunsch
) -> FassungStand:
    """Die beiden Schalter einer Fassung - dieselben Regeln wie bisher fuer 4K.

    Die Fassung ist eingerichtet (sonst stuende sie nicht in
    ``settings.fassungen_fuer``). Die Rolle schlaegt alles; Sofort-Freigabe
    gibt es nur, wo nicht ohnehin der Entscheider waehlt und das Anfragen
    selbst wirkt.
    """
    gew_anfragen, gew_auto = wunsch.fassung_wunsch(fassung.kennung)
    if aus_rolle is not None:
        return FassungStand(anfragen=aus_rolle, auto=aus_rolle)
    anfragen = Stand(frei=True, wirkt=gew_anfragen)
    stufe = fassungen.stufe(fassung.kennung).value
    if settings.approver_picks_target(fassung.media_type, stufe):
        auto = Stand(frei=False, wirkt=False, grund=ENTSCHEIDER_WAEHLT)
    elif anfragen.wirkt:
        auto = Stand(frei=True, wirkt=gew_auto)
    else:
        auto = Stand(frei=False, wirkt=False, grund=FASSUNG_ERST_ERLAUBEN)
    return FassungStand(anfragen=anfragen, auto=auto)


def bewerten(
    settings: AppSettings,
    wunsch: Wunsch,
    *,
    hausordnung_veroeffentlicht: bool,
    offene: frozenset[str] | None = None,
) -> Bewertung:
    """Jeden Wunsch gegen die Einrichtung des Hauses halten.

    ``offene`` sind die Kennungen der Fassungen, die jeder anfragen darf
    (``fassungen.offene_kennungen``); fuer sie gibt es keine Schalter. Ohne
    Angabe gilt die Vorgabe des ARR-Betriebs: Standard offen, 4K nicht.
    """
    if wunsch.rolle == Role.admin:
        aus_rolle: Stand | None = Stand(frei=False, wirkt=True, grund=ROLLE_ADMIN)
    elif wunsch.rolle == Role.approver:
        aus_rolle = Stand(frei=False, wirkt=True, grund=ROLLE_ENTSCHEIDER)
    else:
        aus_rolle = None

    def sofort(media_type: str, gewuenscht: bool) -> Stand:
        # Die Rolle zuerst: Fuer Entscheider gilt "der Entscheider waehlt" nicht,
        # sie waehlen ja selbst (``ziel_erst_bei_freigabe``).
        if aus_rolle is not None:
            return aus_rolle
        if settings.approver_picks_target(media_type):
            return Stand(frei=False, wirkt=False, grund=ENTSCHEIDER_WAEHLT)
        return Stand(frei=True, wirkt=gewuenscht)

    # Je Fassung, die nicht offen fuer alle ist, zwei Schalter. Die alten
    # 4K-Namen darunter sind eine Sicht darauf, bis die Oberflaeche Fassungen
    # spricht (Bauplan NEX-Modus, Scheibe 3).
    offen = offene if offene is not None else fassungen.ARR_OFFEN
    je_fassung = {
        f.kennung: _fassung_bewerten(settings, f, aus_rolle, wunsch)
        for art in ("movie", "tv")
        for f in settings.fassungen_fuer(art)
        if f.kennung not in offen
    }

    def uhd(kennung: str, grund: str) -> Stand:
        # Die Instanz zuerst: Ohne sie hilft auch die Rolle nicht, die Anfrage
        # scheitert beim Anfragen an ``arr_configured``.
        stand = je_fassung.get(kennung)
        if stand is not None:
            return stand.anfragen
        if kennung in offen and settings.fassung(kennung) is not None:
            return Stand(frei=False, wirkt=True, grund=FASSUNG_OFFEN)
        return Stand(frei=False, wirkt=False, grund=grund)

    # Ueber das Modul, nicht als Namen: Grossgeschriebene Namen hier sind
    # Gruende, und ``test_kontorechte`` sucht jedem einen Text.
    uhd_filme = uhd(models.UHD_FILME, KEINE_4K_INSTANZ_FILME)
    uhd_serien = uhd(models.UHD_SERIEN, KEINE_4K_INSTANZ_SERIEN)

    # Sofort freigeben in 4K lohnt nur, wo 4K erlaubt ist und nicht ohnehin der
    # Entscheider waehlt. Sonst waere es ein Haken, der nichts bewirkt.
    #
    # Der Grund nennt, was helfen wuerde: Gibt es eine 4K-Instanz ohne
    # Entscheider, fehlt dort nur das Recht. Waehlt er bei jeder, hilft kein
    # Haekchen. Bis zum 12.09.2026 hiess beides "erst 4K erlauben".
    uhd_staende = [
        je_fassung[k] for k in (models.UHD_FILME, models.UHD_SERIEN) if k in je_fassung
    ]
    ohne_entscheider = [s for s in uhd_staende if s.auto.grund != ENTSCHEIDER_WAEHLT]
    if not settings.uhd_available:
        auto_uhd = Stand(frei=False, wirkt=False, grund=KEINE_4K_INSTANZ)
    elif aus_rolle is not None:
        auto_uhd = aus_rolle
    elif any(s.auto.frei for s in ohne_entscheider):
        auto_uhd = Stand(frei=True, wirkt=wunsch.auto_approve_uhd)
    elif ohne_entscheider:
        auto_uhd = Stand(frei=False, wirkt=False, grund=UHD_ERST_ERLAUBEN)
    else:
        auto_uhd = Stand(frei=False, wirkt=False, grund=ENTSCHEIDER_WAEHLT_4K)

    # Ohne veroeffentlichten Text gibt es nichts zu zeigen; Administratoren
    # schreiben ihn und bekommen ihn deshalb nicht vorgelegt.
    if not hausordnung_veroeffentlicht:
        hausordnung = Stand(frei=False, wirkt=False, grund=KEINE_HAUSORDNUNG)
    elif wunsch.rolle == Role.admin:
        hausordnung = Stand(frei=False, wirkt=False, grund=ADMIN_NICHT_GEFRAGT)
    else:
        hausordnung = Stand(frei=True, wirkt=wunsch.hausordnung)

    return Bewertung(
        kontingent=(
            Stand(frei=False, wirkt=False, grund=KEIN_KONTINGENT)
            if wunsch.rolle == Role.admin
            else Stand(frei=True, wirkt=True)
        ),
        auto_approve_movies=sofort("movie", wunsch.auto_approve_movies),
        auto_approve_series=sofort("tv", wunsch.auto_approve_series),
        can_request_uhd_movies=uhd_filme,
        can_request_uhd_series=uhd_serien,
        auto_approve_uhd=auto_uhd,
        hausordnung=hausordnung,
        fassungen=je_fassung,
    )


def server_stand(settings: AppSettings, provider: str) -> Stand:
    """Kann eine Einladung auf diesem Medienserver Zugang verschaffen?

    ``wirkt`` bleibt hier ``False``: Ob die Person Zugang bekommt, entscheidet
    erst die Auswahl im Assistenten. Hier steht nur, ob es geht, und zwar fuer
    alle drei Anbieter nach derselben Regel.
    """
    verbindung = verbindung_fuer(settings, provider)
    if provider not in PROVIDERS or verbindung is None or not verbindung.nutzbar:
        return Stand(frei=False, wirkt=False, grund=SERVER_NICHT_VERBUNDEN)
    return Stand(frei=True, wirkt=False)


def veroeffentlichte_hausordnung(db: Session) -> Hausordnung | None:
    """Die Hausordnung, wenn ihr Text veroeffentlicht ist - sonst nichts.

    Ein Entwurf gehoert dem Betreiber allein; eine Einladung zeigt ihn nie.
    """
    ordnung = db.get(Hausordnung, 1)
    return ordnung if ordnung is not None and ordnung.veroeffentlicht else None
