"""Das Befund-Register: Was schlaegt an - und vor allem, was nicht.

Zu jeder Pruefung gehoeren zwei Faelle. Der eine zeigt, dass sie ueberhaupt
etwas findet; der andere, dass sie **schweigt**, wenn alles in Ordnung ist.
Der zweite ist der wichtigere: Ein Register, das dauernd meldet, wird
weggeklickt - und danach auch die eine Meldung, auf die es ankommt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    ArrGesundheit,
    ArrWebhook,
    InstanzStand,
    MediaRequest,
    MediaType,
    Notification,
    NotificationType,
    QualityTier,
    RequestStatus,
    Role,
    SpeicherVerlauf,
    StorageEntry,
    StorageState,
    User,
)
from app.services import befunde, mail_outbox
from app.services.settings_service import load_settings

from .conftest import auth_headers, create_user


def _jetzt() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _sammeln(kennung: str | None = None) -> list[befunde.Befund]:
    """Das Register einmal laufen lassen, wahlweise auf eine Kennung gefiltert."""
    with SessionLocal() as session:
        gefunden = befunde.sammeln(session, load_settings(session))
    if kennung is None:
        return gefunden
    return [b for b in gefunden if b.kennung == kennung]


def _anfrage(
    status: RequestStatus,
    *,
    vor_tagen: int = 0,
    freigegeben_vor_tagen: int | None = None,
    tmdb_id: int = 1,
    erschienen_vor_tagen: int | None = None,
) -> None:
    """Eine Anfrage anlegen.

    ``erschienen_vor_tagen`` schreibt das Erscheinungsdatum des Titels:
    ``None`` heisst "steht nicht dabei", eine **negative** Zahl heisst
    "erscheint erst in so vielen Tagen".
    """
    with SessionLocal() as session:
        besitzer = session.query(User).first()
        assert besitzer is not None
        jetzt = _jetzt()
        session.add(
            MediaRequest(
                user_id=besitzer.id,
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=tmdb_id,
                title=f"Titel {tmdb_id}",
                status=status,
                release_date=(
                    None
                    if erschienen_vor_tagen is None
                    else (jetzt - timedelta(days=erschienen_vor_tagen)).date().isoformat()
                ),
                requested_at=jetzt - timedelta(days=vor_tagen),
                approved_at=(
                    None
                    if freigegeben_vor_tagen is None
                    else jetzt - timedelta(days=freigegeben_vor_tagen)
                ),
            )
        )
        session.commit()


# ---------------------------------------------------------------------------
# Nachschub
# ---------------------------------------------------------------------------


def test_haengende_anfrage_wird_gemeldet(admin_client: TestClient) -> None:
    _anfrage(RequestStatus.searching, vor_tagen=40, freigegeben_vor_tagen=30)

    treffer = _sammeln("nachschub.haengt")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.fehler
    assert treffer[0].werte["anzahl"] == 1
    # Ein Befund ohne Ausweg waere eine Sorge, keine Hilfe - und ein Ausweg,
    # der auf der Startseite endet, ist nur ein halber.
    assert treffer[0].ziel == "/admin/requests?filter=searching"


def test_frisch_gesuchte_anfrage_schweigt(admin_client: TestClient) -> None:
    """Suchen ist der Normalzustand - erst die Dauer macht es zum Befund."""
    _anfrage(RequestStatus.searching, vor_tagen=2, freigegeben_vor_tagen=1)
    assert _sammeln("nachschub.haengt") == []


def test_gerechnet_wird_ab_der_freigabe(admin_client: TestClient) -> None:
    """Eine alte Bestellung, die gestern freigegeben wurde, sucht seit gestern.

    Wuerde ab ``requested_at`` gerechnet, waere jede lange zurueckgestellte
    Anfrage sofort nach der Freigabe ein Fehler.
    """
    _anfrage(RequestStatus.searching, vor_tagen=90, freigegeben_vor_tagen=1)
    assert _sammeln("nachschub.haengt") == []


def test_kuenftiger_titel_schweigt(admin_client: TestClient) -> None:
    """Ein Film, der erst in Monaten herauskommt, sucht zu Recht ergebnislos.

    Das war der Fehler, der die Kachel unbrauchbar machte: Sie meldete einen
    Ausfall, wo nur das Erscheinungsdatum in der Zukunft lag.
    """
    _anfrage(
        RequestStatus.searching,
        vor_tagen=40,
        freigegeben_vor_tagen=30,
        erschienen_vor_tagen=-90,
    )
    assert _sammeln("nachschub.haengt") == []


def test_gerade_erschienener_titel_schweigt(admin_client: TestClient) -> None:
    """Auch nach dem Start braucht ein Titel Zeit, bis ihn jemand anbietet.

    Gerechnet wird ab dem **spaeteren** der beiden Zeitpunkte - sonst schlaegt
    der Befund am Tag der Veroeffentlichung sofort an, wenn die Freigabe lange
    vorher lag.
    """
    _anfrage(
        RequestStatus.searching,
        vor_tagen=60,
        freigegeben_vor_tagen=50,
        erschienen_vor_tagen=3,
    )
    assert _sammeln("nachschub.haengt") == []


def test_titel_ohne_erscheinungsdatum_zaehlt_mit(admin_client: TestClient) -> None:
    """Ohne Datum laesst sich nicht sagen, dass der Titel noch nicht heraus ist.

    Die stille Variante waere hier die gefaehrlichere: Ein echter
    Indexer-Ausfall an einem alten Titel bliebe unbemerkt.
    """
    _anfrage(RequestStatus.searching, vor_tagen=40, freigegeben_vor_tagen=30)
    assert len(_sammeln("nachschub.haengt")) == 1


def test_der_befund_nennt_den_aeltesten_titel(admin_client: TestClient) -> None:
    """Eine blosse Zahl half niemandem - man wusste nicht, wonach man sucht."""
    _anfrage(
        RequestStatus.searching,
        vor_tagen=40,
        freigegeben_vor_tagen=20,
        tmdb_id=1,
        erschienen_vor_tagen=300,
    )
    _anfrage(
        RequestStatus.searching,
        vor_tagen=90,
        freigegeben_vor_tagen=80,
        tmdb_id=2,
        erschienen_vor_tagen=300,
    )

    treffer = _sammeln("nachschub.haengt")
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] == 2
    # Titel 2 wartet laenger - er gehoert in die Kachel.
    assert treffer[0].werte["titel"] == "Titel 2"


def test_wartende_freigabe_erst_nach_tagen(admin_client: TestClient) -> None:
    _anfrage(RequestStatus.pending_approval, vor_tagen=0, tmdb_id=1)
    assert _sammeln("nachschub.freigabe_wartet") == []

    _anfrage(RequestStatus.pending_approval, vor_tagen=10, tmdb_id=2)
    treffer = _sammeln("nachschub.freigabe_wartet")
    assert len(treffer) == 1
    # Nur die alte zaehlt, nicht beide.
    assert treffer[0].werte["anzahl"] == 1
    assert treffer[0].schwere is befunde.Schwere.warnung


def test_einzelne_fehlschlaege_sind_kein_muster(admin_client: TestClient) -> None:
    for nummer in range(befunde.FEHLGESCHLAGEN_AB - 1):
        _anfrage(RequestStatus.failed, vor_tagen=1, tmdb_id=100 + nummer)
    assert _sammeln("nachschub.fehlgeschlagen") == []

    _anfrage(RequestStatus.failed, vor_tagen=1, tmdb_id=200)
    treffer = _sammeln("nachschub.fehlgeschlagen")
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] == befunde.FEHLGESCHLAGEN_AB


def test_alte_fehlschlaege_zaehlen_nicht_mehr(admin_client: TestClient) -> None:
    """Was vor Wochen schiefging, ist Geschichte und keine Aufgabe."""
    for nummer in range(befunde.FEHLGESCHLAGEN_AB + 2):
        _anfrage(
            RequestStatus.failed,
            vor_tagen=befunde.FEHLGESCHLAGEN_TAGE + 5,
            tmdb_id=300 + nummer,
        )
    assert _sammeln("nachschub.fehlgeschlagen") == []


# ---------------------------------------------------------------------------
# Bibliothek
# ---------------------------------------------------------------------------


def _posten(*, verwaltet: bool, zustand: StorageState, schluessel: str) -> None:
    with SessionLocal() as session:
        besitzer = session.query(User).first()
        assert besitzer is not None
        session.add(
            StorageEntry(
                key=schluessel,
                user_id=besitzer.id if zustand is not StorageState.house else None,
                media_type=MediaType.movie,
                tier=QualityTier.standard,
                tmdb_id=hash(schluessel) % 100000,
                title="Ein Film",
                size_bytes=5_000_000_000,
                arr_managed=verwaltet,
                state=zustand,
            )
        )
        session.commit()


def test_geisterposten_wird_gemeldet(admin_client: TestClient) -> None:
    _posten(verwaltet=False, zustand=StorageState.owned, schluessel="movie:a")

    treffer = _sammeln("bibliothek.geisterposten")
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] == 1
    assert treffer[0].werte["bytes"] == 5_000_000_000


def test_verwalteter_posten_ist_kein_geist(admin_client: TestClient) -> None:
    _posten(verwaltet=True, zustand=StorageState.owned, schluessel="movie:b")
    assert _sammeln("bibliothek.geisterposten") == []


def test_hausbestand_belastet_niemanden(admin_client: TestClient) -> None:
    """Ein nicht mehr verwalteter Posten **ohne** Besitzer ist kein Leck.

    Das Problem am Geisterposten ist, dass er jemandem angerechnet wird und
    trotzdem nicht mehr geloescht werden kann. Im Hausbestand faellt beides weg.
    """
    _posten(verwaltet=False, zustand=StorageState.house, schluessel="movie:c")
    assert _sammeln("bibliothek.geisterposten") == []


# ---------------------------------------------------------------------------
# Dienste
# ---------------------------------------------------------------------------


def _gesundheit(kennung: str, probleme: list[dict]) -> None:
    with SessionLocal() as session:
        session.add(
            ArrGesundheit(kennung=kennung, stand=probleme, aktualisiert_am=_jetzt())
        )
        session.commit()


def test_instanz_meldung_kommt_im_wortlaut(arr_client: TestClient) -> None:
    """Der Text von Radarr wird durchgereicht, nicht uebersetzt."""
    _gesundheit(
        "radarr-standard",
        [
            {
                "schluessel": "IndexerStatusCheck|warning",
                "typ": "error",
                "text": "Indexers unavailable due to failures",
            }
        ],
    )

    treffer = _sammeln("dienst.meldet_problem")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.fehler
    assert treffer[0].wortlaut == "Indexers unavailable due to failures"
    # Der Instanzname ist unser Teil und geht als Wert an die Uebersetzung.
    assert treffer[0].werte["instanz"]


def test_unbekannter_typ_gilt_als_warnung(arr_client: TestClient) -> None:
    """Nachsichtig lesen statt verwerfen - die Feldwerte sind fremdes Gebiet."""
    _gesundheit("radarr-standard", [{"typ": "notice", "text": "Etwas"}])
    treffer = _sammeln("dienst.meldet_problem")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.warnung


def test_stumme_instanz_meldet_keine_alten_probleme(arr_client: TestClient) -> None:
    """Zwei Zeilen fuer eine Instanz, von denen eine geraten ist - nein.

    Was in ``arr_gesundheit`` steht, ist der zuletzt *gesehene* Stand. Bei
    einer Instanz, die nicht antwortet, weiss niemand, ob er noch gilt; die
    eine Zeile, die zaehlt, ist "antwortet nicht".
    """
    _gesundheit("radarr-standard", [{"typ": "error", "text": "Etwas ging schief"}])
    _stand("radarr-standard", erreichbar=False, seit_minuten=120)

    assert _sammeln("dienst.meldet_problem") == []
    assert len(_sammeln("dienst.nicht_erreichbar")) == 1


def test_erreichbare_instanz_meldet_ihre_probleme_sehr_wohl(
    arr_client: TestClient,
) -> None:
    """Die Gegenprobe - sonst wuerde die Unterdrueckung alles verschlucken."""
    _gesundheit("radarr-standard", [{"typ": "error", "text": "Etwas ging schief"}])
    _stand("radarr-standard", erreichbar=True)

    assert len(_sammeln("dienst.meldet_problem")) == 1


def test_gesunde_instanz_schweigt(arr_client: TestClient) -> None:
    _gesundheit("radarr-standard", [])
    assert _sammeln("dienst.meldet_problem") == []


def _rueckkanal(kennung: str, *, aktiv: bool, fehler: str) -> None:
    with SessionLocal() as session:
        session.add(
            ArrWebhook(
                kennung=kennung,
                geheimnis="egal",
                aktiv=aktiv,
                fehler=fehler,
                fehler_info="Radarr 5.0",
            )
        )
        session.commit()


def test_gestoerter_rueckkanal_wird_gemeldet(arr_client: TestClient) -> None:
    _rueckkanal("radarr-standard", aktiv=True, fehler="unreachable")
    treffer = _sammeln("dienst.rueckkanal_gestoert")
    assert len(treffer) == 1
    assert treffer[0].werte["grund"] == "unreachable"


def test_stiller_aber_heiler_rueckkanal_schweigt(arr_client: TestClient) -> None:
    """Kein Anruf ist kein Fehler.

    Ein Haushalt, in dem eine Woche lang niemand etwas anfragt, bekaeme sonst
    eine Warnung fuer voellig richtiges Verhalten.
    """
    _rueckkanal("radarr-standard", aktiv=True, fehler="")
    assert _sammeln("dienst.rueckkanal_gestoert") == []


def test_abgeschalteter_rueckkanal_schweigt(arr_client: TestClient) -> None:
    """Wer den Haken wegnimmt, will keine Meldungen darueber."""
    _rueckkanal("radarr-standard", aktiv=False, fehler="unreachable")
    assert _sammeln("dienst.rueckkanal_gestoert") == []


def _stand(
    kennung: str,
    *,
    erreichbar: bool = True,
    seit_minuten: int = 0,
    version: str = "6.3.0",
    messwerte: dict | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(
            InstanzStand(
                kennung=kennung,
                erreichbar=erreichbar,
                erreichbar_seit=_jetzt() - timedelta(minutes=seit_minuten),
                version=version,
                messwerte=messwerte,
                gemessen_am=_jetzt(),
            )
        )
        session.commit()


def test_stumme_instanz_wird_gemeldet(arr_client: TestClient) -> None:
    _stand("radarr-standard", erreichbar=False, seit_minuten=120)

    treffer = _sammeln("dienst.nicht_erreichbar")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.fehler
    # Die Dauer ist der Kern: "startet gerade neu" und "seit gestern weg" sind
    # zwei voellig verschiedene Nachrichten.
    assert treffer[0].werte["minuten"] >= 119


def test_kurzer_aussetzer_ist_kein_ausfall(arr_client: TestClient) -> None:
    """Ein Neustart oder Update erzeugt ein paar stumme Runden.

    Wer dafuer eine Meldung bekommt, schaltet sie ab - und danach auch die,
    auf die es ankommt.
    """
    _stand("radarr-standard", erreichbar=False, seit_minuten=2)
    assert _sammeln("dienst.nicht_erreichbar") == []


def test_erreichbare_instanz_schweigt(arr_client: TestClient) -> None:
    _stand("radarr-standard", erreichbar=True, seit_minuten=9999)
    assert _sammeln("dienst.nicht_erreichbar") == []


def test_neue_fassung_ist_nur_ein_hinweis(arr_client: TestClient) -> None:
    """Ein ausstehendes Update ist keine Stoerung."""
    _stand(
        "radarr-standard",
        version="6.3.0",
        messwerte={"aktualisierung": {"version": "6.4.1"}},
    )
    treffer = _sammeln("dienst.version_alt")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.hinweis
    assert treffer[0].werte["neu"] == "6.4.1"
    assert treffer[0].werte["jetzt"] == "6.3.0"


def test_aktuelle_instanz_schweigt(arr_client: TestClient) -> None:
    """``None`` heisst "aktuell **oder** unbekannt" - beides schweigt."""
    _stand("radarr-standard", messwerte={"aktualisierung": None})
    assert _sammeln("dienst.version_alt") == []


# ---------------------------------------------------------------------------
# Platz
# ---------------------------------------------------------------------------


def _platte(belegt: float, gesamt: int = 1000, ordner: str = "/data/Movies") -> dict:
    return {
        "gesamt": gesamt,
        "frei": int(gesamt * (1 - belegt)),
        "ordner": [ordner],
        "belegt_anteil": belegt,
    }


def test_volle_platte_wird_gemeldet(arr_client: TestClient) -> None:
    _stand("radarr-standard", messwerte={"traeger": [_platte(0.91)]})

    treffer = _sammeln("platz.knapp")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.warnung
    assert treffer[0].werte["prozent"] == 91
    # Der Ordner sagt, welche Platte gemeint ist - eine Byte-Zahl tut das nicht.
    assert treffer[0].werte["ordner"] == "/data/Movies"


def test_sehr_volle_platte_ist_ein_fehler(arr_client: TestClient) -> None:
    """Ab 95 Prozent passt der naechste grosse Download schlicht nicht mehr."""
    _stand("radarr-standard", messwerte={"traeger": [_platte(0.97)]})
    treffer = _sammeln("platz.knapp")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.fehler


def test_platte_mit_luft_schweigt(arr_client: TestClient) -> None:
    _stand("radarr-standard", messwerte={"traeger": [_platte(0.62)]})
    assert _sammeln("platz.knapp") == []


def test_dieselbe_platte_wird_nur_einmal_gemeldet(arr_client: TestClient) -> None:
    """Zwei Instanzen, eine Platte - **ein** Befund.

    Filme und Serien liegen fast immer auf demselben Traeger. Je Instanz zu
    melden hiesse, dieselbe volle Platte zwei- oder viermal untereinander zu
    zeigen.
    """
    platte = _platte(0.93)
    _stand("radarr-standard", messwerte={"traeger": [platte]})
    _stand("sonarr-standard", messwerte={"traeger": [platte]})

    assert len(_sammeln("platz.knapp")) == 1


def test_zwei_echte_platten_geben_zwei_befunde(arr_client: TestClient) -> None:
    _stand(
        "radarr-standard",
        messwerte={
            "traeger": [
                _platte(0.93, gesamt=1000, ordner="/data/Movies"),
                _platte(0.98, gesamt=2000, ordner="/data/TV"),
            ]
        },
    )
    treffer = _sammeln("platz.knapp")
    assert len(treffer) == 2
    assert {b.schwere for b in treffer} == {
        befunde.Schwere.warnung,
        befunde.Schwere.fehler,
    }


# ---------------------------------------------------------------------------
# Haengende Downloads
# ---------------------------------------------------------------------------


def test_haengender_import_wird_gemeldet(arr_client: TestClient) -> None:
    _stand(
        "radarr-standard",
        messwerte={"warteschlange": {"gesamt": 5, "eingriff": 2}},
    )
    _stand(
        "sonarr-standard",
        messwerte={"warteschlange": {"gesamt": 3, "eingriff": 1}},
    )

    treffer = _sammeln("nachschub.eingriff_noetig")
    assert len(treffer) == 1
    # Ueber alle Instanzen zusammengezaehlt: Wer drei betreibt, will eine Zahl
    # sehen und nicht drei Zeilen.
    assert treffer[0].werte["anzahl"] == 3
    assert treffer[0].schwere is befunde.Schwere.fehler


def test_laufende_warteschlange_schweigt(arr_client: TestClient) -> None:
    _stand(
        "radarr-standard",
        messwerte={"warteschlange": {"gesamt": 12, "eingriff": 0}},
    )
    assert _sammeln("nachschub.eingriff_noetig") == []


def test_ohne_messung_kein_befund(arr_client: TestClient) -> None:
    """Frisch installiert ist noch nichts gemessen - das ist kein Problem.

    Alle vier Stufe-2-Pruefungen muessen den leeren Zustand aushalten, sonst
    steht direkt nach dem ersten Start eine Wand aus Befunden da.
    """
    for kennung in (
        "dienst.nicht_erreichbar",
        "dienst.version_alt",
        "platz.knapp",
        "nachschub.eingriff_noetig",
    ):
        assert _sammeln(kennung) == [], kennung


# ---------------------------------------------------------------------------
# Betrieb
# ---------------------------------------------------------------------------


def test_aufgegebene_mail_wird_gemeldet(admin_client: TestClient) -> None:
    from app.services import mail_outbox

    with SessionLocal() as session:
        empfaenger = session.query(User).first()
        assert empfaenger is not None
        session.add(
            Notification(
                user_id=empfaenger.id,
                type=NotificationType.approved,
                message_key="notifications.approved",
                mail_pending=False,
                mail_sent_at=None,
                mail_attempts=mail_outbox.MAX_ATTEMPTS,
            )
        )
        session.commit()

    treffer = _sammeln("betrieb.mail_haengt")
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] == 1


def test_versendete_mail_ist_kein_befund(admin_client: TestClient) -> None:
    with SessionLocal() as session:
        empfaenger = session.query(User).first()
        assert empfaenger is not None
        session.add(
            Notification(
                user_id=empfaenger.id,
                type=NotificationType.approved,
                message_key="notifications.approved",
                mail_pending=False,
                mail_sent_at=_jetzt(),
                mail_attempts=1,
            )
        )
        session.commit()
    assert _sammeln("betrieb.mail_haengt") == []


def test_sicherung_abgeschaltet_ist_kein_versaeumnis(admin_client: TestClient) -> None:
    """"off" ist eine Entscheidung des Betreibers, kein Fehler."""
    admin_client.put("/api/settings", json={"backup_schedule": "off"})
    assert _sammeln("betrieb.sicherung_alt") == []
    assert _sammeln("betrieb.sicherung_fehlt") == []


# ---------------------------------------------------------------------------
# Sortierung, Robustheit und die Endpunkte
# ---------------------------------------------------------------------------


def test_dringendstes_zuerst(admin_client: TestClient) -> None:
    _anfrage(RequestStatus.searching, vor_tagen=40, freigegeben_vor_tagen=30, tmdb_id=1)
    _anfrage(RequestStatus.pending_approval, vor_tagen=10, tmdb_id=2)

    gefunden = _sammeln()
    assert gefunden[0].schwere is befunde.Schwere.fehler
    assert [b.kennung for b in gefunden][:2] == [
        "nachschub.haengt",
        "nachschub.freigabe_wartet",
    ]


def test_eine_kaputte_pruefung_nimmt_die_seite_nicht_mit(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sonst kostet ein Fehler genau die Seite, auf der man nachsehen wollte."""

    def kaputt(db, settings, jetzt):
        raise RuntimeError("absichtlich")

    _anfrage(RequestStatus.searching, vor_tagen=40, freigegeben_vor_tagen=30)
    monkeypatch.setattr(befunde, "PRUEFUNGEN", (kaputt, befunde._nachschub_haengt))

    treffer = _sammeln("nachschub.haengt")
    assert len(treffer) == 1


# ---------------------------------------------------------------------------
# Wachstum, Bibliothek und Betrieb (Stufe 3)
# ---------------------------------------------------------------------------


def _verlauf(*, tage: int, zuwachs_je_tag: int, frei: int) -> None:
    """Einen gleichmaessig wachsenden Verlauf anlegen, juengster Punkt heute."""
    with SessionLocal() as session:
        heute = _jetzt()
        for zurueck in range(tage):
            tag = (heute - timedelta(days=zurueck)).strftime("%Y-%m-%d")
            session.add(
                SpeicherVerlauf(
                    tag=tag,
                    belegt_bytes=10 * 1024**4 - zurueck * zuwachs_je_tag,
                    frei_bytes=frei + zurueck * zuwachs_je_tag,
                    gemessen_am=heute - timedelta(days=zurueck),
                )
            )
        session.commit()


def test_wachstum_sagt_voraus(admin_client: TestClient) -> None:
    # 200 GB je Tag, 1 TB frei -> gut fuenf Tage, also weit unter der Schwelle.
    _verlauf(tage=30, zuwachs_je_tag=200 * 1024**3, frei=1024**4)

    treffer = _sammeln("platz.waechst_schnell")
    assert len(treffer) == 1
    assert treffer[0].schwere is befunde.Schwere.hinweis
    assert treffer[0].werte["anzahl"] >= 1


def test_ruhiges_wachstum_schweigt(admin_client: TestClient) -> None:
    # 1 GB je Tag bei 50 TB frei - das reicht ueber Jahre.
    _verlauf(tage=30, zuwachs_je_tag=1024**3, frei=50 * 1024**4)
    assert _sammeln("platz.waechst_schnell") == []


def test_zu_kurzer_verlauf_wird_nicht_hochgerechnet(admin_client: TestClient) -> None:
    """Aus zwei Punkten liesse sich jede beliebige Zukunft herauslesen.

    Ein einziger grosser Download am Vortag ergaebe "in drei Tagen voll" - und
    das waere keine Vorhersage, sondern ein Zufall mit Nachkommastelle.
    """
    _verlauf(tage=3, zuwachs_je_tag=500 * 1024**3, frei=1024**4)
    assert _sammeln("platz.waechst_schnell") == []


def test_aufgeraeumte_platte_warnt_nicht(admin_client: TestClient) -> None:
    """Negative Steigung heisst: Es wird mehr frei, nicht weniger."""
    _verlauf(tage=30, zuwachs_je_tag=-(50 * 1024**3), frei=1024**4)
    assert _sammeln("platz.waechst_schnell") == []


def _abgleich_stand(**abweichend) -> None:
    """Einen fertig gemessenen Abgleich-Stand ablegen.

    Die Messung selbst hat ihre eigenen Tests (``test_abgleich.py``); hier
    geht es nur darum, dass die fuenf Pruefungen darauf ansprechen.
    """
    import json

    from app.models import Setting
    from app.services import abgleich as abgleich_dienst

    werte = {
        "arr_ohne_server": befunde.ARR_OHNE_SERVER_AB + 4,
        "server_ohne_arr": 120,
        "nicht_erkannt": befunde.NICHT_ERKANNT_AB + 2,
        "doppelt": 4,
        "jahr_widerspruch": 3,
        "je_anbieter": {"plex": 3500, "jellyfin": 3400},
        "anbieter_luecke": befunde.ANBIETER_LUECKE_AB + 10,
        "beispiele": {"jahr_widerspruch": ["Ein Film (2023 / 2025)"]},
        "moeglich": True,
    }
    werte.update(abweichend)
    with SessionLocal() as session:
        zeile = session.get(Setting, abgleich_dienst.SCHLUESSEL)
        if zeile is None:
            zeile = Setting(key=abgleich_dienst.SCHLUESSEL, value="")
            session.add(zeile)
        zeile.value = json.dumps(werte)
        session.commit()


def test_abgleich_meldet_die_fuenf_faelle(arr_client: TestClient) -> None:
    _abgleich_stand()
    kennungen = {b.kennung for b in _sammeln() if b.bereich is befunde.Bereich.abgleich}
    assert kennungen == {
        "abgleich.arr_ohne_server",
        "abgleich.nicht_erkannt",
        "abgleich.jahr_widerspruch",
        "abgleich.anbieter_uneinig",
    }


def test_ohne_medienserver_schweigt_der_ganze_bereich(arr_client: TestClient) -> None:
    """Wer keinen Medienserver hat, sieht den Bereich gar nicht.

    Kein "du koenntest einen verbinden", keine leere Tabelle - eine Pruefung,
    die nicht zutrifft, schweigt.
    """
    _abgleich_stand(moeglich=False)
    assert [b for b in _sammeln() if b.bereich is befunde.Bereich.abgleich] == []


def test_ein_einziger_anbieter_erzeugt_keinen_vergleich(arr_client: TestClient) -> None:
    """Die meisten Haeuser haben genau einen Server."""
    _abgleich_stand(je_anbieter={"plex": 3500})
    assert _sammeln("abgleich.anbieter_uneinig") == []
    # Die uebrigen vier gelten weiterhin - sie brauchen keinen zweiten Server.
    assert len(_sammeln("abgleich.nicht_erkannt")) == 1


def test_einzelne_abweichungen_sind_der_normale_betrieb(arr_client: TestClient) -> None:
    """Zwischen zwei Messungen liegt eine Stunde.

    Ein Titel, der gerade geladen wurde, ist in Radarr schon da und im
    Medienserver noch nicht. Ohne Schwellen waere das ein Dauerfehler.
    """
    _abgleich_stand(arr_ohne_server=2, nicht_erkannt=1, anbieter_luecke=3)
    kennungen = {b.kennung for b in _sammeln() if b.bereich is befunde.Bereich.abgleich}
    # Nur der Jahres-Widerspruch bleibt: Der ist selten genug, dass schon
    # einer zaehlt.
    assert kennungen == {"abgleich.jahr_widerspruch"}


def _protokoll_fehler(anzahl: int, *, alt: bool = False) -> None:
    """Fehlerzeilen ins echte Protokoll schreiben - so, wie sie entstehen.

    ⚠️ **Vorher leeren.** Die Protokolldatei ueberlebt den einzelnen Test;
    ohne das zaehlte der naechste Test die Fehler des vorigen mit und
    "alte zaehlen nicht" bestand aus dem falschen Grund. Genau so ist es beim
    ersten Anlauf passiert.
    """
    from app.services import logs as logs_dienst

    logs_dienst.clear()
    datei = logs_dienst.log_file()
    zeitpunkt = _jetzt() - timedelta(hours=48 if alt else 1)
    stempel = zeitpunkt.strftime("%Y-%m-%d %H:%M:%S")
    with datei.open("a", encoding="utf-8") as handle:
        for nummer in range(anzahl):
            handle.write(f"{stempel} ERROR    nexview.test [-] | Fehler {nummer}\n")


def test_viele_protokollfehler_fallen_auf(admin_client: TestClient) -> None:
    _protokoll_fehler(befunde.PROTOKOLL_FEHLER_AB + 5)
    treffer = _sammeln("betrieb.protokoll_fehler")
    assert len(treffer) == 1
    assert treffer[0].werte["anzahl"] >= befunde.PROTOKOLL_FEHLER_AB


def test_wenige_frische_fehler_schweigen(admin_client: TestClient) -> None:
    """Einzelne Fehler gibt es in jedem Betrieb.

    Diesen Test gab es zuerst nicht - die Mutationsprobe hat es gezeigt: Mit
    ``PROTOKOLL_FEHLER_AB = 1`` blieb der Lauf gruen, die Schwelle war also
    durch nichts abgesichert.

    ⚠️ **Die Zahl steht hier fest und kommt nicht aus der Schwelle.**
    ``PROTOKOLL_FEHLER_AB - 1`` waere wieder wertlos: Sie wanderte mit jeder
    Aenderung mit, und bei einer Schwelle von 1 schriebe der Test null Zeilen
    und bestuende aus dem falschen Grund. Genau daran ist der zweite Anlauf
    gescheitert.
    """
    _protokoll_fehler(3)
    assert _sammeln("betrieb.protokoll_fehler") == []


def test_alte_protokollfehler_zaehlen_nicht(admin_client: TestClient) -> None:
    """Ohne Zeitfenster stuende hier die Summe seit dem letzten Neustart.

    Ein Zaehler, der nie kleiner wird, ist keine Auskunft.
    """
    _protokoll_fehler(befunde.PROTOKOLL_FEHLER_AB + 5, alt=True)
    assert _sammeln("betrieb.protokoll_fehler") == []


def _diagnose_an() -> None:
    from app.services import logs as logs_dienst

    logs_dienst.set_mode("detailed", 30)


def test_laufende_diagnose_wird_gemeldet(admin_client: TestClient) -> None:
    _diagnose_an()
    treffer = _sammeln("betrieb.diagnose_an")
    assert len(treffer) == 1
    assert treffer[0].werte["stufe"] == "detailed"


def test_normale_protokollstufe_schweigt(admin_client: TestClient) -> None:
    assert _sammeln("betrieb.diagnose_an") == []


def _update_gemerkt(jetzt: str, neu: str | None) -> None:

    from app.services import updates as updates_dienst

    updates_dienst._cached = updates_dienst.UpdateStatus(
        current=jetzt,
        latest=neu,
        update_available=neu is not None,
        checked_at=datetime.now(UTC),
    )


def test_neue_nexview_fassung_wird_gemeldet(admin_client: TestClient) -> None:
    _update_gemerkt("0.24.0", "0.99.0")
    try:
        treffer = _sammeln("betrieb.aktualisierung")
        assert len(treffer) == 1
        assert treffer[0].werte["neu"] == "0.99.0"
    finally:
        from app.services import updates as updates_dienst

        updates_dienst.reset_cache()


def test_ohne_gemerkten_stand_schweigt_die_aktualisierung(
    admin_client: TestClient,
) -> None:
    """Direkt nach dem Start ist nichts gemerkt - das ist kein Befund."""
    from app.services import updates as updates_dienst

    updates_dienst.reset_cache()
    assert _sammeln("betrieb.aktualisierung") == []


#: Seiten, auf denen man ohne Zusatz nur oben landet - und dann sucht.
STUMPFE_ZIELE = {"/admin/settings", "/admin/requests", "/admin/stats"}


def _alles_ausloesen() -> None:
    """Fuer jede der zwoelf Pruefungen einen Fall herstellen.

    Drei Instanzen, drei Rollen: eine stumm, eine mit Selbstmeldung und
    gestoertem Rueckkanal, eine mit Messwerten. Anders lassen sich
    ``nicht_erreichbar`` und ``meldet_problem`` nicht gleichzeitig ausloesen -
    eine stumme Instanz meldet ihre alten Probleme absichtlich nicht mehr.
    """
    _anfrage(RequestStatus.searching, vor_tagen=40, freigegeben_vor_tagen=30, tmdb_id=1)
    _anfrage(RequestStatus.pending_approval, vor_tagen=10, tmdb_id=2)
    for nummer in range(befunde.FEHLGESCHLAGEN_AB):
        _anfrage(RequestStatus.failed, vor_tagen=1, tmdb_id=400 + nummer)
    _posten(verwaltet=False, zustand=StorageState.owned, schluessel="movie:waechter")

    _abgleich_stand()
    _stand("sonarr-standard", erreichbar=False, seit_minuten=120)
    _gesundheit("radarr-standard", [{"typ": "error", "text": "Etwas ging schief"}])
    _rueckkanal("radarr-standard", aktiv=True, fehler="unreachable")
    # ⚠️ Die Messwerte muessen an einer Instanz haengen, die es in dieser
    # Umgebung wirklich gibt. Sie lagen zuerst an "radarr-uhd" - die richtet
    # die Testumgebung gar nicht ein, und die Instanz-Pruefungen laufen ueber
    # ``settings.arr_instanzen()``. Der Waechter hat es gemeldet.
    _stand(
        "radarr-standard",
        erreichbar=True,
        messwerte={
            "traeger": [
                {"gesamt": 100, "frei": 2, "ordner": ["/x"], "belegt_anteil": 0.98}
            ],
            "warteschlange": {"gesamt": 3, "eingriff": 1},
            "aktualisierung": {"version": "9.9.9"},
        },
    )

    _verlauf(tage=30, zuwachs_je_tag=200 * 1024**3, frei=1024**4)
    _protokoll_fehler(befunde.PROTOKOLL_FEHLER_AB + 5)
    _diagnose_an()
    _update_gemerkt("0.24.0", "0.99.0")

    with SessionLocal() as session:
        # Ohne Sicherungsordner und mit einem Konto, das aelter ist als der
        # Takt, schlaegt "noch keine automatische Sicherung" an.
        for konto in session.scalars(select(User)):
            konto.created_at = _jetzt() - timedelta(days=90)
        empfaenger = session.query(User).first()
        assert empfaenger is not None
        session.add(
            Notification(
                user_id=empfaenger.id,
                type=NotificationType.approved,
                message_key="notifications.approved",
                mail_pending=False,
                mail_sent_at=None,
                mail_attempts=mail_outbox.MAX_ATTEMPTS,
            )
        )
        session.commit()


def test_jede_pruefung_zeigt_wohin_genau(arr_client: TestClient) -> None:
    """Kein Ziel darf auf einer Startseite enden - und zwar bei **jeder** Pruefung.

    Der Nutzer hat das ausdruecklich als laestig benannt, und nicht nur fuers
    Dashboard: Ein Verweis, der auf "Einstellungen" endet statt auf
    "Einstellungen -> Dienste -> Sonarr", laesst einen durch zwoelf Reiter
    suchen.

    ⚠️ **Der erste Anlauf dieses Waechters war zahnlos.** Er lief ueber die
    Befunde, die zufaellig entstanden waren, und uebersah genau die Pruefung,
    deren Ziel danach von Hand verbogen wurde - der Lauf blieb gruen. Deshalb
    wird jetzt **jede Funktion einzeln** aufgerufen und muss etwas liefern:
    Wer eine Pruefung ergaenzt, ohne sie hier ausloesbar zu machen, faellt auf.
    """
    _alles_ausloesen()

    stumm: list[str] = []
    stumpf: list[str] = []
    with SessionLocal() as session:
        einstellungen = load_settings(session)
        # Der Vorrat kommt wie in ``sammeln`` einmal vorab - die Pruefungen
        # selbst gehen seit Punkt 5 nicht mehr einzeln an die Datenbank.
        vorrat = befunde._vorrat_laden(session)
        for pruefung in befunde.PRUEFUNGEN:
            gefunden = pruefung(session, einstellungen, _jetzt(), vorrat)
            if not gefunden:
                stumm.append(pruefung.__name__)
                continue
            stumpf.extend(
                f"{b.kennung} -> {b.ziel}"
                for b in gefunden
                if b.ziel in STUMPFE_ZIELE or not b.ziel
            )

    assert stumm == [], (
        "Diese Pruefungen hat der Waechter nicht ausloesen koennen und damit "
        f"auch nicht geprueft: {stumm}. In _alles_ausloesen() einen Fall dafuer "
        "ergaenzen."
    )
    assert stumpf == [], f"Diese Ziele enden nur oben auf der Seite: {stumpf}"


def test_zaehler_nennt_jede_schwere(admin_client: TestClient) -> None:
    """Auch die Null - sonst muesste jede Anzeige den fehlenden Fall abfangen."""
    gezaehlt = befunde.zaehlen([])
    assert gezaehlt == {"fehler": 0, "warnung": 0, "hinweis": 0}


def test_dashboard_liefert_zahlen_und_befunde(admin_client: TestClient) -> None:
    _anfrage(RequestStatus.pending_approval, vor_tagen=10)

    antwort = admin_client.get("/api/admin/dashboard")
    assert antwort.status_code == 200
    stand = antwort.json()
    assert stand["zahlen"]["freigaben_offen"] == 1
    assert stand["zaehler"]["warnung"] >= 1
    kennungen = [b["kennung"] for b in stand["befunde"]]
    assert "nachschub.freigabe_wartet" in kennungen
    # Kein fertiger Satz, sondern Bausteine fuer die Uebersetzung.
    eintrag = next(
        b for b in stand["befunde"] if b["kennung"] == "nachschub.freigabe_wartet"
    )
    assert eintrag["werte"]["anzahl"] == 1
    assert eintrag["schluessel"]


def test_wartende_freigabe_ist_zahl_und_nicht_gleich_befund(
    admin_client: TestClient,
) -> None:
    """Eine frische Anfrage steht in den Zahlen, aber nicht in den Befunden.

    Sonst waere der Alltag ein Daueralarm - und die echten Befunde daneben
    waeren nichts mehr wert.
    """
    _anfrage(RequestStatus.pending_approval, vor_tagen=0)

    stand = admin_client.get("/api/admin/dashboard").json()
    assert stand["zahlen"]["freigaben_offen"] == 1
    assert "nachschub.freigabe_wartet" not in [b["kennung"] for b in stand["befunde"]]


def test_befunde_lassen_sich_auf_einen_bereich_einschraenken(
    admin_client: TestClient,
) -> None:
    _anfrage(RequestStatus.pending_approval, vor_tagen=10)

    alle = admin_client.get("/api/admin/befunde").json()
    nachschub = admin_client.get("/api/admin/befunde?bereich=nachschub").json()
    dienste = admin_client.get("/api/admin/befunde?bereich=dienste").json()

    assert len(alle) >= 1
    assert {b["bereich"] for b in nachschub} == {"nachschub"}
    assert dienste == []


def test_unbekannter_bereich_gibt_leer_statt_fehler(admin_client: TestClient) -> None:
    antwort = admin_client.get("/api/admin/befunde?bereich=quatsch")
    assert antwort.status_code == 200
    assert antwort.json() == []


def test_nur_administratoren(admin_client: TestClient) -> None:
    """Entscheider haben hier nichts verloren - Betriebsdaten sind Admin-Sache.

    Bewusst anders als die Statistik-Seite, die Entscheider bis 0.24 sehen
    durften: Indexer-Zustand, Plattenplatz und Sicherungen gehen den, der ueber
    Anfragen entscheidet, nichts an.
    """
    create_user(admin_client, "eva", role=Role.approver)
    eva = auth_headers(admin_client, "eva", "passwort-1234")

    assert admin_client.get("/api/admin/dashboard", headers=eva).status_code == 403
    assert admin_client.get("/api/admin/befunde", headers=eva).status_code == 403

    create_user(admin_client, "kim")
    kim = auth_headers(admin_client, "kim", "passwort-1234")
    assert admin_client.get("/api/admin/dashboard", headers=kim).status_code == 403


def test_ohne_anmeldung_gesperrt(client: TestClient) -> None:
    assert client.get("/api/admin/dashboard").status_code == 401
