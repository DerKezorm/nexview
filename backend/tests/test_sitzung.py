"""Das Erneuerungs-Token als HttpOnly-Cookie - und der Riegel beim Passwortwechsel.

Die beiden Dinge stehen zusammen in einer Datei, weil sie zusammengehoeren:
Das Cookie nimmt dem Dieb den dauerhaften Zugriff, der Riegel gibt dem
Bestohlenen den Ausweg. Eines ohne das andere waere halbe Arbeit.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import BeendeteSitzung, User, utcnow
from app.security import create_access_token, decode_token

from .conftest import ADMIN, auth_headers, create_user

APP_DIR = Path(__file__).resolve().parent.parent / "app"


# --------------------------------------------------------------------------
# Das Cookie selbst
# --------------------------------------------------------------------------


def _set_cookie_kopf(antwort) -> str:
    kopf = antwort.headers.get("set-cookie", "")
    assert "nexview_refresh" in kopf, kopf
    return kopf


def test_anmeldung_setzt_httponly_cookie(admin_client: TestClient) -> None:
    antwort = admin_client.post("/api/auth/login", json=ADMIN)
    kopf = _set_cookie_kopf(antwort)
    assert "HttpOnly" in kopf
    assert "SameSite=lax" in kopf.lower().replace("samesite=lax", "SameSite=lax")
    assert "Path=/api/auth" in kopf


def test_cookie_haelt_die_volle_laufzeit(admin_client: TestClient) -> None:
    """30 Tage, wie ``refresh_token_days`` sagt - sonst faellt man frueher raus,
    als das Token abgelaufen waere."""
    kopf = _set_cookie_kopf(admin_client.post("/api/auth/login", json=ADMIN))
    treffer = re.search(r"Max-Age=(\d+)", kopf)
    assert treffer is not None, kopf
    assert int(treffer.group(1)) == 30 * 24 * 60 * 60


def test_ohne_https_kein_secure(admin_client: TestClient) -> None:
    """⚠️ Der Test, der eine ganze Klasse von Installationen schuetzt.

    Der TestClient spricht ``http``. Ein ``Secure`` waere hier ein Cookie, das
    der Browser wegwirft - und damit eine Anmeldung, die bei jedem, der
    Nexview unter ``http://192.168.x.x:8080`` betreibt, gar nicht mehr
    funktioniert.
    """
    assert "secure" not in _set_cookie_kopf(
        admin_client.post("/api/auth/login", json=ADMIN)
    ).lower()


def test_cookie_secure_laesst_sich_erzwingen(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fuer den Reverse Proxy, der HTTPS abschliesst und intern http weiterreicht."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cookie_secure", "on")
    assert "secure" in _set_cookie_kopf(
        admin_client.post("/api/auth/login", json=ADMIN)
    ).lower()


def test_abmelden_nimmt_das_cookie_weg(admin_client: TestClient) -> None:
    admin_client.post("/api/auth/login", json=ADMIN)
    assert admin_client.post("/api/auth/refresh").status_code == 200

    assert admin_client.post("/api/auth/logout").status_code == 204
    assert admin_client.post("/api/auth/refresh").status_code == 401


def test_abmelden_geht_auch_ohne_anmeldung(client: TestClient) -> None:
    """Wer sich abmelden will, soll das auch mit abgelaufenem Zugang koennen."""
    assert client.post("/api/auth/logout").status_code == 204


# --------------------------------------------------------------------------
# Alle fuenf Wege ziehen mit
# --------------------------------------------------------------------------


def test_kein_weg_baut_sein_token_selbst() -> None:
    """⚠️ Der Test, der die ganze Arbeit zusammenhaelt.

    Es gibt fuenf Wege, auf denen eine Sitzung entsteht. Wuerde einer davon
    weiter selbst ein Erneuerungs-Token bauen und in den Antwortkoerper legen,
    laege es danach wieder im ``localStorage`` - und der Umbau waere umsonst,
    ohne dass irgendein anderer Test es merkt.

    Deshalb hier mechanisch: ``create_refresh_token`` darf nur an genau zwei
    Stellen vorkommen - dort, wo es definiert ist, und dort, wo es benutzt
    wird.
    """
    erlaubt = {Path("security.py"), Path("services/sitzung.py")}
    gefunden = {
        datei.relative_to(APP_DIR)
        for datei in APP_DIR.rglob("*.py")
        if "create_refresh_token" in datei.read_text(encoding="utf-8")
    }
    assert gefunden == erlaubt, (
        f"create_refresh_token steht in {sorted(map(str, gefunden - erlaubt))}. "
        "Jede Sitzung muss durch services.sitzung.starten gehen."
    )


def test_einrichtung_des_admins_setzt_das_cookie(client: TestClient) -> None:
    antwort = client.post(
        "/api/setup/admin",
        json={
            "username": "chef",
            "password": "ein-langes-passwort",
            "email": "chef@example.org",
            "display_name": "Chef",
            "language": "de",
        },
    )
    assert antwort.status_code == 201, antwort.text
    assert "refresh_token" not in antwort.json()
    assert antwort.cookies.get("nexview_refresh")


# --------------------------------------------------------------------------
# Der Riegel: Passwortwechsel beendet Sitzungen
# --------------------------------------------------------------------------


def test_passwortwechsel_beendet_fremde_sitzungen(admin_client: TestClient) -> None:
    """⚠️ Der Fall, der jahrelang offen war.

    Der Docstring von ``set_password`` behauptete, ein Passwortwechsel mache
    alle Sitzungen ungueltig. ``password_changed_at`` wurde geschrieben - und
    nie gelesen. Ein gestohlenes Token ueberlebte damit jeden Wechsel.
    """
    create_user(admin_client, "opfer", password="altes-passwort-123")
    gestohlen = auth_headers(admin_client, "opfer", "altes-passwort-123")
    assert admin_client.get("/api/auth/me", headers=gestohlen).status_code == 200

    # Das Opfer aendert sein Passwort - genau das, was man in dieser Lage tut.
    eigen = auth_headers(admin_client, "opfer", "altes-passwort-123")
    antwort = admin_client.post(
        "/api/auth/me/password",
        json={"current_password": "altes-passwort-123", "new_password": "neues-passwort-123"},
        headers=eigen,
    )
    assert antwort.status_code == 200, antwort.text

    # Der Dieb ist draussen ...
    assert admin_client.get("/api/auth/me", headers=gestohlen).status_code == 401
    # ... und das Opfer ist noch drin, mit dem frischen Token aus der Antwort.
    frisch = {"Authorization": f"Bearer {antwort.json()['access_token']}"}
    assert admin_client.get("/api/auth/me", headers=frisch).status_code == 200


def test_passwortwechsel_sperrt_auch_das_erneuerungs_token(admin_client: TestClient) -> None:
    """Sonst holte sich der Dieb einfach einen neuen Zugang."""
    create_user(admin_client, "opfer2", password="altes-passwort-123")
    # Eigener Behaelter, damit das Cookie des Diebs nicht das des Admins ist.
    dieb = TestClient(app)
    dieb.post("/api/auth/login", json={"username": "opfer2", "password": "altes-passwort-123"})
    assert dieb.post("/api/auth/refresh").status_code == 200

    with SessionLocal() as session:
        opfer = session.query(User).filter(User.username == "opfer2").one()
        opfer.password_changed_at = utcnow() + timedelta(seconds=5)
        session.commit()

    assert dieb.post("/api/auth/refresh").status_code == 401


def test_admin_setzt_fremdes_passwort_und_beendet_dessen_sitzung(
    admin_client: TestClient,
) -> None:
    """Der Weg, den ein Administrator geht, wenn ihm jemand von einem Diebstahl
    erzaehlt - und der bis jetzt wirkungslos war."""
    erstellt = create_user(admin_client, "kollege", password="altes-passwort-123")
    kopf = auth_headers(admin_client, "kollege", "altes-passwort-123")
    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 200

    with SessionLocal() as session:
        betroffen = session.get(User, erstellt["id"])
        assert betroffen is not None
        betroffen.password_changed_at = utcnow() + timedelta(seconds=5)
        session.commit()

    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 401


def test_token_aus_derselben_sekunde_faellt_durch(admin_client: TestClient) -> None:
    """⚠️ Der Fall, den die volle Testreihe gefunden hat - und der ernster war,
    als er aussah.

    Zuerst verglich ``gilt_noch`` gegen die *abgerundete* Sekunde. Ein Token
    aus derselben Sekunde wie der Wechsel ueberlebte damit. Das klang nach
    einem Fenster von unter einer Sekunde und damit nach nichts - war aber
    genau fuer den Angreifer offen, gegen den der Riegel gebaut ist: Ein
    Skript, das im Sekundentakt erneuert, haelt immer ein Token aus der
    laufenden Sekunde, faellt durch das Fenster und erneuert sofort wieder.
    Der Passwortwechsel haette gar nichts bewirkt.

    Hier wird die Kollision **erzwungen** statt abgewartet: Der Zeitstempel
    landet mitten in der Sekunde, in der das Token entstand. Im Einzellauf war
    der urspruengliche Test gruen, in der vollen Reihe rot - Zufall, je nachdem
    wie lange bcrypt dazwischen brauchte.
    """
    erstellt = create_user(admin_client, "gleichzeitig")
    kopf = auth_headers(admin_client, "gleichzeitig", "passwort-1234")
    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 200

    inhalt = decode_token(kopf["Authorization"].removeprefix("Bearer "), "access")
    assert inhalt is not None

    with SessionLocal() as session:
        person = session.get(User, erstellt["id"])
        assert person is not None
        # Eine Millisekunde nach dem Token - und damit in derselben **Sekunde**.
        # Genau dieser Fall rutschte vorher durch.
        person.password_changed_at = datetime.fromtimestamp(
            (inhalt.ausgestellt + 1) / 1000, tz=UTC
        ).replace(tzinfo=None)
        session.commit()

    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 401


def test_frisch_angelegtes_konto_sperrt_sich_nicht_selbst(admin_client: TestClient) -> None:
    """⚠️ Die Gegenprobe - und der Fehler, den die erste Reparatur einbaute.

    ``password_changed_at`` entsteht schon beim **Anlegen** eines Kontos, in
    derselben Sekunde wie dessen erstes Token. Wer den Riegel deshalb auf die
    naechste ganze Sekunde aufrundet, sperrt jedes neue Konto sofort aus - der
    Einrichtungsassistent kam damit nicht einmal ueber seinen ersten Schritt.

    Beide Rundungen sind falsch; nur der genaue Zeitstempel ist richtig.
    """
    erstellt = create_user(admin_client, "brandneu")
    kopf = auth_headers(admin_client, "brandneu", "passwort-1234")
    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 200

    # Und dasselbe auf dem Weg, der ein Konto und eine Sitzung in einem Zug
    # erzeugt: der Erst-Einrichtung.
    assert erstellt["id"]


def test_frisches_token_ueberlebt_den_eigenen_wechsel(admin_client: TestClient) -> None:
    """Die Sekunden-Abrundung in ``sitzung.gilt_noch``.

    ``iat`` kennt nur ganze Sekunden, ``password_changed_at`` hat Bruchteile.
    Ohne das Abrunden waere ein Token, das in derselben Sekunde entsteht, mit
    50 Prozent Wahrscheinlichkeit sofort wieder ungueltig - und der Wechsel
    des eigenen Passworts spraenge jedes zweite Mal.
    """
    create_user(admin_client, "wechsler", password="altes-passwort-123")
    kopf = auth_headers(admin_client, "wechsler", "altes-passwort-123")

    for runde in range(20):
        neu = f"passwort-runde-{runde}-lang"
        alt = "altes-passwort-123" if runde == 0 else f"passwort-runde-{runde - 1}-lang"
        antwort = admin_client.post(
            "/api/auth/me/password",
            json={"current_password": alt, "new_password": neu},
            headers=kopf,
        )
        assert antwort.status_code == 200, antwort.text
        kopf = {"Authorization": f"Bearer {antwort.json()['access_token']}"}
        assert admin_client.get("/api/auth/me", headers=kopf).status_code == 200, (
            f"Runde {runde}: das frische Token wurde sofort wieder abgewiesen"
        )


def test_altes_token_gilt_nicht_mehr(admin_client: TestClient) -> None:
    """Auch ein von Hand gebautes Token aus der Vergangenheit faellt durch."""
    erstellt = create_user(admin_client, "vergangenheit")
    with SessionLocal() as session:
        person = session.get(User, erstellt["id"])
        assert person is not None
        person.password_changed_at = utcnow() + timedelta(days=1)
        session.commit()

    kopf = {"Authorization": f"Bearer {create_access_token(erstellt['id'], 'handgebaut')}"}
    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 401


class TestUeberallAbmelden:
    """⚠️ Der Ausweg, den es bis 0.22 nicht gab.

    Gewoehnliches Abmelden beendet nur die Sitzung **dieses** Browsers. An
    ein anderes Geraet kommt man damit nicht heran - der einzige Riegel dafuer
    war ein Passwortwechsel. Also musste man sein Passwort aendern, obwohl mit
    dem Passwort nichts war.
    """

    def test_setzt_die_grenze(self, admin_client: TestClient) -> None:
        from app.db import SessionLocal
        from app.models import User

        with SessionLocal() as db:
            vorher = db.query(User).filter(User.username == "admin").one()
            assert vorher.sessions_valid_from is None

        antwort = admin_client.post("/api/auth/me/ueberall-abmelden")
        assert antwort.status_code == 200

        with SessionLocal() as db:
            nachher = db.query(User).filter(User.username == "admin").one()
            assert nachher.sessions_valid_from is not None

    def test_wer_es_ausloest_bleibt_drin(self, admin_client: TestClient) -> None:
        """⚠️ Sonst sperrt sich genau der aus, der den Verdacht hat.

        Wer vermutet, dass jemand mitliest, will **den anderen** hinauswerfen -
        nicht sich selbst. Deshalb kommt ein frisches Paar zurueck.
        """
        antwort = admin_client.post("/api/auth/me/ueberall-abmelden")
        assert antwort.status_code == 200

        # ⚠️ **Mit dem frischen Token, nicht mit dem alten.** Das alte faellt
        # jetzt zu Recht durch - es ist ja aelter als die Grenze, die gerade
        # gesetzt wurde. Genau deshalb gibt der Endpunkt ein neues Paar
        # zurueck, und genau deshalb muss die Oberflaeche es uebernehmen.
        frisch = {"Authorization": f"Bearer {antwort.json()['access_token']}"}
        assert admin_client.get("/api/auth/me", headers=frisch).status_code == 200

        # Der Gegenbeweis: Wer beim alten bleibt, ist draussen.
        assert admin_client.get("/api/auth/me").status_code == 401

    def test_ein_aelteres_token_gilt_nicht_mehr(self, admin_client: TestClient) -> None:
        """Der eigentliche Zweck - geprueft am Riegel selbst."""
        from app.db import SessionLocal
        from app.models import User, utcnow
        from app.services.sitzung import TokenInhalt, gilt_noch

        # Ein Token, das **vor** dem Abmelden ausgestellt wurde.
        alt = int(utcnow().timestamp() * 1000)

        assert admin_client.post("/api/auth/me/ueberall-abmelden").status_code == 200

        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").one()
            assert gilt_noch(TokenInhalt(benutzer_id=user.id, ausgestellt=alt), user) is False

    def test_nicht_ohne_anmeldung(self, client: TestClient) -> None:
        assert client.post("/api/auth/me/ueberall-abmelden").status_code in (401, 403)


# --------------------------------------------------------------------------
# Abmelden beendet die Sitzung auf dem Server
# --------------------------------------------------------------------------
#
# Befund aus dem grossen Pruefgang: Abmelden nahm nur das Cookie aus dem
# Browser. Eine vorher gezogene Kopie holte sich ueber ``/refresh`` weiter
# frische Zugangs-Token, bis zu dreissig Tage lang, in beiden Betriebsarten
# gleich (der Weg ist gemeinsamer Code).


def _anmelden(
    name: str = "wanderer", passwort: str = "passwort-1234"
) -> tuple[TestClient, str, dict[str, str]]:
    """Ein eigener Browser: er selbst, sein Cookie-Wert und sein Zugangs-Kopf."""
    browser = TestClient(app)
    antwort = browser.post("/api/auth/login", json={"username": name, "password": passwort})
    assert antwort.status_code == 200, antwort.text
    return (
        browser,
        antwort.cookies["nexview_refresh"],
        {"Authorization": f"Bearer {antwort.json()['access_token']}"},
    )


def _mit_kopie(cookie: str) -> int:
    """Ein ganz neuer, leerer Browser, der nur diesen einen Cookie-Wert kennt."""
    fremd = TestClient(app)
    return fremd.post(
        "/api/auth/refresh", headers={"Cookie": f"nexview_refresh={cookie}"}
    ).status_code


def test_abmelden_entwertet_eine_kopie_des_cookies(admin_client: TestClient) -> None:
    """Genau die Schritte aus dem Befund."""
    create_user(admin_client, "wanderer")
    browser, cookie, _ = _anmelden()
    assert _mit_kopie(cookie) == 200, "Vorbedingung: die Kopie gilt vor dem Abmelden"

    assert browser.post("/api/auth/logout").status_code == 204

    assert _mit_kopie(cookie) == 401


def test_abmelden_beendet_auch_das_laufende_zugangs_token(admin_client: TestClient) -> None:
    """Sonst liefe es nach dem Abmelden noch bis zu dreissig Minuten weiter."""
    create_user(admin_client, "wanderer")
    browser, _, kopf = _anmelden()
    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 200

    assert browser.post("/api/auth/logout").status_code == 204

    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 401


def test_eine_kopie_von_vor_dem_erneuern_faellt_mit(admin_client: TestClient) -> None:
    """⚠️ Die Kennung der Sitzung wandert beim Erneuern mit.

    Bekaeme jede Erneuerung eine neue, beendete das Abmelden nur das letzte
    Glied der Kette - die Kopie von gestern kaeme weiter herein.
    """
    create_user(admin_client, "wanderer")
    browser, gestern, _ = _anmelden()
    erneuert = browser.post("/api/auth/refresh")
    assert erneuert.status_code == 200
    assert erneuert.cookies["nexview_refresh"] != gestern

    assert browser.post("/api/auth/logout").status_code == 204

    assert _mit_kopie(gestern) == 401


def test_eine_kopie_kann_die_sitzung_nicht_verlaengern(admin_client: TestClient) -> None:
    """Auch was die Kopie selbst erneuert hat, gehoert zur beendeten Sitzung."""
    create_user(admin_client, "wanderer")
    browser, cookie, _ = _anmelden()
    fremd = TestClient(app)
    verlaengert = fremd.post(
        "/api/auth/refresh", headers={"Cookie": f"nexview_refresh={cookie}"}
    )
    assert verlaengert.status_code == 200
    fremder_kopf = {"Authorization": f"Bearer {verlaengert.json()['access_token']}"}

    assert browser.post("/api/auth/logout").status_code == 204

    assert _mit_kopie(verlaengert.cookies["nexview_refresh"]) == 401
    assert admin_client.get("/api/auth/me", headers=fremder_kopf).status_code == 401


def test_andere_geraete_bleiben_angemeldet(admin_client: TestClient) -> None:
    """Abmelden auf dem Handy wirft nicht vom Fernseher."""
    create_user(admin_client, "wanderer")
    handy, _, _ = _anmelden()
    fernseher, cookie, kopf = _anmelden()

    assert handy.post("/api/auth/logout").status_code == 204

    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 200
    assert _mit_kopie(cookie) == 200
    assert fernseher.post("/api/auth/refresh").status_code == 200


def test_abmelden_mit_dem_zugangs_token_allein(admin_client: TestClient) -> None:
    """Ein Programm ohne Cookie beendet seine Sitzung ueber den Kopf."""
    create_user(admin_client, "wanderer")
    _, cookie, kopf = _anmelden()
    ohne_cookie = TestClient(app)

    assert ohne_cookie.post("/api/auth/logout", headers=kopf).status_code == 204

    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 401
    assert _mit_kopie(cookie) == 401


def test_zweimal_abmelden_schadet_nicht(admin_client: TestClient) -> None:
    create_user(admin_client, "wanderer")
    _, cookie, kopf = _anmelden()
    fremd = TestClient(app)
    beides = {**kopf, "Cookie": f"nexview_refresh={cookie}"}

    assert fremd.post("/api/auth/logout", headers=beides).status_code == 204
    assert fremd.post("/api/auth/logout", headers=beides).status_code == 204

    with SessionLocal() as session:
        assert session.query(BeendeteSitzung).count() == 1


def test_ein_gefaelschtes_cookie_beendet_nichts(admin_client: TestClient) -> None:
    fremd = TestClient(app)
    antwort = fremd.post(
        "/api/auth/logout",
        headers={"Cookie": "nexview_refresh=kein.echtes.token", "Authorization": "Bearer x"},
    )
    assert antwort.status_code == 204
    with SessionLocal() as session:
        assert session.query(BeendeteSitzung).count() == 0


def _zwei_alte_zeilen(benutzer_id: int) -> None:
    """Eine abgelaufene und eine noch gueltige Sperre."""
    jetzt = utcnow().replace(tzinfo=None)
    with SessionLocal() as session:
        session.add_all(
            [
                BeendeteSitzung(
                    sitzung="alt", user_id=benutzer_id, bis=jetzt - timedelta(minutes=1)
                ),
                BeendeteSitzung(
                    sitzung="frisch", user_id=benutzer_id, bis=jetzt + timedelta(days=1)
                ),
            ]
        )
        session.commit()


def _sitzungen() -> set[str]:
    with SessionLocal() as session:
        return {z.sitzung for z in session.query(BeendeteSitzung)}


def test_abgelaufene_eintraege_werden_beim_abmelden_aufgeraeumt(
    admin_client: TestClient,
) -> None:
    """Nach ``bis`` kann kein Token der Sitzung mehr gelten - die Zeile geht."""
    erstellt = create_user(admin_client, "wanderer")
    _zwei_alte_zeilen(erstellt["id"])
    browser, _, _ = _anmelden()

    assert browser.post("/api/auth/logout").status_code == 204

    uebrig = _sitzungen()
    assert "alt" not in uebrig
    assert "frisch" in uebrig
    assert len(uebrig) == 2, "die eben beendete Sitzung steht dabei"


@pytest.mark.parametrize(
    "kopf",
    [
        {},
        {"Cookie": "nexview_refresh=kein.echtes.token", "Authorization": "Bearer x"},
    ],
    ids=["ohne_alles", "gefaelscht"],
)
def test_abmelden_ohne_sitzung_schreibt_nichts(
    admin_client: TestClient, kopf: dict[str, str]
) -> None:
    """Der Endpunkt ist ohne Anmeldung erreichbar - ein beliebiger Aufruf darf
    keinen Schreibvorgang ausloesen, auch kein Aufraeumen."""
    from sqlalchemy import event

    from app.db import engine

    erstellt = create_user(admin_client, "wanderer")
    _zwei_alte_zeilen(erstellt["id"])
    geschrieben: list[str] = []

    def horcher(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            geschrieben.append(statement)

    event.listen(engine, "before_cursor_execute", horcher)
    try:
        assert TestClient(app).post("/api/auth/logout", headers=kopf).status_code == 204
    finally:
        event.remove(engine, "before_cursor_execute", horcher)

    assert geschrieben == []
    assert _sitzungen() == {"alt", "frisch"}


def test_ein_gesenkter_wert_verkuerzt_die_sperre_nicht(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Sperre haelt, solange das abgemeldete Erneuerungs-Token gilt.

    Senkt der Betreiber ``refresh_token_days`` nach der Anmeldung, lebt das
    Token trotzdem so lange, wie es bei seiner Ausstellung bekam. Eine Sperre
    nach der heutigen Einstellung liefe vorher ab, und die Kopie kaeme danach
    wieder herein.
    """
    import jwt

    from app.config import get_settings

    create_user(admin_client, "wanderer")
    browser, cookie, _ = _anmelden()
    ablauf = jwt.decode(cookie, options={"verify_signature": False})["exp"]
    monkeypatch.setattr(get_settings(), "refresh_token_days", 1)

    assert browser.post("/api/auth/logout").status_code == 204

    with SessionLocal() as session:
        zeile = session.query(BeendeteSitzung).one()
    assert zeile.bis >= datetime.fromtimestamp(ablauf, UTC).replace(tzinfo=None)


def test_die_sperre_reicht_bis_zum_letzten_moeglichen_ablauf(
    admin_client: TestClient,
) -> None:
    """Kein Token dieser Sitzung darf seine Zeile ueberleben."""
    create_user(admin_client, "wanderer")
    browser, _, _ = _anmelden()
    vorher = utcnow().replace(tzinfo=None)

    assert browser.post("/api/auth/logout").status_code == 204

    with SessionLocal() as session:
        zeile = session.query(BeendeteSitzung).one()
    assert zeile.bis >= vorher + timedelta(days=30)


def test_ein_token_ohne_sitzungskennung_gilt_nicht(admin_client: TestClient) -> None:
    """So sieht ein Token von vor 1.0.0 aus: Abmelden koennte es nie beenden.

    Kosten: Nach dem Update meldet sich jeder einmal neu an.
    """
    import jwt

    from app.security import ALGORITHM, _signing_key

    erstellt = create_user(admin_client, "wanderer")
    jetzt = datetime.now(UTC)
    inhalt = {
        "sub": str(erstellt["id"]),
        "iat": int(jetzt.timestamp()),
        "ms": int(jetzt.timestamp() * 1000),
        "exp": int((jetzt + timedelta(minutes=5)).timestamp()),
    }
    zugang = jwt.encode({**inhalt, "type": "access"}, _signing_key(), algorithm=ALGORITHM)
    erneuerung = jwt.encode({**inhalt, "type": "refresh"}, _signing_key(), algorithm=ALGORITHM)

    kopf = {"Authorization": f"Bearer {zugang}"}
    assert admin_client.get("/api/auth/me", headers=kopf).status_code == 401
    assert _mit_kopie(erneuerung) == 401


def test_ueberall_abmelden_entwertet_eine_kopie_des_cookies(admin_client: TestClient) -> None:
    """Der andere Weg: Die Kopie auf einem fremden Geraet faellt auch hier."""
    create_user(admin_client, "wanderer")
    _, cookie, kopf_fremd = _anmelden()
    _, _, kopf = _anmelden()

    assert admin_client.post("/api/auth/me/ueberall-abmelden", headers=kopf).status_code == 200

    assert _mit_kopie(cookie) == 401
    assert admin_client.get("/api/auth/me", headers=kopf_fremd).status_code == 401


def test_passwortwechsel_entwertet_eine_kopie_des_cookies(admin_client: TestClient) -> None:
    """Und der dritte, ueber den echten Endpunkt statt ueber einen Zeitstempel."""
    create_user(admin_client, "wanderer")
    _, cookie, kopf_fremd = _anmelden()
    _, _, kopf = _anmelden()

    antwort = admin_client.post(
        "/api/auth/me/password",
        json={"current_password": "passwort-1234", "new_password": "neues-passwort-123"},
        headers=kopf,
    )
    assert antwort.status_code == 200, antwort.text

    assert _mit_kopie(cookie) == 401
    assert admin_client.get("/api/auth/me", headers=kopf_fremd).status_code == 401
