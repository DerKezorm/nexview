"""Bestehende Konten eines Medienservers uebernehmen.

⚠️ **Der wichtigste Test hier ist ``test_wer_uebernommen_wurde_kommt_auch_rein``.**
Alles andere an diesem Feature waere wertlos, wenn er nicht hielte: Ein Import,
der Konten anlegt, in die sich niemand anmelden kann, ist schlimmer als kein
Import - er sieht aus, als haette er funktioniert.

Und ``test_der_betreiber_ist_kein_ziel`` ist der Ersatz fuer eine Wache, die
hier nicht haengen kann. ``deps.betreiberschutz`` liest genau ein ``user_id``
aus dem Pfad; der Import bekommt beliebig viele Ziele im Rumpf. Die Pruefung
sitzt deshalb im Dienst, und ``test_betreiber_waechter.py`` kann sie ueber die
Routentabelle nicht sehen. Dieser Test ist das, was stattdessen aufpasst - und
er ist schwaecher als die Wache, weil er hier steht und nicht dort.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import MediaServerBlock, Role, User
from app.security import has_usable_password
from app.services import mediaserver_accounts, nutzer_import
from app.services.mediaserver.base import ExternalAccount, ServerUser
from app.services.mediaserver.jellyfin import JellyfinServer
from app.services.quota import UNBEGRENZT
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user

ANBIETER = "jellyfin"


def _wunsch(kennung: str, name: str, ziel: int | None = None) -> nutzer_import.Wunsch:
    return nutzer_import.Wunsch(
        account_id=kennung, username=name, email=None, ziel_user_id=ziel
    )


def _konto(kennung: str, name: str) -> ExternalAccount:
    return ExternalAccount(
        provider=ANBIETER, account_id=kennung, username=name, email=None, thumb=None
    )


# --- Der Kern: kommt jemand danach wirklich herein? --------------------------


def test_wer_uebernommen_wurde_kommt_auch_rein(admin_client: TestClient) -> None:
    """Nach dem Import findet die Anmeldung das Konto - ueber die Verknuepfung.

    ⚠️ **Das ist die ganze Zusage des Features in einem Test.** Der Import legt
    Konto *und* Verknuepfung an; beim Anmelden fragt ``resolve`` als zweiten
    Schritt ``find_linked`` nach Anbieter und Kennung. Faende er nichts, fiele
    die Anmeldung durch bis zum ``knows_email``-Zweig und wuerde abgewiesen -
    bei Jellyfin und Emby immer, weil die keine Adresse kennen.
    """
    with SessionLocal() as db:
        ergebnis = nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-1", "neue")], nutzer_import.Vorgaben()
        )
        assert ergebnis.angelegt == 1
        assert ergebnis.abgelehnt == {}

        # Und jetzt der Weg, den die Person nimmt.
        gefunden = mediaserver_accounts.resolve(db, load_settings(db), _konto("jf-1", "neue"))
        assert gefunden.username == "neue"
        # Ohne Passwort, wie jedes Konto aus einer Medienserver-Anmeldung.
        assert not has_usable_password(gefunden.password_hash)


def test_der_betreiber_ist_kein_ziel(admin_client: TestClient) -> None:
    """Das Betreiberkonto laesst sich nicht verknuepfen.

    ⚠️ **Warum das eine Sicherheitsfrage ist und keine Formalie.** Eine
    Medienserver-Identitaet an ein Konto zu haengen heisst: Wer diese Identitaet
    kontrolliert, kommt in dieses Konto - ohne Passwort, denn die Anmeldung
    ueber den Server fragt keines. Ein zweiter Administrator koennte also seine
    eigene Jellyfin-Kennung an das Betreiberkonto haengen und sich danach als
    Betreiber anmelden. Uebernahme in zwei Klicks.

    ⚠️ **Und dieser Test ist der Ersatz fuer die Wache, die hier fehlt.** Wer
    die Pruefung in ``nutzer_import.uebernehmen`` entfernt, macht ihn rot - das
    ist das Einzige, was dann noch aufpasst.
    """
    betreiber_id = admin_client.get("/api/users/betreiber").json()["user_id"]
    assert betreiber_id, "Ohne Betreiber misst dieser Test nichts."

    with SessionLocal() as db:
        ergebnis = nutzer_import.uebernehmen(
            db,
            ANBIETER,
            [_wunsch("jf-boese", "wer-auch-immer", ziel=betreiber_id)],
            nutzer_import.Vorgaben(),
        )

    assert ergebnis.verknuepft == 0
    assert ergebnis.angelegt == 0
    assert "jf-boese" in ergebnis.abgelehnt

    # Und die Gegenprobe: Es liegt wirklich keine Verknuepfung am Betreiber.
    with SessionLocal() as db:
        betreiber = db.get(User, betreiber_id)
        assert mediaserver_accounts.verknuepfung(betreiber, ANBIETER) is None


# --- Zuordnen zu einem bestehenden Konto -------------------------------------


def test_ein_bestehendes_konto_bekommt_die_verknuepfung(admin_client: TestClient) -> None:
    """Der Normalfall beim zweiten Import: derselbe Mensch, zweiter Server."""
    create_user(admin_client, "schondada")
    with SessionLocal() as db:
        ziel = db.query(User).filter(User.username == "schondada").one()
        ergebnis = nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-2", "anders-benannt", ziel=ziel.id)],
            nutzer_import.Vorgaben(),
        )
        assert ergebnis.verknuepft == 1
        assert ergebnis.angelegt == 0

    # Kein zweites Konto - genau das soll der Weg verhindern.
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "anders-benannt").count() == 0
        gefunden = mediaserver_accounts.resolve(
            db, load_settings(db), _konto("jf-2", "anders-benannt")
        )
        assert gefunden.username == "schondada"


def test_zweimal_dieselbe_kennung_geht_nicht(admin_client: TestClient) -> None:
    """Eine Kennung gehoert genau einem Konto.

    Sonst entstuenden zwei Nexview-Konten, in die dieselbe Anmeldung fuehrt -
    und welches davon ``find_linked`` waehlt, waere Zufall.
    """
    with SessionLocal() as db:
        nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-3", "einmal")], nutzer_import.Vorgaben()
        )
        nochmal = nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-3", "zweimal")], nutzer_import.Vorgaben()
        )

    assert nochmal.angelegt == 0
    assert "jf-3" in nochmal.abgelehnt


def test_ein_kinderkonto_ist_kein_ziel(admin_client: TestClient) -> None:
    """Kinder sind Unterprofile ihrer Eltern - beim Anbieter gibt es sie nicht."""
    create_user(admin_client, "elternteil", "eltern-passwort-1", role=Role.user)
    with SessionLocal() as db:
        elternteil = db.query(User).filter(User.username == "elternteil").one()
        kind = User(
            username="das-kind",
            password_hash=elternteil.password_hash,
            parent_id=elternteil.id,
            # ⚠️ Die Rolle macht das Kinderkonto aus, nicht parent_id:
            # User.is_child liest role == Role.child. Ohne diese Zeile
            # baut der Test ein gewoehnliches Konto mit Elternteil und misst
            # etwas anderes, als er behauptet.
            role=Role.child,
            age=8,
        )
        db.add(kind)
        db.commit()
        kind_id = kind.id

    with SessionLocal() as db:
        ergebnis = nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-4", "kind", ziel=kind_id)], nutzer_import.Vorgaben()
        )
    assert ergebnis.verknuepft == 0
    assert "jf-4" in ergebnis.abgelehnt


def test_ein_konto_mit_verknuepfung_bekommt_keine_zweite(admin_client: TestClient) -> None:
    """Je Anbieter genau eine Verknuepfung - sonst laufen Zeile und Spalte auseinander."""
    create_user(admin_client, "hatschon")
    with SessionLocal() as db:
        ziel = db.query(User).filter(User.username == "hatschon").one()
        nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-5", "erste", ziel=ziel.id)], nutzer_import.Vorgaben()
        )
        nochmal = nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-6", "zweite", ziel=ziel.id)], nutzer_import.Vorgaben()
        )
    assert nochmal.verknuepft == 0
    assert "jf-6" in nochmal.abgelehnt


# --- Die Vorgaben fuer den Stapel --------------------------------------------


def test_die_vorgaben_gelten_fuer_alle_angelegten(admin_client: TestClient) -> None:
    """Die Grenzen aus dem Stapel landen an jedem neuen Konto."""
    with SessionLocal() as db:
        nutzer_import.uebernehmen(
            db,
            ANBIETER,
            [_wunsch("jf-7", "einer"), _wunsch("jf-8", "zweiter")],
            nutzer_import.Vorgaben(filme=5, serien="unlimited", speicher_gb=50),
        )

    with SessionLocal() as db:
        for name in ("einer", "zweiter"):
            konto = db.query(User).filter(User.username == name).one()
            assert konto.quota_movies_limit == 5
            # "unlimited" wird in der Datenbank zu -1, nicht zu NULL.
            assert konto.quota_series_limit == UNBEGRENZT
            assert konto.storage_limit_gb == 50
            # Rolle und Freigabe stehen nicht zur Wahl.
            assert konto.role == Role.user
            assert konto.auto_approve is False


def test_die_null_ueberlebt_und_heisst_darf_nichts(admin_client: TestClient) -> None:
    """Eine gesetzte 0 darf nicht als "Hausvorgabe" oder "unbegrenzt" ankommen.

    ⚠️ **Der Fehler aus 0.26.2, eine Ebene hoeher.** Dort verwandelte sich eine
    gesetzte 0 bei jedem Start zurueck in "unbegrenzt", weil zwei Bedeutungen
    auf denselben Wert fielen. Hier ist die Gefahr dieselbe: Wer 0 eintraegt,
    meint "darf nichts anfragen" - und ``NULL`` hiesse "Hausvorgabe", also
    womoeglich sehr viel.
    """
    with SessionLocal() as db:
        nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-null", "keinerlei")],
            nutzer_import.Vorgaben(filme=0, serien=0, speicher_gb=0),
        )
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "keinerlei").one()
        assert konto.quota_movies_limit == 0
        assert konto.quota_series_limit == 0
        assert konto.storage_limit_gb == 0
        # Und ausdruecklich nicht NULL - das waere die Hausvorgabe.
        assert konto.quota_movies_limit is not None


def test_inaktiv_angelegt_bleibt_inaktiv(admin_client: TestClient) -> None:
    """Erst uebernehmen, spaeter freischalten."""
    with SessionLocal() as db:
        nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-aus", "nochnicht")],
            nutzer_import.Vorgaben(aktiv=False),
        )
    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "nochnicht").one().is_active is False


def test_die_vorgaben_fassen_verknuepfte_konten_nicht_an(admin_client: TestClient) -> None:
    """Ein bestehendes Konto behaelt seine Grenzen.

    ⚠️ **Sonst waere der Import ein stiller Datenverlust.** Wer einem Konto
    einmal 10 Filme zugeteilt hat, hat das bewusst getan; ein Import, der
    nebenbei die Stapelwerte darueberschreibt, nimmt ihm das weg, ohne dass
    irgendwo steht, dass er es getan hat.
    """
    create_user(admin_client, "hatgrenzen")
    with SessionLocal() as db:
        ziel = db.query(User).filter(User.username == "hatgrenzen").one()
        ziel.quota_movies_limit = 10
        ziel.storage_limit_gb = 7
        db.commit()
        ziel_id = ziel.id

    with SessionLocal() as db:
        nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-erhalt", "egal", ziel=ziel_id)],
            nutzer_import.Vorgaben(filme=99, serien=99, speicher_gb=99, aktiv=False),
        )

    with SessionLocal() as db:
        konto = db.get(User, ziel_id)
        assert konto.quota_movies_limit == 10
        assert konto.storage_limit_gb == 7
        # Und aktiv ist es auch geblieben.
        assert konto.is_active is True


def test_ohne_vorgaben_darf_niemand_ungefragt_holen(admin_client: TestClient) -> None:
    """Die Vorgabe der Vorgaben: gewoehnlicher Nutzer, Freigabe von Hand.

    ⚠️ Zugriff auf die Bibliothek zu haben heisst nicht, ungefragt herunterladen
    zu duerfen. Dreissig auf einen Schlag angelegte Konten mit automatischer
    Freigabe waeren eine Ueberraschung mit Speicherverbrauch.
    """
    with SessionLocal() as db:
        nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-9", "vorgabe")], nutzer_import.Vorgaben()
        )
    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "vorgabe").one()
        assert konto.role == Role.user
        assert konto.auto_approve is False


# --- Die Liste, die zur Entscheidung vorgelegt wird ---------------------------


def test_zuordenbar_ist_je_anbieter(admin_client: TestClient) -> None:
    """Wer an Plex haengt, steht beim Jellyfin-Import trotzdem zur Auswahl.

    ⚠️ **Der Fall, an dem das zweite Konto entsteht.** Beim zweiten Import hat
    die Person schon ein Nexview-Konto - nur eben mit der Verknuepfung eines
    anderen Anbieters. Faende sie sich in der Auswahl nicht, legte der Betreiber
    ein Duplikat an, und Nexview kann Konten nicht zusammenfuehren.
    """
    create_user(admin_client, "hatplex")
    with SessionLocal() as db:
        ziel = db.query(User).filter(User.username == "hatplex").one()
        mediaserver_accounts.link(
            ziel,
            ExternalAccount(
                provider="plex", account_id="px-1", username="hatplex", email=None, thumb=None
            ),
        )
        db.commit()

    with SessionLocal() as db:
        namen = {z.username for z in nutzer_import.zuordenbare_konten(db, ANBIETER)}
        assert "hatplex" in namen, "Beim Jellyfin-Import muss dieses Konto zur Wahl stehen."

        # Und beim Plex-Import eben nicht mehr: Dort ist es schon verknuepft.
        namen_plex = {z.username for z in nutzer_import.zuordenbare_konten(db, "plex")}
        assert "hatplex" not in namen_plex


def test_die_auswahl_sagt_woran_ein_konto_haengt(admin_client: TestClient) -> None:
    """„jamie (Plex)" statt bloss „jamie" - der einzige Hinweis beim zweiten Import."""
    create_user(admin_client, "mitplex")
    with SessionLocal() as db:
        ziel = db.query(User).filter(User.username == "mitplex").one()
        mediaserver_accounts.link(
            ziel,
            ExternalAccount(
                provider="plex", account_id="px-2", username="mitplex", email=None, thumb=None
            ),
        )
        db.commit()

    with SessionLocal() as db:
        eintrag = next(
            z for z in nutzer_import.zuordenbare_konten(db, ANBIETER) if z.username == "mitplex"
        )
        assert eintrag.verknuepft_mit == ("plex",)


@pytest.mark.parametrize("provider", ["plex", "jellyfin", "emby"])
def test_alle_drei_anbieter_gehen_denselben_weg(
    admin_client: TestClient, provider: str
) -> None:
    """Der Import ist nicht auf einen Anbieter zugeschnitten.

    Die Unterschiede zwischen den dreien stecken im Adapter (woher die Liste
    kommt), nicht hier. Dieser Test haelt fest, dass der Dienst selbst keinen
    von ihnen kennt.
    """
    with SessionLocal() as db:
        ergebnis = nutzer_import.uebernehmen(
            db,
            provider,
            [_wunsch(f"{provider}-x", f"{provider}nutzer")],
            nutzer_import.Vorgaben(),
        )
    assert ergebnis.angelegt == 1


def test_eine_gesperrte_kennung_wird_nicht_uebernommen(admin_client: TestClient) -> None:
    """Wer auf der Sperrliste steht, bekommt kein Konto.

    ⚠️ **Der Fall, den dieses Feature sonst genau falsch macht.** Wird ein
    Konto geloescht, landet seine Medienserver-Identitaet auf der Sperrliste,
    damit sie sich nicht bei der naechsten Anmeldung gleich wieder selbst
    anlegt. ``resolve`` fragt die Liste als **erstes** ab.

    Ein Import ohne diese Pruefung baut Konto und Verknuepfung, und die
    Anmeldung wird trotzdem abgewiesen - ein Konto, das aussieht, als
    funktioniere es, und das niemand benutzen kann. Gemeldet am 02.09.2026
    nach genau diesem Ablauf: importieren, loeschen, wieder importieren.
    """
    with SessionLocal() as db:
        db.add(MediaServerBlock(provider=ANBIETER, account_id="jf-gesperrt", username="weg"))
        db.commit()

        ergebnis = nutzer_import.uebernehmen(
            db, ANBIETER, [_wunsch("jf-gesperrt", "weg")], nutzer_import.Vorgaben()
        )

    assert ergebnis.angelegt == 0
    assert "jf-gesperrt" in ergebnis.abgelehnt
    assert "gesperrt" in ergebnis.abgelehnt["jf-gesperrt"]

    with SessionLocal() as db:
        assert db.query(User).filter(User.username == "weg").count() == 0


def test_die_liste_weist_gesperrte_kennungen_aus(admin_client: TestClient) -> None:
    """Und man sieht es der Zeile an, bevor man sie anhakt.

    Sonst hakt der Betreiber sie an, drückt übernehmen und bekommt eine
    Ablehnung für etwas, das die Liste ihm gerade angeboten hat.
    """
    with SessionLocal() as db:
        db.add(MediaServerBlock(provider=ANBIETER, account_id="jf-rot", username="rot"))
        db.commit()

        vorlage = nutzer_import.kandidaten(
            db,
            ANBIETER,
            [
                ServerUser(account_id="jf-rot", username="rot", email=None),
                ServerUser(account_id="jf-gruen", username="gruen", email=None),
            ],
        )

    nach_kennung = {z.account_id: z for z in vorlage.kandidaten}
    assert nach_kennung["jf-rot"].gesperrt is True
    assert nach_kennung["jf-gruen"].gesperrt is False


def test_mit_ausdruecklichem_ja_wird_die_sperre_aufgehoben(admin_client: TestClient) -> None:
    """Der Administrator holt jemanden absichtlich zurueck.

    ⚠️ **Die Sperre wird dabei geloescht, nicht uebergangen.** Bliebe sie
    stehen, gaebe es Konto und Verknuepfung - und die Anmeldung wiese trotzdem
    ab, weil ``resolve`` die Liste als Erstes fragt. Das waere ein Konto, das
    aussieht, als funktioniere es.
    """
    with SessionLocal() as db:
        db.add(MediaServerBlock(provider=ANBIETER, account_id="jf-zurueck", username="wieder"))
        db.commit()

        ergebnis = nutzer_import.uebernehmen(
            db,
            ANBIETER,
            [
                nutzer_import.Wunsch(
                    account_id="jf-zurueck",
                    username="wieder",
                    email=None,
                    ziel_user_id=None,
                    trotz_sperre=True,
                )
            ],
            nutzer_import.Vorgaben(),
        )

    assert ergebnis.angelegt == 1
    assert ergebnis.aufgehoben == 1
    assert ergebnis.abgelehnt == {}

    with SessionLocal() as db:
        # Die Sperre ist weg ...
        assert not mediaserver_accounts.is_blocked(db, ANBIETER, "jf-zurueck")
        # ... und die Anmeldung kommt jetzt wirklich durch. Ohne diese Zeile
        # bewiese der Test nur, dass eine Tabellenzeile verschwunden ist.
        gefunden = mediaserver_accounts.resolve(
            db, load_settings(db), _konto("jf-zurueck", "wieder")
        )
        assert gefunden.username == "wieder"


# --- Die Adressen selbst ------------------------------------------------------
#
# ⚠️ Der Dienst darueber ist gut abgedeckt, die Adressen waren es nicht. Genau
# dort sitzt aber, wer ueberhaupt hereindarf und was bei einem Anbieter
# passiert, der gar nicht verbunden ist.


def _jellyfin_verbinden() -> None:
    from app.crypto import encrypt
    from app.models import MediaServerConnection

    with SessionLocal() as db:
        db.add(
            MediaServerConnection(
                provider=ANBIETER,
                machine_id="jf-maschine",
                name="Jellyfin",
                url="http://127.0.0.1:8096",
                token=encrypt("admin-token"),
            )
        )
        db.commit()


def test_die_liste_ist_nur_fuer_administratoren(admin_client: TestClient) -> None:
    """Ein gewoehnliches Konto kommt an keine der beiden Adressen.

    ⚠️ Die Liste nennt Benutzernamen und Mailadressen aller Server-Konten -
    das ist nichts, was ein Mitbenutzer sehen muss.
    """
    _jellyfin_verbinden()
    create_user(admin_client, "gewoehnlich", "ein-langes-passwort-1")
    kopf = auth_headers(admin_client, "gewoehnlich", "ein-langes-passwort-1")

    assert admin_client.get(
        f"/api/admin/mediaserver/{ANBIETER}/import-kandidaten", headers=kopf
    ).status_code == 403
    assert admin_client.post(
        f"/api/admin/mediaserver/{ANBIETER}/import", json={"wuensche": []}, headers=kopf
    ).status_code == 403


def test_ohne_anmeldung_gar_nichts(client: TestClient) -> None:
    assert client.get(f"/api/admin/mediaserver/{ANBIETER}/import-kandidaten").status_code == 401
    assert (
        client.post(f"/api/admin/mediaserver/{ANBIETER}/import", json={"wuensche": []}).status_code
        == 401
    )


def test_ein_nicht_verbundener_anbieter_wird_abgewiesen(admin_client: TestClient) -> None:
    """Und zwar mit einer Kennung, nicht mit einem Absturz.

    ⚠️ Ohne diese Pruefung liefe der Adapter gegen eine leere Adresse und
    scheiterte irgendwo tiefer - die Oberflaeche saehe einen Serverfehler, wo
    "der ist gar nicht verbunden" die Auskunft ist.
    """
    antwort = admin_client.get(f"/api/admin/mediaserver/{ANBIETER}/import-kandidaten")
    assert antwort.status_code == 400
    assert antwort.json()["detail"]["code"] == "mediaserver_provider_not_linked"

    zweite = admin_client.post(
        f"/api/admin/mediaserver/{ANBIETER}/import", json={"wuensche": []}
    )
    assert zweite.status_code == 400


def test_ein_unbekannter_anbieter_wird_abgewiesen(admin_client: TestClient) -> None:
    """"gibt es nicht" darf nicht wie "geht gerade nicht" aussehen."""
    antwort = admin_client.get("/api/admin/mediaserver/kodi/import-kandidaten")
    assert antwort.status_code == 400


def test_ein_kaputter_rumpf_wird_abgewiesen(admin_client: TestClient) -> None:
    """Fehlt die Liste, ist das ein 422 und kein halb ausgefuehrter Import."""
    _jellyfin_verbinden()
    assert admin_client.post(f"/api/admin/mediaserver/{ANBIETER}/import", json={}).status_code == 422


def test_die_adresse_reicht_die_entscheidungen_durch(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vom Rumpf bis in die Datenbank - einmal ueber die echte Adresse.

    ⚠️ **Die Tests darueber rufen den Dienst direkt.** Zwischen Rumpf und
    Dienst liegt aber eine Uebersetzung, und genau dort geht so etwas verloren:
    ``user_id`` wird zu ``ziel_user_id``, ``filme`` zu einer Grenze in der
    Datenbank. Ohne diesen Test waere die Umbenennung ungeprueft.
    """
    _jellyfin_verbinden()

    async def _konten(self: object) -> list[ServerUser]:
        return [ServerUser(account_id="jf-adr", username="ueberdieadresse", email=None)]

    monkeypatch.setattr(JellyfinServer, "importierbare_konten", _konten)

    kandidaten = admin_client.get(f"/api/admin/mediaserver/{ANBIETER}/import-kandidaten")
    assert kandidaten.status_code == 200, kandidaten.text
    assert [z["account_id"] for z in kandidaten.json()["kandidaten"]] == ["jf-adr"]

    antwort = admin_client.post(
        f"/api/admin/mediaserver/{ANBIETER}/import",
        json={
            "wuensche": [
                {"account_id": "jf-adr", "username": "ueberdieadresse", "user_id": None}
            ],
            "filme": 3,
            "speicher_gb": "unlimited",
            "aktiv": False,
        },
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["angelegt"] == 1

    with SessionLocal() as db:
        konto = db.query(User).filter(User.username == "ueberdieadresse").one()
        assert konto.quota_movies_limit == 3
        assert konto.storage_limit_gb == UNBEGRENZT
        assert konto.is_active is False
        # Und die Verknuepfung ist mitgekommen - ohne sie waere das Konto tot.
        assert mediaserver_accounts.verknuepfung(konto, ANBIETER) is not None
