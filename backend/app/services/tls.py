"""Ein TLS-Kontext fuer die gemeinsamen HTTP-Clients, gelesen ausserhalb der Ereignisschleife.

httpx baut beim Anlegen eines Clients einen eigenen Kontext und liest dabei die
vertrauenswuerdigen Zertifikate. Das kostete lokal gemessen 0,5 Sekunden
(``load_verify_locations``), auf dem NAS mehr, und zwar **in der Schleife**:
Die erste Entdecken-Liste nach einem Neustart hielt damit jede andere Anfrage
fest, einmal fuer TMDB und einmal fuer nexcrate. Dasselbe Muster wie
``vendor/nexoidc/tls.py``: einmal in einem Arbeitsfaden lesen, mit httpx'
eigenen Vorgaben (certifi, ``SSL_CERT_FILE``/``SSL_CERT_DIR``), und jedem
Client denselben Kontext geben.
"""

from __future__ import annotations

import asyncio
import ssl
import threading

import httpx

_sperre = threading.Lock()
_kontext: ssl.SSLContext | None = None


def _lesen() -> ssl.SSLContext:
    global _kontext
    with _sperre:
        if _kontext is None:
            _kontext = httpx.create_ssl_context(trust_env=True)
        return _kontext


async def kontext() -> ssl.SSLContext:
    """Der Kontext fuer einen Client; beim ersten Mal in einem Arbeitsfaden gelesen."""
    if _kontext is not None:
        return _kontext
    return await asyncio.to_thread(_lesen)
