"""Wem gehören Staffeln, die erst später erscheinen?

Nexview hat zwei Kontingente, und sie beantworten verschiedene Fragen:

* **Stückzahl** zählt Wünsche. Eine künftige Staffel ist kein Wunsch, den
  jemand geäußert hat, also kostet sie keinen Platz.
* **Speicher** zählt Bytes. Die sind da, sobald die Staffel geladen ist, und
  sie gehören dem, der gesagt hat "diese Serie soll weiterlaufen".

Vorher widersprach sich das: Wer die *ganze* Serie anfragte, bekam jede
spätere Staffel zugerechnet. Wer Staffel 1 anfragte und "auch künftige"
anhakte, bekam sie nicht - sie fielen dem Haus zu. Derselbe Wunsch, zwei
Antworten, je nachdem welchen Knopf jemand gedrückt hatte.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    MediaRequest,
    MediaType,
    QualityTier,
    RequestStatus,
    Role,
    User,
)
from app.security import hash_password
from app.services import storage
from app.services.storage import _Gemessen

TVDB = 479935


def _nutzer(db, name: str, rolle: Role = Role.user) -> User:
    person = User(
        username=name,
        email=f"{name}@beispiel.de",
        password_hash=hash_password("passwort-1234"),
        role=rolle,
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def _anfrage(
    db, nutzer: User, season: int | None, *, kuenftige: bool = False
) -> MediaRequest:
    eintrag = MediaRequest(
        user_id=nutzer.id,
        media_type=MediaType.tv,
        tier=QualityTier.standard,
        tmdb_id=331370,
        tvdb_id=TVDB,
        title="Eine Serie",
        season=season,
        monitor_future=kuenftige,
        status=RequestStatus.downloaded,
    )
    db.add(eintrag)
    db.commit()
    return eintrag


def _staffel(nummer: int) -> _Gemessen:
    return _Gemessen(
        key=f"tv:{TVDB}:standard:s{nummer}",
        media_type=MediaType.tv,
        tier=QualityTier.standard,
        tmdb_id=331370,
        tvdb_id=TVDB,
        season=nummer,
        title="Eine Serie",
        size_bytes=10 * 1024**3,
    )


def test_kuenftige_staffel_gehoert_dem_der_sie_zugesagt_hat() -> None:
    """⚠️ Der Kern: Der Haken ist die Zusage.

    Wer "auch künftige Staffeln" anhakt, sagt: Diese Serie soll weiterlaufen,
    und ich stehe dafür ein. Also trägt er sie auch.
    """
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=1, kuenftige=True)

        zuordnung = storage._zuordnung(db, [_staffel(1), _staffel(12)])

    assert zuordnung[f"tv:{TVDB}:standard:s1"] == kim.id
    assert zuordnung[f"tv:{TVDB}:standard:s12"] == kim.id


def test_ohne_haken_faellt_die_spaetere_staffel_dem_haus_zu() -> None:
    """Wer nur Staffel 1 wollte, hat für Staffel 12 nichts zugesagt."""
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=1, kuenftige=False)

        zuordnung = storage._zuordnung(db, [_staffel(1), _staffel(12)])

    assert zuordnung[f"tv:{TVDB}:standard:s1"] == kim.id
    assert f"tv:{TVDB}:standard:s12" not in zuordnung


def test_ganze_serie_traegt_weiterhin_alles() -> None:
    """Der Weg, der schon immer stimmte - er darf nicht kaputtgehen."""
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=None)

        zuordnung = storage._zuordnung(db, [_staffel(3), _staffel(12)])

    assert zuordnung[f"tv:{TVDB}:standard:s3"] == kim.id
    assert zuordnung[f"tv:{TVDB}:standard:s12"] == kim.id


def test_die_genaue_staffel_schlaegt_den_haken() -> None:
    """⚠️ Wer eine Staffel ausdrücklich angefragt hat, trägt sie - auch wenn
    jemand anderes bei derselben Serie den Haken gesetzt hat.

    Sonst zahlte der Zusager für die Wünsche der anderen mit."""
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        alex = _nutzer(db, "alex")
        _anfrage(db, kim, season=1, kuenftige=True)
        _anfrage(db, alex, season=3)

        zuordnung = storage._zuordnung(db, [_staffel(3), _staffel(12)])

    assert zuordnung[f"tv:{TVDB}:standard:s3"] == alex.id
    assert zuordnung[f"tv:{TVDB}:standard:s12"] == kim.id


def test_was_ein_administrator_zusagt_gehoert_dem_haus() -> None:
    """Dieselbe Regel wie überall sonst: Wer kuratiert, holt für alle."""
    with SessionLocal() as db:
        chef = _nutzer(db, "chefin", rolle=Role.admin)
        _anfrage(db, chef, season=1, kuenftige=True)

        zuordnung = storage._zuordnung(db, [_staffel(12)])

    assert zuordnung == {}


def test_eine_andere_serie_bleibt_unberuehrt() -> None:
    """Der Haken gilt für seine Serie, nicht für die Nachbarn."""
    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=1, kuenftige=True)

        fremd = _staffel(12)
        fremd = _Gemessen(
            key="tv:999999:standard:s12",
            media_type=fremd.media_type,
            tier=fremd.tier,
            tmdb_id=1,
            tvdb_id=999999,
            season=12,
            title="Fremde Serie",
            size_bytes=1,
        )
        zuordnung = storage._zuordnung(db, [fremd])

    assert zuordnung == {}


def test_kuenftige_staffel_kostet_keinen_anfrage_platz() -> None:
    """⚠️ Die Stückzahl zählt Wünsche, nicht Bytes.

    Eine später erscheinende Staffel ist kein Wunsch, den jemand geäußert
    hat - dafür einen Platz abzuziehen hieße, etwas zu berechnen, das beim
    Anfragen niemand beziffern konnte. Der Speicher trägt sie dafür.
    """
    from app.services import quota
    from app.services.settings_service import load_settings

    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=1, kuenftige=True)
        einstellungen = load_settings(db)

        stand = quota.state_for(db, kim, MediaType.tv, einstellungen)

    # Eine Anfrage, ein Platz - egal wie viele Staffeln daraus werden.
    assert stand.used == 1


# --- Die Regel gilt nach vorn, nicht rückwärts -------------------------------


def test_bereits_zugerechnete_posten_werden_nicht_umverteilt() -> None:
    """⚠️ Was gestern dem Haus zufiel, bleibt beim Haus.

    Die neue Regel ist eine Regel für **künftige** Staffeln, nicht eine
    Neubewertung der alten. Würde bei jedem Abgleich neu zugerechnet, wanderten
    Staffeln, die seit Monaten dem Haus gehören, plötzlich auf ein persönliches
    Konto - und jemand stünde ohne eigenes Zutun über seiner Grenze.

    Geschrieben wird die Zurechnung deshalb nur, wenn ein Posten **zum ersten
    Mal** auftaucht; danach werden nur noch Größe und Zeitstempel gepflegt.
    """
    from app.models import StorageEntry, StorageState

    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=1, kuenftige=True)

        # Staffel 12 liegt schon da und gehört dem Haus.
        alt = _staffel(12)
        db.add(
            StorageEntry(
                key=alt.key,
                user_id=None,
                media_type=alt.media_type,
                tier=alt.tier,
                tmdb_id=alt.tmdb_id,
                tvdb_id=alt.tvdb_id,
                season=alt.season,
                title=alt.title,
                size_bytes=alt.size_bytes,
                state=StorageState.house,
            )
        )
        db.commit()

        # Ein Abgleich, der denselben Posten wieder meldet.
        storage._schreiben(db, {alt.key: _staffel(12)})

        zeile = db.scalar(select(StorageEntry).where(StorageEntry.key == alt.key))
        assert zeile.user_id is None, "ein alter Posten wurde nachträglich umverteilt"


def test_neu_auftauchende_staffel_bekommt_die_neue_regel() -> None:
    """Die Gegenprobe: Was ab jetzt hereinkommt, folgt der Zusage."""
    from app.models import StorageEntry, StorageState

    with SessionLocal() as db:
        kim = _nutzer(db, "kim")
        _anfrage(db, kim, season=1, kuenftige=True)

        # Ein Posten muss schon existieren, sonst gilt der Lauf als der erste
        # und alles fiele ohnehin dem Haus zu.
        vorhanden = _staffel(1)
        db.add(
            StorageEntry(
                key=vorhanden.key,
                user_id=kim.id,
                media_type=vorhanden.media_type,
                tier=vorhanden.tier,
                tmdb_id=vorhanden.tmdb_id,
                tvdb_id=vorhanden.tvdb_id,
                season=1,
                title=vorhanden.title,
                size_bytes=vorhanden.size_bytes,
                state=StorageState.owned,
            )
        )
        db.commit()

        neu = _staffel(12)
        storage._schreiben(db, {vorhanden.key: _staffel(1), neu.key: neu})

        zeile = db.scalar(select(StorageEntry).where(StorageEntry.key == neu.key))
        assert zeile.user_id == kim.id
