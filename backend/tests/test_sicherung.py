"""Sicherungen: anlegen, auflisten, als verschluesseltes Archiv ausliefern.

Was hier wirklich geprueft wird, ist nicht "eine Datei entsteht" - das waere
billig. Geprueft wird, dass die Sicherung **das** enthaelt, was sie enthalten
muss, damit sie im Ernstfall etwas wert ist, und dass sie nichts hergibt, was
sie nicht hergeben darf.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import sqlite3
import threading
import time
import types
from datetime import UTC
from pathlib import Path

import pytest
import pyzipper
from fastapi.testclient import TestClient

import app.db as db_modul
from app import __version__
from app.config import get_settings
from app.services import settings_service, sicherung

PASSWORT = "ein-langes-testpasswort"


@pytest.fixture(autouse=True)
def leerer_sicherungsordner():
    """Jeder Test faengt ohne fremde Sicherungen an - und hinterlaesst keine.

    ⚠️ ``clean_db`` raeumt nur die Tabellen. Die Sicherungen liegen als Dateien
    daneben und ueberleben sonst den ganzen Testlauf - dann zaehlt ein Test die
    Staende eines anderen mit.

    ⚠️ **Und hinterher der TRaSH-Ordner, das ist der wichtigere Teil.** Die
    Tests hier legen dort Attrappen ab (``{"stand": "damals"}``), und das
    Einspielen schreibt welche zurueck. Beides liegt im gemeinsamen
    Datenverzeichnis. Bleibt es liegen, liest ``trash.schnappschuss`` es in
    **anderen** Testdateien als echten Stand - und fuenfzehn Tests zu den
    Qualitaetsprofilen brechen mit ``KeyError: 'profile'`` an einer Stelle, die
    mit Sicherungen nichts zu tun hat. Genau so ist es passiert.
    """
    from app.services import trash

    def leeren() -> None:
        ordner = sicherung.ordner()
        if ordner.is_dir():
            for datei in list(ordner.glob("*")):
                if datei.is_dir():
                    # Der Beilagenordner einer Sicherung - Bilder und TRaSH-Stand.
                    shutil.rmtree(datei, ignore_errors=True)
                else:
                    datei.unlink(missing_ok=True)

    leeren()
    yield
    leeren()
    shutil.rmtree(get_settings().data_dir / "trash", ignore_errors=True)
    # Ohne das haelt der Zwischenspeicher die Attrappe bis zum Ende des Laufs
    # fest, auch wenn die Datei weg ist.
    trash.schnappschuss.cache_clear()


def _archiv_oeffnen(daten: bytes, passwort: str) -> pyzipper.AESZipFile:
    zip_datei = pyzipper.AESZipFile(io.BytesIO(daten))
    zip_datei.setpassword(passwort.encode("utf-8"))
    return zip_datei


class TestAnlegen:
    def test_legt_datei_und_steckbrief_an(self) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar="vor dem Aufraeumen")

        assert pfad.exists()
        brief = json.loads(pfad.with_suffix(".json").read_text(encoding="utf-8"))
        assert brief["art"] == sicherung.MANUELL
        assert brief["kommentar"] == "vor dem Aufraeumen"
        assert brief["version"] == __version__
        assert brief["schema"].startswith("sha256:")

    def test_kommentar_landet_im_dateinamen_ohne_gefaehrliche_zeichen(self) -> None:
        # ⚠️ Der Kommentar kommt vom Benutzer. Punkte und Schraegstriche darin
        # wuerden aus dem Ordner herausfuehren.
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar="../../etc/passwd")

        assert pfad.parent == sicherung.ordner()
        assert "/" not in pfad.name and "\\" not in pfad.name
        assert ".." not in pfad.stem

    def test_zwischenspeicher_wird_geleert(self) -> None:
        """⚠️ Der Punkt, an dem aus 180 MB rund 3 MB werden.

        Ueber 90 % einer gewachsenen Datenbank ist TMDB-Zwischenspeicher. Der
        ist in Stunden wieder da; in einer Sicherung ist er nur Ballast - und
        eine Sicherung, die zu gross zum Herunterladen ist, laedt niemand
        herunter.
        """
        from sqlalchemy import text

        from app.db import SessionLocal

        with SessionLocal() as db:
            db.execute(
                text(
                    "INSERT INTO tmdb_cache (cache_key, payload, expires_at) "
                    "VALUES ('probe', '{\"viel\": \"text\"}', :wann)"
                ),
                {"wann": "2099-01-01 00:00:00"},
            )
            db.commit()

        pfad = sicherung.anlegen(art=sicherung.MANUELL)

        verbindung = sqlite3.connect(pfad)
        try:
            assert verbindung.execute("SELECT COUNT(*) FROM tmdb_cache").fetchone()[0] == 0
        finally:
            verbindung.close()

        brief = json.loads(pfad.with_suffix(".json").read_text(encoding="utf-8"))
        assert "tmdb_cache" in brief["geleert"]

    def test_echte_daten_bleiben_drin(self, admin_client: TestClient) -> None:
        """Der Gegenbeweis zum Test darueber - geleert wird nur der Ballast.

        Braucht ``admin_client``, damit ueberhaupt ein Konto existiert: Ohne
        Fixture sind die Tabellen leer, und der Test wuerde gruen sein, ohne
        etwas zu zeigen.
        """
        pfad = sicherung.anlegen(art=sicherung.MANUELL)

        verbindung = sqlite3.connect(pfad)
        try:
            assert verbindung.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 1
        finally:
            verbindung.close()


class TestAufraeumen:
    def test_automatische_werden_begrenzt(self) -> None:
        for _ in range(7):
            sicherung.anlegen(art=sicherung.AUTOMATISCH)

        automatisch = [e for e in sicherung.liste() if e.art == sicherung.AUTOMATISCH]
        assert len(automatisch) == sicherung.AUTOMATISCH_BEHALTEN

    def test_von_hand_angelegte_bleiben_liegen(self) -> None:
        """⚠️ Der Fall, um den es geht.

        Wer vor einer riskanten Aktion bewusst eine Sicherung anlegt, darf sie
        nicht dadurch verlieren, dass Nexview danach fuenfmal startet.
        """
        sicherung.anlegen(art=sicherung.MANUELL, kommentar="die wichtige")
        for _ in range(8):
            sicherung.anlegen(art=sicherung.AUTOMATISCH)

        meine = [e for e in sicherung.liste() if e.art == sicherung.MANUELL]
        assert len(meine) == 1
        assert meine[0].kommentar == "die wichtige"


class TestArchiv:
    def test_enthaelt_datenbank_schluessel_und_steckbrief(self) -> None:
        """⚠️ Ohne ``secret.key`` ist die Sicherung nicht das, wofuer man sie haelt.

        Die Zugaenge zu Radarr, Sonarr, TMDB und dem Mailserver liegen
        verschluesselt in der Datenbank; der Schluessel steht daneben. Wer nur
        die Datenbank mitnimmt, steht beim Einspielen vor lauter Zugaengen, die
        sich nicht mehr lesen lassen.
        """
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        with _archiv_oeffnen(daten, PASSWORT) as zip_datei:
            namen = set(zip_datei.namelist())
        assert "nexview.db" in namen
        assert sicherung.STECKBRIEF in namen
        # In der Testumgebung kommt der Schluessel aus der Umgebungsvariablen -
        # dann muss statt der Datei der Hinweis darauf drinliegen.
        assert "secret.key" in namen or "SCHLUESSEL-FEHLT.txt" in namen

    def test_ohne_passwort_kommt_man_nicht_hinein(self) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        zip_datei = pyzipper.AESZipFile(io.BytesIO(daten))
        with pytest.raises(RuntimeError):
            zip_datei.read("nexview.db")

    def test_falsches_passwort_oeffnet_nicht(self) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        with _archiv_oeffnen(daten, "etwas-ganz-anderes") as zip_datei, pytest.raises(Exception):
            zip_datei.read("nexview.db")

    def test_die_datenbank_im_archiv_ist_lesbar(self) -> None:
        """Nicht nur vorhanden - auch brauchbar."""
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        with _archiv_oeffnen(daten, PASSWORT) as zip_datei:
            roh = zip_datei.read("nexview.db")
            brief = json.loads(zip_datei.read(sicherung.STECKBRIEF))

        assert roh[:16] == b"SQLite format 3\x00"
        assert brief["version"] == __version__

    def test_kein_ausbruch_aus_dem_ordner(self) -> None:
        """⚠️ Der Name kommt aus einer Anfrage.

        Ohne Pruefung liesse sich hier genau die Datei herunterladen, die das
        Archiv sonst verschluesselt mitnimmt.
        """
        for versuch in ("../secret.key", "..\\secret.key", "../../nexview.db"):
            with pytest.raises(FileNotFoundError):
                sicherung.archiv(versuch, PASSWORT)

    def test_ohne_passwort_wird_nichts_gebaut(self) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        with pytest.raises(ValueError):
            sicherung.archiv(pfad.name, "")


class TestVertraeglichkeit:
    """⚠️ Die Wanderung kann nur vorwaerts - das muss die Sicherung wissen."""

    def _brief(self, version: str) -> sicherung.Steckbrief:
        return sicherung.Steckbrief(
            version=version, schema="", erstellt="2026-01-01T00:00:00+00:00", art="manuell"
        )

    def test_aeltere_darf_eingespielt_werden(self) -> None:
        ok, _ = sicherung.vertraeglich(self._brief("0.1.0"))
        assert ok is True

    def test_gleiche_darf_eingespielt_werden(self) -> None:
        ok, _ = sicherung.vertraeglich(self._brief(__version__))
        assert ok is True

    def test_neuere_wird_abgelehnt(self) -> None:
        """Der Fall, der eine Installation zerlegen wuerde.

        Die laufende Fassung kennt die Tabellen einer neueren nicht - und was
        sie nicht kennt, liest sie falsch oder gar nicht.
        """
        ok, grund = sicherung.vertraeglich(self._brief("99.0.0"))
        assert ok is False
        assert grund == "backup_newer"

    def test_ohne_versionsangabe_wird_abgelehnt(self) -> None:
        ok, grund = sicherung.vertraeglich(self._brief(""))
        assert ok is False
        assert grund == "unknown_version"


class TestSchnittstelle:
    def test_liste_nur_fuer_administratoren(self, client: TestClient) -> None:
        assert client.get("/api/admin/sicherungen").status_code in (401, 403)

    def test_admin_sieht_die_liste(self, admin_client: TestClient) -> None:
        sicherung.anlegen(art=sicherung.MANUELL, kommentar="Testlauf")

        antwort = admin_client.get("/api/admin/sicherungen")
        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["version"] == __version__
        meine = [e for e in daten["eintraege"] if e["kommentar"] == "Testlauf"]
        assert len(meine) == 1
        assert meine[0]["art"] == "manuell"
        assert meine[0]["einspielbar"] is True

    def test_anlegen_ueber_die_schnittstelle(self, admin_client: TestClient) -> None:
        antwort = admin_client.post("/api/admin/sicherungen", json={"kommentar": "von Hand"})
        assert antwort.status_code == 201
        assert antwort.json()["art"] == "manuell"
        assert antwort.json()["kommentar"] == "von Hand"

    def test_archiv_herunterladen(self, admin_client: TestClient) -> None:
        name = admin_client.post("/api/admin/sicherungen", json={"kommentar": ""}).json()["name"]

        antwort = admin_client.post(
            f"/api/admin/sicherungen/{name}/archiv", json={"passwort": PASSWORT}
        )
        assert antwort.status_code == 200
        assert antwort.headers["content-type"] == "application/zip"
        assert antwort.headers["content-disposition"].endswith('.zip"')

        with _archiv_oeffnen(antwort.content, PASSWORT) as zip_datei:
            assert "nexview.db" in zip_datei.namelist()

    def test_zu_kurzes_passwort_wird_abgelehnt(self, admin_client: TestClient) -> None:
        # Im Archiv liegt der Schluessel zu allen Dienst-Zugaengen. Ein
        # dreistelliges Passwort waere hier keine Bequemlichkeit, sondern ein
        # Versehen mit Folgen.
        name = admin_client.post("/api/admin/sicherungen", json={"kommentar": ""}).json()["name"]

        antwort = admin_client.post(
            f"/api/admin/sicherungen/{name}/archiv", json={"passwort": "kurz"}
        )
        assert antwort.status_code == 422


class TestZeitplan:
    """⚠️ Die Luecke, die der Zeitplan schliesst.

    Bis 0.22 entstand eine automatische Sicherung **nur** bei einer
    Schemaaenderung - also praktisch nur beim Update. Zwischen zwei Fassungen
    koennen Monate liegen.
    """

    def test_ohne_zeitplan_passiert_nichts(self) -> None:
        assert sicherung.faellig("off") is False

    def test_ohne_jede_sicherung_ist_sofort_faellig(self) -> None:
        assert sicherung.faellig("weekly") is True

    def test_frisch_gesichert_ist_nicht_faellig(self) -> None:
        sicherung.anlegen(art=sicherung.AUTOMATISCH)
        assert sicherung.faellig("daily") is False

    def test_nach_der_frist_wieder_faellig(self) -> None:
        from datetime import datetime, timedelta

        sicherung.anlegen(art=sicherung.AUTOMATISCH)
        spaeter = datetime.now(UTC) + timedelta(days=8)
        assert sicherung.faellig("weekly", jetzt=spaeter) is True
        assert sicherung.faellig("monthly", jetzt=spaeter) is False

    def test_eine_manuelle_setzt_den_takt_nicht_zurueck(self) -> None:
        """Sonst verschoebe jede Sicherung von Hand den naechsten Termin.

        Wer vor einer riskanten Aktion sichert, will damit nicht die
        regelmaessige Sicherung aussetzen.
        """
        sicherung.anlegen(art=sicherung.MANUELL, kommentar="eben schnell")
        assert sicherung.faellig("daily") is True


class TestAnzahlAusDenEinstellungen:
    def test_eingestellte_zahl_wird_beachtet(self, admin_client: TestClient) -> None:
        admin_client.put("/api/settings", json={"backup_keep": 3})

        for _ in range(6):
            sicherung.anlegen(art=sicherung.AUTOMATISCH)

        automatisch = [e for e in sicherung.liste() if e.art == sicherung.AUTOMATISCH]
        assert len(automatisch) == 3


class TestLoeschen:
    def test_admin_kann_loeschen(self, admin_client: TestClient) -> None:
        name = admin_client.post("/api/admin/sicherungen", json={"kommentar": "weg damit"}).json()[
            "name"
        ]

        antwort = admin_client.delete(f"/api/admin/sicherungen/{name}")
        assert antwort.status_code == 204
        assert all(e.name != name for e in sicherung.liste())
        # Der Steckbrief darf nicht liegen bleiben.
        assert not (sicherung.ordner() / name).with_suffix(".json").exists()

    def test_nicht_ohne_anmeldung(self, client: TestClient) -> None:
        assert client.delete("/api/admin/sicherungen/irgendwas.db").status_code in (401, 403)

    def test_kein_ausbruch_aus_dem_ordner(self, admin_client: TestClient) -> None:
        """⚠️ Sonst liesse sich hier ``secret.key`` loeschen.

        405 ist hier das erwuenschte Ergebnis und kein Zufall: Der Weg wird
        normalisiert, landet damit auf der Sammel-Adresse - und die kennt kein
        DELETE. Der Ausbruch scheitert also schon am Router, noch bevor eigener
        Code laeuft. Dass auch der Code selbst nicht mitspielt, prueft
        ``TestArchiv.test_kein_ausbruch_aus_dem_ordner``.
        """
        antwort = admin_client.delete("/api/admin/sicherungen/..%2Fsecret.key")
        assert antwort.status_code in (400, 404, 405)


class TestWiederherstellen:
    """⚠️ Die gefaehrlichste Stelle des ganzen Bereichs.

    Alles andere legt Dateien an. Das hier **ersetzt** eine Datenbank - und ein
    Fehler kostet nicht eine Sicherung, sondern die Installation.
    """

    def _archiv(self, passwort: str = PASSWORT) -> bytes:
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar="zum Einspielen")
        return sicherung.archiv(pfad.name, passwort)

    def test_kein_archiv_wird_abgelehnt(self) -> None:
        with pytest.raises(sicherung.SicherungFehler) as fehler:
            sicherung.wiederherstellen(b"das ist kein zip", PASSWORT)
        assert fehler.value.code == "restore_not_an_archive"

    def test_falsches_passwort_wird_abgelehnt(self) -> None:
        with pytest.raises(sicherung.SicherungFehler) as fehler:
            sicherung.wiederherstellen(self._archiv(), "etwas-ganz-anderes")
        assert fehler.value.code == "restore_wrong_password"

    def test_fremdes_zip_wird_abgelehnt(self) -> None:
        """Ein ZIP kann alles enthalten - auch Urlaubsfotos."""
        puffer = io.BytesIO()
        with pyzipper.AESZipFile(
            puffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as zip_datei:
            zip_datei.setpassword(PASSWORT.encode("utf-8"))
            zip_datei.writestr("urlaub.jpg", "kein Nexview")

        with pytest.raises(sicherung.SicherungFehler) as fehler:
            sicherung.wiederherstellen(puffer.getvalue(), PASSWORT)
        assert fehler.value.code == "restore_not_a_backup"

    def test_keine_datenbank_im_archiv(self) -> None:
        """⚠️ Der Fall, der eine Installation gegen eine Textdatei tauschen wuerde."""
        puffer = io.BytesIO()
        brief = sicherung.Steckbrief(
            version=__version__, schema="", erstellt="2026-01-01T00:00:00+00:00", art="manuell"
        )
        with pyzipper.AESZipFile(
            puffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as zip_datei:
            zip_datei.setpassword(PASSWORT.encode("utf-8"))
            zip_datei.writestr(sicherung.STECKBRIEF, brief.als_json())
            zip_datei.writestr("nexview.db", "nur Text, keine Datenbank")

        with pytest.raises(sicherung.SicherungFehler) as fehler:
            sicherung.wiederherstellen(puffer.getvalue(), PASSWORT)
        assert fehler.value.code == "restore_not_a_database"

    def test_neuere_sicherung_wird_abgelehnt(self) -> None:
        """Die Wanderung kann nur vorwaerts - eine neuere Fassung waere ein Blindflug."""
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        brief_pfad = pfad.with_suffix(".json")
        brief = json.loads(brief_pfad.read_text(encoding="utf-8"))
        brief["version"] = "99.0.0"
        brief_pfad.write_text(json.dumps(brief), encoding="utf-8")

        with pytest.raises(sicherung.SicherungFehler) as fehler:
            sicherung.wiederherstellen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT)
        assert fehler.value.code == "restore_backup_newer"

    def test_pruefen_ersetzt_nichts(self, admin_client: TestClient) -> None:
        """⚠️ Der Unterschied zwischen Ansehen und Einspielen.

        ``pruefen`` darf die Datenbank nicht anfassen - sonst waere die Vorschau
        selbst schon der Eingriff.
        """
        from app.db import SessionLocal
        from app.models import User

        daten = self._archiv()
        with SessionLocal() as db:
            vorher = db.query(User).count()

        befund = sicherung.pruefen(daten, PASSWORT)
        assert befund.einspielbar is True
        assert befund.brief.kommentar == "zum Einspielen"

        with SessionLocal() as db:
            assert db.query(User).count() == vorher

    def test_einspielen_bringt_die_daten_zurueck(self, admin_client: TestClient) -> None:
        """Der eigentliche Beweis: Was nach der Sicherung passiert ist, ist danach weg."""
        from app.db import SessionLocal
        from app.models import User
        from app.security import hash_password

        daten = self._archiv()

        # Nach der Sicherung kommt ein Konto dazu - das darf danach nicht mehr da sein.
        with SessionLocal() as db:
            db.add(User(username="danach", password_hash=hash_password("x"), email="d@b.de"))
            db.commit()
        with SessionLocal() as db:
            assert db.query(User).filter(User.username == "danach").count() == 1

        sicherung.wiederherstellen(daten, PASSWORT)

        with SessionLocal() as db:
            assert db.query(User).filter(User.username == "danach").count() == 0
            assert db.query(User).count() >= 1

    def test_der_verschluesselungsschluessel_wird_vergessen(
        self, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sonst laeuft der Dienst mit dem Schluessel von **vor** dem Einspielen weiter.

        ``crypto`` leitet den Fernet einmal je Prozesslauf ab und merkt ihn
        sich - das Ableiten liest ``data/secret.key`` von der Platte und war
        der teuerste Teil am Lesen der Einstellungen. Genau hier kann aber ein
        fremdes ``secret.key`` aus dem Archiv geschrieben worden sein. Ohne das
        Vergessen haelt Nexview jedes eingespielte Geheimnis bis zum Neustart
        fuer unlesbar: Plex "weg", TMDB im Demo-Modus, Radarr "nicht
        eingerichtet" - die drei Raetsel, die schon einmal gemeldet wurden.

        ⚠️ **Der Test spielt den Schluesselwechsel wirklich durch, statt nur
        Objekte zu vergleichen.** Eine fruehere Fassung pruefte allein
        ``_fernet() is not vorher``, und die war doppelt hohl: Sie bestuende
        auch ohne den Merker (dann ist jedes Ergebnis ein neues Objekt), und
        sie bestuende mit jedem beliebigen neuen Schluessel, denn das
        ``NEXVIEW_SECRET_KEY`` aus ``conftest.py`` gewinnt gegen die Datei und
        liess den Schluessel im Testlauf nie wechseln. Deshalb hier: die
        Umgebungsvariable abgeklemmt, ein Geheimnis mit dem Schluessel der
        Sicherung verschluesselt, die laufende Installation auf einen anderen
        Schluessel gestellt - und nach dem Einspielen muss das Geheimnis
        wieder im Klartext herauskommen. Das geht nur, wenn die Ableitung
        wirklich neu geschieht, und zwar aus genau dem ``secret.key``, den das
        Archiv mitgebracht hat.
        """
        from app import crypto

        einstellungen = get_settings()
        # Solange die Umgebungsvariable gesetzt ist, wird das Archiv-secret.key
        # gar nicht geschrieben und der Schluessel wechselt nie.
        monkeypatch.setattr(einstellungen, "secret_key", "")

        try:
            # Die Installation, aus der die Sicherung stammt: eigener
            # Schluessel auf der Platte, ein Geheimnis damit verschluesselt.
            einstellungen.key_file.write_text("schluessel-aus-der-sicherung", encoding="utf-8")
            crypto.fernet_vergessen()
            geheim = crypto.encrypt("radarr-api-key-aus-der-sicherung")
            daten = self._archiv()  # nimmt secret.key von der Platte mit

            # Die laufende Installation, in die eingespielt wird: anderer
            # Schluessel, und der Prozess hat ihn sich gemerkt.
            einstellungen.key_file.write_text(
                "schluessel-der-laufenden-installation", encoding="utf-8"
            )
            crypto.fernet_vergessen()
            vorher = crypto._fernet()
            # Der Merker existiert wirklich. Ohne diese Zeile bestuende der
            # Test auch dann, wenn crypto bei jedem Aufruf neu ableitete.
            assert crypto._fernet() is vorher
            # Und mit dem falschen Schluessel ist das Geheimnis wirklich
            # unlesbar - sonst bewiese der Klartext unten nichts.
            assert crypto.decrypt(geheim) == ""

            sicherung.wiederherstellen(daten, PASSWORT)

            assert crypto._fernet() is not vorher
            assert crypto.decrypt(geheim) == "radarr-api-key-aus-der-sicherung"
        finally:
            # Beides ist prozessweit beziehungsweise liegt im gemeinsamen
            # Datenverzeichnis: Der naechste Test soll wieder mit dem
            # Schluessel aus der Umgebung ableiten, und eine liegengebliebene
            # secret.key wanderte sonst in die Archive der anderen Tests.
            crypto.fernet_vergessen()
            einstellungen.key_file.unlink(missing_ok=True)

    def test_vorher_wird_gesichert(self, admin_client: TestClient) -> None:
        """⚠️ Der einzige Weg zurueck, wenn die eingespielte Sicherung kaputt ist."""
        daten = self._archiv()
        sicherung.wiederherstellen(daten, PASSWORT)

        rueckwege = [e for e in sicherung.liste() if e.kommentar == "vor dem Wiederherstellen"]
        assert len(rueckwege) == 1


class TestEinrichtungsWeg:
    """Der Weg im Assistenten - ohne Anmeldung, aber nur solange es kein Konto gibt."""

    def test_ohne_konto_erlaubt(self, client: TestClient) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        antwort = client.post(
            "/api/setup/sicherung/pruefen",
            files={"datei": ("s.zip", daten, "application/zip")},
            data={"passwort": PASSWORT},
        )
        assert antwort.status_code == 200
        assert antwort.json()["einspielbar"] is True

    def test_mit_konto_gesperrt(self, admin_client: TestClient) -> None:
        """⚠️ Der Schutz des ganzen Weges.

        Ohne diese Sperre koennte jeder, der die Adresse kennt, jederzeit eine
        fremde Datenbank einspielen - ohne Anmeldung.
        """
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        for weg in ("pruefen", "einspielen"):
            antwort = admin_client.post(
                f"/api/setup/sicherung/{weg}",
                files={"datei": ("s.zip", daten, "application/zip")},
                data={"passwort": PASSWORT},
            )
            assert antwort.status_code == 409, weg


class TestProfilbilder:
    """⚠️ Profilbilder liegen als **Dateien** daneben, nicht in der Datenbank.

    Dort steht nur ihr Name. Ohne sie kaeme eine Installation zurueck, in der
    jeder sein Bild verloren hat - und niemand verstuende warum, weil "die
    Sicherung war doch vollstaendig".
    """

    def _bild_anlegen(self, name: str = "probe.jpg") -> Path:

        from app.config import get_settings

        ordner = get_settings().data_dir / "avatars"
        ordner.mkdir(parents=True, exist_ok=True)
        pfad: Path = ordner / name
        pfad.write_bytes(b"\xff\xd8\xff nur ein Testbild")
        return pfad

    def test_wandern_ins_archiv(self) -> None:
        self._bild_anlegen()
        pfad = sicherung.anlegen(art=sicherung.MANUELL)

        with _archiv_oeffnen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT) as zip_datei:
            assert "avatars/probe.jpg" in zip_datei.namelist()

    def test_kommen_beim_einspielen_zurueck(self, admin_client: TestClient) -> None:
        bild = self._bild_anlegen("kommt-zurueck.jpg")
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        bild.unlink()
        assert not bild.exists()

        sicherung.wiederherstellen(daten, PASSWORT)
        assert bild.exists()

    def test_kein_ausbruch_beim_auspacken(self) -> None:
        """⚠️ Ein praepariertes Archiv koennte ``avatars/../../secret.key`` heissen.

        Beim Auspacken zaehlt deshalb nur der reine Dateiname, nie der Pfad aus
        dem Archiv.
        """
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        echt = sicherung.archiv(pfad.name, PASSWORT)

        # Das echte Archiv um einen boesen Eintrag ergaenzen.
        quelle = pyzipper.AESZipFile(io.BytesIO(echt))
        quelle.setpassword(PASSWORT.encode("utf-8"))
        puffer = io.BytesIO()
        with pyzipper.AESZipFile(
            puffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as ziel:
            ziel.setpassword(PASSWORT.encode("utf-8"))
            for name in quelle.namelist():
                ziel.writestr(name, quelle.read(name))
            ziel.writestr("avatars/../../entwischt.txt", "hier sollte nichts landen")
        quelle.close()

        sicherung.wiederherstellen(puffer.getvalue(), PASSWORT)

        from app.config import get_settings

        ordner = get_settings().data_dir
        assert not (ordner.parent / "entwischt.txt").exists()
        assert not (ordner / "entwischt.txt").exists()
        # Der reine Dateiname landet im Bilderordner - das ist in Ordnung.
        assert (ordner / "avatars" / "entwischt.txt").exists()


class TestStandVonDamals:
    """⚠️ **Eine Sicherung ist ein Zeitpunkt, nicht nur eine Datei.**

    Die Datenbank wurde schon immer beim Anlegen kopiert, die Dateien daneben
    aber erst beim Herunterladen aus dem laufenden Datenverzeichnis geholt. Wer
    das Archiv einer vier Wochen alten Sicherung zog, bekam eine alte Datenbank
    mit heutigen Dateien. Der aeltere Bildertest bemerkte das nicht: Er legte
    das Bild **vor** dem Sichern an und liess es liegen - dann sieht beides
    gleich aus.
    """

    def _ablegen(self, unter: str, name: str, inhalt: bytes) -> Path:

        from app.config import get_settings

        ordner = get_settings().data_dir / unter
        ordner.mkdir(parents=True, exist_ok=True)
        pfad: Path = ordner / name
        pfad.write_bytes(inhalt)
        return pfad

    def test_das_bild_von_damals_kommt_ins_archiv(self) -> None:
        bild = self._ablegen("avatars", "getauscht.jpg", b"JPG das war damals")
        pfad = sicherung.anlegen(art=sicherung.MANUELL)

        # Nach der Sicherung tauscht jemand sein Bild.
        bild.write_bytes(b"JPG und das ist von heute")

        with _archiv_oeffnen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT) as zip_datei:
            assert zip_datei.read("avatars/getauscht.jpg") == b"JPG das war damals"

    def test_geloeschtes_bild_ist_trotzdem_im_archiv(self) -> None:
        bild = self._ablegen("avatars", "geloescht.jpg", b"JPG noch da")
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        bild.unlink()

        with _archiv_oeffnen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT) as zip_datei:
            assert "avatars/geloescht.jpg" in zip_datei.namelist()

    def test_der_trash_stand_von_damals_kommt_ins_archiv(self) -> None:
        """⚠️ Kein Beiwerk.

        Zu jedem Qualitaetsprofil steht in der Datenbank, gegen welchen
        TRaSH-Stand es geschrieben wurde. Ein Archiv mit alter Datenbank und
        heutigem Stand meldet nach dem Einspielen Abweichungen an Profilen, an
        denen niemand etwas geaendert hat.
        """
        stand = self._ablegen("trash", "trash-radarr.json", b'{"stand": "damals"}')
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        stand.write_bytes(b'{"stand": "heute"}')

        with _archiv_oeffnen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT) as zip_datei:
            assert zip_datei.read("trash/trash-radarr.json") == b'{"stand": "damals"}'

    def test_der_trash_stand_kommt_beim_einspielen_zurueck(self, admin_client: TestClient) -> None:
        stand = self._ablegen("trash", "herkunft.json", b'{"commit": "abc"}')
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        stand.write_bytes(b'{"commit": "spaeter"}')
        sicherung.wiederherstellen(daten, PASSWORT)

        assert stand.read_bytes() == b'{"commit": "abc"}'

    def test_das_bild_der_hausordnung_kommt_beim_einspielen_zurueck(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ In der Datenbank steht nur der Name des Bildes.

        Fehlte ``hausordnung`` in ``BEILAGEN``, kaeme der Regeltext
        vollstaendig zurueck und zeigte an jeder Bildstelle eine Luecke -
        geschrieben hat ihn jemand von Hand, ein zweites Mal will das niemand.
        """
        bild = self._ablegen("hausordnung", "regel.png", b"\x89PNG\r\n\x1a\ndamals")
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        # Nach der Sicherung wird das Bild ausgetauscht ...
        bild.write_bytes(b"\x89PNG\r\n\x1a\nheute")
        sicherung.wiederherstellen(daten, PASSWORT)

        # ... und kommt beim Einspielen im Stand von damals zurueck.
        assert bild.read_bytes() == b"\x89PNG\r\n\x1a\ndamals"

    def test_sicherung_ohne_hausordnungsbilder_laesst_sich_einspielen(
        self, admin_client: TestClient
    ) -> None:
        """Eine Sicherung aus der Zeit vor diesem Ordner darf nicht scheitern.

        Sie bringt keinen einzigen Eintrag unter ``hausordnung/`` mit - und
        genau so sehen alle Staende aus, die vor dieser Fassung entstanden
        sind.
        """
        # Ausdruecklich leer: Ein frueherer Test in dieser Klasse legt dort ein
        # Bild ab, und das Datenverzeichnis ueberlebt den Testwechsel.
        ordner = get_settings().data_dir / "hausordnung"
        if ordner.is_dir():
            shutil.rmtree(ordner)

        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        with _archiv_oeffnen(daten, PASSWORT) as zip_datei:
            assert not [n for n in zip_datei.namelist() if n.startswith("hausordnung/")]

        # Muss ohne Fehler durchlaufen.
        sicherung.wiederherstellen(daten, PASSWORT)

    def test_beilagen_verschwinden_mit_der_sicherung(self) -> None:
        """⚠️ Sonst sammelt der Ordner die Bilder aller je aufgeraeumten
        Staende, und niemand sucht dort nach Platz."""
        self._ablegen("avatars", "mitweg.jpg", b"JPG x")
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        beilagen = sicherung._beilagen_pfad(pfad)
        assert (beilagen / "avatars" / "mitweg.jpg").is_file()

        sicherung.entfernen(pfad)
        assert not beilagen.exists()

    def test_aufraeumen_nimmt_die_beilagen_mit(self) -> None:
        self._ablegen("avatars", "mitweg.jpg", b"JPG x")
        staende = [sicherung.anlegen(art=sicherung.AUTOMATISCH) for _ in range(3)]

        sicherung.aufraeumen(behalten=1)

        uebrig = [p for p in staende if p.exists()]
        assert len(uebrig) == 1
        for weg in (p for p in staende if p not in uebrig):
            assert not sicherung._beilagen_pfad(weg).exists()


class TestSteckbriefSagtDieWahrheit:
    """⚠️ **Das Feld ``enthaelt`` war ein fester Wert.**

    Geschrieben, bevor feststand, ob eine Schluesseldatei existiert - es nannte
    ``secret.key`` auch dann, wenn stattdessen nur der Hinweis darauf drinlag,
    und die Profilbilder nie. Gelesen wird es nirgends im Programm; getaeuscht
    hat es also genau den Leser, fuer den ueberhaupt ein offenes ZIP gewaehlt
    wurde: den Menschen, der das Archiv aufmacht, weil Nexview nicht laeuft.
    """

    def test_enthaelt_nennt_genau_das_was_drin_ist(self) -> None:
        from app.config import get_settings

        ordner = get_settings().data_dir / "avatars"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "im-steckbrief.jpg").write_bytes(b"JPG x")

        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        with _archiv_oeffnen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT) as zip_datei:
            namen = set(zip_datei.namelist())
            brief = json.loads(zip_datei.read(sicherung.STECKBRIEF))

        # Der Steckbrief zaehlt sich selbst nicht auf - alles andere schon.
        assert set(brief["enthaelt"]) == namen - {sicherung.STECKBRIEF}
        assert "avatars/im-steckbrief.jpg" in brief["enthaelt"]

    def test_nennt_keinen_schluessel_der_nicht_drin_ist(self) -> None:
        """In der Testumgebung kommt der Schluessel aus der Umgebungsvariablen -
        dann liegt statt ``secret.key`` der Hinweis darauf im Archiv."""
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        with _archiv_oeffnen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT) as zip_datei:
            namen = set(zip_datei.namelist())
            brief = json.loads(zip_datei.read(sicherung.STECKBRIEF))

        assert "secret.key" not in namen
        assert "secret.key" not in brief["enthaelt"]
        assert "SCHLUESSEL-FEHLT.txt" in brief["enthaelt"]


class TestVorschauZumSchluessel:
    """⚠️ **Der eine Fall, der still zuschlaegt.**

    Die Vorschau meldete, ob die *Zielinstallation* einen Schluessel in der
    Umgebung hat - nicht, ob im Archiv einer liegt. Kommt das Archiv von einer
    Installation mit Umgebungsvariable und hat das Ziel keine, sah die Vorschau
    beruhigend aus und bedeutete den schlimmsten Fall: ein neuer Schluessel, und
    danach kein lesbarer Zugang mehr.
    """

    def _mit_schluessel(self, daten: bytes) -> bytes:
        """Dasselbe Archiv, aber mit einer ``secret.key`` darin."""
        quelle = pyzipper.AESZipFile(io.BytesIO(daten))
        quelle.setpassword(PASSWORT.encode("utf-8"))
        puffer = io.BytesIO()
        with pyzipper.AESZipFile(
            puffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as ziel:
            ziel.setpassword(PASSWORT.encode("utf-8"))
            for name in quelle.namelist():
                if name != "SCHLUESSEL-FEHLT.txt":
                    ziel.writestr(name, quelle.read(name))
            ziel.writestr("secret.key", "ein-schluessel-aus-der-sicherung")
        quelle.close()
        return puffer.getvalue()

    def test_ohne_schluessel_im_archiv_sagt_die_vorschau_das(self) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        befund = sicherung.pruefen(sicherung.archiv(pfad.name, PASSWORT), PASSWORT)

        assert befund.schluessel_im_archiv is False

    def test_mit_schluessel_im_archiv_sagt_die_vorschau_das(self) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = self._mit_schluessel(sicherung.archiv(pfad.name, PASSWORT))

        assert sicherung.pruefen(daten, PASSWORT).schluessel_im_archiv is True

    def test_die_schnittstelle_reicht_beides_durch(self, admin_client: TestClient) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        antwort = admin_client.post(
            "/api/admin/sicherungen/pruefen",
            files={"datei": ("sicherung.zip", daten, "application/zip")},
            data={"passwort": PASSWORT},
        )
        assert antwort.status_code == 200, antwort.text
        inhalt = antwort.json()
        # Zwei Fragen, nicht eine: Das Ziel hat einen Schluessel in der
        # Umgebung, im Archiv liegt trotzdem keiner.
        assert inhalt["schluessel_aus_umgebung"] is True
        assert inhalt["schluessel_im_archiv"] is False


class TestSitzungenNachDemEinspielen:
    def test_alle_werden_abgemeldet(self, admin_client: TestClient) -> None:
        """⚠️ Passiert **nicht** von selbst.

        Ich hatte angenommen, mit ``secret.key`` wechsele der Schluessel und
        alle Token wuerden ungueltig. Das gilt nur, wenn er sich aendert - wer
        eine Sicherung derselben Installation einspielt, hat hinterher
        denselben. Dann bliebe jede Anmeldung von vorher gueltig.
        """
        from app.db import SessionLocal
        from app.models import User

        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        with SessionLocal() as db:
            assert all(u.sessions_valid_from is None for u in db.query(User).all())

        sicherung.wiederherstellen(daten, PASSWORT)

        with SessionLocal() as db:
            konten = db.query(User).all()
            assert konten, "Ohne Konten prueft der Test nichts."
            assert all(u.sessions_valid_from is not None for u in konten)

    def test_ein_token_von_vorher_gilt_nicht_mehr(self, admin_client: TestClient) -> None:
        """Der Beweis am eigentlichen Riegel."""
        from datetime import timedelta

        from app.db import SessionLocal
        from app.models import User, utcnow
        from app.services.sitzung import TokenInhalt, gilt_noch

        with SessionLocal() as db:
            user = db.query(User).first()
            assert user is not None

            # ⚠️ Das Token muss **nach** ``password_changed_at`` liegen, sonst
            # scheitert schon die Voraussetzung: Bei einem frisch angelegten
            # Konto steht der Passwort-Zeitpunkt auf "jetzt", und ein aelteres
            # Token ist voellig zu Recht ungueltig.
            jetzt = utcnow()
            token = TokenInhalt(
                benutzer_id=user.id,
                ausgestellt=int(jetzt.timestamp() * 1000) + 1000,
            )
            assert gilt_noch(token, user) is True, "Voraussetzung: das Token gilt noch"

            # Und jetzt die Grenze dahinter setzen - wie beim Wiederherstellen.
            #
            # ⚠️ **Ueber die Datenbank, nicht im Speicher.** Der erste Anlauf
            # hat den Wert nur am Objekt gesetzt und war gruen - beim echten
            # Anmelden kam er aber aus SQLite, also **ohne Zeitzone**, und der
            # Vergleich warf ``TypeError``. Ein Test, der den Umweg ueber die
            # Datenbank auslaesst, prueft genau die Stelle nicht, an der es
            # schiefgeht.
            user.sessions_valid_from = jetzt + timedelta(seconds=5)
            db.commit()

        with SessionLocal() as db:
            frisch = db.query(User).filter(User.id == token.benutzer_id).one()
            assert frisch.sessions_valid_from is not None
            assert gilt_noch(token, frisch) is False


class TestWiederherstellenUeberDieSchnittstelle:
    """⚠️ **Die Schicht, die meine Tests uebersprungen hatten.**

    Alle Tests darueber rufen den Dienst direkt auf. Der Weg durch den Router -
    Datei entgegennehmen, Antwort zusammenbauen - war nie geprueft. Genau dort
    ist dann ein Pflichtfeld in der Antwort vergessen worden, und der Aufruf
    endete mit 500, waehrend jeder Dienst-Test gruen blieb.
    """

    def _hochladen(self, client: TestClient, weg: str, daten: bytes, passwort: str = PASSWORT):
        return client.post(
            f"/api/admin/sicherungen/{weg}",
            files={"datei": ("sicherung.zip", daten, "application/zip")},
            data={"passwort": passwort},
        )

    def test_pruefen_liefert_eine_vollstaendige_antwort(self, admin_client: TestClient) -> None:
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar="ueber die Schnittstelle")
        daten = sicherung.archiv(pfad.name, PASSWORT)

        antwort = self._hochladen(admin_client, "pruefen", daten)
        assert antwort.status_code == 200, antwort.text

        inhalt = antwort.json()
        # Jedes Feld einzeln: Ein fehlendes hat den Aufruf zum Absturz gebracht.
        for feld in (
            "version",
            "erstellt",
            "art",
            "kommentar",
            "einspielbar",
            "grund",
            "schluessel_aus_umgebung",
            "schluessel_im_archiv",
        ):
            assert feld in inhalt, f"{feld} fehlt in der Antwort"
        assert inhalt["kommentar"] == "ueber die Schnittstelle"
        assert inhalt["einspielbar"] is True

    def test_falsches_passwort_ist_kein_serverfehler(self, admin_client: TestClient) -> None:
        """400 mit Kennung, nicht 500 - sonst steht in der Oberflaeche nur eine Nummer."""
        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        antwort = self._hochladen(admin_client, "pruefen", daten, passwort="ganz-was-anderes")
        assert antwort.status_code == 400
        assert antwort.json()["detail"]["code"] == "restore_wrong_password"

    def test_einspielen_ueber_die_schnittstelle(self, admin_client: TestClient) -> None:
        from app.db import SessionLocal
        from app.models import User
        from app.security import hash_password

        pfad = sicherung.anlegen(art=sicherung.MANUELL)
        daten = sicherung.archiv(pfad.name, PASSWORT)

        with SessionLocal() as db:
            db.add(User(username="danach", password_hash=hash_password("x"), email="d@b.de"))
            db.commit()

        antwort = self._hochladen(admin_client, "einspielen", daten)
        assert antwort.status_code == 200, antwort.text

        with SessionLocal() as db:
            assert db.query(User).filter(User.username == "danach").count() == 0

    def test_nur_fuer_administratoren(self, client: TestClient) -> None:
        assert self._hochladen(client, "pruefen", b"egal").status_code in (401, 403)
        assert self._hochladen(client, "einspielen", b"egal").status_code in (401, 403)


class TestRunForever:
    """Der stuendliche Takt verlaesst die Ereignisschleife.

    ``anlegen`` blockiert (``VACUUM INTO`` plus Packen), und dasselbe gilt fuer
    das Dateilisten in ``faellig``. Laeuft das im Schleifenthread, steht
    waehrenddessen jede andere Anfrage. Deshalb schiebt ``run_forever`` die
    ganze Runde per ``asyncio.to_thread`` hinaus, und genau das wird hier
    festgenagelt.
    """

    async def test_der_takt_laeuft_nicht_im_schleifenthread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gesehen: list[int] = []

        def merken(*, art: str, kommentar: str) -> Path:
            gesehen.append(threading.get_ident())
            return Path("unbenutzt")

        monkeypatch.setattr(sicherung, "anlegen", merken)
        monkeypatch.setattr(sicherung, "faellig", lambda takt: True)

        stop = asyncio.Event()
        aufgabe = asyncio.create_task(sicherung.run_forever(stop))
        for _ in range(500):
            if gesehen:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await aufgabe

        assert gesehen, "der Takt ist nie bis zu anlegen gekommen"
        # Der Test selbst laeuft im Thread der Ereignisschleife; der Takt
        # muss woanders gelaufen sein.
        assert gesehen[0] != threading.get_ident()

    async def test_eine_ausnahme_im_takt_toetet_die_schleife_nicht(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Die Schutzregel der Schleife gilt weiter: sie darf nie sterben.

        Und der Takt laeuft genau einmal je Runde, nicht oefter: mehr als der
        eine Aufruf vor dem Stopp darf nicht passiert sein.
        """
        aufrufe: list[int] = []

        def kaputt() -> None:
            aufrufe.append(1)
            raise RuntimeError("Takt kaputt")

        monkeypatch.setattr(sicherung, "_takt", kaputt)

        stop = asyncio.Event()
        aufgabe = asyncio.create_task(sicherung.run_forever(stop))
        for _ in range(500):
            if aufrufe:
                break
            await asyncio.sleep(0.01)
        stop.set()
        # Truege die Aufgabe die Ausnahme weiter, wuerfe dieses await sie hier.
        await aufgabe

        assert len(aufrufe) == 1


class TestTaktGegenEinspielen:
    """Takt-Thread und Einspielen teilen sich ``_pflege_schloss``.

    Seit die Taktrunde per ``asyncio.to_thread`` in einem Arbeitsthread
    laeuft, konnte sie mit ``wiederherstellen`` ueberlappen: Der Takt fuellt
    den Verbindungsvorrat nach jedem ``engine.dispose()`` sofort wieder und
    haelt die ``-wal``-Datei offen - das Einspielen scheiterte dann mit
    ``restore_files_busy``, und einmal busy blieb es das auch beim
    Wiederholen (gemessen am 02.09.2026: 4 von 8 Versuchen). Das Schloss
    serialisiert beide: Einspielen wartet die laufende Runde ab, der Takt
    setzt aus, solange eingespielt wird.
    """

    def test_einspielen_wartet_auf_die_laufende_taktrunde(
        self, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Einspielen mitten in einer kuenstlich verlangsamten Taktrunde.

        Es darf weder mit ``restore_files_busy`` scheitern noch haengen - es
        wartet, bis die Runde fertig ist, und laeuft dann durch. Die
        Reihenfolge-Liste haelt genau das fest: erst die Runde, dann das
        Einspielen.
        """
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar="takt-probe")
        daten = sicherung.archiv(pfad.name, PASSWORT)

        # ``off`` steht nicht in ``TAKTE``: die Runde legt keine Sicherung
        # an, ihr langsamer Teil ist allein die gebremste Pflege darunter.
        monkeypatch.setattr(
            settings_service,
            "load_settings",
            lambda db: types.SimpleNamespace(backup_schedule="off"),
        )

        betreten = threading.Event()
        reihenfolge: list[str] = []

        def gebremste_pflege(seiten: int = 1000) -> int:
            betreten.set()
            time.sleep(0.8)
            reihenfolge.append("takt fertig")
            return 0

        monkeypatch.setattr(db_modul, "platz_zurueckgeben", gebremste_pflege)

        faden = threading.Thread(target=sicherung._takt)
        faden.start()
        try:
            assert betreten.wait(5), "die Taktrunde ist nie angelaufen"
            befund = sicherung.wiederherstellen(daten, PASSWORT)
            reihenfolge.append("einspielen fertig")
        finally:
            faden.join(10)
        assert not faden.is_alive(), "die Taktrunde haengt"
        assert befund.grund == "ok"
        assert reihenfolge == ["takt fertig", "einspielen fertig"], (
            f"das Einspielen lief mitten in die Taktrunde hinein: {reihenfolge}"
        )

    def test_der_takt_setzt_aus_solange_eingespielt_wird(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Haelt das Einspielen das Schloss, laeuft keine Runde an.

        Und danach wird sie nachgeholt, nicht verschluckt: Der Takt wartet am
        Schloss und traegt dann ab.
        """
        monkeypatch.setattr(
            settings_service,
            "load_settings",
            lambda db: types.SimpleNamespace(backup_schedule="off"),
        )
        gepflegt: list[int] = []
        monkeypatch.setattr(
            db_modul,
            "platz_zurueckgeben",
            lambda seiten=1000: gepflegt.append(seiten) or 0,
        )

        # Stellvertretend fuer ein laufendes Einspielen: dasselbe Schloss,
        # das ``wiederherstellen`` um den Dateitausch haelt.
        with sicherung._pflege_schloss:
            faden = threading.Thread(target=sicherung._takt)
            faden.start()
            time.sleep(0.3)
            assert not gepflegt, "die Taktrunde lief mitten ins Einspielen"
        faden.join(5)
        assert not faden.is_alive(), "die Taktrunde haengt"
        assert gepflegt == [1000], "nach dem Einspielen holt der Takt die Runde nach"
