"""Verbindungen anlegen und pruefen - und was passiert, wenn es schiefgeht.

⚠️ **Die Regel, um die es hier geht: erst die Probe, dann der Eintrag.**
Die Adresse, unter der *Nexview* einen Medienserver erreicht, muss nicht die
sein, unter der *Radarr* ihn erreicht - Radarr steckt womoeglich in einem
Container mit eigener Sicht aufs Netz. Wird ein Eintrag angelegt, der nie
funktioniert, ist das schlimmer als gar keiner: Er steht in der Liste, sieht
richtig aus, und niemand hinterfragt ihn je wieder.
"""

from __future__ import annotations

import pytest

from app.services import medienserver_verbindung as mv
from app.services import pfad_zuordnung as pz
from app.services.arr import ArrError

SCHEMA_MEDIABROWSER = {
    "implementation": "MediaBrowser",
    "configContract": "MediaBrowserSettings",
    "supportsOnDownload": True,
    "supportsOnRename": True,
    "supportsOnMovieDelete": True,
}
SCHEMA_PLEX = {
    "implementation": "PlexServer",
    "configContract": "PlexServerSettings",
    "supportsOnDownload": True,
    "supportsOnRename": True,
}


class GespieltesArr:
    """Radarr, das sich merkt, was es angelegt hat - und auf Wunsch zickt."""

    label = "Test-Radarr"

    def __init__(self, vorhandene=None, wurzeln=None, probe_scheitert=False):
        self._vorhandene = list(vorhandene or [])
        # ⚠️ Nicht ``wurzeln or [...]``: Eine **leere** Liste ist eine Aussage
        # ("diese Instanz hat keinen Stammordner") und darf nicht stillschweigend
        # durch den Standardwert ersetzt werden.
        self._wurzeln = list([{"path": "/data/Movies"}] if wurzeln is None else wurzeln)
        self.probe_scheitert = probe_scheitert
        self.angelegt: list[dict] = []
        self.geprueft: list[dict] = []

    async def notifications(self):
        return [dict(v) for v in self._vorhandene]

    async def root_folders(self):
        return [dict(w) for w in self._wurzeln]

    async def get(self, pfad, params=None):
        if pfad == "/notification/schema":
            return [SCHEMA_MEDIABROWSER, SCHEMA_PLEX]
        return []

    async def post(self, pfad, payload=None):
        if pfad == "/notification/test":
            self.geprueft.append(dict(payload or {}))
            if self.probe_scheitert:
                raise ArrError("nicht erreichbar", 500, code="arr_http_error")
            return {}
        return {}

    async def notification_anlegen(self, payload):
        self.angelegt.append(dict(payload))
        return {"id": len(self.angelegt)}


def _server(provider="jellyfin", url="http://10.0.0.1:8096", zugang="k"):
    return mv.Medienserver(
        id=1, provider=provider, name=provider.title(), url=url,
        zugang=zugang, braucht_schluessel=provider != "plex",
    )


def _eintrag(umsetzung, wirt, tor):
    return {
        "id": 7, "name": "Nexview: X", "implementation": umsetzung,
        "fields": [{"name": "host", "value": wirt}, {"name": "port", "value": tor}],
    }


# ---------------------------------------------------------------------------
# Anlegen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_erst_pruefen_dann_eintragen():
    """Die Reihenfolge ist der ganze Sinn der Sache."""
    arr = GespieltesArr()
    grund = await mv.herstellen(
        arr, "jellyfin", "Jellyfin", "http://10.0.0.1:8096", "k",
        pz.Zuordnung(von="/data", nach="/media"),
    )
    assert grund == ""
    assert len(arr.geprueft) == 1, "es muss geprueft worden sein"
    assert len(arr.angelegt) == 1
    felder = {f["name"]: f["value"] for f in arr.angelegt[0]["fields"]}
    assert felder["mapFrom"] == "/data" and felder["mapTo"] == "/media"


@pytest.mark.anyio
async def test_gescheiterte_probe_legt_nichts_an():
    """⚠️ Der wichtigste Test dieser Datei.

    Ein Eintrag, den die Instanz nicht erreicht, darf **nicht** entstehen. Er
    saehe richtig aus und taete nie etwas.
    """
    arr = GespieltesArr(probe_scheitert=True)
    grund = await mv.herstellen(
        arr, "jellyfin", "Jellyfin", "http://10.0.0.1:8096", "k", pz.Zuordnung()
    )
    assert grund == "unreachable"
    assert arr.geprueft, "geprueft wurde"
    assert arr.angelegt == [], "aber nichts angelegt"


@pytest.mark.anyio
async def test_ohne_zugang_wird_gar_nicht_erst_gefragt():
    arr = GespieltesArr()
    grund = await mv.herstellen(arr, "jellyfin", "J", "http://x:8096", "", pz.Zuordnung())
    assert grund == "kein_schluessel"
    assert arr.geprueft == [] and arr.angelegt == []


@pytest.mark.anyio
async def test_zu_alte_instanz_wird_benannt():
    """Kennt die Instanz die Umsetzung nicht, fehlt ihr die Faehigkeit."""

    class Alt(GespieltesArr):
        async def get(self, pfad, params=None):
            return []  # kein passendes Schema

    grund = await mv.herstellen(Alt(), "jellyfin", "J", "http://x:8096", "k", pz.Zuordnung())
    assert grund == "too_old"


# ---------------------------------------------------------------------------
# Luecken finden
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_luecke_verschwindet_wenn_die_verbindung_steht():
    arr = GespieltesArr(vorhandene=[_eintrag("MediaBrowser", "10.0.0.1", 8096)])
    offen = await mv.luecken(arr, [_server()])
    assert offen == []


@pytest.mark.anyio
async def test_zwei_server_auf_einem_rechner_bleiben_getrennt():
    """⚠️ Der Fall, der im Betrieb aufgefallen ist.

    Jellyfin (8096) und Emby (8097) laufen unter derselben Umsetzung auf
    demselben Rechner. Ohne Ruecksicht auf den Port meldete Nexview "alle
    verbunden", waehrend Emby fehlte - und legte die Verbindung nie an.
    """
    arr = GespieltesArr(vorhandene=[_eintrag("MediaBrowser", "10.0.0.1", 8096)])
    offen = await mv.luecken(
        arr, [_server("jellyfin", "http://10.0.0.1:8096"),
              _server("emby", "http://10.0.0.1:8097")]
    )
    assert [l.provider for l in offen] == ["emby"]


@pytest.mark.anyio
async def test_fehlender_schluessel_steht_als_hindernis_in_der_luecke():
    arr = GespieltesArr()
    offen = await mv.luecken(arr, [_server(zugang="")])
    assert offen[0].hindernis == "kein_schluessel"
    assert offen[0].selbst_moeglich is False


@pytest.mark.anyio
async def test_unbekannter_anbieter_wird_benannt():
    arr = GespieltesArr()
    offen = await mv.luecken(arr, [_server("kodi", "http://x:8080")])
    assert offen[0].hindernis == "unknown_provider"


@pytest.mark.anyio
async def test_die_zuordnung_wird_in_die_luecke_gereicht():
    """Die Oberflaeche zeigt sie als Vorschau - sie muss also ankommen."""
    arr = GespieltesArr()
    karte = {"jellyfinhttp://10.0.0.1:8096": pz.Zuordnung(von="/data", nach="/media")}
    offen = await mv.luecken(arr, [_server()], karte)
    assert (offen[0].zuordnung.von, offen[0].zuordnung.nach) == ("/data", "/media")


# ---------------------------------------------------------------------------
# Bestehende pruefen
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bestehende_verbindung_wird_als_gut_gemeldet():
    """⚠️ Auch das Gelungene gehoert in die Antwort.

    Wer nur Fehler zurueckgibt, zwingt die Oberflaeche dazu, ausschliesslich
    Luecken zu zeigen - und dann sieht eine halb verbundene Instanz aus, als
    waere gar nichts eingerichtet.
    """
    arr = GespieltesArr(vorhandene=[_eintrag("MediaBrowser", "10.0.0.1", 8096)])
    ergebnis = await mv.bestehende_pruefen(arr, [_server()])
    assert ergebnis == [("jellyfin", "")]


@pytest.mark.anyio
async def test_tote_verbindung_wird_gemeldet():
    arr = GespieltesArr(
        vorhandene=[_eintrag("MediaBrowser", "10.0.0.1", 8096)], probe_scheitert=True
    )
    ergebnis = await mv.bestehende_pruefen(arr, [_server()])
    assert ergebnis == [("jellyfin", "unreachable")]


@pytest.mark.anyio
async def test_die_probe_traegt_die_nummer_des_eintrags():
    """⚠️ Ohne ``id`` haelt die Instanz den Eintrag fuer neu.

    Dann stoesst sie sich am eigenen bestehenden Namen ("Should be unique") und
    antwortet mit einem Fehler - und Nexview meldete daraufhin funktionierende
    Verbindungen als kaputt. Genau so ist es passiert.
    """
    arr = GespieltesArr(vorhandene=[_eintrag("MediaBrowser", "10.0.0.1", 8096)])
    await mv.bestehende_pruefen(arr, [_server()])
    assert arr.geprueft[0]["id"] == 7
    assert arr.geprueft[0]["name"] == "Nexview: X"


@pytest.mark.anyio
async def test_wo_nichts_steht_wird_nichts_geprueft():
    arr = GespieltesArr(vorhandene=[])
    assert await mv.bestehende_pruefen(arr, [_server()]) == []


@pytest.mark.anyio
async def test_verbindung_ohne_schluessel_gilt_als_kaputt():
    """Der Schluessel wurde entfernt - die Verbindung steht noch, taugt aber nichts."""
    arr = GespieltesArr(vorhandene=[_eintrag("MediaBrowser", "10.0.0.1", 8096)])
    ergebnis = await mv.bestehende_pruefen(arr, [_server(zugang="")])
    assert ergebnis == [("jellyfin", "kein_schluessel")]


# ---------------------------------------------------------------------------
# Zuordnungen ermitteln
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ohne_stammordner_bleibt_die_zuordnung_offen(monkeypatch):
    """⚠️ "Weiss ich nicht" darf nicht als "nichts noetig" durchgehen."""

    async def keine_pfade(*_a, **_k):
        return pz.Serverpfade(pfade=["/media/Movies"])

    monkeypatch.setattr(mv, "server_pfade", keine_pfade)
    arr = GespieltesArr(wurzeln=[])
    karte = await mv.zuordnungen(arr, [_server()])
    assert karte["jellyfinhttp://10.0.0.1:8096"].hindernis == "keine_wurzeln"


@pytest.mark.anyio
async def test_stummer_medienserver_wird_durchgereicht(monkeypatch):
    async def stumm(*_a, **_k):
        return pz.Serverpfade(hindernis="unreachable")

    monkeypatch.setattr(mv, "server_pfade", stumm)
    karte = await mv.zuordnungen(GespieltesArr(), [_server()])
    assert karte["jellyfinhttp://10.0.0.1:8096"].hindernis == "unreachable"


@pytest.mark.anyio
async def test_die_zuordnung_entsteht_aus_beiden_seiten(monkeypatch):
    async def pfade(*_a, **_k):
        return pz.Serverpfade(pfade=["/media/Movies", "/media/TV-Shows"])

    monkeypatch.setattr(mv, "server_pfade", pfade)
    arr = GespieltesArr(wurzeln=[{"path": "/data/Movies"}])
    karte = await mv.zuordnungen(arr, [_server()])
    zuordnung = karte["jellyfinhttp://10.0.0.1:8096"]
    assert (zuordnung.von, zuordnung.nach) == ("/data", "/media")


@pytest.mark.anyio
async def test_stumme_instanz_kippt_die_ermittlung_nicht(monkeypatch):
    """Antwortet Radarr nicht, bleibt die Zuordnung offen statt zu werfen."""

    async def pfade(*_a, **_k):
        return pz.Serverpfade(pfade=["/media/Movies"])

    monkeypatch.setattr(mv, "server_pfade", pfade)

    class Stumm(GespieltesArr):
        async def root_folders(self):
            raise ArrError("weg", code="arr_unreachable")

    karte = await mv.zuordnungen(Stumm(), [_server()])
    assert karte["jellyfinhttp://10.0.0.1:8096"].hindernis == "keine_wurzeln"
