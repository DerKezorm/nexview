"""Bestaetigung per Code: hat die Testnachricht den Empfaenger wirklich erreicht?

Ein HTTP 200 vom Push-Dienst heisst nur "angenommen", nicht "angekommen". Ein
falsches Topic, eine App, die nichts abonniert hat, eine stumm geschaltete
Benachrichtigung - all das quittiert der Dienst freundlich mit 200, und
niemand merkt etwas, bis Wochen spaeter die erste echte Meldung ausbleibt.

Deshalb steht in der Testnachricht ein vierstelliger Code. Wer ihn eintippen
kann, hat sie gesehen - und erst dann laesst sich speichern.

Der Merkposten liegt bewusst nur im Arbeitsspeicher: Er lebt Minuten, gehoert
zu genau einer Person an genau einem Bildschirm und hat in der Datenbank
nichts verloren. Ein Neustart mittendrin kostet einen erneuten Klick auf
"Testen" - mehr nicht.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from ..models import ChannelKind

# Wie lange ein Code gilt. Lang genug, um zum Handy zu greifen; kurz genug,
# dass ein liegengelassener Bildschirm nichts freischaltet.
GUELTIG_SEKUNDEN = 600

# Nach so vielen Fehleingaben ist Schluss. Vier Ziffern sind schnell geraten,
# wenn man beliebig oft raten darf.
MAX_VERSUCHE = 5


@dataclass
class _Offen:
    code: str
    abdruck: tuple
    erstellt: float
    versuche: int = 0
    bestaetigt: bool = field(default=False)


_offen: dict[tuple[int, str], _Offen] = {}


def _abgelaufen(eintrag: _Offen) -> bool:
    return time.monotonic() - eintrag.erstellt > GUELTIG_SEKUNDEN


def _aufraeumen() -> None:
    for schluessel in [s for s, e in _offen.items() if _abgelaufen(e)]:
        _offen.pop(schluessel, None)


def start(admin_id: int, kind: ChannelKind, abdruck: tuple) -> str:
    """Einen neuen Code erzeugen und vormerken. Ersetzt einen aelteren."""
    _aufraeumen()
    code = f"{secrets.randbelow(10000):04d}"
    _offen[(admin_id, kind.value)] = _Offen(
        code=code, abdruck=abdruck, erstellt=time.monotonic()
    )
    return code


def confirm(admin_id: int, kind: ChannelKind, eingabe: str) -> tuple[bool, str]:
    """Den eingetippten Code pruefen. Liefert (geklappt, Begruendung)."""
    _aufraeumen()
    eintrag = _offen.get((admin_id, kind.value))
    if eintrag is None:
        return False, "Es liegt kein Code vor. Bitte zuerst eine Testnachricht senden."

    eintrag.versuche += 1
    if eintrag.versuche > MAX_VERSUCHE:
        _offen.pop((admin_id, kind.value), None)
        return False, "Zu viele Fehlversuche. Bitte eine neue Testnachricht senden."

    # ``compare_digest`` statt ``==``: der Vergleich soll nicht daran zu
    # erkennen sein, wie lange er dauert.
    if not secrets.compare_digest(eintrag.code, eingabe.strip()):
        return False, "Der Code stimmt nicht."

    eintrag.bestaetigt = True
    return True, "Code bestätigt."


def bestaetigt_fuer(admin_id: int, kind: ChannelKind, abdruck: tuple) -> bool:
    """Wurde **genau diese** Verbindung bestaetigt?

    Der Abdruck haengt daran, weil sonst eine funktionierende Adresse getestet
    und anschliessend eine andere gespeichert werden koennte.
    """
    _aufraeumen()
    eintrag = _offen.get((admin_id, kind.value))
    return bool(eintrag and eintrag.bestaetigt and eintrag.abdruck == abdruck)


def vergessen(admin_id: int, kind: ChannelKind) -> None:
    """Nach dem Speichern aufraeumen - der Code hat seinen Zweck erfuellt."""
    _offen.pop((admin_id, kind.value), None)
