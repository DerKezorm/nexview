"""Die bcrypt-Rundenzahl: 12 im Betrieb, niedrig im Testlauf.

Der Rechenaufwand beim Hashen **ist** der Schutz des Passworts, und genau
darum kostete er in der Testreihe den groesseren Teil der Laufzeit: Sie legt
tausende Konten an und meldet sich tausende Male an. ``tests/conftest.py``
dreht die Zahl deshalb auf 4 herunter.

Die Waechter hier haengen alle an derselben Sorge: Die niedrige Zahl gilt fuer
den Testlauf und **nur** fuer ihn. Wanderte sie in eine echte Installation,
waere jedes Passwort geschwaecht, das ab dann gesetzt wird, denn Nexview hasht
beim Anmelden nicht nach.

Der Test in die andere Richtung steht daneben: Faellt die Zeile in
``conftest.py`` aus (etwa weil sie hinter den ersten ``app``-Import rutscht),
schlaegt nichts fehl. Der Lauf wird nur wieder eine Viertelstunde laenger, und
niemand merkt es. ``test_der_testlauf_laeuft_wirklich_niedrig`` macht daraus
einen roten Test.
"""

from __future__ import annotations

import logging

import pytest

from app.config import BCRYPT_ROUNDS_DEFAULT, Settings, get_settings
from app.security import hash_password, verify_password

#: Ein echter Hash mit 12 Runden zum Passwort ``passwort-1234``, hier fest
#: hinterlegt. Er stammt aus der Zeit vor dieser Einstellung und steht fuer
#: jedes Konto, das seines schon hat.
ALTER_HASH = "$2b$12$uSWlS4z2yABf54jbpR2g7unkXb2.xexAa2DsSjfCGDyXZWL8JvPbC"


def test_vorgabe_im_code_ist_zwoelf() -> None:
    """Der Waechter fuer den Betrieb.

    Bewusst am Feld selbst und nicht an ``get_settings()``: Im Testlauf steht
    NEXVIEW_BCRYPT_ROUNDS=4 in der Umgebung, ein Blick auf den geltenden Wert
    koennte die Vorgabe hier also gar nicht pruefen.
    """
    assert Settings.model_fields["bcrypt_rounds"].default == 12


def test_ohne_umgebungsvariable_zwoelf_runden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derselbe Waechter noch einmal, diesmal ueber den echten Ladeweg.

    ``_env_file=None`` haelt eine lokale ``.env`` heraus: Im Repo liegt keine,
    aber ``.env.example`` legt eine nahe, und ihr Inhalt duerfte dieses
    Ergebnis nicht faerben.
    """
    monkeypatch.delenv("NEXVIEW_BCRYPT_ROUNDS", raising=False)
    assert Settings(_env_file=None).bcrypt_rounds == 12


def test_hash_password_nimmt_die_eingestellte_zahl() -> None:
    """Die Verdrahtung zwischen Einstellung und Verhalten.

    Die Rundenzahl steht im Hash selbst. Schreibt jemand spaeter wieder
    ``bcrypt.gensalt()`` ohne Argument, faellt es hier auf.
    """
    runden = get_settings().bcrypt_rounds
    assert hash_password("probe").startswith(f"$2b${runden:02d}$")


def test_der_testlauf_laeuft_wirklich_niedrig() -> None:
    """Aus einem stillen Zeitverlust einen roten Test machen.

    ``app.db`` ruft ``get_settings()`` schon beim Import, und das Ergebnis ist
    gepuffert. Die Zeile in ``conftest.py`` wirkt nur, solange sie **vor** dem
    ersten ``from app...`` steht.
    """
    assert get_settings().bcrypt_rounds < BCRYPT_ROUNDS_DEFAULT


@pytest.mark.parametrize("wert", [0, 3, 32, 99])
def test_unsinnige_werte_fallen_auf_zwoelf(wert: int) -> None:
    """bcrypt nimmt 4 bis 31; alles andere wirft erst beim Hashen.

    Ohne den Rueckfall waere das ein ValueError mitten in einer Anmeldung,
    lange nach dem Start.
    """
    assert Settings(bcrypt_rounds=wert).bcrypt_rounds == 12


def test_warnung_unterhalb_der_schwelle(caplog: pytest.LogCaptureFixture) -> None:
    """Eine kleinere Zahl wird angenommen, aber nicht stillschweigend."""
    with caplog.at_level(logging.WARNING, logger="nexview.config"):
        Settings(bcrypt_rounds=4)

    assert "NEXVIEW_BCRYPT_ROUNDS" in caplog.text
    assert [satz.levelno for satz in caplog.records] == [logging.WARNING]


def test_alter_zwoelf_runden_hash_gilt_weiter() -> None:
    """Bestehende Konten bleiben pruefbar, egal was beim Erzeugen gilt.

    ``verify_password`` liest die Runden aus dem Hash und nicht aus den
    Einstellungen. Das ist die Zusage, an der die ganze Aenderung haengt: Ein
    Konto von frueher behaelt seine 12 bis zum naechsten Passwortwechsel.
    """
    assert verify_password("passwort-1234", ALTER_HASH) is True
    assert verify_password("falsches-passwort", ALTER_HASH) is False
