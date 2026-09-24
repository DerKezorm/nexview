"""nexcrates ``/api/v1`` als Attrappe auf HTTP-Ebene.

Nach den Antworten einer echten Wegwerf-nexcrate 0.1.0 (Vertrag ``major 1``,
Stand ``V5``), gemessen am 22.09.2026; das Protokoll der Messung steht in
``homelab/nexcrate-pruefstand/nexview-anbindung/messung-nexview.md``.

Sie haengt unter ``client.use_transport``, also laeuft der **echte** Client
darueber: Kopfzeilen, beide Fehlerformen, die Stapelgrenzen, das Format des
Ereignisstroms. Eine Attrappe auf Methodenebene wuerde genau das nicht
pruefen.

Was hier steht, ist gemessen - nicht aus dem Plan abgeschrieben. Wo etwas
nachgestellt ist, weil es sich ohne Indexer und Download-Programm nicht
erzeugen liess (Warteschlange, Probleme, Papierkorb), steht es dabei; die
Felder stammen dann aus nexcrates eigenen Vertragstests
(``nexcrate/backend/tests/test_api_v3.py``).

Am 24.09.2026 Feld fuer Feld gegen nexcrates Modelle in ``39dfc05``
abgeglichen (``routers/v1.py``, ``v1_round.py``, ``v1_write.py``,
``v1_back.py``, ``v1_pairing.py``): Jede Antwort traegt dieselben Schluessel
wie das Modell dort, ``null`` eingeschlossen. Was aelter ist, steht als
Schalter daneben (``imported_at=FEHLT``, ``files=FEHLT``, ``liste_staffeln``,
``stapel_nur_imdb``, ``ausserhalb_aufloesen``).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

HOST = "nexcrate.test"
URL = f"http://{HOST}:8391"
KEY = "nxv_test_key_only_for_tests_aaaaaaaaaaaaaaaaaaaaaaaa"

#: Steht fuer "das Feld fehlt ganz", im Unterschied zu ``None`` (``null``).
FEHLT: Any = object()

#: Was Liste und ``lookup`` unter ``series.seasons`` zeigen (``liste_staffeln``).
#: Seit nexcrate 39dfc05 dieselben Staffeln wie die Einzelansicht; davor stand
#: dort ``null`` (gemessen). ``fehlt`` laesst das Feld ganz weg.
STAFFELN_WIE_EINZELANSICHT = "wie_einzelansicht"
STAFFELN_NULL = "null"
STAFFELN_FEHLT = "fehlt"

#: Die vier Fassungen des Pruefstands, wie ``GET /versions`` sie nannte.
FILM_HD = "v_6a0763e8"
FILM_UHD = "v_b4272077"
SERIE_HD = "v_96e766c4"
SERIE_UHD = "v_66260bea"

#: Die Saetze, die nexcrate zu den Wertungen mitschickt (``services/ratings.py``).
IMDB_NENNUNG = "Information courtesy of IMDb (https://www.imdb.com). Used with permission."
OMDB_NENNUNG = (
    "Rotten Tomatoes and Metacritic through the OMDb API (https://www.omdbapi.com), CC BY-NC 4.0."
)

#: Nexcrates Zustaende, wie ``GET /states`` sie fuehrt.
ZUSTAENDE = [
    ("problem", "A download is stuck or failed and waits for the owner."),
    ("downloading", "A download for it is running."),
    ("incomplete", "Files are there, but tracks of the album are missing. Albums only."),
    ("upgrade", "A file is there, and the profile would still take a better one."),
    ("available", "A file is there and good enough."),
    ("wanted", "Watched and without a file: nexcrate looks for it."),
    ("unmonitored", "Without a file, and nexcrate leaves it alone."),
]


def _fehler(status: int, code: str, message: str = "", **params: Any) -> httpx.Response:
    """Die flache Fehlerform unter ``/api/v1``."""
    return httpx.Response(status, json={"code": code, "message": message or code, "params": params})


def _fehler_aussen(status: int, code: str, message: str = "") -> httpx.Response:
    """Die Form der Oberflaeche - eine vertippte Adresse antwortet so."""
    return httpx.Response(status, json={"detail": {"code": code, "message": message or code}})


class FakeNexcrate:
    """Eine nexcrate, die nur im Speicher steht."""

    def __init__(self) -> None:
        self.installation_id = "mv77y2nj5lqkwsdi"
        self.version = "0.1.0"
        self.stage = "V5"
        self.key = KEY
        self.scopes = ["read", "request", "operate"]
        self.web_url: str | None = None
        self.anime = True
        self.stream = True
        self.calendar = True
        self.wishes_search_at_once = True
        #: Die Fassungen, wie ``/versions`` sie liefert.
        self.versions: list[dict[str, Any]] = [
            self._version(FILM_HD, "movie", "Movies", 1, "hd"),
            self._version(FILM_UHD, "movie", "Movies 4K", 2, "uhd"),
            self._version(SERIE_HD, "series", "Series", 1, "hd"),
            self._version(SERIE_UHD, "series", "Series 4K", 2, "uhd"),
        ]
        #: Die Titel, nach ``(kind, ref)``. Aufgebaut mit ``film`` und ``serie``.
        self.titles: dict[tuple[str, str], dict[str, Any]] = {}
        #: Die Staffeln je Serie: ``(ref, nummer) -> Antwort von /seasons/{n}``.
        self.seasons: dict[tuple[str, int], dict[str, Any]] = {}
        #: Wie Liste und ``lookup`` die Staffeln einer Serie zeigen. Ohne
        #: Angabe wie nexcrate 39dfc05; ``STAFFELN_NULL`` ist die aeltere
        #: nexcrate, bei der nur die Einzelansicht sie nennt.
        self.liste_staffeln = STAFFELN_WIE_EINZELANSICHT
        #: Serien (``ref``), fuer die Liste und ``lookup`` ``series.seasons``
        #: als ``null`` zeigen, gleich was ``liste_staffeln`` sagt: ein Titel mit
        #: Staffeln neben einem ohne in derselben Antwort.
        self.staffeln_null_fuer: set[str] = set()
        self.removed: list[dict[str, Any]] = []
        self.seq = 0
        self.health: list[dict[str, Any]] = []
        self.storage: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []
        self.recycle: list[dict[str, Any]] = []
        self.history: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.why: dict[tuple[str, str], dict[str, Any]] = {}
        self.calendar_items: list[dict[str, Any]] = []
        #: Eingerichtete Wertungen je ``ref``, gesetzt mit ``wertung``. Ohne
        #: Eintrag antwortet die Attrappe wie gemessen: alles ``null``.
        self.wertungen: dict[str, dict[str, Any]] = {}
        #: Hat der Betreiber einen OMDb-Schluessel hinterlegt? Ohne ihn nennt
        #: der Stapel ``omdb: no_key`` und keine Portale.
        self.omdb_schluessel = True
        #: Der Stapel wie vor nexcrate 39dfc05: nur IMDb, ohne
        #: ``rotten_tomatoes``, ``metacritic``, ``sources`` und ``omdb_attribution``.
        self.stapel_nur_imdb = False
        #: Findet nexcrate die IMDb-Nummer eines ``tmdb:``-Titels, den es nicht
        #: fuehrt, bei TMDB (39dfc05, ``_resolve_outside``)? ``False`` ist eine
        #: aeltere nexcrate oder eine ohne TMDB-Token: dann ``imdb_unknown``.
        self.ausserhalb_aufloesen = True
        self.events: list[dict[str, Any]] = []
        self.pairings: dict[str, dict[str, Any]] = {}
        self.update: dict[str, Any] = {
            "current": self.version,
            "latest": None,
            "available": None,
            "checked_at": None,
        }
        #: Naechste Antwort je ``"METHODE /pfad"`` vorgeben - einmal verbraucht.
        self.next_answer: dict[str, httpx.Response | Exception] = {}
        #: Jeder Aufruf: ``(METHODE, Pfad, Abfrage, Koerper)``.
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        #: Die Kopfzeilen des letzten Aufrufs.
        self.kopfzeilen: Any = {}

    # ---- Aufbau fuer Tests --------------------------------------------------

    @staticmethod
    def _version(
        kennung: str, kind: str, name: str, order: int, tier: str | None, *, ready: bool = True
    ) -> dict[str, Any]:
        return {
            "version_id": kennung,
            "kind": kind,
            "name": name,
            "order": order,
            "tier": tier,
            "ready": ready,
            "reasons": [] if ready else [{"code": "no_profile", "params": {}}],
        }

    def wertung(
        self,
        tmdb_id: int,
        *,
        imdb: float | None = None,
        stimmen: int = 0,
        tomaten: int | None = None,
        metacritic: int | None = None,
        im_speicher: bool = True,
    ) -> None:
        """Wertungen fuer einen Titel, als haette der Betreiber IMDb und OMDb eingerichtet.

        Die Formen stehen so in nexcrates OpenAPI (``ImdbRatingOut`` traegt
        ``rating`` und ``votes``, ``RatingOut`` zusaetzlich die Portale); gemessen
        wurde nur der Fall ohne Quelle.

        ``im_speicher=False``: OMDb wurde fuer den Titel noch nie gefragt. Die
        Einzelansicht fragt dann OMDb, der Stapel nennt ``not_cached``
        (nexcrate 39dfc05, ``ratings.omdb_cached_many``).
        """
        self.wertungen[f"tmdb:{tmdb_id}"] = {
            "imdb_ref": f"imdb:tt{tmdb_id:07d}",
            "imdb": {"rating": imdb, "votes": stimmen} if imdb is not None else None,
            "rotten_tomatoes": tomaten,
            "metacritic": metacritic,
            "im_speicher": im_speicher,
        }

    def nicht_bereit(self, kennung: str, *codes: str) -> None:
        """Eine Fassung auf "nicht bereit" setzen, mit Gruenden."""
        for eintrag in self.versions:
            if eintrag["version_id"] == kennung:
                eintrag["ready"] = False
                eintrag["reasons"] = [{"code": code, "params": {}} for code in codes]

    def _touch(self) -> int:
        self.seq += 1
        return self.seq

    def film(
        self,
        tmdb_id: int = 603,
        *,
        name: str = "Example Movie",
        year: int = 1999,
        versionen: list[dict[str, Any]] | None = None,
        origin: str | None = None,
        imdb: str | None = None,
    ) -> dict[str, Any]:
        """Einen Film anlegen - die Form von ``GET /titles?kind=movie``."""
        kennzeichen = imdb or f"tt{tmdb_id:07d}"
        titel = {
            "kind": "movie",
            "ref": f"tmdb:{tmdb_id}",
            "refs": [f"tmdb:{tmdb_id}", f"imdb:{kennzeichen}"],
            "name": name,
            "year": year,
            "poster_path": "/example.jpg",
            "origin": origin,
            "monitored": True,
            "versions": versionen
            if versionen is not None
            else [self.fassung(FILM_HD, "available", size_bytes=8_000_000_000)],
            "tags": [],
            "seq": self._touch(),
        }
        self.titles[("movie", titel["ref"])] = titel
        return titel

    def serie(
        self,
        tmdb_id: int = 1399,
        *,
        name: str = "Example Show",
        year: int = 2011,
        versionen: list[dict[str, Any]] | None = None,
        typ: str = "standard",
        tvdb: int | None = 121361,
        staffeln: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Eine Serie anlegen. ``staffeln`` ist die Liste aus der Einzelansicht."""
        refs = [f"tmdb:{tmdb_id}"]
        if tvdb:
            refs.append(f"tvdb:{tvdb}")
        refs.append("imdb:tt0944947")
        titel = {
            "kind": "series",
            "ref": f"tmdb:{tmdb_id}",
            "refs": refs,
            "name": name,
            "year": year,
            "poster_path": None,
            "origin": None,
            "monitored": True,
            "versions": versionen
            if versionen is not None
            else [
                self.fassung(
                    SERIE_HD,
                    "wanted",
                    size_bytes=3_000_000_000,
                    series={"counts": {"have": 2, "aired": 3, "expected": 4}},
                )
            ],
            "tags": [],
            "series": {
                "type": typ,
                "status": "Returning Series",
                "next_air_date": "2099-01-01",
                # Liste und ``lookup`` zeigen sie je nach ``liste_staffeln``;
                # eine aeltere nexcrate nennt sie nur in der Einzelansicht.
                "seasons": staffeln,
            },
            "seq": self._touch(),
        }
        self.titles[("series", titel["ref"])] = titel
        return titel

    @staticmethod
    def fassung(
        kennung: str,
        state: str = "wanted",
        *,
        monitored: bool = True,
        size_bytes: int | None = None,
        quality: str | None = None,
        origin: str | None = None,
        series: dict[str, Any] | None = None,
        imported_at: Any = None,
    ) -> dict[str, Any]:
        """Eine Fassung an einem Titel (``TitleVersionOut``)."""
        eintrag: dict[str, Any] = {
            "version_id": kennung,
            "state": state,
            "monitored": monitored,
            "size_bytes": size_bytes,
            "quality": quality,
            "origin": origin,
        }
        # Seit nexcrate 39dfc05 an jeder Fassung, auch als ``null``: pydantic
        # schreibt das Feld immer. ``FEHLT`` ist eine aeltere nexcrate.
        if imported_at is not FEHLT:
            eintrag["imported_at"] = imported_at
        if series is not None:
            eintrag["series"] = series
        return eintrag

    @staticmethod
    def staffel_fassung(
        kennung: str,
        state: str = "wanted",
        *,
        monitored: bool = True,
        counts: dict[str, int] | None = None,
        size_bytes: int | None = None,
        imported_at: Any = None,
    ) -> dict[str, Any]:
        """Eine Fassung an einer Staffel unter ``series.seasons`` (``SeasonVersionOut``).

        ``imported_at`` wie bei ``fassung``: die aelteste Datei der Staffel,
        ``null`` ohne Wissen, ``FEHLT`` fuer eine aeltere nexcrate.
        """
        eintrag: dict[str, Any] = {
            "version_id": kennung,
            "state": state,
            "monitored": monitored,
            "counts": counts if counts is not None else {"have": 0, "aired": 0, "expected": 0},
            "size_bytes": size_bytes,
        }
        if imported_at is not FEHLT:
            eintrag["imported_at"] = imported_at
        return eintrag

    @staticmethod
    def staffel_eintrag(
        nummer: int,
        versionen: list[dict[str, Any]] | None = None,
        *,
        name: str | None = None,
        air_date: str | None = "2020-01-01",
        folgen: int = 0,
        gesendet: int = 0,
    ) -> dict[str, Any]:
        """Eine Staffel unter ``series.seasons`` (``SeasonOut``).

        ``folgen`` und ``gesendet`` sind nexcrates ``episodes`` und ``aired``:
        Zahlen, keine Listen (die Folgen selbst nennt ``/seasons/{n}``).
        """
        return {
            "season": nummer,
            "name": name if name is not None else f"Season {nummer}",
            "air_date": air_date,
            "episodes": folgen,
            "aired": gesendet,
            "versions": versionen or [],
        }

    def staffel(self, ref: str, nummer: int, folgen: list[dict[str, Any]]) -> None:
        self.seasons[(ref, nummer)] = {
            "season": nummer,
            "name": f"Season {nummer}",
            "episodes": folgen,
        }

    @staticmethod
    def folge(
        nummer: int,
        *,
        name: str | None = None,
        air_date: str | None = "2020-01-01",
        aired: bool = True,
        versionen: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "episode": nummer,
            "name": name or f"Episode {nummer}",
            "air_date": air_date,
            "aired": aired,
            "versions": versionen or [],
        }

    @staticmethod
    def folgen_fassung(
        kennung: str,
        state: str = "wanted",
        *,
        monitored: bool = True,
        size_bytes: int | None = None,
        quality: str | None = None,
        files: Any = None,
    ) -> dict[str, Any]:
        """Eine Fassung an einer Folge (``EpisodeVersionOut``).

        Ohne ``origin`` und ohne ``imported_at``: beides nennt nexcrate nur am
        Titel und an der Staffel. ``files`` ist ohne Datei ``[]``, nie ``null``
        (nexcrate 5427612); ``FEHLT`` ist eine aeltere nexcrate.
        """
        eintrag: dict[str, Any] = {
            "version_id": kennung,
            "state": state,
            "monitored": monitored,
            "size_bytes": size_bytes,
            "quality": quality,
        }
        if files is not FEHLT:
            eintrag["files"] = list(files) if files is not None else []
        return eintrag

    def entfernt(self, kind: str, ref: str) -> None:
        """Einen Titel entfernen - er steht danach unter ``removed``."""
        self.titles.pop((kind, ref), None)
        self.removed.append({"kind": kind, "ref": ref, "seq": self._touch()})

    def ereignis(self, typ: str, **felder: Any) -> dict[str, Any]:
        eintrag = {
            "seq": self._touch(),
            "type": typ,
            "at": "2026-09-22T18:49:00.520760Z",
            "title": None,
            "version_id": None,
            "origin": None,
            "download_id": None,
            "params": {},
            **felder,
        }
        self.events.append(eintrag)
        return eintrag

    # ---- Transport ----------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != HOST:
            raise AssertionError(f"Die Attrappe kennt nur {HOST}, nicht {request.url.host}")
        pfad = request.url.path
        abfrage = dict(request.url.params)
        koerper = json.loads(request.content) if request.content else None
        self.calls.append((request.method, pfad, abfrage, koerper))
        self.kopfzeilen = request.headers

        schluessel = f"{request.method} {pfad}"
        vorgegeben = self.next_answer.pop(schluessel, None)
        if isinstance(vorgegeben, Exception):
            raise vorgegeben
        if vorgegeben is not None:
            return vorgegeben

        if not pfad.startswith("/api/v1"):
            return _fehler_aussen(404, "not_found", "Not Found")
        rest = pfad[len("/api/v1") :]

        # Der Schluessel reist nur in der Kopfzeile - und ohne ihn geht nichts
        # ausser dem Koppeln (die eine Tuer).
        if not rest.startswith("/pairing"):
            kopf = request.headers.get("authorization", "")
            if not kopf:
                return _fehler(401, "api_key_missing", "No key was sent.")
            if kopf != f"Bearer {self.key}":
                return _fehler(401, "api_key_invalid", "This key is not known.")

        return self._route(request.method, rest, abfrage, koerper)

    def _route(
        self, methode: str, rest: str, abfrage: dict[str, str], koerper: Any
    ) -> httpx.Response:
        teile = [t for t in rest.split("/") if t]
        ok = lambda daten: httpx.Response(200, json=daten)  # noqa: E731

        if rest == "/system":
            return ok(self._system())
        if rest == "/versions":
            kind = abfrage.get("kind")
            gewaehlt = [v for v in self.versions if not kind or v["kind"] == kind]
            return ok({"items": gewaehlt})
        if rest == "/states":
            return ok({"items": [{"state": s, "meaning": m} for s, m in ZUSTAENDE]})
        if rest == "/health":
            return ok({"items": self.health})
        if rest == "/storage":
            return ok({"items": self.storage})
        if rest == "/queue":
            return ok({"items": self._queue(abfrage.get("kind"))})
        if rest == "/problems":
            eintraege = [item for item in self._queue(abfrage.get("kind")) if item.get("problem")]
            eintraege.sort(key=lambda item: not item["problem"].get("needs_owner"))
            return ok({"items": eintraege})
        if rest == "/recycle-bin":
            return ok({"items": self.recycle})
        if rest == "/calendar":
            return self._calendar(abfrage)
        if rest == "/events":
            return ok(self._events(abfrage))
        if rest == "/events/stream":
            return self._stream(abfrage)
        if rest == "/titles":
            return ok(self._titles(abfrage))
        if rest == "/titles/lookup":
            return ok({"items": [self._lookup(item) for item in (koerper or {}).get("items", [])]})
        if rest == "/titles/why":
            return ok({"items": [self._why_stapel(item) for item in (koerper or {}).get("items", [])]})
        if rest == "/ratings":
            eintraege = (koerper or {}).get("items", [])
            # nexcrate 39dfc05: ``RatingsIn.items`` hat ``max_length`` 100
            # (``ratings.LOOKUP_MAX``), mehr ist die Pruefung von FastAPI.
            if len(eintraege) > 100:
                return _fehler(422, "invalid_input", "The input is not valid.", fields=["items"])
            return ok(self._ratings(eintraege))
        if rest == "/pairing" and methode == "POST":
            return self._pairing_ask(koerper or {})
        if teile[:1] == ["pairing"] and len(teile) == 2:
            return self._pairing_poll(teile[1])
        if teile[:1] == ["titles"] and len(teile) >= 3:
            return self._titel_weg(methode, teile, koerper)
        if teile[:1] == ["downloads"] and len(teile) >= 3:
            return self._download_weg(methode, teile, koerper)
        if teile[:1] == ["requests"] and methode == "POST":
            return self._request(koerper or {})
        if teile[:1] == ["recycle-bin"] and len(teile) == 3:
            return ok(self._zurueckgeholt(teile[1]))
        if teile[:1] == ["ratings"] and len(teile) == 3:
            if self._ohne_imdb_nummer(teile[1], teile[2]):
                return _fehler(
                    404,
                    "imdb_unknown",
                    "nexcrate knows no IMDb number for this title; ask by imdb:.",
                    ref=teile[2],
                )
            return ok(self._rating_einzeln(teile[1], teile[2]))
        return _fehler(404, "not_found", "This nexcrate does not know that address.")

    # ---- Die einzelnen Antworten -------------------------------------------

    def _system(self) -> dict[str, Any]:
        return {
            "app": "nexcrate",
            "version": self.version,
            "contract": {"major": 1, "stage": self.stage},
            "installation_id": self.installation_id,
            "scopes": self.scopes,
            "capabilities": {
                "movies": True,
                "series": True,
                "music": True,
                "calendar": self.calendar,
                "events": True,
                "stream": self.stream,
                "event_types": [
                    "title.added",
                    "title.changed",
                    "title.removed",
                    "version.added",
                    "version.removed",
                    "download.started",
                    "download.state",
                    "download.imported",
                    "download.failed",
                    "download.removed",
                    "problem.opened",
                    "problem.closed",
                    "file.deleted",
                    "file.restored",
                    "request.made",
                    "request.withdrawn",
                    "series.season_complete",
                    "version_definition.added",
                    "version_definition.changed",
                    "version_definition.removed",
                    "health.changed",
                    "source.taken_over",
                    "source.takeover_undone",
                ],
                "kinds": {
                    "movie": {"read": True, "request": True, "operate": True, "ratings": False},
                    "series": {"read": True, "request": True, "operate": True, "ratings": False},
                    "album": {"read": True, "request": True, "operate": True, "ratings": False},
                    "artist": {"read": True, "request": True, "operate": False, "ratings": False},
                },
                "wishes_search_at_once": self.wishes_search_at_once,
                "anime": self.anime,
            },
            "web_url": self.web_url,
            "links": {
                "title": "/open/title/{kind}/{ref}",
                "version": "/open/version/{version_id}",
                "profile": "/open/profile/{version_id}",
                "download": "/open/download/{download_id}",
                "problems": "/open/problems",
                "recycle_bin": "/open/recycle-bin",
                "calendar": "/open/calendar",
            },
            "update": self.update,
        }

    def _titles(self, abfrage: dict[str, str]) -> dict[str, Any]:
        kind = abfrage.get("kind") or "movie"
        after = int(abfrage.get("after") or 0)
        grenze = int(abfrage.get("limit") or 500)
        gefunden = sorted(
            (t for (art, _), t in self.titles.items() if art == kind and t["seq"] > after),
            key=lambda t: t["seq"],
        )
        gezeigt = gefunden[:grenze]
        weg = [e for e in self.removed if e["kind"] == kind and e["seq"] > after]
        letzte = max(
            [t["seq"] for t in gezeigt] + [e["seq"] for e in weg] + [after],
            default=after,
        )
        return {
            "items": [self._ohne_seq(t) | {"seq": t["seq"]} for t in gezeigt],
            "removed": weg,
            "next_after": letzte,
            "more": len(gefunden) > len(gezeigt),
            # ⚠️ ``after`` ueber ``latest`` antwortet still leer, nicht 410
            # (nexbeat-Befund 11, hier nachgemessen).
            "latest": self.seq,
        }

    def _ohne_seq(self, titel: dict[str, Any]) -> dict[str, Any]:
        """Ein Titel, wie Liste und ``lookup`` ihn zeigen.

        Seit nexcrate 39dfc05 mit denselben Staffeln wie die Einzelansicht;
        mit ``liste_staffeln`` auch wie eine aeltere nexcrate.
        """
        gezeigt = {k: v for k, v in titel.items() if k != "seq"}
        if gezeigt.get("kind") == "series" and self.liste_staffeln != STAFFELN_WIE_EINZELANSICHT:
            serie = dict(gezeigt["series"])
            if self.liste_staffeln == STAFFELN_FEHLT:
                serie.pop("seasons", None)
            else:
                serie["seasons"] = None
            gezeigt["series"] = serie
        elif gezeigt.get("kind") == "series" and gezeigt.get("ref") in self.staffeln_null_fuer:
            gezeigt["series"] = {**gezeigt["series"], "seasons": None}
        return gezeigt

    def _finden(self, kind: str, ref: str) -> dict[str, Any] | None:
        """Ein Titel ueber **irgendeine** seiner Kennungen.

        ⚠️ Serien lassen sich bei nexcrate auch ueber ``tvdb:`` fragen
        (``SOURCES``); der Anker bleibt TMDB, und ``refs`` nennt beide. Genau
        das braucht der Umstieg, um Nexviews TVDB-Schluessel zu uebersetzen.
        """
        gefunden = self.titles.get((kind, ref))
        if gefunden is not None:
            return gefunden
        for (art, _), titel in self.titles.items():
            if art == kind and ref in (titel.get("refs") or []):
                return titel
        return None

    def _lookup(self, eintrag: dict[str, Any]) -> dict[str, Any]:
        kind = str(eintrag.get("kind") or "")
        ref = str(eintrag.get("ref") or "")
        erlaubt = ("tmdb:", "imdb:") if kind == "movie" else ("tmdb:", "tvdb:", "imdb:")
        if not ref.startswith(erlaubt):
            return {"kind": kind, "ref": ref, "known": False, "title": None, "error": "ref_source_unknown"}
        titel = self._finden(kind, ref)
        return {
            "kind": kind,
            "ref": ref,
            "known": titel is not None,
            "title": self._ohne_seq(titel) if titel else None,
            "error": None,
        }

    def _titel_weg(self, methode: str, teile: list[str], koerper: Any) -> httpx.Response:
        # /titles/series/{ref}/seasons/{n}
        if teile[1] == "series" and len(teile) == 5 and teile[3] == "seasons":
            if ("series", teile[2]) not in self.titles:
                return _fehler(404, "title_not_found", "nexcrate does not have this title.")
            staffel = self.seasons.get((teile[2], int(teile[4])))
            if staffel is None:
                return _fehler(404, "season_not_found", "The series has no such season.")
            return httpx.Response(200, json=staffel)

        kind, ref = teile[1], teile[2]
        if ref != ref.lower() or not ref.startswith(("tmdb:", "imdb:", "tvdb:")):
            return _fehler(
                422,
                "ref_source_unknown",
                f"A {kind} cannot be asked by this source.",
                kind=kind,
                sources=["tmdb", "imdb"],
            )
        if kind not in ("movie", "series"):
            return _fehler(422, "kind_unsupported", f"This nexcrate does not answer for the kind {kind}.")
        titel = self._finden(kind, ref)
        if titel is None:
            return _fehler(404, "title_not_found", "nexcrate does not have this title.", kind=kind, ref=ref)

        if len(teile) == 3 and methode == "GET":
            return httpx.Response(200, json={k: v for k, v in titel.items() if k != "seq"})
        rest = teile[3] if len(teile) > 3 else ""
        if rest == "history":
            return httpx.Response(
                200, json={"items": self.history.get((kind, ref), []), "next_before": None}
            )
        if rest == "why":
            return httpx.Response(200, json=self._why(kind, ref))
        if rest == "search":
            return httpx.Response(202, json={"search": "queued"})
        if rest == "withdraw":
            # ``albums`` nennt nexcrate nur bei einem Kuenstler, sonst ``null``.
            return httpx.Response(200, json={"title_removed": False, "versions": [], "albums": None})
        if rest == "monitoring":
            # Nur die Schalter, und der Titel danach (``MonitoringOut``).
            return httpx.Response(
                200,
                json={
                    "versions": [
                        {"version_id": kennung, "changed": True}
                        for kennung in (koerper or {}).get("versions") or []
                    ],
                    "title": {k: v for k, v in titel.items() if k != "seq"},
                },
            )
        return _fehler(404, "not_found", "This nexcrate does not know that address.")

    def _why(self, kind: str, ref: str) -> dict[str, Any]:
        gemerkt = self.why.get((kind, ref))
        if gemerkt is not None:
            return gemerkt
        titel = self.titles.get((kind, ref)) or {}
        return {
            "title": {"kind": kind, "ref": ref, "name": titel.get("name")},
            "automatic": False,
            "search_wish": False,
            "last_search_at": None,
            "next_search_at": None,
            "next_search_reason": "nothing_wanted",
            "release": {"date": None, "kind": "year", "country": None},
            "versions": [
                {
                    "version_id": v["version_id"],
                    "state": v["state"],
                    "monitored": v["monitored"],
                    "because": {"code": v["state"], "params": {}},
                    "last_search": None,
                }
                for v in titel.get("versions", [])
            ],
        }

    def _why_stapel(self, eintrag: dict[str, Any]) -> dict[str, Any]:
        kind = str(eintrag.get("kind") or "")
        ref = str(eintrag.get("ref") or "")
        bekannt = (kind, ref) in self.titles
        return {
            "kind": kind,
            "ref": ref,
            "known": bekannt,
            "why": self._why(kind, ref) if bekannt else None,
            "error": None,
        }

    def _queue(self, kind: str | None) -> list[dict[str, Any]]:
        if kind and kind not in ("movie", "series", "album"):
            return []
        return [item for item in self.queue if not kind or item["title"]["kind"] == kind]

    def _download_weg(self, methode: str, teile: list[str], koerper: Any) -> httpx.Response:
        kennung = int(teile[1])
        vorhanden = next((item for item in self.queue if item["download_id"] == kennung), None)
        if vorhanden is None:
            return _fehler(404, "download_not_found", "nexcrate does not know that download.")
        if teile[2] == "files":
            return httpx.Response(200, json={"files": [], "series": {"episodes": []}})
        erlaubt = (vorhanden.get("problem") or {}).get("actions") or []
        aktion = teile[2].replace("-", "_")
        if aktion not in erlaubt and teile[2] not in erlaubt:
            return _fehler(409, "action_not_allowed", "This action is not offered here.")
        # ``ActOut``: der Download, wie er nach der Aktion steht.
        return httpx.Response(200, json={"download": vorhanden})

    def _request(self, koerper: dict[str, Any]) -> httpx.Response:
        kind = str(koerper.get("kind") or "")
        ref = str(koerper.get("ref") or "")
        titel = self.titles.get((kind, ref))
        if titel is None:
            return _fehler(404, "title_not_found", "nexcrate does not have this title.")
        gewuenscht = list(koerper.get("versions") or [])
        bekannt = {v["version_id"] for v in titel["versions"]}
        return httpx.Response(
            200,
            json={
                "created": any(k not in bekannt for k in gewuenscht),
                "versions": [
                    {"version_id": k, "outcome": "unchanged" if k in bekannt else "watched"}
                    for k in gewuenscht
                ],
                "albums_watched": None,
                "search": "queued" if koerper.get("search_now") else "not_asked",
                "notes": [],
                "title": {k: v for k, v in titel.items() if k != "seq"},
            },
        )

    def _zurueckgeholt(self, eintrag_id: str) -> dict[str, Any]:
        """``RestoredOut``: der Titel, zu dem die Datei zurueckkam.

        Steht er nicht in der Attrappe, bleiben nur ``kind`` und ``ref`` des
        Eintrags - eine echte nexcrate sagte dann ``recycle_title_gone``.
        """
        eintrag = next(
            (e for e in self.recycle if str(e.get("entry_id")) == eintrag_id), {}
        )
        quelle = eintrag.get("title") if isinstance(eintrag.get("title"), dict) else eintrag
        kind, ref = str(quelle.get("kind") or ""), str(quelle.get("ref") or "")
        titel = self._finden(kind, ref)
        if titel is None:
            return {"title": {"kind": kind, "ref": ref}}
        return {"title": {k: v for k, v in titel.items() if k != "seq"}}

    def _calendar(self, abfrage: dict[str, str]) -> httpx.Response:
        von, bis = abfrage.get("from", ""), abfrage.get("to", "")
        # Gemessen: mehr als 100 Tage am Stueck sind ``invalid_input``.
        if von and bis and (_tage(bis) - _tage(von)) > 100:
            return _fehler(422, "invalid_input", "The span covers at most 100 days.", fields=["to"])
        return httpx.Response(
            200,
            json={
                "from": von,
                "to": bis,
                "region": "",
                "items": [
                    item for item in self.calendar_items if von <= str(item.get("date")) <= bis
                ],
                "truncated": False,
            },
        )

    def _omdb_im_stapel(self, kind: Any, gesetzt: dict[str, Any] | None) -> tuple[str, Any, Any]:
        """Was der Stapel zu OMDb sagt: nur aus dem Speicher, nie OMDb selbst.

        Wie ``v1_round.ratings_many`` in nexcrate 39dfc05: Serien haben dort
        nichts, ohne Schluessel gibt es nichts, und was nie gefragt wurde, ist
        ``not_cached``.
        """
        if kind != "movie":
            return "not_for_kind", None, None
        if not self.omdb_schluessel:
            return "no_key", None, None
        if gesetzt is None or not gesetzt["im_speicher"]:
            return "not_cached", None, None
        tomaten, metacritic = gesetzt["rotten_tomatoes"], gesetzt["metacritic"]
        return ("ok" if tomaten is not None or metacritic is not None else "not_found"), tomaten, metacritic

    def _ohne_imdb_nummer(self, kind: Any, ref: Any) -> bool:
        """Kennt nexcrate fuer diesen Titel keine IMDb-Nummer (``imdb_unknown``)?

        Nur fuer einen ``tmdb:``-Titel, den es nicht fuehrt, und nur, wenn es
        ausserhalb nicht aufloest.
        """
        return (
            not self.ausserhalb_aufloesen
            and str(ref).startswith("tmdb:")
            and (str(kind), str(ref)) not in self.titles
        )

    def _ratings(self, eintraege: list[dict[str, Any]]) -> dict[str, Any]:
        items = []
        for eintrag in eintraege:
            unbekannt = self._ohne_imdb_nummer(eintrag.get("kind"), eintrag.get("ref"))
            gesetzt = None if unbekannt else self.wertungen.get(str(eintrag.get("ref")))
            zeile = {
                "kind": eintrag.get("kind"),
                "ref": eintrag.get("ref"),
                "imdb_ref": None if unbekannt else gesetzt["imdb_ref"] if gesetzt else eintrag.get("ref"),
                "imdb": gesetzt["imdb"] if gesetzt else None,
                "error": "imdb_unknown" if unbekannt else None,
            }
            if not self.stapel_nur_imdb:
                zustand, tomaten, metacritic = self._omdb_im_stapel(eintrag.get("kind"), gesetzt)
                zeile["rotten_tomatoes"] = tomaten
                zeile["metacritic"] = metacritic
                zeile["sources"] = {"imdb": "loaded" if self.wertungen else "off", "omdb": zustand}
            items.append(zeile)
        antwort: dict[str, Any] = {
            "items": items,
            "imdb": "loaded" if self.wertungen else "off",
            "attribution": IMDB_NENNUNG,
        }
        if not self.stapel_nur_imdb:
            gezeigt = any(
                zeile["rotten_tomatoes"] is not None or zeile["metacritic"] is not None
                for zeile in items
            )
            antwort["omdb_attribution"] = OMDB_NENNUNG if gezeigt else None
        return antwort

    def _rating_einzeln(self, kind: str, ref: str) -> dict[str, Any]:
        gesetzt = self.wertungen.get(ref)
        if gesetzt is not None:
            gesetzt = {k: v for k, v in gesetzt.items() if k != "im_speicher"}
        if gesetzt is None:
            return {
                "kind": kind,
                "ref": ref,
                "imdb_ref": None,
                "imdb": None,
                "rotten_tomatoes": None,
                "metacritic": None,
                "sources": {"imdb": "off", "omdb": "no_key"},
                "attribution": [IMDB_NENNUNG],
            }
        omdb = gesetzt["rotten_tomatoes"] is not None or gesetzt["metacritic"] is not None
        return {
            "kind": kind,
            "ref": ref,
            **gesetzt,
            "sources": {"imdb": "loaded", "omdb": "ok" if omdb else "not_found"},
            # nexcrate nennt OMDb nur, wenn von dort etwas kam (``v1_round.rating_one``).
            "attribution": [IMDB_NENNUNG] + ([OMDB_NENNUNG] if omdb else []),
        }

    def _events(self, abfrage: dict[str, str]) -> dict[str, Any]:
        after = int(abfrage.get("after") or 0)
        grenze = int(abfrage.get("limit") or 200)
        gefunden = [e for e in self.events if e["seq"] > after]
        gezeigt = gefunden[:grenze]
        return {
            "items": gezeigt,
            "next_after": gezeigt[-1]["seq"] if gezeigt else after,
            "more": len(gefunden) > len(gezeigt),
            "latest": self.seq,
        }

    def _stream(self, abfrage: dict[str, str]) -> httpx.Response:
        """Der Strom als Server-Sent Events - ``retry``, dann je Ereignis drei Zeilen."""
        after = int(abfrage.get("after") or 0)
        zeilen = ["retry: 3000", ""]
        for eintrag in (e for e in self.events if e["seq"] > after):
            zeilen += [
                f"id: {eintrag['seq']}",
                f"event: {eintrag['type']}",
                f"data: {json.dumps(eintrag)}",
                "",
            ]
        zeilen += [": keep-alive", ""]
        return httpx.Response(
            200, text="\n".join(zeilen), headers={"content-type": "text/event-stream"}
        )

    def _pairing_ask(self, koerper: dict[str, Any]) -> httpx.Response:
        if len(self.pairings) >= 5:
            return _fehler(429, "pairing_limit", "Too many open pairing requests.")
        kennung = f"pr_{len(self.pairings) + 1:04d}"
        self.pairings[kennung] = {
            "secret": f"geheim-{kennung}",
            "state": "pending",
            "app": koerper.get("app"),
            "scopes": koerper.get("scopes"),
            "key": f"{KEY}",
        }
        # ``201 Created`` wie nexcrate (``v1_pairing.py``).
        return httpx.Response(
            201,
            json={
                "pairing_id": kennung,
                "code": "5Z3-M4G",
                "secret": self.pairings[kennung]["secret"],
                "poll_seconds": 2,
                "expires_at": "2026-09-22T19:00:00Z",
            },
        )

    def bestaetigen(self, pairing_id: str) -> None:
        """Was der Betreiber in nexcrate taete."""
        self.pairings[pairing_id]["state"] = "confirmed"

    def _pairing_poll(self, pairing_id: str) -> httpx.Response:
        offen = self.pairings.get(pairing_id)
        # ⚠️ Ohne oder mit falschem Geheimnis sagt nexcrate ``404``, nicht
        # ``401``: Man erfaehrt nicht, ob es die Bitte gibt (nexbeat).
        if offen is None or self.kopfzeilen.get("x-pairing-secret") != offen["secret"]:
            return _fehler(404, "pairing_not_found", "There is no such pairing request.")
        zustand = offen["state"]
        # ``PairingStateOut``: ``scopes`` nur mit dem Schluessel, sonst ``null``.
        ablauf = "2026-09-22T19:00:00Z"
        if zustand != "confirmed":
            return httpx.Response(
                200, json={"state": zustand, "key": None, "scopes": None, "expires_at": ablauf}
            )
        # ⚠️ Der Schluessel kommt genau einmal; danach ``delivered`` ohne ihn.
        offen["state"] = "delivered"
        return httpx.Response(
            200,
            json={
                "state": "confirmed",
                "key": offen["key"],
                "scopes": offen["scopes"],
                "expires_at": ablauf,
            },
        )


def _tage(datum: str) -> int:
    from datetime import date

    jahr, monat, tag = (int(teil) for teil in datum.split("-"))
    return date(jahr, monat, tag).toordinal()
