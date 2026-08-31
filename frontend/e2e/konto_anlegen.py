"""Ein gewoehnliches Konto fuer den Ende-zu-Ende-Test anlegen.

    python konto_anlegen.py <benutzername> <passwort>

⚠️ **Warum nicht ueber die API.** Konten entstehen im Betrieb nur ueber eine
Einladung - mit Mailversand, Link und Formular. Fuer einen Test, der einfach
nur *irgendein* gewoehnliches Konto braucht, waere das reine Zeremonie; den
echten Weg prueft ``backend/tests/test_onboarding.py``.

Gebraucht wird es, seit Administratoren von der Hausordnung ausgenommen sind:
Der Test meldet sich sonst als Administrator an und saehe den Knopf nie.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Das Backend liegt zwei Ebenen hoeher neben der Oberflaeche - dieselbe Zeile
# wie in ``bestaetigungslink.py`` daneben.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.db import SessionLocal  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> None:
    benutzername, passwort = sys.argv[1], sys.argv[2]
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter(User.username == benutzername).one_or_none()
        if konto is None:
            konto = User(
                username=benutzername,
                email=f"{benutzername}@beispiel.test",
                email_verified=True,
                display_name=benutzername,
                role=Role.user,
                # ⚠️ Englisch wie ``KONTO`` - sonst steht die Oberflaeche des
                # Lesers auf Deutsch, waehrend der Test englische
                # Beschriftungen sucht. Genau daran ist er zuerst gescheitert.
                language="en",
            )
            sitzung.add(konto)
        konto.password_hash = hash_password(passwort)
        sitzung.commit()
    print(benutzername)


if __name__ == "__main__":
    main()
