"""Eine Einladung einloesen - gleich, auf welchem Weg die Person hereinkommt.

Es gibt drei Wege: das Formular hinter dem Link (``routers/onboarding.py``),
die Anmeldung ueber den Medienserver (``mediaserver_accounts._anlegen``) und
die ueber einen Anmeldedienst (``oidc_accounts``). Alle drei landen hier.

⚠️ **Warum eine eigene Stelle.** Vorher las jeder Weg die Einladung selbst und
uebernahm Rolle, Kontingent und Profilsperren. Kam etwas dazu, musste es an
drei Stellen nachgetragen werden - und wer sich mit Plex anmeldete statt ueber
den Link, haette still weniger bekommen, als der Administrator eingestellt hat.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuthToken, EinladungsServer, Hausordnung, NotificationType, Role, User
from . import kontorechte, notify
from .settings_service import load_settings


def bewerten(
    db: Session, token: AuthToken
) -> tuple[kontorechte.Wunsch, kontorechte.Bewertung, Hausordnung | None]:
    """Was von der Einladung heute noch gilt.

    Zwischen Einladen und Einloesen koennen Tage liegen, und das Haus kann sich
    geaendert haben: eine 4K-Instanz weg, die Hausordnung zurueckgezogen. Es
    gilt der Stand von jetzt, gefragt wird dieselbe Stelle wie beim Anlegen.
    Die Hausordnung kommt nur zurueck, wenn sie gezeigt werden soll und darf.
    """
    ordnung = kontorechte.veroeffentlichte_hausordnung(db)
    wunsch = kontorechte.Wunsch(
        rolle=token.invite_role or Role.user,
        auto_approve_movies=token.invite_auto_approve_movies,
        auto_approve_series=token.invite_auto_approve_series,
        can_request_uhd_movies=token.invite_can_request_uhd_movies,
        can_request_uhd_series=token.invite_can_request_uhd_series,
        auto_approve_uhd=token.invite_auto_approve_uhd,
        hausordnung=token.invite_hausordnung,
    )
    bewertung = kontorechte.bewerten(
        load_settings(db), wunsch, hausordnung_veroeffentlicht=ordnung is not None
    )
    return wunsch, bewertung, (ordnung if bewertung.hausordnung.wirkt else None)


def kontowerte(
    token: AuthToken, wunsch: kontorechte.Wunsch, bewertung: kontorechte.Bewertung
) -> dict[str, object]:
    """Was die Einladung am neuen Konto einstellt - fertig fuer ``User(...)``."""
    return {
        "role": wunsch.rolle,
        "quota_movies_limit": token.invite_quota_movies,
        "quota_series_limit": token.invite_quota_series,
        "storage_limit_gb": token.invite_storage_limit_gb,
        "blocked_movie_profiles": token.invite_blocked_movie_profiles,
        "blocked_series_profiles": token.invite_blocked_series_profiles,
        **bewertung.werte_fuers_konto(wunsch),
    }


def nur_ueber_den_link(einladung: AuthToken) -> bool:
    """Laesst sich diese Einladung nur ueber den Link in der Mail einloesen?

    Sobald sie Zugang zu einem Medienserver vergibt, ja. Nur das Formular hinter
    dem Link fragt ein Passwort fuer die neuen Konten ab und fuehrt durch das
    Verknuepfen mit Plex. Ueber die Anmeldung mit dem Medienserver oder einem
    Anmeldedienst entstuende das Nexview-Konto ohne diese Konten, und zwar still.
    """
    return bool(einladung.server)


def abschliessen(db: Session, token: AuthToken, benutzer: User, entfallen: list[str]) -> None:
    """Festhalten, welches Konto entstand und was nicht mehr ging - und Bescheid geben.

    Der Aufrufer hat das Konto schon geschrieben (``flush``, damit es eine
    Nummer hat) und committet danach selbst.
    """
    festhalten(token, benutzer, entfallen)
    bescheid_geben(db, token, benutzer)


def festhalten(token: AuthToken, benutzer: User, entfallen: list[str]) -> None:
    """Welches Konto aus der Einladung wurde und welche Rechte nicht mehr gingen."""
    token.redeemed_by = benutzer.id
    token.invite_dropped = ",".join(entfallen)


def bescheid_geben(db: Session, token: AuthToken, benutzer: User) -> None:
    """Den Administratoren sagen, dass die Einladung eingeloest ist, und ob ganz.

    Getrennt von ``festhalten``, weil das Einloesen ueber den Link erst die
    Freigaben auf den Medienservern abwartet. Ob alles ankam, steht erst danach fest.
    """
    titel = benutzer.display_name or benutzer.username
    unvollstaendig = bool(token.invite_dropped) or any(
        ziel.zustand == EinladungsServer.FEHLT for ziel in token.server
    )
    # Zwei feste Schluessel statt eines zusammengesetzten: Nur so sieht
    # ``test_nachrichtentexte`` beide und prueft, dass es sie als Text gibt.
    if unvollstaendig:
        notify.create_for_admins(
            db,
            kind=NotificationType.invitation_redeemed,
            message_key="notifications.invitationRedeemedPartly",
            title=titel,
        )
    else:
        notify.create_for_admins(
            db,
            kind=NotificationType.invitation_redeemed,
            message_key="notifications.invitationRedeemed",
            title=titel,
        )
