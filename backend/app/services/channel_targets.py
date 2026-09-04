"""Die eingerichteten Ziele der serverseitigen Kanaele - anlegen, aendern, loeschen.

Ein Ziel ist eine Kachel in den Einstellungen. Je Dienst darf es mehrere geben:
mehrere Gotify-Postfaecher, mehrere ntfy-Instanzen.

Manche Dienste haben zwei Ebenen. Bei ntfy traegt die **Instanz** Adresse und
Anmeldung, das **Topic** darunter ist das eigentliche Postfach - und nur dort
laesst sich beweisen, dass etwas ankommt. Ein Topic erbt die Verbindungsdaten
seiner Instanz; gespeichert werden sie nur einmal, an der Instanz.

Geheimnisse (Token, Passwort) liegen verschluesselt in der Tabelle und
verlassen den Server nur maskiert. Ein leeres Feld beim Speichern bedeutet
"unveraendert" - sonst wuerde der maskierte Wert aus der Oberflaeche das echte
Token ueberschreiben.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crypto import decrypt, encrypt, mask
from ..models import ChannelKind, ChannelTarget
from . import channels


def alle(db: Session, kind: ChannelKind | None = None) -> list[ChannelTarget]:
    """Die Ziele der **oberen** Ebene, in der Reihenfolge des Anlegens.

    ⚠️ **Ohne die persoenlichen Rueckkanaele, und das ist keine Kosmetik.**
    Ein Ziel mit Besitzer entsteht nicht hier, sondern indem eine Anbindung es
    ueber ``/api/v1/me/push`` anmeldet. Stuende es in dieser Liste, koennte ein
    Administrator es wie eine Kachel bearbeiten: die Adresse eines Fremden auf
    seine eigene umbiegen, ohne dass dort je jemand einen Code gelesen haette,
    oder ihm Haken setzen, die es gar nicht kennt. Ansehen und abschalten kann
    er sie unter ``/api/settings/channels/rueckkanaele``.
    """
    abfrage = (
        select(ChannelTarget)
        .where(ChannelTarget.parent_id.is_(None), ChannelTarget.user_id.is_(None))
        .order_by(ChannelTarget.created_at, ChannelTarget.id)
    )
    if kind is not None:
        abfrage = abfrage.where(ChannelTarget.channel == kind)
    return list(db.scalars(abfrage))


def _felder(target: ChannelTarget) -> tuple[str, ...]:
    """Welche Felder gehoeren zu **dieser** Ebene?"""
    if target.parent_id is None:
        return channels.parent_fields(target.channel)
    return channels.child_fields(target.channel)


def werte(target: ChannelTarget, settings=None) -> dict[str, str]:
    """Die vollstaendigen Verbindungsdaten im Klartext.

    Instanz und Topic zusammen - und bei Diensten, deren Zugang zur ganzen
    Installation gehoert (E-Mail: Mailserver und Absender), zusaetzlich die
    Werte aus den allgemeinen Einstellungen.
    """
    geheim = channels.secrets_of(target.channel)

    def eigene(zeile: ChannelTarget) -> dict[str, str]:
        daten = {feld: str(getattr(zeile, feld, "") or "") for feld in _felder(zeile)}
        return {
            feld: decrypt(wert) if feld in geheim else wert for feld, wert in daten.items()
        }

    daten = dict.fromkeys(channels.fields(target.channel), "")
    for feld in channels.global_fields(target.channel):
        daten[feld] = "" if settings is None else str(getattr(settings, feld, "") or "")
    if target.parent is not None:
        daten.update(eigene(target.parent))
    daten.update(eigene(target))
    return daten


def config(target: ChannelTarget, settings=None):
    """Der fertige Zugang dieses Ziels - ``None``, wenn er unvollstaendig ist."""
    return channels.build(target.channel, werte(target, settings))


def entwurf_werte(
    kind: ChannelKind,
    entwurf: dict[str, object],
    target: ChannelTarget | None = None,
    parent: ChannelTarget | None = None,
    settings=None,
) -> dict[str, str]:
    """Die Werte, mit denen gerade getestet werden soll.

    Beim Bearbeiten liegt ein Ziel vor: was das Formular nicht mitschickt -
    typischerweise das Token, das nur maskiert herausgegeben wurde - kommt von
    dort. Beim Anlegen eines Topics liefert die Instanz die Verbindungsdaten.
    """
    daten = dict.fromkeys(channels.fields(kind), "")
    for feld in channels.global_fields(kind):
        daten[feld] = "" if settings is None else str(getattr(settings, feld, "") or "")
    if target is not None:
        daten.update(werte(target, settings))
    elif parent is not None:
        daten.update(werte(parent, settings))

    geheim = channels.secrets_of(kind)
    for feld in channels.fields(kind):
        wert = entwurf.get(feld)
        if wert is None:
            continue
        text = str(wert).strip()
        if feld in geheim and (not text or text.startswith("•")):
            continue
        daten[feld] = text
    return daten


def anwenden(target: ChannelTarget, entwurf: dict[str, object]) -> None:
    """Einen Entwurf uebernehmen - nur die Felder **dieser** Ebene.

    Was ``None`` ist, bleibt unangetastet. Bei Geheimnissen gilt zusaetzlich ein
    **leeres** Feld als "unveraendert": Die Oberflaeche bekommt das Token nie im
    Klartext zurueck, sondern maskiert - wuerde das durchgereicht, loeschte ein
    versehentliches Speichern die Zugangsdaten.
    """
    geheim = channels.secrets_of(target.channel)
    for feld in _felder(target):
        wert = entwurf.get(feld)
        if wert is None:
            continue
        text = str(wert).strip()
        if feld in geheim:
            if not text or text.startswith("•"):
                continue
            text = encrypt(text)
        setattr(target, feld, text)


def als_json(db: Session, target: ChannelTarget) -> dict[str, object]:
    """Darstellung fuer die Oberflaeche - Geheimnisse nur maskiert, Kinder mit dabei."""
    from . import channel_outbox

    geheim = channels.secrets_of(target.channel)
    daten: dict[str, object] = {
        "id": target.id,
        "channel": target.channel.value,
        "parent_id": target.parent_id,
        "name": target.name,
        "enabled": target.enabled,
        "verified": target.verified,
        "events": target.events or {},
        "created_at": target.created_at.isoformat(),
    }
    for feld in _felder(target):
        roh = str(getattr(target, feld, "") or "")
        if feld in geheim:
            klar = decrypt(roh)
            daten[feld] = mask(klar)
            daten[f"{feld}_set"] = bool(klar)
        else:
            daten[feld] = roh

    if target.parent_id is None and channels.has_children(target.channel):
        daten["children"] = [
            als_json(db, kind)
            for kind in sorted(target.children, key=lambda k: (k.created_at, k.id))
        ]

    # Ein Ziel, ueber das nichts mehr durchgeht, sieht genauso aus wie eines,
    # ueber das es nichts zu berichten gibt. Deshalb steht der letzte
    # endgueltige Fehlschlag auf der Kachel.
    fehler = channel_outbox.last_failure(db, target)
    daten["last_error"] = fehler.last_error if fehler else None
    daten["last_error_at"] = fehler.created_at.isoformat() if fehler else None
    return daten
