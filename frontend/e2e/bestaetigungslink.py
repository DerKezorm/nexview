"""Den Bestaetigungslink erzeugen, den sonst die Mail bringt.

Der Einrichtungsassistent verlangt einen funktionierenden Mailserver, bevor er
sich abschliessen laesst - und zwar mit Absicht: ohne Mail ginge die
Bestaetigung nie raus, und niemand kaeme je wieder in die frische
Installation. Fuer einen Test ist ein echter SMTP-Server aber die falsche
Abhaengigkeit.

Deshalb wird hier nur der eine Schritt ersetzt, den sonst das Postfach
uebernimmt: den Link auszuhaendigen. Bestaetigt wird danach ueber
``POST /api/onboarding/verify/<link>`` - also ueber genau die Route, die auch
ein Klick in der Mail aufruft. Der Weg selbst bleibt damit im Test.

Aufruf: python bestaetigungslink.py <adresse>
"""

import sys
from pathlib import Path

# Das Backend liegt zwei Ebenen hoeher neben der Oberflaeche.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.db import SessionLocal  # noqa: E402
from app.models import TokenPurpose, User  # noqa: E402
from app.services import tokens  # noqa: E402


def main() -> int:
    adresse = tokens.normalize_email(sys.argv[1])
    with SessionLocal() as db:
        nutzer = db.query(User).filter(User.email == adresse).one_or_none()
        if nutzer is None:
            print(f"Kein Konto zu {adresse}", file=sys.stderr)
            return 1
        klartext, _ = tokens.create(
            db, TokenPurpose.email_verification, adresse, user=nutzer
        )
        db.commit()
    print(klartext)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
