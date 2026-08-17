"""SMTP-Einstellungen, Verbindungstest und Testnachricht."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.services import mail, mail_templates

from .conftest import auth_headers, create_user

ZUGANG = {
    "smtp_host": "smtp.beispiel.de",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_username": "post@beispiel.de",
    "smtp_password": "geheim-1234",
    "smtp_from_address": "nexview@beispiel.de",
    "smtp_from_name": "Nexview",
}


@pytest.fixture
def versand(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Merkt sich, was verschickt worden wäre - ohne echten Mailserver."""
    gesendet: list[EmailMessage] = []

    def _sende(_config: mail.MailConfig, nachricht: EmailMessage) -> None:
        gesendet.append(nachricht)

    def _pruefe(_config: mail.MailConfig) -> None:
        return None

    monkeypatch.setattr(mail, "_sende", _sende)
    monkeypatch.setattr(mail, "_pruefe", _pruefe)
    return gesendet


# --- Einstellungen ----------------------------------------------------------


def test_zugang_speichern_und_lesen(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json=ZUGANG)
    daten = admin_client.get("/api/settings").json()

    assert daten["smtp_host"] == "smtp.beispiel.de"
    assert daten["smtp_port"] == 587
    assert daten["smtp_security"] == "starttls"
    assert daten["smtp_from_address"] == "nexview@beispiel.de"
    assert daten["mail_configured"] is True


def test_passwort_wird_nur_maskiert_ausgeliefert(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json=ZUGANG)
    daten = admin_client.get("/api/settings").json()

    assert daten["smtp_password_set"] is True
    assert "geheim" not in daten["smtp_password"]
    assert daten["smtp_password"].startswith("•")


def test_leeres_passwort_laesst_das_alte_stehen(admin_client: TestClient) -> None:
    """Sonst würde ein versehentliches Speichern das Passwort löschen."""
    admin_client.put("/api/settings", json=ZUGANG)
    admin_client.put("/api/settings", json={"smtp_host": "neu.beispiel.de", "smtp_password": ""})

    daten = admin_client.get("/api/settings").json()
    assert daten["smtp_host"] == "neu.beispiel.de"
    assert daten["smtp_password_set"] is True


def test_passwort_entfernen(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json=ZUGANG)
    admin_client.delete("/api/settings/secret/smtp_password")
    assert admin_client.get("/api/settings").json()["smtp_password_set"] is False


def test_ungueltige_verschluesselung(admin_client: TestClient) -> None:
    assert admin_client.put("/api/settings", json={"smtp_security": "quatsch"}).status_code == 422


def test_ungueltige_absenderadresse(admin_client: TestClient) -> None:
    antwort = admin_client.put("/api/settings", json={"smtp_from_address": "kein-at-zeichen"})
    assert antwort.status_code == 422


def test_ungueltiger_port(admin_client: TestClient) -> None:
    assert admin_client.put("/api/settings", json={"smtp_port": 0}).status_code == 422
    assert admin_client.put("/api/settings", json={"smtp_port": 99999}).status_code == 422


def test_nur_admins(admin_client: TestClient) -> None:
    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")

    assert admin_client.get("/api/settings", headers=kim).status_code == 403
    assert (
        admin_client.post("/api/settings/test/smtp", json={}, headers=kim).status_code == 403
    )
    assert (
        admin_client.post(
            "/api/settings/test-mail", json={"recipient": "a@b.de"}, headers=kim
        ).status_code
        == 403
    )


# --- Verbindungstest --------------------------------------------------------


def test_verbindungstest_ohne_server(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/settings/test/smtp", json={}).json()
    assert antwort["ok"] is False
    assert "Mailserver" in antwort["message"]


def test_verbindungstest_erfolgreich(admin_client: TestClient, versand: list) -> None:
    admin_client.put("/api/settings", json=ZUGANG)

    antwort = admin_client.post("/api/settings/test/smtp", json={}).json()
    assert antwort["ok"] is True
    assert "smtp.beispiel.de:587" in antwort["message"]
    assert "STARTTLS" in antwort["message"]
    # Ein Verbindungstest verschickt nichts.
    assert versand == []


def test_verbindungstest_vor_dem_speichern(admin_client: TestClient, versand: list) -> None:
    """Man soll testen können, bevor man speichert."""
    antwort = admin_client.post(
        "/api/settings/test/smtp",
        json={"host": "noch.nicht.gespeichert.de", "port": 465, "security": "ssl"},
    ).json()
    assert antwort["ok"] is True
    assert "noch.nicht.gespeichert.de:465" in antwort["message"]


def test_verbindungstest_meldet_fehler_verstaendlich(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_client.put("/api/settings", json=ZUGANG)

    def kaputt(_config: mail.MailConfig) -> None:
        raise mail.MailError("smtp.beispiel.de:587 hat nicht rechtzeitig geantwortet.")

    monkeypatch.setattr(mail, "_pruefe", kaputt)

    antwort = admin_client.post("/api/settings/test/smtp", json={}).json()
    assert antwort["ok"] is False
    assert "nicht rechtzeitig" in antwort["message"]


def test_falsche_anmeldedaten(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein abgelehnter Login wird zu einer Meldung, nicht zu einem Absturz."""
    geschlossen: list[bool] = []

    class Blindserver:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def starttls(self, **_kwargs: object) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def login(self, *_args: object) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"nope")

        def close(self) -> None:
            geschlossen.append(True)

    monkeypatch.setattr(smtplib, "SMTP", Blindserver)

    config = mail.MailConfig(
        host="smtp.beispiel.de",
        port=587,
        security="starttls",
        username="post@beispiel.de",
        password="falsch",
        from_address="nexview@beispiel.de",
        from_name="Nexview",
    )
    with pytest.raises(mail.MailError) as fehler:
        mail._pruefe(config)

    assert "Benutzername und Passwort" in fehler.value.message
    # Die Verbindung darf nicht offen liegen bleiben.
    assert geschlossen == [True]


def test_unerreichbarer_server_meldet_verstaendlich(monkeypatch: pytest.MonkeyPatch) -> None:
    def verweigert(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr(smtplib, "SMTP", verweigert)

    config = mail.MailConfig(
        host="smtp.beispiel.de",
        port=2525,
        security="none",
        username="",
        password="",
        from_address="nexview@beispiel.de",
        from_name="Nexview",
    )
    with pytest.raises(mail.MailError) as fehler:
        mail._pruefe(config)

    assert "2525" in fehler.value.message
    assert "Port" in fehler.value.message


# --- Testnachricht ----------------------------------------------------------


def test_testmail_verschicken(admin_client: TestClient, versand: list[EmailMessage]) -> None:
    admin_client.put("/api/settings", json=ZUGANG)

    antwort = admin_client.post(
        "/api/settings/test-mail", json={"recipient": "kim@beispiel.de"}
    ).json()
    assert antwort["ok"] is True
    assert "kim@beispiel.de" in antwort["message"]

    assert len(versand) == 1
    nachricht = versand[0]
    assert nachricht["To"] == "kim@beispiel.de"
    assert nachricht["From"] == "Nexview <nexview@beispiel.de>"
    assert "Test" in nachricht["Subject"]


def test_testmail_hat_text_und_html(admin_client: TestClient, versand: list[EmailMessage]) -> None:
    """Reines HTML landet eher im Spam, und Textleser saehen nichts."""
    admin_client.put("/api/settings", json=ZUGANG)
    admin_client.post("/api/settings/test-mail", json={"recipient": "kim@beispiel.de"})

    typen = {teil.get_content_type() for teil in versand[0].walk()}
    assert "text/plain" in typen
    assert "text/html" in typen


def test_testmail_ohne_einstellungen(admin_client: TestClient, versand: list) -> None:
    antwort = admin_client.post(
        "/api/settings/test-mail", json={"recipient": "kim@beispiel.de"}
    ).json()
    assert antwort["ok"] is False
    assert versand == []


def test_testmail_an_unsinnige_adresse(admin_client: TestClient, versand: list) -> None:
    admin_client.put("/api/settings", json=ZUGANG)

    antwort = admin_client.post("/api/settings/test-mail", json={"recipient": "kein-at"}).json()
    assert antwort["ok"] is False
    assert versand == []


def test_vorlage_gibt_es_auf_deutsch_und_englisch() -> None:
    betreff_de, html_de, text_de = mail_templates.test_mail("de")
    betreff_en, html_en, text_en = mail_templates.test_mail("en")

    assert betreff_de != betreff_en
    assert "Es funktioniert" in html_de and "Es funktioniert" in text_de
    assert "It works" in html_en and "It works" in text_en
    # Keine externen Bilder - die blockieren die meisten Mailprogramme.
    assert "<img" not in html_de
    assert "http://" not in html_de


def test_adresspruefung() -> None:
    assert mail.valid_address("kim@beispiel.de")
    assert not mail.valid_address("kim@beispiel")
    assert not mail.valid_address("kim beispiel.de")
    assert not mail.valid_address("")
