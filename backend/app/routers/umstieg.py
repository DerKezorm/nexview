"""Der Umstiegsassistent: von Radarr und Sonarr auf nexcrate (Bauplan 7.3).

⚠️ **Sieben Adressen, und nur eine davon schreibt.** ``umschalten`` ist der
Schritt ohne Rückweg; alles davor ist Lesen, Rechnen und Zeigen. Wer eine
dieser Adressen erweitert, prüfe zuerst, auf welcher Seite dieser Linie sie
liegt.

⚠️ **Der Assistent läuft, solange ``arr`` gilt** - und fragt trotzdem die neue
nexcrate. Dafür gibt es ``umstieg.nex_sicht``: eine Sicht der Einstellungen,
in der schon ``nex`` steht, ohne dass etwas gespeichert wäre.

Die Riegel, jeder aus einem anderen Grund:

* **Administrator.** Der Umstieg ändert die Beschaffung des ganzen Hauses.
* **Nur im ARR-Betrieb.** Ein zweiter Umstieg von nexcrate nach nexcrate würde
  Kennungen umschreiben, die schon stimmen.
* **Nur mit Sicherung.** Es gibt keinen Rückweg außer ihr (7.3, Schritt 5); der
  Server prüft, dass die genannte Datei wirklich liegt, statt der Oberfläche zu
  glauben.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .. import meldungen
from ..deps import AdminUser, DbSession
from ..services import fassungen as fassungen_dienst
from ..services import nachreichen, sicherung, umstieg
from ..services.beschaffung import (
    ARR,
    SPERRT,
    BeschaffungError,
    fassungen_auffrischen,
    get_beschaffung,
)
from ..services.settings_service import load_settings

router = APIRouter(prefix="/api/umstieg", tags=["umstieg"])

logger = logging.getLogger("nexview.umstieg")

#: Der Kommentar, den die Sicherung des Assistenten trägt. Er steht in der
#: Liste der Sicherungen und sagt einem späteren Leser, warum sie entstand.
SICHERUNG_KOMMENTAR = "Before switching from Radarr/Sonarr to nexcrate"


def _nur_vom_arr_betrieb(db: DbSession) -> Any:
    """Den Umstieg gibt es genau einmal, und nur in diese Richtung."""
    settings = load_settings(db)
    if settings.beschaffung != ARR:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "not_in_this_mode",
                "Diese Installation beschafft schon über nexcrate.",
                beschaffung=settings.beschaffung,
            ),
        )
    return settings


def _als_meldung(fehler: BeschaffungError) -> HTTPException:
    return HTTPException(status_code=502, detail=fehler.als_meldung())


# --------------------------------------------------------------------------
# Schritt 1: die Zahlen vorab


class VorabAntwort(BaseModel):
    downloads_laufend: int
    anfragen_offen: int
    posten: int
    instanzen: list[str]


@router.get("/vorab", response_model=VorabAntwort)
async def vorab(admin: AdminUser, db: DbSession) -> VorabAntwort:
    """Was sich ändert - in Zahlen, bevor irgendetwas geschieht."""
    settings = _nur_vom_arr_betrieb(db)
    stand = await umstieg.vorab(db, settings)
    return VorabAntwort(
        downloads_laufend=stand.downloads_laufend,
        anfragen_offen=stand.anfragen_offen,
        posten=stand.posten,
        instanzen=stand.instanzen,
    )


# --------------------------------------------------------------------------
# Schritt 2 und 3: Standprüfung und Abbildung


class Abbildungsvorschlag(BaseModel):
    """Die Fassungen beider Seiten und der Vorschlag dazwischen."""

    #: Was gegen diese nexcrate spricht (7.2). Ein ``sperrt`` hält den
    #: Assistenten an; die Oberfläche zeigt beides.
    pruefung: list[dict[str, Any]] = Field(default_factory=list)
    sperrt: bool = False
    arr_fassungen: list[dict[str, Any]] = Field(default_factory=list)
    nex_fassungen: list[dict[str, Any]] = Field(default_factory=list)
    #: Arr-Kennung -> nexcrate-Kennung (oder ``None`` für "keine").
    vorschlag: dict[str, str | None] = Field(default_factory=dict)


@router.get("/abbildung", response_model=Abbildungsvorschlag)
async def abbildung(admin: AdminUser, db: DbSession) -> Abbildungsvorschlag:
    """Standprüfung, beide Fassungslisten und der Vorschlag (7.3, Schritte 2 und 3).

    ⚠️ **Die Prüfung steht vor dem Vorschlag**, nicht daneben: Eine nexcrate,
    die Nexview nicht bedienen kann, soll nicht erst eine hübsche Zuordnung
    anbieten, die niemand benutzen darf.
    """
    settings = _nur_vom_arr_betrieb(db)
    sicht = umstieg.nex_sicht(db)
    weg = get_beschaffung(sicht)
    try:
        befunde = await weg.pruefen()
    except BeschaffungError as fehler:
        raise _als_meldung(fehler) from fehler

    antwort = Abbildungsvorschlag(
        pruefung=[
            {"code": b.code, "stufe": b.stufe, "werte": b.werte} for b in befunde
        ],
        sperrt=any(b.stufe == SPERRT for b in befunde),
        # ⚠️ Der Name kommt ueber ``info``: Der Betreiber hat seine Instanzen
        # womoeglich benannt, und in der Zuordnung soll stehen, was er kennt.
        arr_fassungen=[
            {
                "kennung": f.kennung,
                "media_type": f.media_type,
                "name": fassungen_dienst.info(settings, f.kennung).name,
                "klasse": f.klasse,
            }
            for f in fassungen_dienst.ARR_FASSUNGEN
        ],
    )
    if antwort.sperrt:
        # Nichts weiter lesen: Die Fassungen dieser nexcrate wären eine
        # Einladung, trotzdem weiterzuklicken.
        return antwort

    # ⚠️ **Holen, nicht nur abgleichen.** Die Tabelle kennt die Fassungen von
    # nexcrate noch gar nicht - ein Abgleich gegen sie ergaebe eine leere
    # Liste und damit eine Zuordnung ohne Ziele.
    try:
        await fassungen_auffrischen(db, sicht)
        weg.fassungen_abgleichen(db)
        db.commit()
    except BeschaffungError as fehler:
        db.rollback()
        raise _als_meldung(fehler) from fehler

    nex = list(weg.fassungen())
    antwort.nex_fassungen = [
        {
            "kennung": f.kennung,
            "media_type": f.media_type,
            "name": f.name,
            "klasse": f.klasse,
        }
        for f in nex
    ]
    antwort.vorschlag = umstieg.vorschlag(db, nex)
    return antwort


# --------------------------------------------------------------------------
# Schritt 4: die Probe


class AbbildungEingabe(BaseModel):
    abbildung: dict[str, str | None]


class ProbeAntwort(BaseModel):
    fehler: list[str] = Field(default_factory=list)
    bekannt: int = 0
    ohne_fassung: int = 0
    unbekannt: int = 0
    anime_offen: int = 0
    #: Rechte an Konten und offenen Einladungen, deren Fassung auf „Keine"
    #: zeigt. Sie entfallen beim Umschalten ersatzlos.
    rechte_entfallen: int = 0
    #: Die Titel, die eine Entscheidung brauchen: geladene Posten, die nexcrate
    #: nicht führt, Serien ohne Übersetzung nach TMDB, solche, die mit einem
    #: anderen Posten denselben neuen Speicherschlüssel bekämen, und offene
    #: Anfragen, die mit so einem Posten stehen bleiben.
    zu_entscheiden: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/probe", response_model=ProbeAntwort)
async def probe(
    eingabe: AbbildungEingabe, admin: AdminUser, db: DbSession
) -> ProbeAntwort:
    """Kennt nexcrate die Titel, an denen etwas hängt? (7.3, Schritt 4.)"""
    _nur_vom_arr_betrieb(db)
    sicht = umstieg.nex_sicht(db)
    nex = list(get_beschaffung(sicht).fassungen())
    fehler = umstieg.pruefe_abbildung(eingabe.abbildung, nex)
    if fehler:
        return ProbeAntwort(fehler=fehler)

    try:
        ergebnis = await umstieg.probe(db, sicht, eingabe.abbildung)
    except BeschaffungError as caught:
        raise _als_meldung(caught) from caught

    return ProbeAntwort(
        bekannt=len(ergebnis.bekannt),
        ohne_fassung=len(ergebnis.ohne_fassung),
        unbekannt=len(ergebnis.unbekannt),
        anime_offen=len(ergebnis.anime_offen),
        rechte_entfallen=umstieg.rechte_entfallen(db, eingabe.abbildung),
        zu_entscheiden=[
            {
                "media_type": b.media_type,
                "tmdb_id": b.tmdb_id,
                "titel": b.titel,
                "fassung": b.fassung,
                "ergebnis": b.ergebnis,
                # ⚠️ Eine Serie **mit** Gegenstück, aber ohne TMDB-Nummer in
                # nexcrate, ist derselbe Fall: Ihr Speicherschlüssel ließe sich
                # nicht übersetzen (7.3, Schritt 4, letzter Satz).
                "ohne_uebersetzung": bool(
                    (b.ergebnis == "bekannt" and not b.tmdb_aus_nexcrate) or b.anfrage_bleibt
                ),
                # Die offene Anfrage bleibt mit ihrem Posten bei der alten
                # Fassung, statt allein hinüberzugehen.
                "anfrage_bleibt": b.anfrage_bleibt,
                # ⚠️ **Zwei Posten, ein neuer Schlüssel.** Der dritte Grund,
                # hier zu stehen - und der einzige, der ohne diese Liste als
                # Absturz endete (23.09.2026).
                "kollidiert": b.kollidiert,
            }
            for b in ergebnis.zu_entscheiden
        ],
    )


# --------------------------------------------------------------------------
# Schritt 5: die Sicherung


class SicherungAntwort(BaseModel):
    name: str
    groesse: int
    erstellt: str


@router.post(
    "/sicherung", response_model=SicherungAntwort, status_code=status.HTTP_201_CREATED
)
def sicherung_anlegen(admin: AdminUser, db: DbSession) -> SicherungAntwort:
    """Die Sicherung, ohne die es nicht weitergeht (7.3, Schritt 5)."""
    _nur_vom_arr_betrieb(db)
    try:
        pfad = sicherung.anlegen(art=sicherung.MANUELL, kommentar=SICHERUNG_KOMMENTAR)
    except Exception as fehler:  # noqa: BLE001 - dem Betreiber sagen, was war
        logger.exception("The backup before switching failed")
        raise HTTPException(
            status_code=500,
            detail=meldungen.meldung(
                "backup_failed",
                f"Die Sicherung konnte nicht angelegt werden: {fehler}",
            ),
        ) from fehler
    for eintrag in sicherung.liste():
        if eintrag.name == pfad.name:
            return SicherungAntwort(
                name=eintrag.name, groesse=eintrag.groesse, erstellt=eintrag.erstellt
            )
    raise HTTPException(  # pragma: no cover - die Datei liegt gerade erst
        status_code=500,
        detail=meldungen.meldung("backup_failed", "Die Sicherung ist nicht auffindbar."),
    )


# --------------------------------------------------------------------------
# Schritt 6: umschalten


class UmschaltenEingabe(BaseModel):
    abbildung: dict[str, str | None]
    #: Der Name der Sicherung aus Schritt 5. ⚠️ **Der Server sieht nach, ob sie
    #: liegt.** Eine Zusage der Oberfläche ist hier keine Grundlage.
    sicherung: str = Field(min_length=1, max_length=200)
    #: Der Betreiber hat die Liste aus Schritt 4 gesehen und will trotzdem.
    posten_ohne_gegenstueck_behalten: bool = False


class UmschaltenAntwort(BaseModel):
    fassungen: int
    #: Kennungen, keine Sätze - die Oberfläche macht daraus einen Satz.
    verlassen: list[dict[str, Any]]
    anfragen: int
    anfragen_ohne_uebersetzung: int = 0
    posten: int
    posten_schluessel: int
    posten_ohne_uebersetzung: int
    posten_doppelt: int = 0
    rechte: int
    rechte_entfallen: int = 0
    einladungen: int
    regeln: int
    zeilen_entfernt: int


@router.post("/umschalten", response_model=UmschaltenAntwort)
async def umschalten(
    eingabe: UmschaltenEingabe, admin: AdminUser, db: DbSession
) -> UmschaltenAntwort:
    """Der eine Schritt, der sich nicht zurücknehmen lässt (7.3, Schritt 6).

    Vorher wird die Probe **noch einmal** gefahren - nicht aus Misstrauen
    gegen die Oberfläche, sondern weil daraus die Übersetzung der
    Speicherschlüssel entsteht. Sie aus dem Browser entgegenzunehmen hieße,
    die Zurechnung des ganzen Hauses von einem Formularfeld abhängig zu machen.
    """
    _nur_vom_arr_betrieb(db)
    if not any(e.name == eingabe.sicherung for e in sicherung.liste()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "umstieg_ohne_sicherung",
                "Vor dem Umschalten muss eine Sicherung liegen.",
            ),
        )
    # ⚠️ Der Name allein genügt nicht: Eine leere Datei mit passendem Namen
    # ginge sonst als Rückweg durch.
    if not sicherung.brauchbar(eingabe.sicherung):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "umstieg_sicherung_unbrauchbar",
                "Die genannte Sicherung lässt sich nicht als Datenbank öffnen.",
            ),
        )

    sicht = umstieg.nex_sicht(db)
    nex = list(get_beschaffung(sicht).fassungen())
    fehler = umstieg.pruefe_abbildung(eingabe.abbildung, nex)
    if fehler:
        raise HTTPException(
            status_code=422,
            detail=meldungen.meldung(fehler[0], "Die Zuordnung der Fassungen stimmt nicht."),
        )

    try:
        ergebnis = await umstieg.probe(db, sicht, eingabe.abbildung)
    except BeschaffungError as caught:
        raise _als_meldung(caught) from caught

    haengt = ergebnis.zu_entscheiden
    if haengt and not eingabe.posten_ohne_gegenstueck_behalten:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=meldungen.meldung(
                "umstieg_posten_ohne_gegenstueck",
                "Es gibt Posten, die nexcrate nicht führt.",
                anzahl=len(haengt),
            ),
        )

    settings = load_settings(db)
    try:
        bericht = await umstieg.umschalten(
            db, settings, eingabe.abbildung, ergebnis.tmdb_je_tvdb()
        )
    except BeschaffungError as caught:
        db.rollback()
        raise _als_meldung(caught) from caught

    return UmschaltenAntwort(
        fassungen=bericht.fassungen,
        verlassen=[
            {"code": zeile.code, "werte": zeile.werte} for zeile in bericht.verlassen
        ],
        anfragen=bericht.wanderung.anfragen,
        anfragen_ohne_uebersetzung=bericht.wanderung.anfragen_ohne_uebersetzung,
        posten=bericht.wanderung.posten,
        posten_schluessel=bericht.wanderung.posten_schluessel,
        posten_ohne_uebersetzung=bericht.wanderung.posten_ohne_uebersetzung,
        posten_doppelt=bericht.wanderung.posten_doppelt,
        rechte=bericht.wanderung.rechte,
        rechte_entfallen=bericht.wanderung.rechte_entfallen,
        einladungen=bericht.wanderung.einladungen,
        regeln=bericht.wanderung.regeln,
        zeilen_entfernt=bericht.wanderung.zeilen_entfernt,
    )


# --------------------------------------------------------------------------
# Schritt 7: danach


class NachreichenAntwort(BaseModel):
    gereicht: int
    #: Freigegeben auf einer Fassung, die nexcrate nicht kennt: kommt nie an.
    #: Der Assistent sagt es hier, das Dashboard als Befund.
    liegen: int = 0
    #: Ist noch etwas offen? Dann holt der Rundgang den Rest - der Assistent
    #: muss nicht warten.
    weiter: bool


@router.post("/nachreichen", response_model=NachreichenAntwort)
async def nachreichen_einmal(admin: AdminUser, db: DbSession) -> NachreichenAntwort:
    """Freigegebene Anfragen an nexcrate nachreichen (7.3, Schritt 7).

    ⚠️ **Diese Adresse gilt nach dem Umschalten**, also im NEX-Betrieb - der
    Riegel von oben passt hier nicht. Ihr eigener ist ``beschaffung_gewechselt_am``:
    Ohne den Merker gibt es nichts nachzureichen.
    """
    settings = load_settings(db, frisch=True)
    if not nachreichen.faellig(settings):
        return NachreichenAntwort(gereicht=0, weiter=False)
    ergebnis = await nachreichen.einmal(db, settings)
    return NachreichenAntwort(
        gereicht=ergebnis.gereicht,
        liegen=ergebnis.liegen,
        weiter=nachreichen.faellig(load_settings(db, frisch=True)),
    )
