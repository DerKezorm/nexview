"""Verschluesselung der API-Keys, die in der Datenbank liegen.

Damit landet ein TMDB-/Radarr-/Sonarr-Key nicht im Klartext in der
SQLite-Datei. Der Schluessel dafuer wird aus ``NEXVIEW_SECRET_KEY``
abgeleitet (bzw. aus der automatisch erzeugten ``data/secret.key``).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_PREFIX = "enc:"


def _fernet() -> Fernet:
    secret = get_settings().resolved_secret_key().encode("utf-8")
    # Eigenes Praefix, damit dieser Schluessel nicht mit dem Signierschluessel
    # aus security.py identisch ist.
    digest = hashlib.sha256(b"nexview-settings:" + secret).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: str) -> str:
    """Wert entschluesseln; unverschluesselte Altwerte werden durchgereicht."""
    if not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX) :].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Passiert, wenn NEXVIEW_SECRET_KEY nachtraeglich geaendert wurde.
        return ""


def mask(value: str) -> str:
    """Maskierte Darstellung fuer die Oberflaeche, z. B. ``••••3f2a``."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]
