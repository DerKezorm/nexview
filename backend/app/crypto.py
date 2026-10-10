"""Verschluesselung der API-Keys, die in der Datenbank liegen.

Damit landet ein TMDB-/Radarr-/Sonarr-Key nicht im Klartext in der
SQLite-Datei. Der Schluessel dafuer wird aus ``NEXVIEW_SECRET_KEY``
abgeleitet (bzw. aus der automatisch erzeugten ``data/secret.key``).
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

logger = logging.getLogger("nexview.crypto")

_PREFIX = "enc:"

#: Der abgeleitete Schluessel, einmal je Prozesslauf.
#:
#: ⚠️ **Wie viel das bringt, haengt daran, woher der Schluessel kommt.**
#: ``resolved_secret_key`` liefert ``NEXVIEW_SECRET_KEY`` sofort zurueck, wenn
#: die Variable gesetzt ist. Ist sie leer - und das ist die Vorgabe, siehe
#: ``.env.example`` -, macht sie bei **jedem** Aufruf ein ``mkdir`` und liest
#: ``data/secret.key`` von der Platte. ``load_settings`` leitet einmal je
#: gespeichertem Geheimnis ab, in der vorbereiteten Datenbank achtmal (fuenf
#: Geheimnisse plus drei Verbindungs-Token).
#:
#: Gemessen an dieser Datenbank, eine Anfrage mit acht ``load_settings``:
#: mit gesetzter Variable 10,7 auf 1,3 ms, davon 0,3 ms durch diesen Merker
#: und der Rest durch den Sitzungs-Merker in ``settings_service``. Ohne die
#: Variable, also mit ``secret.key`` auf der Platte, 25,6 auf 1,4 ms - dort
#: traegt dieser Merker mehr als die Haelfte.
#:
#: ⚠️ **Ein von aussen ausgetauschtes ``secret.key`` greift damit erst nach
#: einem Neustart.** Das ist kein Verlust, sondern besser: Ein im Betrieb
#: getauschter Schluessel macht ohnehin **alle** gespeicherten Geheimnisse
#: unlesbar - bisher schlug das mitten im Betrieb zu, mit dem Merker bleiben
#: sie bis zum Neustart lesbar, und der Betreiber sieht die Folgen dann
#: geschlossen statt haeppchenweise.
#:
#: Wer den Schluessel von innen austauscht, muss ``fernet_vergessen`` rufen.
#: Das tut heute genau eine Stelle, das Einspielen einer Sicherung.
_gemerkt: Fernet | None = None


def _fernet() -> Fernet:
    global _gemerkt
    if _gemerkt is None:
        secret = get_settings().resolved_secret_key().encode("utf-8")
        # Eigenes Praefix, damit dieser Schluessel nicht mit dem Signierschluessel
        # aus security.py identisch ist.
        digest = hashlib.sha256(b"nexview-settings:" + secret).digest()
        _gemerkt = Fernet(base64.urlsafe_b64encode(digest))
    return _gemerkt


def fernet_vergessen() -> None:
    """Den gemerkten Schluessel wegwerfen; der naechste Aufruf leitet neu ab."""
    global _gemerkt
    _gemerkt = None
    _im_kontext.clear()


#: Je Kontext ein eigener Schluessel (``encrypt_im_kontext``), einmal je Lauf.
_im_kontext: dict[str, Fernet] = {}


def _fernet_im_kontext(kontext: str) -> Fernet:
    """Ein Schluessel nur fuer diesen Kontext, abgeleitet wie ``_fernet``.

    Fuer Geheimnisse, die an einer bestimmten Zeile haengen (das
    Client-Geheimnis eines Anmelde-Anbieters, Kontext ``oidc-provider:<id>``):
    Ein Geheimtext, den jemand in die Zeile eines anderen Anbieters kopiert,
    laesst sich dort nicht entschluesseln.
    """
    gemerkt = _im_kontext.get(kontext)
    if gemerkt is None:
        secret = get_settings().resolved_secret_key().encode("utf-8")
        digest = hashlib.sha256(
            b"nexview-settings:" + secret + b"|context:" + kontext.encode("utf-8")
        ).digest()
        gemerkt = _im_kontext[kontext] = Fernet(base64.urlsafe_b64encode(digest))
    return gemerkt


def encrypt_im_kontext(value: str, kontext: str) -> str:
    """Verschluesseln mit dem Schluessel eines Kontexts; leer bleibt leer."""
    if not value:
        return ""
    return _PREFIX + _fernet_im_kontext(kontext).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_im_kontext(value: str, kontext: str) -> str:
    """Gegenstueck zu ``encrypt_im_kontext``; ein unlesbarer Wert wird "" und laermt."""
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    try:
        return (
            _fernet_im_kontext(kontext)
            .decrypt(value[len(_PREFIX) :].encode("utf-8"))
            .decode("utf-8")
        )
    except InvalidToken:
        logger.warning(
            "A stored credential (%s) cannot be decrypted with the current secret key. "
            "Was NEXVIEW_SECRET_KEY changed, or was data/secret.key lost? It has to be "
            "entered again.",
            kontext,
        )
        return ""


def encrypt(value: str) -> str:
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: str) -> str:
    """Wert entschluesseln; unverschluesselte Altwerte werden durchgereicht."""
    if not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX) :].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Der Schluessel passt nicht mehr zu den gespeicherten Werten - etwa
        # weil ``NEXVIEW_SECRET_KEY`` geaendert wurde oder ``data/secret.key``
        # beim Neu-Erstellen des Containers verlorenging (Datei nicht im
        # gemounteten Volume). Frueher wurde hier stumm "" geliefert: Die
        # Plex-Verbindung war dann einfach "weg", TMDB lief als Demo, und
        # nirgends stand, warum. Zwei Betreiber haben genau das als Raetsel
        # gemeldet - deshalb laermt es jetzt.
        logger.warning(
            "A stored credential cannot be decrypted with the current secret "
            "key. Was NEXVIEW_SECRET_KEY changed, or was data/secret.key lost "
            "when the container was rebuilt? The affected credentials have to "
            "be entered again."
        )
        return ""


def mask(value: str) -> str:
    """Maskierte Darstellung fuer die Oberflaeche, z. B. ``••••3f2a``."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]
