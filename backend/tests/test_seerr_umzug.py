"""Der lesende Teil des Seerr-Umzugs.

⚠️ **Warum hier gegen erfundene Antworten geprueft wird und nicht gegen eine
echte Installation.** Eine echte, kleine Seerr-Instanz enthaelt genau die
Faelle **nicht**, an denen dieser Umzug scheitern kann: mehrere Konten mit
Anfragen, gesetzte Kontingente, eine Null als Grenze, 4K-Anfragen, eine
Serie ohne TVDB-Nummer. An einer solchen Instanz gemessen (03.09.2026) waren
es drei Konten, davon eines mit Anfragen, kein einziges Kontingent, keine
4K-Anfrage und eine leere Sperrliste. Ein gruener Lauf dagegen haette
bewiesen, dass der ungefaehrliche Teil geht - und ueber den gefaehrlichen
nichts gesagt.

⚠️ **Die drei Tests, ohne die der Rest wertlos waere:**

* ``test_die_null_wird_zu_unbegrenzt`` - sonst sperrt der Umzug genau die
  Konten, die drueben keine Grenze hatten.
* ``test_erledigtes_wird_nicht_als_freigegeben_eingespielt`` - sonst nimmt der
  Status-Poller die Historie auf und rechnet rueckwirkend Speicher zu, und die
  Hausbestands-Entscheidung ist innerhalb von Stunden still aufgehoben.
* ``test_kein_pfad_ausserhalb_der_erlaubnisliste`` - sonst kann ein spaeterer
  Zusatz einen der zerstoerenden GET-Pfade treffen, die Seerr hat.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models import RequestStatus, Role
from app.services.quota import UNBEGRENZT
from app.services.seerr import client as seerr_client
from app.services.seerr import vorschau as seerr_vorschau

from .conftest import create_user

ADRESSE = "https://seerr.example.com"


# --------------------------------------------------------------------------
# Attrappen
# --------------------------------------------------------------------------


def _konto(
    kennung: int,
    *,
    name: str = "beispiel",
    art: int = 1,
    plex_id: int | None = 12345,
    jellyfin: str | None = None,
    rechte: int = 32,
    filme: int | None = None,
    filme_tage: int | None = None,
    serien: int | None = None,
) -> dict:
    return {
        "id": kennung,
        "displayName": name,
        "email": f"{name}@example.com",
        "userType": art,
        "plexId": plex_id,
        "jellyfinUserId": jellyfin,
        "permissions": rechte,
        "movieQuotaLimit": filme,
        "movieQuotaDays": filme_tage,
        "tvQuotaLimit": serien,
        "tvQuotaDays": None,
    }


def _anfrage(
    kennung: int,
    *,
    status: int = 5,
    werk_status: int = 5,
    art: str = "movie",
    tmdb: int | None = 603,
    tvdb: int | None = None,
    besteller: int = 1,
    uhd: bool = False,
    werk_status4k: int = 1,
    staffeln: list[int] | None = None,
) -> dict:
    return {
        "id": kennung,
        "status": status,
        "type": art,
        "is4k": uhd,
        "serverId": 0,
        "media": {
            "tmdbId": tmdb,
            "tvdbId": tvdb,
            "status": werk_status,
            "status4k": werk_status4k,
        },
        "seasons": [
            {"seasonNumber": nummer, "status": werk_status, "status4k": werk_status4k}
            for nummer in (staffeln or [])
        ],
        "requestedBy": {"id": besteller},
    }


def _bauen(**abweichung) -> seerr_vorschau.Vorschau:
    grund = {
        "status": {"version": "3.4.1"},
        "einstellungen": {"mediaServerType": 1},
        "konten": [_konto(1)],
        "anfragen": [],
        "sperrliste": [],
        "meldungen": [],
        "radarr": [],
        "sonarr": [],
        "nexview_konten": [],
    }
    grund.update(abweichung)
    return seerr_vorschau.vorschau_bauen(**grund)


# --------------------------------------------------------------------------
# Die Kontingent-Null
# --------------------------------------------------------------------------


def test_die_null_wird_zu_unbegrenzt() -> None:
    """In Seerr heisst 0 "nicht zaehlen", in Nexview "darf nichts".

    Ohne diese Umdeutung sperrt der Umzug genau die Konten, die drueben
    ausdruecklich keine Grenze hatten. Nexview hatte dieselbe Null bis 0.19
    andersherum - der Fehler ist hier schon einmal passiert.
    """
    wert, hinweise = seerr_vorschau.kontingent_aus_seerr(0, None, "filme")
    assert wert == UNBEGRENZT
    assert hinweise, "Eine stille Umdeutung ist so schlimm wie die falsche Zahl."


def test_nichts_gesetzt_bleibt_hausvorgabe() -> None:
    assert seerr_vorschau.kontingent_aus_seerr(None, None, "filme") == (None, [])


def test_eine_zahl_kommt_als_zahl_an() -> None:
    wert, _ = seerr_vorschau.kontingent_aus_seerr(5, 30, "filme")
    assert wert == 5


def test_serien_grenze_meldet_die_andere_zaehlweise() -> None:
    """Seerr zaehlt Staffeln, Nexview zaehlt Anfragen.

    Dieselbe Zahl ist hier die lockerere Grenze, und das muss dranstehen -
    sonst haelt der Betreiber sie fuer dieselbe Regel.
    """
    _, hinweise = seerr_vorschau.kontingent_aus_seerr(5, None, "serien")
    assert any(h.kennung == "kontingent_staffeln" for h in hinweise)


def test_zeitraum_wird_als_unterschied_gemeldet() -> None:
    _, hinweise = seerr_vorschau.kontingent_aus_seerr(5, 30, "filme")
    assert any(h.kennung == "kontingent_zeitraum" for h in hinweise)


# --------------------------------------------------------------------------
# Der Zustand aus zwei Quellen
# --------------------------------------------------------------------------


def test_erledigtes_wird_nicht_als_freigegeben_eingespielt() -> None:
    """Der Test, an dem die Hausbestands-Entscheidung haengt.

    ``status_poller`` beobachtet ``approved`` und ``searching``. Wird eine
    solche Anfrage fertig, ruft er ``storage.verbuchen``, und das nimmt sich
    herrenlose Hausposten und schreibt sie dem Besteller zu. Kaeme laengst
    erledigte Historie als ``approved`` herein, waere die Entscheidung
    "alles bleibt Hausbestand" ueber Stunden hinweg still aufgehoben.
    """
    ziel, grund = seerr_vorschau.zustand_aus_seerr(
        seerr_vorschau.ANFRAGE_ABGESCHLOSSEN, seerr_vorschau.WERK_VERFUEGBAR
    )
    assert ziel is RequestStatus.downloaded
    assert grund is None


def test_was_wirklich_noch_laeuft_bleibt_freigegeben() -> None:
    """Die Gegenrichtung, und sie ist genauso wichtig.

    Pauschal alles auf "heruntergeladen" zu setzen waere eine Behauptung ueber
    Dateien, die niemand geprueft hat. An einer echten Installation gemessen
    war das jede fuenfte Anfrage.
    """
    ziel, _ = seerr_vorschau.zustand_aus_seerr(seerr_vorschau.ANFRAGE_FREIGEGEBEN, 3)
    assert ziel is RequestStatus.approved


def test_bei_4k_zaehlt_die_4k_spalte() -> None:
    """Sonst behauptet der Umzug fuer eine 4K-Anfrage die normale Fassung."""
    vorschau = _bauen(
        anfragen=[_anfrage(1, uhd=True, werk_status=5, werk_status4k=1)],
    )
    zeile = vorschau.anfragen[0]
    assert zeile.werk_status == 1
    assert zeile.ziel_status == RequestStatus.approved.value


def test_fehlgeschlagenes_kommt_nicht_mit() -> None:
    ziel, grund = seerr_vorschau.zustand_aus_seerr(
        seerr_vorschau.ANFRAGE_FEHLGESCHLAGEN, None
    )
    assert ziel is None
    assert grund


def test_offenes_bleibt_offen() -> None:
    ziel, _ = seerr_vorschau.zustand_aus_seerr(seerr_vorschau.ANFRAGE_OFFEN, 1)
    assert ziel is RequestStatus.pending_approval


# --------------------------------------------------------------------------
# Rollen
# --------------------------------------------------------------------------


def test_rechte_werden_nach_unten_gerundet() -> None:
    """Wer nur Einstellungen durfte, wird **nicht** Administrator.

    Die gefaehrliche Richtung ist die nach oben: Sie gaebe jemandem Rechte,
    die er in Seerr nie hatte, und zwar nebenbei.
    """
    rolle, verlust = seerr_vorschau.rolle_aus_rechten(
        seerr_vorschau.RECHT_EINSTELLUNGEN
    )
    assert rolle is Role.user
    assert verlust, "Der Verlust muss dranstehen, sonst faellt er niemandem auf."


def test_admin_bleibt_admin() -> None:
    rolle, _ = seerr_vorschau.rolle_aus_rechten(seerr_vorschau.RECHT_ADMIN)
    assert rolle is Role.admin


def test_anfragen_verwalten_wird_entscheider() -> None:
    rolle, _ = seerr_vorschau.rolle_aus_rechten(
        seerr_vorschau.RECHT_ANFRAGEN_VERWALTEN
    )
    assert rolle is Role.approver


# --------------------------------------------------------------------------
# Frische Installation gegen laufenden Betrieb
# --------------------------------------------------------------------------


def test_im_laufenden_betrieb_entstehen_nur_nutzer() -> None:
    """⚠️ Dieselbe Regel wie beim Medienserver-Import.

    "Ein Import legt gewoehnliche Nutzer an, nie Entscheider und nie
    Administratoren." Wer dreissig Konten auf einmal anlegt, soll dabei nicht
    dreissig Leuten Rechte geben koennen, die er einzeln sorgfaeltig vergeben
    wuerde.
    """
    vorschau = _bauen(
        konten=[_konto(1, art=2, plex_id=None, rechte=seerr_vorschau.RECHT_ADMIN)],
        frische_installation=False,
    )
    zeile = vorschau.konten[0]
    assert zeile.rolle_seerr == "admin", "Was es drueben war, gehoert trotzdem hin."
    assert zeile.rolle_neu == "user"


def test_bei_frischer_installation_darf_ein_admin_admin_bleiben() -> None:
    """⚠️ Und warum die Regel dort **nicht** gilt.

    Im laufenden Betrieb gibt es schon Administratoren; weitere im Stapel
    anzulegen ist der Fehler. Bei einer frischen Installation ist es umgekehrt:
    Der Betreiber richtet gerade ein, und die Rollen, die er drueben ueber
    Jahre vergeben hat, von Hand nachzubauen ist genau die Arbeit, die dieser
    Umzug abnehmen soll.
    """
    vorschau = _bauen(
        konten=[_konto(1, art=2, plex_id=None, rechte=seerr_vorschau.RECHT_ADMIN)],
        frische_installation=True,
    )
    assert vorschau.konten[0].rolle_neu == "admin"
    assert vorschau.frische_installation is True


def test_der_betreiber_haken_kommt_nie_mit() -> None:
    """⚠️ Die eine Grenze, die auch bei frischer Installation hart bleibt.

    Der Haken sagt "diese Installation gehoert diesem Menschen" und gehoert
    dem, der die Einrichtung durchlaeuft. Ein Umzug, der ihn mitbraechte, gaebe
    die Anlage an jemanden weiter, der gerade nicht davorsitzt.

    Der Test haelt es an der Stelle fest, an der es zaehlt: Die Vorlage hat
    ueberhaupt kein Feld dafuer, also kann die Schreibstufe es nicht
    versehentlich lesen.
    """
    from dataclasses import fields

    namen = {f.name for f in fields(seerr_vorschau.Kontozeile)}
    assert not {n for n in namen if "betreiber" in n.lower()}
    assert namen, "Ohne Felder prueft dieser Test nichts."


# --------------------------------------------------------------------------
# Zuordnung
# --------------------------------------------------------------------------


def test_ohne_sicheren_anker_wird_nichts_vorgeschlagen() -> None:
    """Ein lokales Seerr-Konto hat kein Merkmal, dem man trauen duerfte.

    Ein Vorschlag ueber aehnliche Namen oder ueber die Adresse taeuschte
    Sicherheit vor - und ein falsch zugeordnetes Konto laesst sich in Nexview
    nicht mehr trennen.
    """
    vorschau = _bauen(konten=[_konto(1, art=2, plex_id=None)])
    zeile = vorschau.konten[0]
    assert zeile.herkunft == "lokal"
    assert zeile.treffer_user_id is None


def test_gleiche_adresse_ist_kein_anker(admin_client: TestClient) -> None:
    """⚠️ **Der Test, der die Sicherheitsluecke dieses Features zuhaelt.**

    Hier steht ein Nexview-Konto mit **derselben** Adresse wie das
    Seerr-Konto, und trotzdem darf kein Treffer herauskommen. Beide Seiten
    koennen die Adresse unabhaengig aendern: Wer sich beim Anbieter ein Konto
    mit fremder Adresse anlegt, bekaeme ueber eine solche Zuordnung den Weg in
    ein fremdes Nexview-Konto.

    Die erste Fassung dieses Tests pruefte gegen eine leere Datenbank und war
    damit hohl - sie waere auch gruen geblieben, wenn jemand die Zuordnung
    ueber die Adresse eingebaut haette.
    """
    from app.db import SessionLocal
    from app.models import User

    create_user(admin_client, "opfer")
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter(User.username == "opfer").one()
        # Genau die Adresse, die das Seerr-Konto unten traegt.
        konto.email = "angreifer@example.com"
        sitzung.commit()
        sitzung.refresh(konto)
        vorschau = _bauen(
            konten=[_konto(1, art=2, plex_id=None, name="angreifer")],
            nexview_konten=[konto],
        )

    zeile = vorschau.konten[0]
    assert zeile.email == "angreifer@example.com", (
        "Ohne gleiche Adresse prueft dieser Test nichts."
    )
    assert zeile.treffer_user_id is None
    assert zeile.treffer_grund is None


def test_jellyfin_treffer_traegt_den_vorbehalt(admin_client: TestClient) -> None:
    """Bei Jellyfin ist die Kennung serverbezogen, und Seerr notiert nicht,
    welcher Server gemeint war. Ein Treffer heisst dort also nur "gleiche
    Zeichenkette" - und genau das muss dranstehen."""
    from app.db import SessionLocal
    from app.models import User

    create_user(
        admin_client,
        "jemand",
        mediaserver_provider="jellyfin",
        mediaserver_account_id="abc123",
    )
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter(User.username == "jemand").one()
        vorschau = _bauen(
            konten=[_konto(1, art=3, plex_id=None, jellyfin="abc123")],
            nexview_konten=[konto],
        )
        erwartet = konto.id

    zeile = vorschau.konten[0]
    assert zeile.treffer_user_id == erwartet
    assert zeile.treffer_grund is not None and zeile.treffer_grund.kennung == "treffer_unsicher"


def test_plex_treffer_gilt_ohne_vorbehalt(admin_client: TestClient) -> None:
    """Seerrs plexId und Nexviews Verknuepfung stammen aus derselben Quelle
    (plex.tv). Derselbe Wert bedeutet denselben Menschen."""
    from app.db import SessionLocal
    from app.models import User

    create_user(
        admin_client,
        "andere",
        mediaserver_provider="plex",
        mediaserver_account_id="12345",
    )
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter(User.username == "andere").one()
        vorschau = _bauen(
            konten=[_konto(1, art=1, plex_id=12345)],
            nexview_konten=[konto],
        )
        erwartet = konto.id

    zeile = vorschau.konten[0]
    assert zeile.treffer_user_id == erwartet
    assert zeile.treffer_grund is not None and zeile.treffer_grund.kennung == "treffer_plex"


# --------------------------------------------------------------------------
# Uebersprungenes
# --------------------------------------------------------------------------


def test_serie_ohne_tvdb_nummer_wird_uebersprungen() -> None:
    """Ohne sie findet Nexview die Serie in Sonarr nicht wieder."""
    vorschau = _bauen(anfragen=[_anfrage(1, art="tv", tvdb=None, staffeln=[1])])
    assert vorschau.anfragen[0].uebersprungen
    assert vorschau.anfragen_uebernehmbar == 0


def test_anfrage_ohne_konto_wird_uebersprungen() -> None:
    vorschau = _bauen(anfragen=[_anfrage(1, besteller=99)])
    assert vorschau.anfragen[0].uebersprungen


def test_mehrere_staffeln_werden_zu_mehreren_zeilen() -> None:
    """Nexview fuehrt Staffeln einzeln, Seerr haengt sie an eine Anfrage."""
    vorschau = _bauen(
        anfragen=[_anfrage(1, art="tv", tmdb=1399, tvdb=121361, staffeln=[1, 2, 3])]
    )
    assert len(vorschau.anfragen) == 3
    assert sorted(z.staffel for z in vorschau.anfragen) == [1, 2, 3]


def test_die_zahl_je_konto_passt_zur_gesamtzahl() -> None:
    """⚠️ **Zwei Zahlen, eine Einheit.**

    Eine Serienanfrage ueber drei Staffeln ist in Seerr *eine* Anfrage und in
    Nexview *drei*, weil Staffeln einzeln gefuehrt werden. Die erste Fassung
    zaehlte je Konto in Seerr-Anfragen und in der Kopfzeile in Nexview-Zeilen;
    an einer echten Installation stand damit "81" am Konto und "97" darueber.
    Beide Zahlen waren richtig und sahen aus wie ein Fehler.

    Die Summe ueber alle Konten muss deshalb genau die uebernehmbaren Zeilen
    ergeben.
    """
    vorschau = _bauen(
        konten=[_konto(1), _konto(2, name="zweite", plex_id=999)],
        anfragen=[
            _anfrage(1, art="tv", tmdb=1399, tvdb=121361, staffeln=[1, 2, 3], besteller=1),
            _anfrage(2, besteller=1),
            _anfrage(3, besteller=2),
        ],
    )
    summe = sum(k.anfragen for k in vorschau.konten)
    assert vorschau.anfragen_uebernehmbar == 5, "3 Staffeln + 2 Filme"
    assert summe == vorschau.anfragen_uebernehmbar


def test_uebersprungene_zaehlen_am_konto_nicht_mit() -> None:
    """Sonst verspricht die Zeile etwas, das nicht kommt."""
    vorschau = _bauen(
        konten=[_konto(1)],
        anfragen=[
            _anfrage(1, besteller=1),
            # Ohne TVDB-Nummer faellt die Serie heraus.
            _anfrage(2, art="tv", tvdb=None, staffeln=[1], besteller=1),
        ],
    )
    assert vorschau.konten[0].anfragen == 1


# --------------------------------------------------------------------------
# Die Fassung
# --------------------------------------------------------------------------


def test_geprueft_bis_wird_eingehalten() -> None:
    assert _bauen(status={"version": "3.4.1"}).fassung_geprueft is True


def test_neuere_fassung_gilt_als_ungeprueft() -> None:
    """Ein Abbruch kostet zehn Minuten, das andere kostet den Bestand."""
    ergebnis = _bauen(status={"version": "3.9.0"})
    assert ergebnis.fassung_geprueft is False
    assert ergebnis.fassung_hinweis


def test_unlesbare_fassung_gilt_als_ungeprueft() -> None:
    assert _bauen(status={"version": "irgendwas"}).fassung_geprueft is False


# --------------------------------------------------------------------------
# Die Erlaubnisliste
# --------------------------------------------------------------------------


def test_kein_pfad_ausserhalb_der_erlaubnisliste() -> None:
    """In Seerr stehen zerstoerende Vorgaenge hinter GET.

    ``GET /settings/discover/reset`` leert die Startseiten-Regale.
    ``GET /user/{id}/watchlist`` schreibt die Merkliste bei Plex nach.
    Ein Werkzeug, das Pfade abklappert, trifft sie irgendwann.
    """
    zugang = seerr_client.Zugang(basis=ADRESSE, schluessel="egal")
    kunde = seerr_client.SeerrClient(zugang)
    import asyncio

    with pytest.raises(AssertionError):
        asyncio.run(kunde._hole("/api/v1/settings/discover/reset"))


def test_die_gefaehrlichen_pfade_stehen_nicht_drauf() -> None:
    verboten = {
        "/api/v1/settings/discover/reset",
        "/api/v1/user/{id}/watchlist",
        "/avatarproxy/{id}",
        "/imageproxy",
        "/api/v1/movie/{id}",
        "/api/v1/tv/{id}",
    }
    assert not (verboten & seerr_client.ERLAUBTE_PFADE)
    # Bodenschwelle: Der Test darf nicht deshalb gruen sein, weil die Liste
    # leer ist oder umbenannt wurde.
    assert len(seerr_client.ERLAUBTE_PFADE) >= 8
    assert "/api/v1/status" in seerr_client.ERLAUBTE_PFADE


def test_abfrageparameter_umgehen_die_liste_nicht() -> None:
    """Der erste Entwurf haengte ``?take=100`` an die Vorlage und fiel damit
    durch die eigene Pruefung. Wer das repariert, indem er die Liste lockert,
    hat die Liste abgeschafft."""
    zugang = seerr_client.Zugang(basis=ADRESSE, schluessel="egal")
    kunde = seerr_client.SeerrClient(zugang)
    import asyncio

    with pytest.raises(AssertionError):
        asyncio.run(kunde._hole("/api/v1/user?take=100"))


def test_der_client_kann_nicht_schreiben() -> None:
    """Es gibt hier kein post, put oder delete - und das soll auffallen,
    wenn es jemand ergaenzt."""
    for verboten in ("post", "put", "delete", "patch"):
        assert not hasattr(seerr_client.SeerrClient, verboten)


def test_adresse_ohne_schema_wird_abgewiesen() -> None:
    with pytest.raises(seerr_client.SeerrFehler):
        seerr_client.Zugang(basis="seerr.example.com", schluessel="egal")


# --------------------------------------------------------------------------
# Ueber die Adressen
# --------------------------------------------------------------------------


@pytest.fixture
def seerr_attrappe(monkeypatch):
    """Eine Seerr-Installation, die es nicht gibt.

    ``spur`` sammelt die tatsaechlich abgerufenen Pfade. Das ist kein Beiwerk:
    Ohne sie liesse sich nicht pruefen, dass eine Weigerung die fremde Instanz
    gar nicht erst leerliest.
    """

    def stelle(antworten: dict[str, object], spur: list[str] | None = None) -> None:
        def bediene(anfrage: httpx.Request) -> httpx.Response:
            assert anfrage.method == "GET", "Der Client darf nur lesen."
            assert anfrage.headers.get("X-Api-Key") == "geheim-im-test"
            pfad = anfrage.url.path
            if spur is not None:
                spur.append(pfad)
            if pfad not in antworten:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=antworten[pfad])

        transport = httpx.MockTransport(bediene)
        echt = httpx.AsyncClient

        def gefaelscht(*args, **kwargs):
            kwargs["transport"] = transport
            return echt(*args, **kwargs)

        monkeypatch.setattr(seerr_client.httpx, "AsyncClient", gefaelscht)

    return stelle


def test_pruefen_meldet_die_fassung(client: TestClient, seerr_attrappe) -> None:
    """Der gute Fall: Status **und** eine beglaubigte Adresse antworten."""
    seerr_attrappe(
        {
            "/api/v1/status": {"version": "3.4.1", "commitTag": "abc123"},
            "/api/v1/settings/main": {"mediaServerType": 1},
        }
    )
    antwort = client.post(
        "/api/setup/seerr/pruefen",
        json={"url": ADRESSE, "api_key": "geheim-im-test"},
    )
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["version"] == "3.4.1"
    assert antwort.json()["geprueft"] is True


def test_pruefen_meldet_eine_falsche_adresse_verstaendlich(
    client: TestClient, seerr_attrappe
) -> None:
    """"Da ist kein Seerr" und "der Schluessel stimmt nicht" sind verschiedene
    Auskuenfte, und wer sie verwechselt, sucht an der falschen Stelle."""
    seerr_attrappe({})
    antwort = client.post(
        "/api/setup/seerr/pruefen",
        json={"url": ADRESSE, "api_key": "geheim-im-test"},
    )
    assert antwort.status_code == 502
    assert antwort.json()["detail"]["code"] == "seerr_not_found"


def test_pruefen_faellt_auf_einen_falschen_schluessel_herein_nicht(
    client: TestClient, seerr_attrappe
) -> None:
    """⚠️ **Seerrs Status-Adresse verlangt keine Anmeldung.**

    An einer echten Installation gemessen (03.09.2026): ``/api/v1/status``
    antwortet mit 200, ganz ohne Schluessel und ebenso mit einem falschen. Die
    erste Fassung dieser Pruefung hat deshalb "verbunden" gemeldet, ohne den
    Schluessel je angefasst zu haben - der Betreiber sah gruen und waere erst
    beim Holen der Vorschau gescheitert.

    Hier antwortet der Status, aber die beglaubigte Adresse weist ab. Genau
    dieser Fall muss durchfallen.
    """
    gesehen: list[str] = []
    seerr_attrappe({"/api/v1/status": {"version": "3.4.1"}}, spur=gesehen)
    antwort = client.post(
        "/api/setup/seerr/pruefen",
        json={"url": ADRESSE, "api_key": "geheim-im-test"},
    )
    assert antwort.status_code == 502
    assert antwort.json()["detail"]["code"] == "seerr_not_found"
    assert "/api/v1/settings/main" in gesehen, (
        "Ohne einen beglaubigten Aufruf prueft die Verbindungspruefung nichts."
    )


def test_vorschau_weigert_sich_bei_unbekannter_fassung(
    client: TestClient, seerr_attrappe
) -> None:
    """Und liest die fremde Instanz dann gar nicht erst leer."""
    gesehen: list[str] = []
    seerr_attrappe({"/api/v1/status": {"version": "9.9.9"}}, spur=gesehen)
    antwort = client.post(
        "/api/setup/seerr/vorschau",
        json={"url": ADRESSE, "api_key": "geheim-im-test"},
    )
    assert antwort.status_code == 409
    assert antwort.json()["detail"]["code"] == "seerr_version_unknown"
    assert gesehen == ["/api/v1/status"], (
        "Wer sich weigert, soll die fremde Instanz nicht vorher leerlesen."
    )


def test_vorschau_schreibt_nichts(client: TestClient, seerr_attrappe) -> None:
    """Die Zusage des ganzen Bauabschnitts, maschinell festgehalten."""
    from app.db import SessionLocal
    from app.models import MediaRequest, User

    seerr_attrappe(
        {
            "/api/v1/status": {"version": "3.4.1"},
            "/api/v1/settings/main": {"mediaServerType": 1},
            "/api/v1/settings/radarr": [],
            "/api/v1/settings/sonarr": [],
            "/api/v1/settings/plex": {},
            "/api/v1/settings/jellyfin": {},
            "/api/v1/settings/notifications/email": {},
            "/api/v1/user": {"pageInfo": {"pages": 1}, "results": [_konto(1)]},
            "/api/v1/request": {"pageInfo": {"pages": 1}, "results": [_anfrage(1)]},
            "/api/v1/blocklist": {"pageInfo": {"pages": 1}, "results": []},
            "/api/v1/issue": {"pageInfo": {"pages": 1}, "results": []},
        }
    )
    with SessionLocal() as sitzung:
        vorher_konten = sitzung.query(User).count()
        vorher_anfragen = sitzung.query(MediaRequest).count()

    antwort = client.post(
        "/api/setup/seerr/vorschau",
        json={"url": ADRESSE, "api_key": "geheim-im-test"},
    )
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert len(daten["konten"]) == 1
    assert len(daten["anfragen"]) == 1
    assert daten["kommt_nicht_mit"], "Was nicht mitkommt, gehoert in die Vorlage."

    with SessionLocal() as sitzung:
        assert sitzung.query(User).count() == vorher_konten
        assert sitzung.query(MediaRequest).count() == vorher_anfragen


def test_neue_und_verknuepfte_konten_werden_getrennt_gezaehlt(
    admin_client: TestClient,
) -> None:
    """⚠️ **Die eine Zahl, an der ein Duplikat auffaellt.**

    Zwei Seerr-Konten koennen derselbe Mensch sein: ein Plex-Konto und daneben
    ein lokales, das der Betreiber irgendwann von Hand angelegt hat. Nexview
    kann das nicht wissen, und ueber Namen oder Adresse zu raten waere genau
    der Abgleich, den ``nutzer_import`` ablehnt.

    Was Nexview kann, ist die Zahl hinstellen: Wer weiss, wie viele Menschen
    bei ihm mitlesen, und hier eine hoehere Zahl liest, stutzt. Eine
    Gesamtzahl stutzt niemanden. Genau dieser Fall ist am 03.09.2026 beim
    ersten Lauf gegen eine echte Installation aufgetreten.
    """
    from app.db import SessionLocal
    from app.models import User

    create_user(
        admin_client,
        "vorhanden",
        mediaserver_provider="plex",
        mediaserver_account_id="12345",
    )
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter(User.username == "vorhanden").one()
        vorschau = _bauen(
            konten=[
                # Trifft sicher auf das vorhandene Konto.
                _konto(1, art=1, plex_id=12345),
                # Derselbe Mensch, aber lokal angelegt: kein Anker, also neu.
                _konto(2, art=2, plex_id=None, name="derselbe"),
            ],
            nexview_konten=[konto],
        )

    assert vorschau.konten_verknuepft == 1
    assert vorschau.konten_neu == 1


def test_die_zahlen_stehen_auch_in_der_antwort(
    client: TestClient, seerr_attrappe
) -> None:
    """``asdict`` traegt keine Eigenschaften.

    Die abgeleiteten Zahlen waeren sonst stillschweigend nicht in der Antwort,
    und die Oberflaeche zeigte drei Nullen, ohne dass irgendetwas kaputt
    aussieht.
    """
    seerr_attrappe(
        {
            "/api/v1/status": {"version": "3.4.1"},
            "/api/v1/settings/main": {"mediaServerType": 1},
            "/api/v1/settings/radarr": [],
            "/api/v1/settings/sonarr": [],
            "/api/v1/settings/plex": {},
            "/api/v1/settings/jellyfin": {},
            "/api/v1/settings/notifications/email": {},
            "/api/v1/user": {"pageInfo": {"pages": 1}, "results": [_konto(1)]},
            "/api/v1/request": {"pageInfo": {"pages": 1}, "results": [_anfrage(1)]},
            "/api/v1/blocklist": {"pageInfo": {"pages": 1}, "results": []},
            "/api/v1/issue": {"pageInfo": {"pages": 1}, "results": []},
        }
    )
    antwort = client.post(
        "/api/setup/seerr/vorschau",
        json={"url": ADRESSE, "api_key": "geheim-im-test"},
    )
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    for feld in ("konten_neu", "konten_verknuepft", "anfragen_uebernehmbar"):
        assert feld in daten, f"{feld} fehlt in der Antwort"
    assert daten["konten_neu"] == 1
    assert daten["konten_verknuepft"] == 0


def test_sobald_ein_konto_da_ist_ist_der_umzug_zu(admin_client: TestClient) -> None:
    """⚠️ **Der einzige Riegel, den dieses Feature hat.**

    Der Umzug lief einmal auch in eine laufende Anlage, ueber einen eigenen
    Router unter ``/api/admin/seerr`` mit Administratorpflicht. Der Weg ist
    verworfen; geblieben sind die Adressen der Erst-Einrichtung, und die
    haengen an ``has_any_user`` statt an einer Rolle.

    Das ist enger als vorher, aber es ist eine **andere** Art Schutz - und
    wenn jemand ihn beim Aufraeumen vergisst, steht eine Adresse offen, die
    ohne Anmeldung Einstellungen schreibt. Deshalb dieser Test, und deshalb
    alle drei.
    """
    for pfad in ("pruefen", "vorschau", "abschliessen"):
        antwort = admin_client.post(
            f"/api/setup/seerr/{pfad}",
            json={"url": ADRESSE, "api_key": "egal", **_abschluss_rumpf()},
        )
        assert antwort.status_code == 409, f"{pfad}: {antwort.status_code}"
        assert antwort.json()["detail"]["code"] == "setup_already_done"


def test_der_schluessel_steht_in_keiner_adresse(caplog, seerr_attrappe) -> None:
    """Ein Schluessel in der Adresszeile stuende im Protokoll jedes
    Vermittlers dazwischen - deshalb POST und deshalb dieser Test."""
    import asyncio

    gesehen: list[str] = []

    def bediene(anfrage: httpx.Request) -> httpx.Response:
        gesehen.append(str(anfrage.url))
        return httpx.Response(200, json={"version": "3.4.1"})

    transport = httpx.MockTransport(bediene)
    echt = httpx.AsyncClient

    def gefaelscht(*args, **kwargs):
        kwargs["transport"] = transport
        return echt(*args, **kwargs)

    import app.services.seerr.client as modul

    urspruenglich = modul.httpx.AsyncClient
    modul.httpx.AsyncClient = gefaelscht  # type: ignore[assignment]
    try:
        kunde = seerr_client.SeerrClient(
            seerr_client.Zugang(basis=ADRESSE, schluessel="streng-geheim")
        )
        asyncio.run(kunde.status())
    finally:
        modul.httpx.AsyncClient = urspruenglich  # type: ignore[assignment]

    assert gesehen, "Ohne Aufruf beweist der Test nichts."
    for adresse in gesehen:
        assert "streng-geheim" not in adresse


# --------------------------------------------------------------------------
# Die Bereiche der Uebernahme
# --------------------------------------------------------------------------


def test_der_medienserver_bereich_schreibt_nichts() -> None:
    """⚠️ **Er kann es nicht, und der erste Entwurf hat es trotzdem behauptet.**

    Eine Verbindung gilt erst als brauchbar, wenn Anbieter, Maschinenkennung
    **und Token** dastehen (``settings_service.Verbindung.nutzbar``). Das
    Plex-Token gibt Seerr nie heraus. Anbieter und Kennung allein zu schreiben
    erzeugt einen Torso, der nirgends greift - und ein Haken darauf waere ein
    Versprechen ohne Deckung.
    """
    from app.services.seerr import uebernahme

    bereiche = uebernahme.bereiche_bauen(
        main={"mediaServerType": 1},
        plex={"name": "Wohnzimmer", "machineId": "abcdef0123456789"},
        jellyfin={},
        radarr=[],
        sonarr=[],
        email={},
    )
    server = next(b for b in bereiche if b.kennung == "medienserver")
    assert server.werte == {}, "Der Medienserver-Bereich darf nichts schreiben."
    assert server.zeilen, "Auskunft geben soll er trotzdem."
    assert server.luecken, "Und sagen, warum es nicht geht."
    # ⚠️ Und er darf nicht als leer gelten: Sonst meldet die Oberflaeche "hier
    # ist nichts eingestellt" und verschweigt dabei genau die Kennung, auf die
    # der Satz darunter verweist.
    assert not server.leer


def test_jellyfin_und_emby_kommen_aus_derselben_quelle() -> None:
    """⚠️ **Seerr laeuft nicht nur an Plex, und Emby hat keine eigene Adresse.**

    ``MediaServerType`` kennt drei Anbieter (1/2/3). Emby liest und schreibt
    seine Einstellungen aber unter ``settings/jellyfin`` - eine Adresse
    ``settings/emby`` gibt es in 3.4.1 nicht. Wer danach sucht, findet nichts
    und haelt Emby fuer unbedienbar; wer bei Typ 3 in den Plex-Block greift,
    zeigt einen leeren Kasten.
    """
    from app.services.seerr import uebernahme

    jelly = {
        "name": "Keller",
        "ip": "jellyfin.example.com",
        "port": 8096,
        "serverId": "0123456789abcdef",
        "apiKey": "y" * 32,
    }
    for typ, erwartet in ((2, "jellyfin"), (3, "emby")):
        bereiche = uebernahme.bereiche_bauen(
            main={"mediaServerType": typ},
            plex={"name": "Nicht dieser", "machineId": "z" * 16},
            jellyfin=jelly,
            radarr=[],
            sonarr=[],
            email={},
        )
        server = next(b for b in bereiche if b.kennung == "medienserver")
        assert server.anbieter == erwartet
        assert any(w.kennung == "l_server_in_seerr" and v == "Keller" for w, v in server.zeilen)
        assert server.verbindung["adresse"] == "http://jellyfin.example.com:8096"
        assert server.verbindung["kennung"] == "0123456789abcdef"
        # Und trotz vorhandenem Schluessel wird nichts geschrieben.
        assert server.werte == {}
        assert not server.leer


def test_der_schluessel_von_jellyfin_hilft_nexview_nicht() -> None:
    """⚠️ **Der erste Entwurf hat hier das Gegenteil behauptet.**

    ``settings/jellyfin`` liefert ``apiKey`` und ``serverId``, also sah es aus,
    als ginge die Verbindung dort vollstaendig mit. Nexview nimmt fuer diese
    beiden Anbieter aber gar keinen Schluessel entgegen: ``connect/password``
    verlangt Benutzername und Passwort eines Server-Administrators. Der Satz in
    der Oberflaeche muss das sagen, sonst wartet der Betreiber auf eine
    Verbindung, die von selbst nie entsteht.
    """
    from app.services.mediaserver import PROVIDERS
    from app.services.seerr import uebernahme

    # Die Behauptung an der Quelle nachgeschlagen, nicht geglaubt.
    assert PROVIDERS["jellyfin"].supports_password_login()
    assert PROVIDERS["emby"].supports_password_login()
    assert not PROVIDERS["plex"].supports_password_login()

    bereiche = uebernahme.bereiche_bauen(
        main={"mediaServerType": 2},
        plex={},
        jellyfin={"name": "Keller", "apiKey": "y" * 32},
        radarr=[],
        sonarr=[],
        email={},
    )
    server = next(b for b in bereiche if b.kennung == "medienserver")
    grund = " ".join(l.text for l in server.luecken)
    assert "Benutzername und Passwort" in grund
    assert "Jellyfin" in grund
    for _, anzeige in server.zeilen:
        assert "y" * 32 not in str(anzeige)


def test_ohne_medienserver_bleibt_der_bereich_stumm() -> None:
    """``NOT_CONFIGURED`` ist die 4, nicht die 0 - beides muss greifen."""
    from app.services.seerr import uebernahme

    for typ in (4, 0, None):
        bereiche = uebernahme.bereiche_bauen(
            main={"mediaServerType": typ},
            plex={"name": "Wohnzimmer"},
            jellyfin={"name": "Keller"},
            radarr=[],
            sonarr=[],
            email={},
        )
        server = next(b for b in bereiche if b.kennung == "medienserver")
        assert server.anbieter == ""
        assert server.zeilen == []
        assert server.verbindung == {}
        assert server.luecken


def test_die_arr_schluessel_kommen_mit_aber_nicht_nach_aussen() -> None:
    """Die Werte sind da, die Anzeige verraet sie nicht."""
    from app.services.seerr import uebernahme

    bereiche = uebernahme.bereiche_bauen(
        main={"mediaServerType": 1},
        plex={},
        jellyfin={},
        radarr=[{"name": "Radarr", "hostname": "radarr.example.com", "port": 7878,
                 "apiKey": "x" * 32, "isDefault": True, "activeDirectory": "/data/Filme"}],
        sonarr=[],
        email={},
    )
    dienste = next(b for b in bereiche if b.kennung == "dienste")
    posten = next(p for p in dienste.posten if p.kennung == "radarr")
    assert posten.werte["radarr_api_key"] == "x" * 32
    assert posten.werte["radarr_url"] == "http://radarr.example.com:7878"
    for _, anzeige in posten.zeilen:
        assert "x" * 32 not in str(anzeige), "Ein Schluessel gehoert nicht in die Anzeige."


def test_die_haus_null_wird_nicht_zur_sperre() -> None:
    """Dieselbe umgedrehte Null wie am Konto, diesmal als Hausvorgabe."""
    from app.services.seerr import uebernahme

    bereiche = uebernahme.bereiche_bauen(
        main={"mediaServerType": 1, "defaultQuotas": {"movie": {"quotaLimit": 0}}},
        plex={}, jellyfin={}, radarr=[], sonarr=[], email={},
    )
    allgemein = next(b for b in bereiche if b.kennung == "allgemein")
    # ⚠️ In den Posten nachsehen, nicht in ``werte``: Seit der Bereich sich in
    # zwei Haken teilt, ist ``werte`` immer leer - eine Behauptung darueber
    # waere ab sofort wahr, egal was der Code tut.
    alle = {schluessel for posten in allgemein.posten for schluessel in posten.werte}
    assert "quota_default_movies" not in alle
    assert any(l.kennung.startswith("vorgabe_null_") for l in allgemein.luecken)


def test_region_und_kontingent_haben_getrennte_haken() -> None:
    """⚠️ **Ein Haken fuer beides war eine falsche Zusage.**

    Er hiess „Region und Sprache uebernehmen" und schrieb die
    Kontingent-Vorgabe still mit - im selben Kasten stand „Filme je Zeitraum",
    worauf der Haken sich dem Wortlaut nach gar nicht bezog. Wer Region und
    Sprache wollte, bekam eine Mengengrenze fuer jedes kuenftige Konto dazu.
    """
    from app.services.seerr import uebernahme

    bereiche = uebernahme.bereiche_bauen(
        main={
            "mediaServerType": 1,
            "discoverRegion": "DE",
            "locale": "de",
            "defaultQuotas": {"movie": {"quotaLimit": 5}, "tv": {"quotaLimit": 3}},
        },
        plex={}, jellyfin={}, radarr=[], sonarr=[], email={},
    )
    allgemein = next(b for b in bereiche if b.kennung == "allgemein")
    nach_kennung = {p.kennung: p for p in allgemein.posten}
    assert set(nach_kennung) == {"vorgabe_region", "vorgabe_kontingent"}

    # Der Ortsposten traegt keine Zahl, der Mengenposten keine Sprache.
    assert set(nach_kennung["vorgabe_region"].werte) == {"default_region", "default_language"}
    assert set(nach_kennung["vorgabe_kontingent"].werte) == {
        "quota_default_movies",
        "quota_default_series",
    }
    # ⚠️ Und der Bereich selbst schreibt nichts mehr: Sonst laege ein Wert
    # ausserhalb jedes Hakens und kaeme mit, egal was angehakt ist.
    assert allgemein.werte == {}


def test_die_kontingent_vorgabe_sagt_fuer_wen_sie_gilt() -> None:
    """Seerr fuehrt beides, und nur eines davon steht hier.

    ``server/entity/User.ts``: ``this.movieQuotaLimit ?? defaultQuotas.movie``
    - der Wert am Konto gewinnt, die Vorgabe fuellt nur die Luecke. Die
    Konto-Werte kommen im Benutzer-Schritt mit; hier steht, was fuer **neue**
    Konten gaelte. Ohne diesen Satz liest man es als „die Kontingente der
    Leute", und das waere zweimal dasselbe.
    """
    from app.services.seerr import uebernahme

    bereiche = uebernahme.bereiche_bauen(
        main={"mediaServerType": 1, "defaultQuotas": {"movie": {"quotaLimit": 5}}},
        plex={}, jellyfin={}, radarr=[], sonarr=[], email={},
    )
    allgemein = next(b for b in bereiche if b.kennung == "allgemein")
    menge = next(p for p in allgemein.posten if p.kennung == "vorgabe_kontingent")
    # ⚠️ Beide Haelften, und beide kurz: Die Wertspalte schneidet ab, und ein
    # Satz, der an "nicht fuer die aus Seerr" abgeschnitten wird, sagt das
    # Gegenteil von dem, was dasteht.
    beschriftungen = {w.kennung: v for w, v in menge.zeilen}
    assert beschriftungen["l_gilt_fuer"].kennung == "w_neue_konten"
    assert beschriftungen["l_nicht_fuer"].kennung == "w_die_aus_seerr"


# --------------------------------------------------------------------------
# Der Abschluss: alles in einem Zug
# --------------------------------------------------------------------------


def _abschluss_rumpf(**abweichung) -> dict:
    """Ein gueltiger Rumpf fuer ``abschliessen`` - der Besitzer ist Konto 1."""
    rumpf = {
        "url": ADRESSE,
        "api_key": "geheim-im-test",
        "bereiche": [],
        "besitzer": {
            "seerr_id": 1,
            "username": "chefin",
            "password": "geheim-1234",
            "email": "chefin@example.com",
            "language": "de",
        },
        "konten": [],
        "tmdb_api_key": "",
        "public_url": "",
    }
    rumpf.update(abweichung)
    return rumpf


def _volle_attrappe(konten: list[dict] | None = None) -> dict:
    """Ein Seerr mit allem, was der Abschluss anfasst."""
    return {
        "/api/v1/status": {"version": "3.4.1"},
        "/api/v1/settings/main": {
            "mediaServerType": 1,
            "discoverRegion": "DE",
            "locale": "de",
            "defaultQuotas": {"movie": {"quotaLimit": 5, "quotaDays": 30}},
        },
        "/api/v1/settings/radarr": [
            {
                "id": 0,
                "name": "Radarr",
                "is4k": False,
                "hostname": "radarr.example.com",
                "port": 7878,
                "apiKey": "a" * 32,
                "isDefault": True,
                "activeDirectory": "/data/Filme",
            }
        ],
        "/api/v1/settings/sonarr": [],
        "/api/v1/settings/plex": {
            "name": "Wohnzimmer",
            "ip": "10.0.0.2",
            "port": 32400,
            "machineId": "beispiel-maschinenkennung",
        },
        "/api/v1/settings/jellyfin": {},
        "/api/v1/settings/notifications/email": {
            "enabled": True,
            "options": {
                "smtpHost": "mail.example.com",
                "smtpPort": 465,
                "secure": True,
                "authUser": "nexview@example.com",
                "authPass": "beispielpasswort",
                "emailFrom": "nexview@example.com",
            },
        },
        "/api/v1/settings/notifications/discord": {
            "enabled": True,
            "options": {"webhookUrl": "https://discord.example.com/api/webhooks/1/x"},
        },
        "/api/v1/user": {
            "pageInfo": {"pages": 1},
            "results": konten
            if konten is not None
            else [
                _konto(1, name="chefin", rechte=2),
                {
                    **_konto(2, name="Jürgen Müller", plex_id=777, rechte=16, filme=0),
                    "email": "juergen@example.com",
                },
                _konto(3, name="robin-privat", art=2, plex_id=None, serien=5),
            ],
        },
        "/api/v1/blocklist": {
            "pageInfo": {"pages": 1},
            "results": [{"id": 1, "tmdbId": 111, "mediaType": "movie", "title": "Beispiel"}],
        },
        "/api/v1/issue": {"pageInfo": {"pages": 1}, "results": []},
        "/api/v1/request": {"pageInfo": {"pages": 1}, "results": []},
    }


def _konten_in_der_datenbank() -> dict[str, object]:
    from app.db import SessionLocal
    from app.models import Blocked, ChannelTarget, Setting, User

    with SessionLocal() as sitzung:
        return {
            "konten": {k.username: k for k in sitzung.query(User).all()},
            "einstellungen": sitzung.query(Setting).count(),
            "gesperrt": sitzung.query(Blocked).count(),
            "kanaele": sitzung.query(ChannelTarget).count(),
        }


def test_abschliessen_schreibt_alles_in_einem_zug(client: TestClient, seerr_attrappe) -> None:
    """Der gute Fall: Einstellungen, Sperrliste, Kanal, Besitzer, Konten, Sitzung."""
    from app.db import SessionLocal
    from app.models import Setting, User
    from app.services.settings_service import load_settings

    seerr_attrappe(_volle_attrappe())
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(
            bereiche=["radarr", "mail", "sperrliste", "kanal_discord", "vorgabe_region"],
            konten=[{"seerr_id": 2, "rolle": "approver"}, {"seerr_id": 3}],
            public_url="https://nexview.example.com/",
        ),
    )
    assert antwort.status_code == 201, antwort.text
    daten = antwort.json()
    assert daten["access_token"], "Die Antwort ist die Sitzung des Besitzers."
    bericht = daten["bericht"]
    assert bericht["besitzer"]["username"] == "chefin"
    assert bericht["besitzer"]["zugang"] == "plex"
    assert [k["username"] for k in bericht["konten"]] == ["Jurgen.Muller", "robin-privat"]
    assert bericht["abgelehnt"] == []
    assert {k["bild"] for k in bericht["konten"]} == {"keins"}, "Seerr hatte keine Bilder."
    assert bericht["gesperrt"] == 1
    assert bericht["kanaele"] == 1
    assert bericht["public_url"] is True

    with SessionLocal() as sitzung:
        einstellungen = load_settings(sitzung)
        assert einstellungen.radarr_url == "http://radarr.example.com:7878"
        assert einstellungen.radarr_api_key == "a" * 32
        assert einstellungen.smtp_password == "beispielpasswort"
        assert einstellungen.public_url == "https://nexview.example.com"
        assert einstellungen.default_region == "DE"
        # Das Geheimnis steht verschluesselt da, nicht im Klartext.
        roh = sitzung.get(Setting, "smtp_password")
        assert roh is not None and roh.value != "beispielpasswort"

        chefin = sitzung.query(User).filter_by(username="chefin").one()
        assert chefin.role is Role.admin
        assert chefin.is_betreiber, "Der Haken gehoert dem, der einrichtet."
        assert chefin.email_verified is False, "Wie bei /api/setup/admin: erst bestaetigen."
        assert chefin.display_name == "chefin"
        assert [z.provider for z in chefin.mediaserver_accounts] == ["plex"]

        juergen = sitzung.query(User).filter_by(username="Jurgen.Muller").one()
        assert juergen.role is Role.approver
        assert juergen.quota_movies_limit == UNBEGRENZT, "Seerrs Null heisst ohne Grenze."
        assert juergen.quota_series_limit is None
        assert juergen.display_name == "Jürgen Müller"
        assert juergen.email == "juergen@example.com"
        assert [(z.provider, z.account_id) for z in juergen.mediaserver_accounts] == [
            ("plex", "777")
        ]
        assert juergen.email_verified is True, "Die Adresse kommt von plex.tv, wie bei link()."

        robin = sitzung.query(User).filter_by(username="robin-privat").one()
        assert robin.role is Role.user, "Ohne Angabe: Nutzer."
        assert robin.quota_series_limit == 5
        assert robin.mediaserver_accounts == []
        assert robin.email_verified is False
        assert robin.password_hash and not robin.password_hash.startswith("$2")

    # Ab jetzt ist die Einrichtung zu.
    assert client.get("/api/setup/status").json()["needs_setup"] is False
    zweiter = client.post("/api/setup/seerr/abschliessen", json=_abschluss_rumpf())
    assert zweiter.status_code == 409


def test_abschliessen_schreibt_bei_fehler_nichts(
    client: TestClient, seerr_attrappe, monkeypatch
) -> None:
    """⚠️ **Die Zusage, an der dieses Feature haengt: alles oder nichts.**

    Ein Fehler mitten im Schreiben - hier nachgestellt in der Sperrliste,
    also **nach** den Einstellungen und **vor** dem Besitzer - darf nichts
    stehen lassen. Sonst liegen SMTP-Passwort und Arr-Schluessel in der
    Datenbank, ohne dass es ein Konto gibt, dem sie gehoeren.
    """
    from app.routers import seerr_umzug

    seerr_attrappe(_volle_attrappe())

    def kaputt(db, eintraege):
        raise RuntimeError("nachgestellter Fehler mitten im Schreiben")

    monkeypatch.setattr(seerr_umzug, "_sperren_anlegen", kaputt)
    with pytest.raises(RuntimeError):
        client.post(
            "/api/setup/seerr/abschliessen",
            json=_abschluss_rumpf(bereiche=["radarr", "mail", "sperrliste"]),
        )

    stand = _konten_in_der_datenbank()
    assert stand["konten"] == {}, "Kein Besitzer, kein halbes Konto."
    assert stand["einstellungen"] == 0, "Die Einstellungen davor sind zurueckgerollt."
    assert client.get("/api/setup/status").json()["needs_setup"] is True


def test_ein_falscher_tmdb_schluessel_bricht_ab_bevor_geschrieben_wird(
    client: TestClient, seerr_attrappe, monkeypatch
) -> None:
    """Der Schluessel wird vor dem Schreiben geprueft - der Assistent hat
    keinen eigenen Pruefknopf dafuer, und ein falscher fiele sonst Wochen
    spaeter auf, wenn die erste Suche leer bleibt."""
    from app.routers import seerr_umzug
    from app.services.tmdb import TmdbError

    seerr_attrappe(_volle_attrappe())

    async def abgelehnt(self):
        raise TmdbError("TMDB meldet einen Fehler (HTTP 401).", 401)

    monkeypatch.setattr(seerr_umzug.TmdbClient, "verify", abgelehnt)
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(bereiche=["radarr"], tmdb_api_key="falsch"),
    )
    assert antwort.status_code == 422, antwort.text
    assert antwort.json()["detail"]["code"] == "tmdb_key_rejected"
    assert _konten_in_der_datenbank()["konten"] == {}
    assert _konten_in_der_datenbank()["einstellungen"] == 0


def test_ein_richtiger_tmdb_schluessel_wird_gespeichert(
    client: TestClient, seerr_attrappe, monkeypatch
) -> None:
    from app.db import SessionLocal
    from app.routers import seerr_umzug
    from app.services.settings_service import load_settings

    seerr_attrappe(_volle_attrappe())

    async def geht(self):
        return None

    monkeypatch.setattr(seerr_umzug.TmdbClient, "verify", geht)
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(tmdb_api_key="richtig-1234"),
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["bericht"]["tmdb"] is True
    with SessionLocal() as sitzung:
        assert load_settings(sitzung).tmdb_api_key == "richtig-1234"


def test_der_besitzer_darf_nicht_auch_in_der_liste_stehen(
    client: TestClient, seerr_attrappe
) -> None:
    seerr_attrappe(_volle_attrappe())
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(konten=[{"seerr_id": 1}]),
    )
    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == "seerr_owner_in_list"
    assert _konten_in_der_datenbank()["konten"] == {}


def test_ein_verschwundenes_konto_bricht_ab_bevor_geschrieben_wird(
    client: TestClient, seerr_attrappe
) -> None:
    """Zwischen Vorschau und Abschluss hat drueben jemand geloescht."""
    seerr_attrappe(_volle_attrappe())
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(konten=[{"seerr_id": 99}]),
    )
    assert antwort.status_code == 422
    assert antwort.json()["detail"]["code"] == "seerr_account_unknown"
    assert antwort.json()["detail"]["seerr_id"] == 99
    assert _konten_in_der_datenbank()["konten"] == {}


def test_eine_rolle_die_der_umzug_nicht_vergibt_wird_abgewiesen(
    client: TestClient, seerr_attrappe
) -> None:
    """Kinderkonten entstehen nie aus einem Umzug - sie sind Unterprofile."""
    seerr_attrappe(_volle_attrappe())
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(konten=[{"seerr_id": 2, "rolle": "child"}]),
    )
    assert antwort.status_code == 422
    assert _konten_in_der_datenbank()["konten"] == {}


def test_doppelte_adresse_bleibt_draussen_mit_grund(client: TestClient, seerr_attrappe) -> None:
    """Der Besitzer tippt seine Adresse, die in Seerr an einem anderen Konto
    haengt. Nexview fuehrt Adressen eindeutig - die Zeile faellt weg, aber
    sichtbar, nicht still."""
    seerr_attrappe(
        _volle_attrappe(
            konten=[
                _konto(1, name="chefin", rechte=2),
                _konto(2, name="zweitkonto", plex_id=None, art=2),
            ]
        )
    )
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(
            besitzer={
                "seerr_id": 1,
                "username": "chefin",
                "password": "geheim-1234",
                "email": "zweitkonto@example.com",
                "language": "de",
            },
            konten=[{"seerr_id": 2}],
        ),
    )
    assert antwort.status_code == 201, antwort.text
    bericht = antwort.json()["bericht"]
    assert bericht["konten"] == []
    assert len(bericht["abgelehnt"]) == 1
    assert bericht["abgelehnt"][0]["seerr_id"] == 2
    assert bericht["abgelehnt"][0]["grund"]["kennung"] == "adresse_vergeben"
    assert set(_konten_in_der_datenbank()["konten"]) == {"chefin"}


def test_jellyfin_konten_bekommen_die_kennung_aber_keine_bestaetigung(
    client: TestClient, seerr_attrappe
) -> None:
    """Die Jellyfin-Kennung kommt mit (sonst kaeme niemand herein), aber die
    Adresse gilt nicht als bestaetigt: Sie hat in Seerr ein Administrator
    getippt, kein Anbieter geprueft."""
    from app.db import SessionLocal
    from app.models import User

    seerr_attrappe(
        _volle_attrappe(
            konten=[
                _konto(1, name="chefin", rechte=2),
                _konto(2, name="kellerkind", art=3, plex_id=None, jellyfin="abc123"),
            ]
        )
    )
    antwort = client.post(
        "/api/setup/seerr/abschliessen", json=_abschluss_rumpf(konten=[{"seerr_id": 2}])
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["bericht"]["konten"][0]["zugang"] == "jellyfin"
    with SessionLocal() as sitzung:
        konto = sitzung.query(User).filter_by(username="kellerkind").one()
        assert [(z.provider, z.account_id) for z in konto.mediaserver_accounts] == [
            ("jellyfin", "abc123")
        ]
        assert konto.email_verified is False


def test_benutzername_aus_anzeigename() -> None:
    """Der Zwilling von ``benutzernameAus`` in der Oberflaeche."""
    from app.routers.seerr_umzug import _benutzername_aus

    assert _benutzername_aus("Kim Beispiel") == "Kim.Beispiel"
    assert _benutzername_aus("Jürgen Müller") == "Jurgen.Muller"
    assert _benutzername_aus("Straße") == "Strasse"
    assert _benutzername_aus("🎬") == ""
    assert _benutzername_aus("  .robin.  ") == "robin"
    assert len(_benutzername_aus("x" * 50)) == 32


def test_zwei_gleiche_anzeigenamen_ergeben_zwei_benutzernamen(
    client: TestClient, seerr_attrappe
) -> None:
    seerr_attrappe(
        _volle_attrappe(
            konten=[
                _konto(1, name="chefin", rechte=2),
                {**_konto(2, name="Robin", plex_id=11), "email": "a@example.com"},
                {**_konto(3, name="Robin", plex_id=12), "email": "b@example.com"},
            ]
        )
    )
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(konten=[{"seerr_id": 2}, {"seerr_id": 3}]),
    )
    assert antwort.status_code == 201, antwort.text
    namen = [k["username"] for k in antwort.json()["bericht"]["konten"]]
    assert namen == ["Robin", "Robin-2"]


def _png() -> bytes:
    """Ein gueltiges, winziges PNG (1x1), damit ``avatars.save`` es annimmt.

    Aus seinen Teilen gebaut statt als Hex-Zeichenkette: Die sah fuer den
    Waechter gegen Schluessel im Quelltext genauso aus wie ein Schluessel.
    """
    import struct
    import zlib

    def block(art: bytes, daten: bytes) -> bytes:
        return (
            struct.pack(">I", len(daten))
            + art
            + daten
            + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + block(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + block(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + block(b"IEND", b"")
    )


_PNG = _png()


def test_profilbilder_kommen_mit(client: TestClient, seerr_attrappe, monkeypatch) -> None:
    """Entschieden am 03.09.2026: Die Bilder werden geholt, nicht nur gezeigt."""
    from app.db import SessionLocal
    from app.models import User
    from app.routers import seerr_umzug
    from app.services import avatars

    geholt: list[str] = []

    async def bild(adresse: str) -> bytes | None:
        geholt.append(adresse)
        return _PNG if "plex.tv" in adresse else None

    monkeypatch.setattr(seerr_umzug, "_bild_laden", bild)
    seerr_attrappe(
        _volle_attrappe(
            konten=[
                {**_konto(1, name="chefin", rechte=2), "avatar": "https://plex.tv/users/1/avatar"},
                {**_konto(2, name="robin", plex_id=7), "avatar": "https://plex.tv/users/2/avatar"},
                {
                    **_konto(3, name="lokal", art=2, plex_id=None),
                    "avatar": "https://gravatar.com/avatar/abc",
                },
            ]
        )
    )
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(konten=[{"seerr_id": 2}, {"seerr_id": 3}]),
    )
    assert antwort.status_code == 201, antwort.text
    bericht = antwort.json()["bericht"]
    assert sorted(geholt) == [
        "https://gravatar.com/avatar/abc",
        "https://plex.tv/users/1/avatar",
        "https://plex.tv/users/2/avatar",
    ]
    assert bericht["bilder"] == 2
    assert bericht["besitzer"]["bild"] == "uebernommen"
    assert [k["bild"] for k in bericht["konten"]] == ["uebernommen", "nicht_geladen"]

    with SessionLocal() as sitzung:
        robin = sitzung.query(User).filter_by(username="robin").one()
        assert robin.avatar_path, "Das Bild haengt am Konto."
        assert (avatars.avatar_dir() / robin.avatar_path).read_bytes() == _PNG
        lokal = sitzung.query(User).filter_by(username="lokal").one()
        assert lokal.avatar_path is None, "Ein Bild, das nicht kommt, ist ein fehlendes Bild."


def test_region_und_sprache_kommen_je_konto_mit(client: TestClient, seerr_attrappe) -> None:
    """Seerr fuehrt Region und Sprache je Konto; ohne diesen Abruf fragte
    Nexview jeden beim ersten Anmelden, obwohl er es drueben gesagt hat."""
    from app.db import SessionLocal
    from app.models import User

    antworten = _volle_attrappe()
    # Die Feldnamen sind Seerrs eigene (usersettings.ts, GET /main): discoverRegion, locale.
    antworten["/api/v1/user/1/settings/main"] = {"discoverRegion": "at", "locale": "en-US", "streamingRegion": "US"}
    antworten["/api/v1/user/2/settings/main"] = {"discoverRegion": "", "locale": "fr"}
    # Konto 3 hat den Pfad nicht - Seerr antwortet 404, das heisst "nichts".
    seerr_attrappe(antworten)
    antwort = client.post(
        "/api/setup/seerr/abschliessen",
        json=_abschluss_rumpf(konten=[{"seerr_id": 2}, {"seerr_id": 3}]),
    )
    assert antwort.status_code == 201, antwort.text
    with SessionLocal() as sitzung:
        chefin = sitzung.query(User).filter_by(username="chefin").one()
        assert chefin.discover_region == "AT"
        assert chefin.language == "en"
        juergen = sitzung.query(User).filter_by(username="Jurgen.Muller").one()
        assert juergen.discover_region is None, "Leer heisst Hausvorgabe."
        assert juergen.language == "de", "Franzoesisch kennt Nexview nicht: Hausvorgabe."
        robin = sitzung.query(User).filter_by(username="robin-privat").one()
        assert robin.discover_region is None

