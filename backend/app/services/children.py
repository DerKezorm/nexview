"""Kinderkonten: ein erwachsenes Konto verwaltet seine eigenen Kinder.

Ein Kinderkonto ist ein gewoehnlicher ``User`` mit der Rolle ``child`` und
einem ``parent_id``. Es hat **keine Mailadresse** - Benutzername und Passwort
vergibt das Elternteil, und nur es kann sie wieder aendern.

Die Regeln stehen **einmal hier** und nicht verteilt ueber die Endpunkte:
"gehoert dieses Kind zu mir" ist dieselbe Art Frage wie die Sichtbarkeit eines
Tickets, und dort hat sich genau das bewaehrt (siehe ``tickets``). Eine
vergessene Pruefung waere hier nicht ein falsches Abzeichen, sondern Zugriff
auf ein fremdes Konto.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import meldungen
from ..models import Role, User, utcnow
from ..security import hash_password


# Rubriken der Kinderansicht.
#
# Bewusst **eigene Rubriken statt roher TMDB-Genres**: Film und Serie fuehren
# fuer dieselbe Sache verschiedene Nummern - "Abenteuer" ist bei Filmen 12, bei
# Serien steckt es in "Action & Adventure" (10759), und "Kinder" (10762) gibt es
# ueberhaupt nur bei Serien. Zwei Haekchenlisten fuer dasselbe Beduerfnis waeren
# eine Zumutung, und die Zuordnung waere jedes Mal neu zu raten.
#
# Je Eintrag: (Film-Genres, Serien-Genres). Eine leere Seite heisst "diese
# Rubrik gibt es dort nicht".
RUBRIKEN: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {
    "animation": ((16,), (16,)),
    "family": ((10751,), (10751,)),
    # Nur Serien - TMDB fuehrt keine eigene Kinder-Sparte bei Filmen.
    "kids": ((), (10762,)),
    "adventure": ((12,), (10759,)),
    "fantasy": ((14,), (10765,)),
    "comedy": ((35,), (35,)),
    # Nur Filme.
    "music": ((10402,), ()),
    "documentary": ((99,), (99,)),
}


def rubriken_von(kind: User) -> list[str]:
    """Die Rubriken dieses Kindes - leer gespeichert heisst **alle**.

    Der Unterschied ist wichtig: Ein Kind ohne jede Rubrik saehe gar nichts,
    und genau das ist nie gemeint. Bestandskonten aus der Zeit vor dieser
    Einstellung tragen deshalb den leeren Wert und sehen alles.
    """
    gewaehlt = [teil for teil in (kind.child_genres or "").split(",") if teil]
    return gewaehlt or list(RUBRIKEN)


def _rubriken_pruefen(genres: list[str] | None) -> str | None:
    """Aus der Auswahl die gespeicherte Komma-Liste machen.

    ``None`` heisst "nicht mitgeschickt" und laesst den Wert unveraendert.
    """
    if genres is None:
        return None
    unbekannt = [name for name in genres if name not in RUBRIKEN]
    if unbekannt:
        raise ChildError(f"Unbekannte Rubrik: {', '.join(unbekannt)}.", 422)
    # Die gespeicherte Reihenfolge folgt RUBRIKEN, nicht der Reihenfolge der
    # Haekchen - sonst haengt die Anzeige davon ab, in welcher Reihenfolge
    # jemand geklickt hat.
    return ",".join(name for name in RUBRIKEN if name in set(genres))


class ChildError(Exception):
    """Etwas ist nicht erlaubt oder nicht moeglich.

    ``code`` ist der Schluessel, unter dem die Oberflaeche den Satz in ihrer
    Sprache baut (``errors.byCode``, siehe ``meldungen``). Er ist **optional**:
    Die aelteren Meldungen hier tragen keinen und kommen deshalb auf Deutsch
    an - ein bekannter Rueckstand, der sich Stueck fuer Stueck aufloesen laesst,
    ohne dass jemand alle auf einmal anfassen muss.

    ⚠️ Wer einen ergaenzt, braucht zwei Uebersetzungen: ``test_fehlermeldungen``
    findet jedes ``code="..."`` im Quelltext und besteht auf einem Eintrag in
    beiden Sprachdateien.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str | None = None,
        **zahlen: object,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.zahlen = zahlen

    def als_meldung(self) -> dict[str, object] | str:
        """Der Inhalt fuer ``detail`` - mit Kennung, wenn es eine gibt."""
        if not self.code:
            return self.message
        return meldungen.meldung(self.code, self.message, **self.zahlen)


def darf_kinder_anlegen(user: User) -> bool:
    """Wer darf Kinderkonten fuehren?

    Wer den Haken vom Administrator bekommen hat - und Administratoren immer,
    wie ueberall sonst auch (den Haken koennten sie sich selbst setzen, ihn
    erst zu verlangen waere ein Umweg, der nichts schuetzt).

    Zwei harte Ausnahmen: Wer selbst untergeordnet oder altersbeschraenkt ist,
    verwaltet niemanden - sonst legte sich ein 14-Jaehriger ein "Kind" ohne
    Altersgrenze an und saehe darueber alles. Diese beiden stehen **vor** dem
    Haken, damit auch ein versehentlich gesetzter nichts aufreisst.
    """
    if user.is_child or user.age is not None:
        return False
    return user.is_admin or user.can_manage_children


def recht_entzogen(db: Session, elternteil: User) -> int:
    """Nimmt der Administrator das Recht weg, werden die Kinder stillgelegt.

    ⚠️ Sonst waere der Entzug wirkungslos: Die Konten bestuenden weiter, die
    Kinder koennten sich weiter anmelden und weiter wuenschen - nur haette
    niemand mehr eine Stelle, an der er das verwaltet. Geloescht werden sie
    **nicht**; der Administrator soll die Freigabe zuruecknehmen koennen, ohne
    dass dabei Konten verschwinden.

    Committet nicht - der Aufrufer haengt es an seine Transaktion.
    """
    kinder = [kind for kind in eigene_kinder(db, elternteil) if kind.is_active]
    for kind in kinder:
        kind.is_active = False
    return len(kinder)


def eigene_kinder(db: Session, elternteil: User) -> list[User]:
    """Die Kinder dieses Kontos - aeltestes zuerst."""
    return list(
        db.scalars(
            select(User)
            .where(User.parent_id == elternteil.id, User.role == Role.child)
            .order_by(User.created_at)
        )
    )


def kind_von(db: Session, elternteil: User, kind_id: int) -> User:
    """Ein einzelnes eigenes Kind - oder 404.

    Bewusst 404 und nicht 403: ein "verboten" bestaetigt, dass es diese Nummer
    gibt. Dieselbe Regel wie bei fremden Tickets.
    """
    kind = db.get(User, kind_id)
    if kind is None or kind.role != Role.child or kind.parent_id != elternteil.id:
        raise ChildError("Kinderkonto nicht gefunden.", 404)
    return kind


def _name_frei(db: Session, username: str) -> bool:
    """Ist dieser Benutzername noch zu haben?

    Benutzernamen sind **installationsweit** eindeutig, nicht je Familie: Die
    Anmeldung kennt nur den Namen, nicht das Elternteil dazu. Zwei Kinder
    namens "max" in verschiedenen Haushalten liessen sich beim Anmelden nicht
    auseinanderhalten.
    """
    return (
        db.scalar(select(User.id).where(func.lower(User.username) == username.lower())) is None
    )


def _namensvorschlaege(db: Session, username: str) -> list[str]:
    """Ein paar freie Abwandlungen, damit die Fehlermeldung weiterhilft.

    Eine blosse Absage ("Name vergeben") laesst das Elternteil raten - und der
    naechste Versuch ist oft wieder vergeben, weil kurze Namen die beliebten
    sind.
    """
    kandidaten = [f"{username}i", f"{username}1", f"{username}-2"]
    return [name for name in kandidaten if _name_frei(db, name)][:2]


def anlegen(
    db: Session,
    elternteil: User,
    *,
    username: str,
    password: str,
    age: int,
    display_name: str | None = None,
    genres: list[str] | None = None,
    child_trailers: bool = True,
    language: str | None = None,
) -> User:
    """Ein Kinderkonto anlegen.

    ``email_verified`` steht auf **true**, obwohl es keine Adresse gibt: Die
    Anmeldung verlangt eine bestaetigte Adresse, und ein Kind hat keine, die zu
    bestaetigen waere. Ohne dieses Zugestaendnis koennte sich niemals ein Kind
    anmelden.

    Sprache, Darstellung und Pruefland kommen vom Elternteil - das ist die
    einzige Vorgabe, die es dazu gibt, und sie ist besser als die Vorgabe des
    Hauses.
    """
    if not darf_kinder_anlegen(elternteil):
        raise ChildError(
            "Dieses Konto darf keine Kinderkonten anlegen.",
            403,
        )

    username = username.strip()
    if not _name_frei(db, username):
        vorschlaege = _namensvorschlaege(db, username)
        hinweis = (
            f" Frei wäre zum Beispiel „{'“ oder „'.join(vorschlaege)}“."
            if vorschlaege
            else ""
        )
        raise ChildError(
            f"Der Benutzername „{username}“ ist bereits vergeben.{hinweis}",
            409,
        )

    rubriken = _rubriken_pruefen(genres)

    kind = User(
        username=username,
        password_hash=hash_password(password),
        role=Role.child,
        parent_id=elternteil.id,
        display_name=(display_name or "").strip() or None,
        age=age,
        # Ohne Nachweis kein Zutritt: Bei einem Kinderkonto ist das keine
        # Abwaegung wie bei Erwachsenen, sondern der Sinn der Sache. Deshalb
        # fest und ohne Schalter.
        hide_unrated=True,
        child_genres=rubriken or "",
        child_trailers=child_trailers,
        rating_region=elternteil.rating_region,
        # Ohne Angabe die Sprache des Elternteils: Ein Kind stellt sie nicht
        # selbst um - in der Kinderansicht gibt es dafuer bewusst keinen
        # Schalter -, also muss sie beim Anlegen stimmen.
        language=language or elternteil.language,
        theme=elternteil.theme,
        # Es gibt keine Adresse - siehe oben.
        email=None,
        email_verified=True,
    )
    db.add(kind)
    db.commit()
    db.refresh(kind)
    return kind


def aendern(
    db: Session,
    elternteil: User,
    kind_id: int,
    *,
    daten: dict[str, object],
) -> User:
    """Alter, Anzeigename oder aktiv/inaktiv aendern."""
    kind = kind_von(db, elternteil, kind_id)
    for feld, wert in daten.items():
        if feld == "display_name":
            wert = (str(wert) if wert is not None else "").strip() or None
        if feld == "genres":
            # Die Rubriken liegen als Komma-Liste in einer anders benannten
            # Spalte - deshalb hier umgebogen statt blind gesetzt.
            kind.child_genres = _rubriken_pruefen(wert) or ""
            continue
        setattr(kind, feld, wert)
    db.commit()
    db.refresh(kind)
    return kind


def passwort_setzen(db: Session, elternteil: User, kind_id: int, password: str) -> User:
    """Neues Passwort.

    Es gibt kein "Passwort vergessen" fuer Kinder - ohne Mailadresse gaebe es
    keinen Weg zurueck. Das Elternteil ist dieser Weg.
    """
    kind = kind_von(db, elternteil, kind_id)
    kind.password_hash = hash_password(password)
    kind.password_changed_at = utcnow()
    db.commit()
    db.refresh(kind)
    return kind


def loeschen(db: Session, elternteil: User, kind_id: int) -> None:
    """Kinderkonto entfernen.

    **Ohne Kontoaufloesung.** Einem Kind gehoert keine einzige Datei: Was aus
    seinen Wuenschen wird, ist eine Anfrage des Elternteils und bleibt bei ihm.
    Auch Profilbild und Media-Server-Sperre entfallen - beides kann ein
    Kinderkonto gar nicht haben.
    """
    kind = kind_von(db, elternteil, kind_id)
    db.delete(kind)
    db.commit()


def alle_kinder_loeschen(db: Session, elternteil: User) -> int:
    """Alle Kinder eines Kontos entfernen - **ohne** zu committen.

    Wird beim Loeschen des Elternteils gebraucht. Ohne diesen Schritt bliebe
    ein Kinderkonto mit einem Verweis ins Leere zurueck: Die Spalte
    ``parent_id`` traegt auf aktualisierten Datenbanken keine
    Fremdschluessel-Regel (siehe Kommentar am Modell), und auf frischen
    scheiterte das Loeschen des Elternteils schlicht an der Regel. Beides
    waere falsch - hier ist der eine Ort, an dem es richtig passiert.

    Der Aufrufer committet, damit Kinder und Elternteil in einem Zug
    verschwinden.
    """
    # Der Import steht hier unten: ``child_wishes`` braucht seinerseits
    # ``children`` - oben waere es ein Ringschluss.
    from . import child_wishes

    kinder = eigene_kinder(db, elternteil)
    for kind in kinder:
        # Erst die Wuensche des Kindes, sonst bleibt ein Verweis ins Leere.
        child_wishes.wuensche_loeschen(db, kind)
        db.delete(kind)
    # ⚠️ Das ``flush`` ist Pflicht, nicht Kosmetik. Ohne es sammelt SQLAlchemy
    # Kind und Elternteil in **einem** ``executemany`` ueber dieselbe Tabelle,
    # und in welcher Reihenfolge die Zeilen darin stehen, ist nicht festgelegt.
    # Kommt das Elternteil zuerst, schlaegt SQLites Fremdschluessel-Pruefung zu
    # und das Loeschen scheitert mit "FOREIGN KEY constraint failed". Genau so
    # ist es aufgefallen (``test_elternteil_loeschen_nimmt_die_kinder_mit``).
    if kinder:
        db.flush()
    return len(kinder)
