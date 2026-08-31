"""Jede Meldungsart weiss, wohin ihr Klick fuehrt.

⚠️ **Der Fehler, den dieser Test verhindert, ist ein Rueckfall.** Vorher gab es
zwei Listen mit je einem "alles andere nach ...": die Kanaele fielen auf die
Freigabeliste zurueck, die Glocke auf die eigene Anfrageliste. Genau in diesen
Rueckfaellen verschwanden die Faelle, an die niemand gedacht hatte - und zwar
lautlos, denn ein Rueckfall sieht nie kaputt aus.

Konkret landeten "Jemand hat einen Titel abgegeben" und "Instanz meldet ein
Problem" ueber die Glocke bei den **eigenen Anfragen** des Betreibers. Dort
steht weder das eine noch das andere, und es gibt von dort auch keinen Weg
weiter zur Entscheidung.

Deshalb ist die Liste in ``services/meldungsziele.py`` vollstaendig, und
dieser Test besteht darauf. Wer eine neue Meldungsart ergaenzt, muss sich
entscheiden - vergessen kann er es nicht mehr.
"""

from __future__ import annotations

import pytest

from app.models import Notification, NotificationType
from app.services import meldungsziele


def test_jede_meldungsart_hat_ein_ziel() -> None:
    """Kein Rueckfall, keine Ausnahme - jede Art traegt einen Eintrag."""
    fehlend = sorted(m.name for m in NotificationType if m not in meldungsziele.ZIELE)
    assert not fehlend, (
        f"Diese Meldungsarten wissen nicht, wohin ihr Klick fuehrt: {fehlend}.\n"
        "Einzutragen in app/services/meldungsziele.py. Die Frage dabei ist "
        "nicht 'wo passt es ungefaehr hin', sondern: Auf welcher Seite steht "
        "der Sachverhalt, um den es geht?"
    )


def test_keine_karteileichen() -> None:
    """Die andere Richtung - ein Ziel fuer eine Art, die es nicht mehr gibt."""
    arten = set(NotificationType)
    tote = sorted(str(k) for k in meldungsziele.ZIELE if k not in arten)
    assert not tote, f"Ziele fuer Meldungsarten, die es nicht gibt: {tote}"


@pytest.mark.parametrize("art", list(NotificationType))
def test_jedes_ziel_ist_ein_pfad(art: NotificationType) -> None:
    """Ein Ziel ohne fuehrenden Schraegstrich waere weder Pfad noch Adresse.

    Die Kanaele haengen ``settings.link(...)`` davor, die Oberflaeche ihren
    eigenen Grundpfad. Beides setzt einen Pfad voraus, der bei ``/`` anfaengt.
    """
    ziel = meldungsziele.ziel_fuer(Notification(type=art, message_key="x"))
    assert ziel.startswith("/"), f"{art.name}: {ziel!r}"
    assert "://" not in ziel, f"{art.name} traegt eine ganze Adresse: {ziel!r}"


def test_die_beiden_faelle_die_es_ausgeloest_haben() -> None:
    """⚠️ Namentlich festgehalten, damit sie nicht zurueckfallen.

    Beide gehen an den Betreiber, und beide landeten ueber die Glocke bei
    seinen eigenen Anfragen.
    """
    def ziel(art: NotificationType) -> str:
        return meldungsziele.ziel_fuer(Notification(type=art, message_key="x"))

    assert ziel(NotificationType.storage_release_requested) == "/admin/settings"
    assert ziel(NotificationType.instanz_gesundheit) == "/admin/settings"
    # Und die Gegenprobe: Was wirklich die eigene Anfrage betrifft, bleibt dort.
    assert ziel(NotificationType.download_complete) == "/requests"
