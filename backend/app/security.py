"""Passwort-Hashing (bcrypt) und Login-Tokens (JWT)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from .config import get_settings

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]


def _signing_key() -> bytes:
    """Signierschluessel aus dem Geheimnis ableiten.

    Ueber SHA-256 ist der Schluessel immer 32 Byte lang, auch wenn jemand ein
    kurzes ``NEXVIEW_SECRET_KEY`` setzt. Das Praefix trennt diesen Zweck von
    der Verschluesselung der API-Keys in ``crypto.py``.
    """
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nexview-jwt:" + secret).digest()

# bcrypt verarbeitet maximal 72 Bytes; laengere Passwoerter wuerden in
# bcrypt 5.x einen Fehler ausloesen statt still abgeschnitten zu werden.
_BCRYPT_MAX_BYTES = 72


def _password_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    # Die Rundenzahl kommt aus den Einstellungen (Vorgabe 12, siehe
    # ``config.py``). ``get_settings`` ist gepuffert, der Nachschlag kostet
    # neben dem Hashen nichts.
    runden = get_settings().bcrypt_rounds
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(runden)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    # Bewusst ohne Rundenzahl: Sie steht im Hash selbst (``$2b$12$...``).
    # Ein Konto von frueher bleibt darum pruefbar, auch wenn beim Erzeugen
    # inzwischen eine andere Zahl gilt.
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


# Kein bcrypt-Hash, also kann keine Eingabe dazu passen. ``verify_password``
# faengt das ueber den ValueError ab und meldet schlicht "falsch".
UNUSABLE_PASSWORD = "!kein-passwort-gesetzt"


def unusable_password() -> str:
    """Platzhalter fuer Konten, die ihr Passwort erst noch waehlen.

    Die Spalte darf nicht leer bleiben, und ein zufaelliges Passwort waere
    gefaehrlich: es koennte theoretisch jemand erraten. Ein Wert, der gar kein
    gueltiger Hash ist, kann dagegen prinzipiell nicht passen.
    """
    return UNUSABLE_PASSWORD


def has_usable_password(password_hash: str) -> bool:
    return password_hash != UNUSABLE_PASSWORD


def _create_token(subject: int, token_type: TokenType, expires_in: timedelta) -> str:
    """Ein Token bauen.

    ⚠️ **Neben ``iat`` steht ``ms`` - derselbe Zeitpunkt, aber in
    Millisekunden.** Das ist kein Schmuck, sondern die Lehre aus einem Fehler.

    ``iat`` ist nach RFC 7519 auf ganze Sekunden gerundet. Wer damit gegen
    ``password_changed_at`` vergleicht, hat nur schlechte Wahlen: Rundet er
    ab, ueberlebt ein gestohlenes Token aus derselben Sekunde den
    Passwortwechsel - und ein Skript, das im Sekundentakt erneuert, faellt
    zuverlaessig durch dieses Loch. Rundet er auf, sperrt sich jedes frisch
    angelegte Konto im selben Atemzug selbst aus, denn sein
    ``password_changed_at`` entsteht in derselben Sekunde wie sein erstes
    Token.

    Mit Millisekunden gibt es die Zwickmuehle nicht mehr: Der Vergleich ist
    genau, ohne Rundung und ohne Sonderfaelle.
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "ms": int(now.timestamp() * 1000),
        "exp": int((now + expires_in).timestamp()),
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    return _create_token(user_id, "access", timedelta(minutes=settings.access_token_minutes))


def create_refresh_token(user_id: int) -> str:
    settings = get_settings()
    return _create_token(user_id, "refresh", timedelta(days=settings.refresh_token_days))


@dataclass(frozen=True)
class TokenInhalt:
    """Was in einem gueltigen Token steht - mehr braucht niemand.

    ``ausgestellt`` ist der Ausstellungszeitpunkt in **Millisekunden** seit
    1970. Damit laesst sich ein Token gegen ``password_changed_at`` halten
    (``services/sitzung.py``) - warum es nicht das gerundete ``iat`` tut,
    steht bei ``_create_token``.
    """

    benutzer_id: int
    ausgestellt: int


def decode_token(token: str, expected_type: TokenType) -> TokenInhalt | None:
    """Token pruefen; None wenn ungueltig.

    ⚠️ Ein gueltiges Ergebnis heisst nur "richtig unterschrieben, richtige Art,
    noch nicht abgelaufen". Ob das **Konto** dieses Token noch gelten laesst,
    entscheidet ``sitzung.gilt_noch`` - dafuer muss der Benutzer geladen sein,
    und das gehoert nicht hierher.
    """
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None

    if payload.get("type") != expected_type:
        return None
    try:
        benutzer_id = int(payload.get("sub"))
        # Fehlt ``ms``, stammt das Token nicht aus dieser Fassung. Es dann
        # ueber ``iat`` durchzulassen waere genau das Loch, dessentwegen es
        # ``ms`` gibt - also gilt es nicht. Kosten: keine. Beim Umstieg auf
        # 0.21 faellt ohnehin jede bestehende Sitzung einmal heraus.
        ausgestellt = int(payload["ms"])
    except (KeyError, TypeError, ValueError):
        return None
    return TokenInhalt(benutzer_id=benutzer_id, ausgestellt=ausgestellt)


def access_token_expires_in() -> int:
    """Restlaufzeit des Access-Tokens in Sekunden (fuer das Frontend)."""
    return get_settings().access_token_minutes * 60
