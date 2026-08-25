"""Die Bremse an den Tueren, hinter denen ein Geheimnis geprueft wird.

Geprueft wird nicht nur, *dass* gesperrt wird, sondern vor allem, wen es
**nicht** treffen darf: den Vertipper, den Menschen mit unbestaetigter
Adresse, den Haushalt hinter einem Reverse Proxy, und den Nachbarn, dessen
Medienserver gerade neu startet. Eine Bremse, die Fremde aussperrt und die
eigenen Leute gleich mit, ist keine Verbesserung.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import anmeldebremse

from .conftest import create_user

FALSCH = {"username": "anna", "password": "voellig-falsch"}


@pytest.fixture(autouse=True)
def _frei() -> None:
    anmeldebremse.zuruecksetzen()


@pytest.fixture
def adresse(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Nexview so einstellen, dass es die Adresse des Anfragenden kennt.

    Ueber den Proxy-Weg statt ueber ``direct``, weil ``TestClient`` sich als
    Gegenstelle ``"testclient"`` meldet - und das ist keine Adresse. Der
    Proxy-Weg ist ohnehin der, den die Zielgruppe fährt.
    """
    monkeypatch.setattr(get_settings(), "client_ip", "proxy")
    admin_client.headers["X-Forwarded-For"] = "203.0.113.7"
    return admin_client


def _falsch_anmelden(client: TestClient, mal: int) -> list[int]:
    return [client.post("/api/auth/login", json=FALSCH).status_code for _ in range(mal)]


# ---------------------------------------------------------------- Zaehlwerk


def test_die_ersten_drei_fehlversuche_kosten_nichts(admin_client: TestClient) -> None:
    """Wer sich vertippt, merkt nichts. Das ist der haeufigste Fall."""
    create_user(admin_client, "anna")
    assert _falsch_anmelden(admin_client, 3) == [401, 401, 401]


def test_ab_dem_vierten_wird_gebremst(admin_client: TestClient) -> None:
    create_user(admin_client, "anna")
    _falsch_anmelden(admin_client, 4)

    antwort = admin_client.post("/api/auth/login", json=FALSCH)
    assert antwort.status_code == 429
    assert antwort.json()["detail"]["code"] == "too_many_attempts"


def test_die_bremse_sagt_wie_lange(admin_client: TestClient) -> None:
    """``Retry-After`` steht drin, damit die Oberflaeche mitzaehlen kann,
    statt zu raten."""
    create_user(admin_client, "anna")
    _falsch_anmelden(admin_client, 4)

    antwort = admin_client.post("/api/auth/login", json=FALSCH)
    assert antwort.status_code == 429
    assert int(antwort.headers["Retry-After"]) >= 1


def test_die_wartezeit_waechst(admin_client: TestClient) -> None:
    create_user(admin_client, "anna")
    zeiten = []
    for _ in range(6):
        anmeldebremse.fehlschlag("login|konto|anna")
        zeiten.append(anmeldebremse.restliche_sperre("login|konto|anna"))

    # Die ersten drei sind frei, danach verdoppelt es sich.
    assert zeiten[0] == zeiten[1] == zeiten[2] == 0.0
    assert 0 < zeiten[3] < zeiten[4] < zeiten[5]


def test_ab_dem_zehnten_ist_es_eine_echte_sperre(client: TestClient) -> None:
    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
        anmeldebremse.fehlschlag("login|konto|anna")

    rest = anmeldebremse.restliche_sperre("login|konto|anna")
    assert rest > anmeldebremse.WARTE_MAX_SEKUNDEN
    assert rest <= anmeldebremse.SPERRE_SEKUNDEN


def test_die_sperre_geht_von_selbst_auf(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ Eine Sperre, die den Administrator zum Aufschliessen braucht,
    sperrt in der Praxis den Administrator aus."""
    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
        anmeldebremse.fehlschlag("login|konto|anna")
    assert anmeldebremse.restliche_sperre("login|konto|anna") > 0

    spaeter = anmeldebremse._jetzt() + anmeldebremse.SPERRE_SEKUNDEN + 1
    monkeypatch.setattr(anmeldebremse, "_jetzt", lambda: spaeter)
    assert anmeldebremse.restliche_sperre("login|konto|anna") == 0


def test_nach_einer_stunde_ruhe_faengt_es_bei_null_an(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sonst summieren sich Vertipper ueber Wochen zu einer Sperre."""
    for _ in range(5):
        anmeldebremse.fehlschlag("login|konto|anna")

    spaeter = anmeldebremse._jetzt() + anmeldebremse.GEDAECHTNIS_SEKUNDEN + 1
    monkeypatch.setattr(anmeldebremse, "_jetzt", lambda: spaeter)

    anmeldebremse.fehlschlag("login|konto|anna")
    # Wieder der erste Versuch - also noch frei.
    assert anmeldebremse.restliche_sperre("login|konto|anna") == 0


def test_richtiges_passwort_loescht_den_zaehler(admin_client: TestClient) -> None:
    create_user(admin_client, "anna", "passwort-1234")
    _falsch_anmelden(admin_client, 3)

    assert admin_client.post(
        "/api/auth/login", json={"username": "anna", "password": "passwort-1234"}
    ).status_code == 200

    # Und danach sind wieder drei frei.
    assert _falsch_anmelden(admin_client, 3) == [401, 401, 401]


# ------------------------------------------------- wen es nicht treffen darf


def test_unbestaetigte_adresse_zaehlt_nicht_als_fehlversuch(admin_client: TestClient) -> None:
    """⚠️ Wer auf seine Bestaetigungsmail wartet und es alle zwei Minuten
    noch einmal versucht, kennt sein Passwort. Wuerde das mitzaehlen, sperrte
    sich genau diese Person aus."""
    create_user(admin_client, "anna", "passwort-1234", email_verified=False)

    for _ in range(6):
        antwort = admin_client.post(
            "/api/auth/login", json={"username": "anna", "password": "passwort-1234"}
        )
        assert antwort.status_code == 403, antwort.text


def test_deaktiviertes_konto_zaehlt_nicht_als_fehlversuch(admin_client: TestClient) -> None:
    create_user(admin_client, "anna", "passwort-1234", is_active=False)

    for _ in range(6):
        antwort = admin_client.post(
            "/api/auth/login", json={"username": "anna", "password": "passwort-1234"}
        )
        assert antwort.status_code == 403, antwort.text


def test_ein_gesperrtes_konto_sperrt_die_anderen_nicht(admin_client: TestClient) -> None:
    create_user(admin_client, "anna")
    create_user(admin_client, "bernd", "passwort-1234")

    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
        admin_client.post("/api/auth/login", json=FALSCH)
    assert admin_client.post("/api/auth/login", json=FALSCH).status_code == 429

    assert admin_client.post(
        "/api/auth/login", json={"username": "bernd", "password": "passwort-1234"}
    ).status_code == 200


def test_schreibweise_gibt_keine_freien_versuche(admin_client: TestClient) -> None:
    """``Anna`` und ``anna`` sind derselbe Zaehler - die Anmeldung selbst
    unterscheidet sie ja auch nicht."""
    create_user(admin_client, "anna")
    for name in ("anna", "Anna", "ANNA", "AnNa"):
        admin_client.post("/api/auth/login", json={"username": name, "password": "falsch"})

    antwort = admin_client.post("/api/auth/login", json={"username": "anna", "password": "x"})
    assert antwort.status_code == 429


def test_ein_name_den_es_nicht_gibt_wird_auch_gebremst(client: TestClient) -> None:
    """Damit faengt jeder Angriff an - waere das frei, waere die Bremse
    umgehbar, indem man einfach immer neue Namen probiert."""
    for _ in range(4):
        client.post("/api/auth/login", json={"username": "gibtsnicht", "password": "x"})

    antwort = client.post("/api/auth/login", json={"username": "gibtsnicht", "password": "x"})
    assert antwort.status_code == 429


# ------------------------------------------------------------ Adresse / Proxy


def test_ohne_einstellung_wird_die_adresse_nicht_benutzt(admin_client: TestClient) -> None:
    """⚠️ Der wichtigste Test der Datei.

    Nexview weiss von sich aus nicht, ob ein Reverse Proxy davorsteht. Wuerde
    es die Gegenstelle einfach als echte Adresse nehmen, saehen hinter einem
    Proxy **alle** Anfragen gleich aus - und der erste Vertipper sperrte den
    ganzen Haushalt aus, den Administrator eingeschlossen.
    """
    create_user(admin_client, "anna")
    create_user(admin_client, "bernd", "passwort-1234")

    # Anna wird gesperrt. Bernd sitzt im Test an derselben Adresse.
    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
        admin_client.post("/api/auth/login", json=FALSCH)

    assert admin_client.post(
        "/api/auth/login", json={"username": "bernd", "password": "passwort-1234"}
    ).status_code == 200


def test_mit_bekannter_adresse_zaehlt_auch_die_adresse(adresse: TestClient) -> None:
    create_user(adresse, "anna")
    create_user(adresse, "bernd", "passwort-1234")

    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
        adresse.post("/api/auth/login", json=FALSCH)

    # Jetzt ist die Adresse dran, nicht nur das Konto - Bernd kommt von
    # derselben Maschine und muss ebenfalls warten.
    assert adresse.post(
        "/api/auth/login", json={"username": "bernd", "password": "passwort-1234"}
    ).status_code == 429


def test_proxy_nimmt_den_letzten_eintrag_nicht_den_ersten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ ``X-Forwarded-For`` darf jeder vorne befuellen.

    Wer den **ersten** Eintrag naehme, liesse sich vom Angreifer bei jeder
    Anfrage eine andere Adresse vorsetzen - die Bremse waere wertlos. Deshalb
    wird von hinten gezaehlt: Der letzte Eintrag stammt vom eigenen Proxy.
    """
    from starlette.requests import Request

    monkeypatch.setattr(get_settings(), "client_ip", "proxy")

    def anfrage(kette: str) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(b"x-forwarded-for", kette.encode())],
                "client": ("10.0.0.9", 1234),
            }
        )

    # Der Angreifer behauptet vorne 1.2.3.4; der eigene Proxy haengt 9.9.9.9 an.
    assert anmeldebremse.client_ip(anfrage("1.2.3.4, 9.9.9.9")) == "9.9.9.9"


def test_proxy_mit_zu_kurzer_kette_liefert_keine_adresse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lieber gar keine Adresse als eine falsche: Wenn weniger Eintraege
    ankommen als eingestellt, ist die Kette nicht die erwartete."""
    from starlette.requests import Request

    monkeypatch.setattr(get_settings(), "client_ip", "proxy:2")
    anfrage = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"9.9.9.9")],
            "client": ("10.0.0.9", 1234),
        }
    )
    assert anmeldebremse.client_ip(anfrage) is None


def test_unsinn_in_der_einstellung_schaltet_die_adresse_ab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Eine unverstandene Einstellung darf nicht dazu fuehren, dass etwas
    Beliebiges gezaehlt wird."""
    from starlette.requests import Request

    monkeypatch.setattr(get_settings(), "client_ip", "vielleicht")
    anfrage = Request({"type": "http", "headers": [], "client": ("10.0.0.9", 1234)})
    assert anmeldebremse.client_ip(anfrage) is None


def test_keine_adresse_aus_muell(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    monkeypatch.setattr(get_settings(), "client_ip", "proxy")
    anfrage = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"nicht-mal-eine-adresse")],
            "client": ("10.0.0.9", 1234),
        }
    )
    assert anmeldebremse.client_ip(anfrage) is None


# ------------------------------------------------------------- Aufraeumen


def test_erfundene_namen_fuellen_die_ablage_nicht_endlos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sonst waere die Bremse selbst der Angriff: Wer sich zehntausend
    Benutzernamen ausdenkt, legt zehntausend Zaehler an."""
    for nummer in range(50):
        anmeldebremse.fehlschlag(f"login|konto|erfunden-{nummer}")
    assert len(anmeldebremse._zaehler) == 50

    spaeter = anmeldebremse._jetzt() + anmeldebremse.GEDAECHTNIS_SEKUNDEN + 1
    monkeypatch.setattr(anmeldebremse, "_jetzt", lambda: spaeter)

    anmeldebremse.fehlschlag("login|konto|noch-einer")
    assert len(anmeldebremse._zaehler) == 1


# ------------------------------------------------------ die anderen Tueren


def test_die_token_tuer_wird_ohne_adresse_nicht_gesperrt(admin_client: TestClient) -> None:
    """⚠️ Bewusst so. Die Token haben 256 Bit Zufall und sind nicht ratbar;
    eine Sperre je Token waere Theater. Und eine haus-weite Sperre liesse
    sich von aussen ausloesen und verbaute dann allen das Zuruecksetzen ihres
    Passworts - echter Schaden gegen einen unmoeglichen Angriff."""
    for _ in range(20):
        antwort = admin_client.get("/api/onboarding/password/gibtsnicht")
        assert antwort.status_code == 404


def test_die_token_tuer_bremst_mit_adresse(adresse: TestClient) -> None:
    for _ in range(4):
        adresse.get("/api/onboarding/password/gibtsnicht")

    assert adresse.get("/api/onboarding/password/gibtsnicht").status_code == 429


def test_die_medienserver_tuer_hat_einen_eigenen_zaehler(admin_client: TestClient) -> None:
    """Sie darf sich mit der Nexview-Anmeldung nicht vermischen: Es ist ein
    anderes Passwort, oft ein anderer Name, und ein anderer Angriff."""
    create_user(admin_client, "anna", "passwort-1234")

    for _ in range(anmeldebremse.SPERRE_AB_VERSUCH):
        anmeldebremse.fehlschlag(
            anmeldebremse.schluessel_konto("medienserver:plex", "anna")
        )

    # Die Medienserver-Tuer ist zu ...
    assert (
        anmeldebremse.restliche_sperre(
            anmeldebremse.schluessel_konto("medienserver:plex", "anna")
        )
        > 0
    )
    # ... die Nexview-Anmeldung aber nicht.
    assert admin_client.post(
        "/api/auth/login", json={"username": "anna", "password": "passwort-1234"}
    ).status_code == 200


# ------------------------------------------- Kopfzeilen im Fehlerfall


def test_fehlerantwort_behaelt_ihre_kopfzeilen() -> None:
    """⚠️ Hier lag ein stiller Fehler, den die Bremse aufgedeckt hat.

    Der SPA-Rueckfall in ``main`` faengt *jede* HTTPException ab und baut die
    Antwort neu. Dabei fiel ``exc.headers`` weg - jede 401 verlor ihr
    ``WWW-Authenticate``, und das ``Retry-After`` der Bremse kam nie an. Der
    Status stimmte, der Text stimmte, es fehlte nur die Haelfte.

    Direkt gegen die Funktion geprueft und nicht ueber die Anwendung: Der
    Rueckfall haengt nur dann im Programm, wenn ein gebautes Frontend
    danebenliegt - in der CI laufen die Tests aber **vor** dem Frontend-Bau.
    Ueber die Anwendung geprueft waere dieser Test dort blind.
    """
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.main import fehler_als_json

    antwort = fehler_als_json(
        StarletteHTTPException(
            status_code=429, detail="Zu viele Fehlversuche.", headers={"Retry-After": "42"}
        )
    )
    assert antwort.status_code == 429
    assert antwort.headers["Retry-After"] == "42"


def test_fehlerantwort_ohne_kopfzeilen_geht_auch() -> None:
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.main import fehler_als_json

    antwort = fehler_als_json(StarletteHTTPException(status_code=404, detail="Weg."))
    assert antwort.status_code == 404


def test_eine_abgewiesene_anfrage_verlaengert_die_sperre_nicht(
    admin_client: TestClient,
) -> None:
    """⚠️ Sonst haette jeder Fremde einen Hebel, jemanden **dauerhaft**
    auszusperren: einfach weiterhaemmern, und die Sperre erneuert sich
    endlos. Wer gebremst ist, wird abgewiesen, **bevor** gezaehlt wird - die
    Wartezeit laeuft also ab, ganz gleich wie oft es jemand versucht.
    """
    create_user(admin_client, "anna")
    _falsch_anmelden(admin_client, 4)

    erste = anmeldebremse.restliche_sperre("login|konto|anna")
    assert erste > 0

    # Zwanzig Versuche gegen die geschlossene Tuer.
    for _ in range(20):
        assert admin_client.post("/api/auth/login", json=FALSCH).status_code == 429

    # Die Wartezeit ist kuerzer geworden, nicht laenger - sie laeuft ab.
    assert anmeldebremse.restliche_sperre("login|konto|anna") <= erste


def test_die_sperrmeldung_kommt_als_kennung_nicht_als_satz(
    admin_client: TestClient,
) -> None:
    """⚠️ Damit die Meldung in der eingestellten Sprache erscheint.

    Auf der Anmeldeseite ist niemand angemeldet - es gibt kein
    ``User.language``, und die im Kopf gewaehlte Sprache liegt im
    ``localStorage`` des Browsers, den der Server nicht kennt. Wer hier
    serverseitig uebersetzt, muss raten und liegt genau bei der Person
    falsch, die bewusst umgeschaltet hat. Also: Kennung und Zahl hin, Satz
    baut das Frontend (``client.ts``, ``EIGENE_TEXTE``).
    """
    create_user(admin_client, "anna")
    _falsch_anmelden(admin_client, 4)

    detail = admin_client.post("/api/auth/login", json=FALSCH).json()["detail"]
    assert detail["code"] == "too_many_attempts"
    assert detail["retry_after"] >= 1
    # Deutscher Rueckfall bleibt dabei - fuer alles, was die API ohne diese
    # Oberflaeche benutzt.
    assert "Fehlversuche" in detail["message"]
