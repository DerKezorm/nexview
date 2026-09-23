"""Die Standprüfung: Taugt diese nexcrate für Nexview? (Bauplan 7.2)

⚠️ **Vorher fragen, nicht später scheitern.** Ohne diese Prüfung merkt man
eine zu alte nexcrate erst an der ersten Adresse, die es noch nicht gibt -
mitten im Umstieg, an einer Stelle, an der niemand mehr weiß, woran es lag.

Sie läuft an drei Stellen mit derselben Antwort: in der Einrichtung, im
Umstiegsassistenten und als Befund im laufenden Betrieb (`dienst.*`).

⚠️ **Kennungen, keine Sätze** (N5). Jeder Befund trägt seinen Code; den Satz
baut die Oberfläche, und der englische `message` von nexcrate steht nur
daneben, für den Betreiber.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Der Vertrag, gegen den Nexview gebaut ist.
MAJOR = 1
#: Und die Etappe, ab der alles da ist, was Nexview braucht (V4: Kalender,
#: Wertungen, Sprünge, Koppeln). Frühere Etappen können lesen, aber nicht alles.
MINDEST_STUFE = 4

#: Wie schwer ein Befund wiegt.
SPERRT = "sperrt"
WARNT = "warnt"


@dataclass(frozen=True)
class Befund:
    code: str
    stufe: str
    werte: dict[str, Any] = field(default_factory=dict)


def _stufe_als_zahl(stage: Any) -> int | None:
    """``"V4"`` als 4. Alles andere: unbekannt - und das ist kein Vorwurf."""
    text = str(stage or "").strip().upper()
    return int(text[1:]) if text.startswith("V") and text[1:].isdigit() else None


def pruefen(daten: dict[str, Any]) -> list[Befund]:
    """Was gegen diese nexcrate spricht - leer heißt: sie taugt.

    ``daten`` ist die Antwort von ``GET /system``.

    ⚠️ **Ohne gelesenen Stand gibt es nichts zu prüfen.** Ein leeres
    Wörterbuch heißt „noch nicht gefragt", nicht „kein Vertrag" - sonst meldet
    der erste Rundgang nach einem Neustart eine fremde nexcrate.
    """
    if not daten:
        return []
    gefunden: list[Befund] = []
    vertrag = daten.get("contract") or {}
    major = vertrag.get("major")
    if major != MAJOR:
        # Ein anderer Hauptvertrag heißt: Die Felder sind nicht mehr dieselben.
        # Weitermachen hieße raten.
        gefunden.append(
            Befund("nexcrate_vertrag_fremd", SPERRT, {"major": major, "erwartet": MAJOR})
        )
        return gefunden

    stufe = _stufe_als_zahl(vertrag.get("stage"))
    if stufe is not None and stufe < MINDEST_STUFE:
        gefunden.append(
            Befund(
                "nexcrate_zu_alt",
                SPERRT,
                {"stage": vertrag.get("stage"), "erwartet": f"V{MINDEST_STUFE}"},
            )
        )

    koennen = daten.get("capabilities") or {}
    for art in ("movies", "series"):
        if not koennen.get(art):
            gefunden.append(Befund("nexcrate_ohne_medienart", SPERRT, {"art": art}))

    rechte = set(daten.get("scopes") or [])
    for recht in ("request", "operate"):
        if recht not in rechte:
            # ⚠️ Ohne ``operate`` lässt sich nichts an einem klemmenden
            # Download tun und nichts aus dem Papierkorb zurückholen. Das
            # fällt sonst erst beim ersten Knopf auf.
            gefunden.append(Befund("nexcrate_recht_fehlt", SPERRT, {"recht": recht}))

    if not koennen.get("wishes_search_at_once"):
        # nexbeat-Befund 21: Ein Suchwunsch, den niemand abarbeitet, sieht aus
        # wie eine laufende Suche. Kein Grund zu sperren, aber einer zu sagen.
        gefunden.append(Befund("nexcrate_wuensche_warten", WARNT))

    if "anime" in koennen and not koennen.get("anime"):
        gefunden.append(Befund("nexcrate_ohne_anime", WARNT))

    return gefunden


def sperrt(befunde: list[Befund]) -> bool:
    return any(befund.stufe == SPERRT for befund in befunde)


#: Ein englischer Satz je Kennung - fuer das Protokoll und die Meldung an den
#: Betreiber. Die Oberflaeche übersetzt selbst, nach Kennung (N5); dieser Satz
#: steht dort, wo kein Übersetzer mitliest.
SAETZE: dict[str, str] = {
    "nexcrate_vertrag_fremd": "This nexcrate speaks contract {major}; Nexview is built for {erwartet}.",
    "nexcrate_zu_alt": "This nexcrate is at {stage}; Nexview needs at least {erwartet}.",
    "nexcrate_ohne_medienart": "This nexcrate does not serve {art}.",
    "nexcrate_recht_fehlt": "The key is missing the {recht} scope in nexcrate.",
    "nexcrate_wuensche_warten": (
        "Older nexcrate: a requested title waits for the automation instead of "
        "being searched right away."
    ),
    "nexcrate_ohne_anime": "This nexcrate does not search anime yet.",
}


def satz(befund: Befund) -> str:
    """Der englische Satz zu einem Befund, mit eingesetzten Werten."""
    vorlage = SAETZE.get(befund.code, befund.code)
    try:
        return vorlage.format(**befund.werte)
    except (KeyError, IndexError):
        # Ein fehlender Wert darf keinen Rundgang kosten.
        return vorlage


def als_health(befunde: list[Befund]) -> list[dict[str, Any]]:
    """Die Befunde in der Form von nexcrates ``/health``.

    ⚠️ **Damit sie denselben Weg gehen wie nexcrates eigene Meldungen**:
    entprellt, auf der Dienste-Seite, einmal als Benachrichtigung. Wer sie
    daneben stellte, bekäme eine zweite Liste mit eigenen Regeln.
    """
    return [
        {
            "code": befund.code,
            "level": "error" if befund.stufe == SPERRT else "warning",
            "message": satz(befund),
            "params": dict(befund.werte),
        }
        for befund in befunde
    ]
