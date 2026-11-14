"""Wer fragt? Anmeldung an der Seerr-Schicht.

Seerr kennt **einen** Schluessel fuer die ganze Installation (``X-Api-Key``)
und dazu ``X-Api-User``: "ich frage im Namen von Nutzer 7". Nexview kennt
dagegen nur persoenliche Schluessel (``nxv_...``), und jeder gehoert einem
Konto. Beides passt so zusammen:

* **Der Schluessel ist ein ganz gewoehnlicher Nexview-Schluessel.** Wer ihn in
  Silo oder Litefin eintraegt, fragt als dessen Besitzer an, mit dessen
  Rolle, Kontingent und Regeln. Fuer Silo legt man sich dafuer am besten ein
  eigenes Konto an ("Silo"); Silos eigene Freigabe bleibt dann davor.
* **``X-Api-User`` wirkt nur, wenn der Schluessel einem Administrator
  gehoert**, genau wie bei Seerr, wo nur der Installationsschluessel das darf.
  Litefin schickt damit die Nexview-Nummer der Person mit, die es ueber
  ``jellyfinUserId`` gefunden hat; die Anfrage laeuft dann auf deren Konto,
  mit deren Regeln.

⚠️ **Konten legt diese Schicht nie an.** Silos Plugin versucht es, wenn es
einen Nutzer nicht findet (``POST /api/v1/user``); das wird abgelehnt, und
Silo faellt auf den Besitzer des Schluessels zurueck (oder bricht ab, wenn es
so eingestellt ist).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ApiKey, Role, User
from ..services import api_schluessel, logs


class SeerrFehler(Exception):
    """Ein Fehler in Seerrs Form: Status und ``{"message": ...}``.

    ⚠️ Die Saetze sind englisch und gehen so nach aussen - ein Seerr-Client
    zeigt sie seinen Nutzern teils woertlich an.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Zugang:
    schluessel: ApiKey
    #: Wem der Schluessel gehoert.
    besitzer: User
    #: In wessen Namen gefragt wird - der Besitzer oder die Person aus ``X-Api-User``.
    nutzer: User

    @property
    def im_auftrag(self) -> bool:
        return self.nutzer.id != self.besitzer.id


def zugang(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: Annotated[str | None, Header()] = None,
    x_api_user: Annotated[str | None, Header()] = None,
) -> Zugang:
    klartext = (x_api_key or "").strip()
    if not klartext:
        raise SeerrFehler(401, "Missing API key. Send a Nexview API key as X-Api-Key.")

    eintrag = api_schluessel.einloesen(db, klartext) if api_schluessel.sieht_aus_wie_schluessel(klartext) else None
    if eintrag is None:
        raise SeerrFehler(401, "Invalid API key.")

    besitzer = eintrag.user
    # ⚠️ Das Geraet eines Kindes hat einen Schluessel (``app_kopplung``), aber
    # keinen Platz an einer Schnittstelle, die im Namen Erwachsener anfragt
    # und Fremden zeigt, was im Haus liegt.
    if besitzer.role == Role.child:
        raise SeerrFehler(403, "This API key belongs to a child account.")

    if eintrag.nur_lesen and request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
        raise SeerrFehler(403, "This API key is read-only.")

    nutzer = besitzer
    if x_api_user is not None and x_api_user.strip():
        try:
            kennung = int(x_api_user.strip())
        except ValueError:
            raise SeerrFehler(400, "X-Api-User must be a user id.") from None
        if kennung != besitzer.id:
            if besitzer.role != Role.admin:
                raise SeerrFehler(403, "Only an administrator's API key may act for another user.")
            ziel = db.get(User, kennung)
            # Ein stillgelegtes Konto und ein Kinderkonto sehen von aussen aus
            # wie ein unbekanntes: Sonst liesse sich durchprobieren, welche
            # Nummern es gibt und wer davon ein Kind ist.
            if ziel is None or not ziel.is_active or ziel.role == Role.child:
                raise SeerrFehler(403, "Unknown user in X-Api-User.")
            nutzer = ziel

    logs.set_actor(
        f"{besitzer.username} [{eintrag.name}]"
        + (f" for {nutzer.username}" if nutzer.id != besitzer.id else "")
    )
    return Zugang(schluessel=eintrag, besitzer=besitzer, nutzer=nutzer)


SeerrZugang = Annotated[Zugang, Depends(zugang)]
