"""Der Abdruck des Schemas: Was sich am Modell aendert, taucht im Diff auf.

⚠️ **Warum es diese Datei gibt.** Nexview fuehrt keine gezaehlten Wanderungen.
Der Startweg vergleicht die Modelle mit der vorhandenen Datei und ergaenzt, was
fehlt: ``_add_missing_columns``, ``_add_missing_indexes``. Das deckt den
haeufigsten Fall ab, eine Spalte kommt dazu, und ist mit rund vierzig Tests in
``test_migration.py`` belegt.

Es deckt **Umbenennung, Typwechsel und Wegfall nicht ab**, und es sagte das
bisher auch nicht. Nachgestellt am 02.09.2026: Im Modell aus ``display_name``
ein ``anzeigename`` gemacht, dann gestartet wie bei einem Update.

    Spalten vorher : ['username', 'display_name', 'mediaserver_username']
    Wert vorher    : [('Anzeigename',)]
    Anzeigename nach dem Update: None
    Spalten nachher: [..., 'anzeigename']
    Werte nachher  : [('Anzeigename', None)]

Kein Fehler, keine Warnung, kein Eintrag im Protokoll. Die Daten standen in
einer Spalte, die niemand mehr las. Und die Testreihe konnte es nicht sehen,
weil jeder Test auf einer frischen Datenbank beginnt: Mit der Umbenennung im
Modell liefen alle 2.491 Tests mit Rueckgabecode 0 durch.

**Zwei Haelften, und beide braucht es:**

* Hier die Golddatei. Sie haelt fest, welche Spalten mit welchem Typ es gibt.
  Eine Umbenennung ist damit **eine Zeile weniger und eine Zeile mehr im
  Diff**, und wer sie sieht, weiss, dass er die Werte mitnehmen muss.
* In ``db._verwaiste_spalten_melden`` die andere Haelfte, fuer die
  Installationen, die es schon getroffen hat: Der Start sagt, welche Spalte die
  Datenbank noch hat und das Modell nicht mehr.

⚠️ **Wird dieser Test rot, ist die Antwort nicht, den Abdruck neu zu
schreiben.** Erst kommt die Frage, ob die Aenderung einen Umzug der Daten
braucht. Ein Abdruck, der bei jeder Meldung nachgezogen wird, misst nichts
mehr - dieselbe Regel wie bei den beiden Waagen.

Neu erzeugen, wenn die Aenderung geklaert ist:

    python -m pytest tests/test_schema_abdruck.py --abdruck-schreiben
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.schema import CreateTable

from app.db import engine
from app.models import Base

ABDRUCK = Path(__file__).with_name("schema_abdruck.json")

#: So viele Tabellen hat das Schema mindestens.
#:
#: ⚠️ **Ohne diese Schwelle waere der Test still gruen, sobald die Ableitung
#: leer laeuft** - etwa weil ``Base.metadata`` zum falschen Zeitpunkt gelesen
#: wird und noch kein Modell importiert ist. Ein Abdruck von nichts stimmt
#: immer mit einem Abdruck von nichts ueberein.
MINDESTENS_TABELLEN = 35

#: Und so viele Spalten insgesamt. Am 02.09.2026: 42 Tabellen, 448 Spalten.
MINDESTENS_SPALTEN = 350


def _abdruck() -> dict[str, dict[str, object]]:
    """Das Schema, wie die Modelle es erzeugen.

    Aufgenommen wird, was ein Update **nicht** von selbst hinbekommt: Name,
    Typ, ob eine Spalte leer sein darf, und der Vorgabewert. Die Reihenfolge
    der Spalten steht bewusst nicht drin - sie ist in SQLite ohne Bedeutung und
    haette den Abdruck bei jeder Umsortierung im Modell rot gemacht.
    """
    schema: dict[str, dict[str, object]] = {}
    for tabelle in sorted(Base.metadata.sorted_tables, key=lambda t: t.name):
        spalten = {}
        for spalte in sorted(tabelle.columns, key=lambda s: s.name):
            spalten[spalte.name] = {
                "typ": str(spalte.type.compile(dialect=engine.dialect)),
                "leer_erlaubt": bool(spalte.nullable),
                "schluessel": bool(spalte.primary_key),
            }
        schema[tabelle.name] = {
            "spalten": spalten,
            "indizes": sorted(index.name or "" for index in tabelle.indexes),
            "eindeutig": sorted(
                sorted(spalte.name for spalte in zwang.columns)
                for zwang in tabelle.constraints
                if zwang.__class__.__name__ == "UniqueConstraint"
            ),
        }
    return schema


def test_der_abdruck_misst_wirklich_etwas() -> None:
    """Die Bodenschwelle unter allem anderen."""
    schema = _abdruck()
    spalten = sum(len(t["spalten"]) for t in schema.values())  # type: ignore[arg-type]
    assert len(schema) >= MINDESTENS_TABELLEN, (
        f"Nur {len(schema)} Tabellen im Abdruck, erwartet mindestens "
        f"{MINDESTENS_TABELLEN}. Sind die Modelle überhaupt importiert?"
    )
    assert spalten >= MINDESTENS_SPALTEN, (
        f"Nur {spalten} Spalten im Abdruck, erwartet mindestens {MINDESTENS_SPALTEN}."
    )


def test_das_schema_passt_zum_abdruck(request: pytest.FixtureRequest) -> None:
    """Jede Aenderung am Schema steht im Diff, nicht in einer Fehlermeldung."""
    schema = _abdruck()

    if request.config.getoption("--abdruck-schreiben", default=False):
        ABDRUCK.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip("Abdruck neu geschrieben.")

    assert ABDRUCK.exists(), (
        f"{ABDRUCK.name} fehlt. Neu erzeugen mit "
        "`python -m pytest tests/test_schema_abdruck.py --abdruck-schreiben`."
    )
    alt = json.loads(ABDRUCK.read_text(encoding="utf-8"))

    verschwunden = sorted(set(alt) - set(schema))
    dazu = sorted(set(schema) - set(alt))
    meldungen: list[str] = []
    if verschwunden:
        meldungen.append(f"Tabellen weg: {verschwunden}")
    if dazu:
        meldungen.append(f"Tabellen neu: {dazu}")

    for name in sorted(set(alt) & set(schema)):
        alte = alt[name]["spalten"]
        neue = schema[name]["spalten"]
        weg = sorted(set(alte) - set(neue))
        neu = sorted(set(neue) - set(alte))
        if weg or neu:
            meldungen.append(f"{name}: Spalten weg {weg}, neu {neu}")
        for spalte in sorted(set(alte) & set(neue)):
            if alte[spalte] != neue[spalte]:
                meldungen.append(f"{name}.{spalte}: {alte[spalte]} -> {neue[spalte]}")
        if alt[name]["indizes"] != schema[name]["indizes"]:
            meldungen.append(
                f"{name}: Indizes {alt[name]['indizes']} -> {schema[name]['indizes']}"
            )
        if alt[name]["eindeutig"] != schema[name]["eindeutig"]:
            meldungen.append(
                f"{name}: Eindeutigkeit {alt[name]['eindeutig']} -> "
                f"{schema[name]['eindeutig']}"
            )

    assert not meldungen, (
        "Das Schema weicht vom Abdruck ab:\n  "
        + "\n  ".join(meldungen)
        + "\n\n⚠️ Bevor du den Abdruck neu schreibst: Braucht diese Änderung einen "
        "Umzug der Daten? Eine weggefallene und eine neue Spalte in derselben Tabelle "
        "ist meistens eine Umbenennung, und die nimmt der Startweg NICHT mit - er legt "
        "die neue leer an und lässt die Werte in der alten stehen. Ist das geklärt: "
        "`python -m pytest tests/test_schema_abdruck.py --abdruck-schreiben`."
    )


def test_jede_tabelle_laesst_sich_wirklich_anlegen() -> None:
    """Gegenprobe: Der Abdruck beschreibt ein Schema, das SQLite auch baut.

    ⚠️ Ohne sie koennte der Abdruck ein Modell festhalten, das gar nicht
    uebersetzbar ist - und der Waechter waere ein Abgleich zweier Papiere.
    """
    for tabelle in Base.metadata.sorted_tables:
        anweisung = str(CreateTable(tabelle).compile(dialect=engine.dialect)).strip()
        assert anweisung.upper().startswith("CREATE TABLE")
        assert tabelle.name in anweisung


# ---------------------------------------------------------------------------
# Die andere Haelfte: was tut der Startweg mit einer verwaisten Spalte?
# ---------------------------------------------------------------------------


@pytest.fixture()
def eigene_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``init_db()`` auf eine frische Datenbank in einem eigenen Verzeichnis.

    Dieselbe Bauart wie ``alte_installation`` in ``test_migration.py``: Ohne
    das Umbiegen liefe der Start gegen die Datenbank der laufenden Testreihe.
    """
    from sqlalchemy import create_engine

    from app import db as db_modul

    verzeichnis = tmp_path / "data"
    verzeichnis.mkdir()
    pfad = verzeichnis / "nexview.db"
    eigener = create_engine(
        f"sqlite:///{pfad}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(db_modul, "engine", eigener)
    monkeypatch.setattr(db_modul._settings, "data_dir", verzeichnis)
    db_modul.init_db()
    yield db_modul, pfad
    eigener.dispose()


def test_eine_verwaiste_spalte_wird_gemeldet(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """Der Fall nach einer Umbenennung: Die alte Spalte steht noch da.

    ⚠️ Repariert wird nichts, und das ist Absicht - eine Spalte automatisch zu
    loeschen waere genau der Griff, der Daten kostet. Gesagt werden muss es
    aber, sonst merkt es niemand.
    """
    db_modul, pfad = eigene_installation
    with db_modul.engine.begin() as verbindung:
        verbindung.exec_driver_sql("ALTER TABLE users ADD COLUMN anzeigename VARCHAR(120)")

    with caplog.at_level("WARNING"):
        db_modul.init_db()

    meldungen = [satz.getMessage() for satz in caplog.records if satz.levelname == "WARNING"]
    passend = [m for m in meldungen if "users.anzeigename" in m]
    assert passend, f"Keine Warnung über die verwaiste Spalte. Gesehen: {meldungen}"
    assert "Nothing was changed" in passend[0]

    # Und sie steht wirklich noch da - gemeldet heisst nicht geloescht.
    with db_modul.engine.connect() as verbindung:
        assert "anzeigename" in db_modul._existing_columns(verbindung, "users")


def test_ohne_verwaiste_spalte_schweigt_der_start(
    eigene_installation, caplog: pytest.LogCaptureFixture
) -> None:
    """Die Gegenprobe: Der Normalfall darf nicht warnen.

    ⚠️ **Ohne sie waere die Warnung wertlos.** Eine, die bei jedem Start kommt,
    liest nach der zweiten Woche niemand mehr - und nimmt die echte Meldung
    mit, wenn sie einmal berechtigt ist.
    """
    db_modul, _ = eigene_installation
    with caplog.at_level("WARNING"):
        db_modul.init_db()

    verwaist = [
        satz.getMessage()
        for satz in caplog.records
        if "not in the model any more" in satz.getMessage()
    ]
    assert not verwaist, f"Warnung ohne Anlass: {verwaist}"
