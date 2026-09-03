"""Wiederverwendbare Pruefungen: eingeloggt? Admin?"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import meldungen
from .db import get_db
from .models import ApiKey, User
from .security import decode_token
from .services import api_schluessel, logs, sitzung

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=meldungen.meldung("not_signed_in", "Nicht angemeldet."),
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    # ⚠️ **Zwei Wege, eine Tuer.** Neben dem Sitzungs-Token der Oberflaeche
    # darf hier auch ein persoenlicher Zugriffs-Schluessel stehen. Welcher es
    # ist, verraet das Praefix - ohne Datenbank und ohne Raten.
    if api_schluessel.sieht_aus_wie_schluessel(credentials.credentials):
        return _mit_schluessel(request, credentials.credentials, db, unauthorized)

    inhalt = decode_token(credentials.credentials, "access")
    if inhalt is None:
        raise unauthorized

    user = db.get(User, inhalt.benutzer_id)
    if user is None or not user.is_active:
        raise unauthorized

    # Ein Token, das aelter ist als der letzte Passwortwechsel, gilt nicht
    # mehr. Das ist der einzige Ausweg, den ein Bestohlener hat - siehe
    # ``sitzung.gilt_noch``. Kostet nichts: Der Benutzer ist gerade geladen.
    if not sitzung.gilt_noch(inhalt, user):
        raise unauthorized

    # Ab hier steht in jeder Protokollzeile dieser Anfrage, wer sie gestellt hat.
    logs.set_actor(user.username)
    return user


def _mit_schluessel(
    request: Request, klartext: str, db: Session, unauthorized: HTTPException
) -> User:
    """Anmeldung ueber einen persoenlichen Zugriffs-Schluessel."""
    eintrag = api_schluessel.einloesen(db, klartext)
    if eintrag is None:
        raise unauthorized

    # ⚠️ **Die Sperre steht hier, nicht in den einzelnen Routern.** Ein
    # Schluessel mit "nur lesen" darf keine veraendernde Anfrage stellen, und
    # das muss an **einer** Stelle gelten - sonst ist die naechste Route, die
    # jemand vergisst, ein Loch.
    if not api_schluessel.darf(eintrag, request.method):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "apikey_read_only",
                "Dieser Schluessel darf nur lesen.",
            ),
        )

    # In jeder Protokollzeile steht damit **welcher** Schluessel es war, nicht
    # nur wer sein Besitzer ist. Ohne das liesse sich hinterher nicht sagen,
    # welche Anbindung etwas getan hat - genau der Mangel, den Seerrs einzelner
    # Schluessel hat.
    logs.set_actor(f"{eintrag.user.username} [{eintrag.name}]")

    # ⚠️ **Der Eintrag bleibt an der Anfrage haengen.** Zurueckgegeben wird der
    # Besitzer - wer wissen will, *womit* angeklopft wurde, findet es hier.
    # Genau eine Adresse braucht das: ``/api/v1/me`` sagt einer Anbindung, was
    # ihr Schluessel darf, und "nur lesen" haengt am Schluessel, nicht am
    # Konto. Ohne diese Zeile muesste sie ihn ein zweites Mal einloesen - und
    # damit ``last_used_at`` ein zweites Mal anfassen.
    request.state.api_schluessel = eintrag
    return eintrag.user


def schluessel_der_anfrage(request: Request) -> ApiKey | None:
    """Womit wurde angeklopft - oder ``None``, wenn es eine Sitzung war.

    Ein Sitzungs-Token kennt keine Beschraenkung: Wer im Browser angemeldet
    ist, darf alles, was sein Konto darf. Ein Schluessel kann zusaetzlich auf
    "nur lesen" stehen. Der Unterschied ist genau das, was eine Anbindung
    wissen muss, bevor sie sich einrichtet.
    """
    return getattr(request.state, "api_schluessel", None)


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "admins_only",
                "Diese Aktion ist Administratoren vorbehalten.",
            ),
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def betreiberschutz(user_id: int, user: CurrentUser, db: DbSession) -> None:
    """Das Zielkonto darf nicht der Betreiber sein - ausser er ist es selbst.

    ⚠️ **Diese Wache haengt an der Adresse, nicht im Rumpf.** Sie steht als
    ``dependencies=[Depends(betreiberschutz)]`` am Router-Aufruf, und genau
    dadurch ist sie von aussen sichtbar: ``test_betreiber_waechter.py`` laeuft
    ueber die Routentabelle und sieht, ob sie da ist. Eine Pruefung mitten im
    Rumpf koennte er nicht finden - und die naechste Adresse, die jemand
    anlegt, waere still ungeschuetzt.

    Sie ist die einzige der Wachen hier, die ein **Ziel** braucht. Deshalb
    liest sie ``user_id`` aus dem Pfad, so wie es die Adresse selbst tut; die
    Reihenfolge der Parameter ist FastAPI dabei egal.

    ⚠️ **Sie gibt dem Betreiber nichts.** Sie nimmt allen anderen etwas. Wer
    hier jemals eine Zeile ergaenzt, die dem Betreiber ein Recht *gibt*, hat
    die Regel gebrochen, fuer die es diesen Haken gibt.

    Ein unbekanntes ``user_id`` laesst sie durch: Die Adresse hat ihr eigenes
    404, und zwei Stellen mit derselben Aufgabe laufen frueher oder spaeter
    auseinander.
    """
    ziel = db.get(User, user_id)
    if ziel is None or not ziel.is_betreiber or ziel.id == user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=meldungen.meldung(
            "betreiber_geschuetzt",
            "Das Konto des Betreibers kann von niemandem sonst geändert werden.",
        ),
    )


def require_approver(user: CurrentUser) -> User:
    """Admin oder Entscheider - darf ueber fremde Anfragen bestimmen."""
    if not user.can_approve:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "approvers_only",
                "Diese Aktion ist Administratoren und Entscheidern vorbehalten.",
            ),
        )
    return user


ApproverUser = Annotated[User, Depends(require_approver)]


def require_adult(user: CurrentUser) -> User:
    """Alles ausser Kinderkonten.

    ⚠️ **Die Sperre fuer Kinder ist eine Erlaubnisliste, keine Verbotsliste.**
    Sie haengt in ``main.py`` an jedem Router, der *nicht* ausdruecklich fuer
    Kinder gedacht ist. Umgekehrt - "an jedem Endpunkt, der Kindern schadet" -
    waere es eine Verbotsliste, und die erste vergessene Zeile hiesse nicht ein
    falsches Abzeichen, sondern ein Kind in einer Erwachsenenfunktion.

    ``test_child_permissions.py`` laeuft ueber die ganze Routentabelle und
    schlaegt fehl, sobald ein Pfad weder hier haengt noch in der
    Kinder-Erlaubnisliste steht. Ein kuenftiger neuer Router kann das also
    nicht stillschweigend unterlaufen.
    """
    if user.is_child:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung("not_for_children", "Das ist nichts für Kinderkonten."),
        )
    return user


AdultUser = Annotated[User, Depends(require_adult)]


def require_child(user: CurrentUser) -> User:
    """Nur Kinderkonten - fuer die Kinderansicht."""
    if not user.is_child:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=meldungen.meldung(
                "children_only",
                "Diese Ansicht gibt es nur für Kinderkonten.",
            ),
        )
    return user


ChildUser = Annotated[User, Depends(require_child)]


def has_any_user(db: Session) -> bool:
    """Gibt es bereits mindestens einen Benutzer? (steuert die Erst-Einrichtung)"""
    return db.scalar(select(User.id).limit(1)) is not None
