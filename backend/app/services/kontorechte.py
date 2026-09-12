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

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Hausordnung, Role
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
KEIN_KONTINGENT = "admin_no_quota"
KEINE_HAUSORDNUNG = "no_house_rules"
ADMIN_NICHT_GEFRAGT = "admin_not_asked"

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


@dataclass(frozen=True)
class Bewertung:
    kontingent: Stand
    auto_approve_movies: Stand
    auto_approve_series: Stand
    can_request_uhd_movies: Stand
    can_request_uhd_series: Stand
    auto_approve_uhd: Stand
    hausordnung: Stand

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


def bewerten(
    settings: AppSettings, wunsch: Wunsch, *, hausordnung_veroeffentlicht: bool
) -> Bewertung:
    """Jeden Wunsch gegen die Einrichtung des Hauses halten."""
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

    def uhd(media_type: str, gewuenscht: bool, grund: str) -> Stand:
        # Die Instanz zuerst: Ohne sie hilft auch die Rolle nicht, die Anfrage
        # scheitert beim Anfragen an ``arr_configured``.
        if not settings.arr_configured(media_type, "uhd"):
            return Stand(frei=False, wirkt=False, grund=grund)
        if aus_rolle is not None:
            return aus_rolle
        return Stand(frei=True, wirkt=gewuenscht)

    uhd_filme = uhd("movie", wunsch.can_request_uhd_movies, KEINE_4K_INSTANZ_FILME)
    uhd_serien = uhd("tv", wunsch.can_request_uhd_series, KEINE_4K_INSTANZ_SERIEN)

    # Sofort freigeben in 4K lohnt nur, wo 4K erlaubt ist und nicht ohnehin der
    # Entscheider waehlt. Sonst waere es ein Haken, der nichts bewirkt.
    #
    # Der Grund nennt, was helfen wuerde: Gibt es eine 4K-Instanz ohne
    # Entscheider, fehlt dort nur das Recht. Waehlt er bei jeder, hilft kein
    # Haekchen. Bis zum 12.09.2026 hiess beides "erst 4K erlauben".
    ohne_entscheider = [
        stand
        for media_type, stand in (("movie", uhd_filme), ("tv", uhd_serien))
        if settings.arr_configured(media_type, "uhd")
        and not settings.approver_picks_target(media_type, "uhd")
    ]
    if not settings.uhd_available:
        auto_uhd = Stand(frei=False, wirkt=False, grund=KEINE_4K_INSTANZ)
    elif aus_rolle is not None:
        auto_uhd = aus_rolle
    elif any(stand.wirkt for stand in ohne_entscheider):
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
    )


def veroeffentlichte_hausordnung(db: Session) -> Hausordnung | None:
    """Die Hausordnung, wenn ihr Text veroeffentlicht ist - sonst nichts.

    Ein Entwurf gehoert dem Betreiber allein; eine Einladung zeigt ihn nie.
    """
    ordnung = db.get(Hausordnung, 1)
    return ordnung if ordnung is not None and ordnung.veroeffentlicht else None
