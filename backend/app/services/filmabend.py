"""Der gefuehrte Filmabend - "Keine Ahnung, was du gucken sollst".

Ein paar Fragen statt dreizehn Regler. Der Unterschied ist nicht die Zahl der
Bedienelemente, sondern **wonach gefragt wird**: nicht nach Stimmenzahlen und
Originalsprachen, sondern nach dem, was den Abend tatsaechlich bestimmt - wie
viel Zeit ist, worauf man Lust hat, und ob es sofort laufen muss.

⚠️ **Anbieter-Neutralitaet**, wie in ``stoebern.py``: Dieses Modul liest den
Sehstand ausschliesslich aus der neutralen Tabelle ``UserWatched`` und kennt
keinen einzelnen Media-Server. Ein Test sichert das ab.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MediaType, UserWatched, utcnow
from .filters import (
    BEKANNTE_TITEL_STIMMEN,
    LAUFZEITEN,
    MIN_FEATURE_RUNTIME,
    PERLEN_MINDESTALTER_JAHRE,
    PERLEN_STIMMEN,
    PERLEN_STIMMEN_TV,
    DiscoverFilters,
)

# So viele Titel liegen am Ende auf dem Stapel. Bewusst klein: Der ganze Zweck
# des Assistenten ist, die Auswahl zu **verkleinern**. Eine Ergebnisliste mit
# 500 Seiten waere dieselbe Ratlosigkeit wie vorher, nur mit Zwischenschritten.
STAPEL_GROESSE = 12

# Ab wann etwas "lange nicht gesehen" ist.
#
# ⚠️ Zwei Fassungen hatte das schon. Zuerst **zwei Jahre** - unbrauchbar, weil
# ein Media-Server den Verlauf erst ab dem Tag der Verbindung sammelt: Das
# Konto mit dem laengsten Verlauf hatte 389 Eintraege, der aelteste
# eineinhalb Jahre alt, die Funktion waere fuer jeden leer gewesen. Danach
# **ohne** Grenze, rein nach "am laengsten her" - da schlug sie aber auch
# Titel von letzter Woche vor, wenn der Verlauf kurz war, und das ist gelogen.
#
# Sechs Monate ist die Entscheidung des Nutzers: lang genug, dass man einen
# Film wieder sehen mag, kurz genug, dass ein junger Verlauf schon etwas
# hergibt. **Passt nichts, wird das Regal ausgeblendet** statt etwas
# Unpassendes zu zeigen.
LANGE_HER_TAGE = 182

# Wie gross der Vorrat hoechstens ist, aus dem gewuerfelt wird.
VORRAT = 150


class UngueltigeAntwort(ValueError):
    """Eine Frage oder Antwort, die es im Fragebaum nicht gibt."""


# --- Der Fragebaum ---------------------------------------------------------


@dataclass(frozen=True)
class Frage:
    """Eine Frage samt der Bedingung, unter der sie **entfaellt**.

    Die Verzweigung sitzt als Bedingung an der Frage und nicht als Sprungziel
    an der Antwort. Grund: Ob "Grosser Titel oder Geheimtipp?" noch Sinn ergibt,
    haengt an **zwei** frueheren Antworten. Ein Baum, in dem jede Antwort ihre
    eine Folgefrage nennt, kann das nicht ausdruecken - man muesste Zweige
    duplizieren, und beim naechsten Zusatz waeren es vier.

    Texte stehen hier nicht: Sie kommen als ``stoebern.filmabend.<frage>.*``
    aus der Uebersetzung. Ein fertiger Text hier waere einsprachig.
    """

    kennung: str
    antworten: tuple[str, ...]
    # {Frage: Antworten, bei denen diese Frage uebersprungen wird}
    entfaellt_wenn: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Einzelne **Antwortmoeglichkeiten**, die verschwinden:
    # {Antwort: {fruehere Frage: Antworten, bei denen sie wegfaellt}}
    #
    # Zurzeit benutzt das **keine** Frage, und das ist eine Entscheidung, keine
    # Luecke: Der einzige Kandidat war "Gruseln" bei Kindern, und die Messung
    # hat gezeigt, dass die Altersfreigabe das besser loest (siehe FRAGEN).
    # Der Mechanismus bleibt, weil Server, Schnittstelle und Oberflaeche ihn
    # durchgaengig beherrschen - und weil die naechste solche Frage sonst
    # wieder als Ausblenden statt als Grenze gebaut wuerde.
    antworten_entfallen_wenn: dict[str, dict[str, tuple[str, ...]]] = field(
        default_factory=dict
    )


FRAGEN: tuple[Frage, ...] = (
    # Die natuerlichste erste Frage am Filmabend - und die einzige, die eine
    # **harte** Einschraenkung mitbringt.
    #
    # ⚠️ Sie setzt eine Altersfreigabe, **keine** Genres. Der Unterschied ist
    # wichtig: "mit Kindern" heisst nachweisbar "hoechstens FSK 6", aber es
    # heisst nicht "Zeichentrick". Und "mit Freunden" heisst gar nichts
    # Bestimmtes - wer daraus "also Action" macht, raet, und der Assistent
    # waere nur noch ein Vorurteil mit Knoepfen. Worauf jemand Lust hat, fragt
    # die naechste Frage; die kann er selbst beantworten.
    Frage("gesellschaft", ("allein", "zu_zweit", "freunde", "familie", "kinder")),
    # ⚠️ **Keine Stimmung wird ausgeblendet - auch nicht bei Kindern.**
    #
    # Der erste Bauversuch nahm "Gruseln" und "fuers Herz" weg, sobald Kinder
    # mitschauen. Das war falsch, und zwar messbar: Mit der Altersfreigabe
    # FSK 0/6 liefert das Horror-Genre 30 Treffer, und zwar genau die
    # richtigen - "Hexen hexen", "Das Haus der geheimnisvollen Uhren",
    # "King Kong". Kinder *moegen* Gruseln; sie brauchen eine Grenze, keine
    # Bevormundung. "Fuers Herz" ergibt mit derselben Grenze 1787 Treffer
    # ("Your Name", "Die Schoene und das Biest").
    #
    # Die Lehre gilt allgemein: **Erst die Grenze messen, dann die Auswahl
    # beschneiden.** Eine weggenommene Frage sieht nicht aus wie Schutz,
    # sondern wie eine fehlende Funktion.
    Frage(
        "stimmung",
        ("lachen", "spannung", "nachdenken", "mitfiebern", "gruseln", "herz", "ueberrasch"),
    ),
    Frage("verfuegbar", ("sofort", "laden", "egal")),
    Frage("zeit", ("kurz", "mittel", "lang", "egal")),
    Frage("vertraut", ("neu", "wieder", "egal")),
    # Entfaellt beim Wiedersehen: Wer aus dem eigenen Sehverlauf waehlt, hat
    # seinen Zeitraum damit schon bestimmt.
    Frage("epoche", ("aktuell", "modern", "alt", "egal"),
          entfaellt_wenn={"vertraut": ("wieder",)}),
    # Entfaellt gleich zweifach:
    #
    # * Bei "muss sofort laufen" waehlt man aus der eigenen Bibliothek - dort
    #   ist "Geheimtipp" keine sinnvolle Frage mehr.
    # * Beim Wiedersehen kennt man die Titel ohnehin alle.
    Frage("bekanntheit", ("bekannt", "geheimtipp", "egal"),
          entfaellt_wenn={"verfuegbar": ("sofort",), "vertraut": ("wieder",)}),
)

FRAGEN_NACH_KENNUNG = {frage.kennung: frage for frage in FRAGEN}


def entfaellt(frage: Frage, antworten: dict[str, str]) -> bool:
    """Wird diese Frage angesichts der bisherigen Antworten uebersprungen?"""
    return any(
        antworten.get(vorher) in ausloeser for vorher, ausloeser in frage.entfaellt_wenn.items()
    )


def verfuegbare_antworten(frage: Frage, antworten: dict[str, str]) -> tuple[str, ...]:
    """Die Antworten, die angesichts der bisherigen noch angeboten werden."""
    return tuple(
        antwort
        for antwort in frage.antworten
        if not any(
            antworten.get(vorher) in ausloeser
            for vorher, ausloeser in frage.antworten_entfallen_wenn.get(antwort, {}).items()
        )
    )


def fragen_fuer(mit_wiedersehen: bool) -> tuple[Frage, ...]:
    """Der Fragebaum, zugeschnitten auf das, was diese Person haben kann.

    ⚠️ "Etwas, das ich lange nicht gesehen habe" wird **weggelassen**, wenn es
    dafuer nichts gibt. Vorher stand die Antwort immer da und fuehrte dann in
    eine Sackgasse mit einer Meldung, die obendrein falsch war ("dein
    Sehverlauf ist leer", obwohl er voll war - nur eben zu frisch).

    Eine Antwort, die nie ein Ergebnis liefern kann, gehoert nicht ins Menue.
    Das ist etwas anderes als die Regel weiter oben, keine Stimmung
    auszublenden: Dort **gab** es Ergebnisse, sie waren nur unerwuenscht
    bevormundet. Hier gibt es schlicht keine.
    """
    if mit_wiedersehen:
        return FRAGEN
    return tuple(
        Frage(
            frage.kennung,
            tuple(a for a in frage.antworten if a != "wieder"),
            frage.entfaellt_wenn,
            frage.antworten_entfallen_wenn,
        )
        if frage.kennung == "vertraut"
        else frage
        for frage in FRAGEN
    )


def naechste_frage(antworten: dict[str, str]) -> Frage | None:
    """Die naechste zu stellende Frage - oder ``None``, wenn es reicht.

    Liegt auch serverseitig vor, obwohl das Frontend den Baum selbst ablaeuft:
    Die Ergebnis-Berechnung muss wissen, welche Antworten ueberhaupt zaehlen,
    und darf sich dabei nicht auf den Browser verlassen.
    """
    for frage in FRAGEN:
        if frage.kennung in antworten or entfaellt(frage, antworten):
            continue
        return frage
    return None


def pruefe(antworten: dict[str, str]) -> dict[str, str]:
    """Unbekanntes aussortieren und uebersprungene Antworten verwerfen.

    Beides ist noetig, weil die Antworten aus dem Browser kommen: Eine
    mitgeschickte Antwort auf eine Frage, die gar nicht gestellt wurde, duerfte
    das Ergebnis nicht beeinflussen.
    """
    sauber: dict[str, str] = {}
    for frage in FRAGEN:
        if entfaellt(frage, sauber):
            continue
        wert = antworten.get(frage.kennung)
        if wert is None:
            continue
        # Gegen die **verfuegbaren** Antworten pruefen, nicht gegen alle: Wer
        # "mit Kindern" waehlt und trotzdem "Zum Gruseln" mitschickt, hat eine
        # Auswahl benutzt, die gar nicht angeboten wurde.
        if wert not in verfuegbare_antworten(frage, sauber):
            raise UngueltigeAntwort(f"{frage.kennung}={wert}")
        sauber[frage.kennung] = wert
    unbekannt = set(antworten) - set(FRAGEN_NACH_KENNUNG)
    if unbekannt:
        raise UngueltigeAntwort(", ".join(sorted(unbekannt)))
    return sauber


# --- Von der Antwort zum Filter --------------------------------------------

# Worauf man Lust hat, in TMDB-Genres.
#
# Film und Serie fuehren verschiedene Nummern, und TMDB kennt bei Serien
# **kein** Horror, Thriller oder Liebesfilm - die Sender-Genres sind grober
# geschnitten. Wo nichts Passendes existiert, wird das naechstgelegene genommen
# und lieber breiter gefiltert als leer.
STIMMUNGEN: dict[str, dict[str, tuple[int, ...]]] = {
    "movie": {
        "lachen": (35,),
        "spannung": (53, 9648),
        "nachdenken": (18,),
        "mitfiebern": (28, 12),
        "gruseln": (27,),
        "herz": (10749,),
        "ueberrasch": (),
    },
    "tv": {
        "lachen": (35,),
        "spannung": (9648, 80),
        "nachdenken": (18,),
        "mitfiebern": (10759,),
        # Kein Horror-Genre bei Serien; Mystery kommt dem am naechsten.
        "gruseln": (9648,),
        # Und kein Liebesfilm-Genre. Drama traegt es mit.
        "herz": (18,),
        "ueberrasch": (),
    },
}

# Bis zu welchem Alter freigegeben, je nachdem wer mitschaut.
#
# Nicht in der Liste = keine Altersgrenze. "Allein", "zu zweit" und "mit
# Freunden" sagen ueber die Freigabe nichts aus, und etwas hineinzudichten
# waere geraten.
ALTERSGRENZE: dict[str, int] = {
    "kinder": 6,
    "familie": 12,
}

# Rueckfall, wenn der Freigabefilter nicht greift.
#
# Gebraucht in **zwei** Faellen: bei Serien, fuer die TMDB gar keinen
# Freigabefilter anbietet, und bei Filmen, wenn die Freigabe-Tabelle nicht
# abrufbar war. Beide Male ist eine grobe Genre-Auswahl besser als gar kein
# Schutz - "mit Kindern" darf unter keinen Umstaenden Horror liefern.
KINDGERECHTE_GENRES: dict[str, tuple[int, ...]] = {
    "movie": (16, 10751, 12, 35),
    "tv": (16, 10762, 10751, 35),
}

# Wie viel Zeit ist. Dieselbe Tabelle wie die Filterleiste - zwei
# Definitionen liefen beim ersten Feinschliff auseinander.
ZEITEN = LAUFZEITEN

# Aus welcher Zeit, in Jahren vor heute. (frueheste, spaeteste)
EPOCHEN: dict[str, tuple[int | None, int | None]] = {
    "aktuell": (3, 0),
    "modern": (26, 7),
    "alt": (None, 26),
    "egal": (None, None),
}


def _modus(antworten: dict[str, str]) -> str:
    """Der Bestandsfilter, wie ``stoebern.sammle`` ihn versteht."""
    return {
        "sofort": "nur_vorhanden",
        "laden": "nur_neu",
        "egal": "egal",
    }[antworten.get("verfuegbar", "egal")]


def hoechstalter(antworten: dict[str, str]) -> int | None:
    """Bis zu welcher Freigabe, wenn jemand mitschaut. ``None`` = keine Grenze."""
    return ALTERSGRENZE.get(antworten.get("gesellschaft", ""))


def filter_aus(
    antworten: dict[str, str],
    media_type: str,
    *,
    page: int = 1,
    heute: date | None = None,
    freigaben: tuple[str | None, str] = (None, ""),
) -> DiscoverFilters:
    """Die Antworten in TMDB-Filter uebersetzen.

    Die Uebersetzung liegt bewusst im Server und nicht im Browser: Sonst
    stuenden Fragebaum und Bedeutung an zwei Orten und liefen auseinander.

    ``freigaben`` ist das Ergebnis von ``kids.freigaben_bis_alter`` - Land und
    Bezeichnungen fuer den TMDB-Freigabefilter. Es wird hereingereicht statt
    hier geholt, damit diese Funktion ohne Datenbank auskommt und sich
    vollstaendig testen laesst.
    """
    heute = heute or date.today()
    ist_film = media_type == "movie"

    genres = STIMMUNGEN[media_type][antworten.get("stimmung", "ueberrasch")]

    # Wer mitschaut, entscheidet ueber die Altersfreigabe.
    land, bezeichnungen = freigaben
    grenze = hoechstalter(antworten)
    if grenze is not None and not (land and bezeichnungen):
        # Kein Freigabefilter verfuegbar - dann wenigstens ueber die Genres.
        # Bei Serien ist das der Normalfall: TMDB bietet dort keinen.
        kindgerecht = set(KINDGERECHTE_GENRES[media_type])
        gemeinsam = tuple(g for g in genres if g in kindgerecht)
        # Passt die Stimmung zu keinem kindgerechten Genre, gilt die
        # kindgerechte Auswahl - lieber breiter als ungeschuetzt.
        genres = gemeinsam or tuple(sorted(kindgerecht))
    mindestens, hoechstens = ZEITEN[antworten.get("zeit", "egal")]
    von_jahre, bis_jahre = EPOCHEN[antworten.get("epoche", "egal")]

    date_from = f"{heute.year - von_jahre}-01-01" if von_jahre is not None else None
    date_to = f"{heute.year - bis_jahre}-12-31" if bis_jahre is not None else None

    min_votes: int | None = None
    max_votes: int | None = None
    bekanntheit = antworten.get("bekanntheit", "egal")
    if bekanntheit == "bekannt":
        min_votes = BEKANNTE_TITEL_STIMMEN
    elif bekanntheit == "geheimtipp":
        # Dasselbe Fenster wie im Perlen-Regal - samt Altersgrenze. Ohne sie
        # misst die Stimmenzahl nur, wie neu ein Titel ist, und "Geheimtipp"
        # lieferte die Blockbuster der laufenden Saison.
        min_votes, max_votes = PERLEN_STIMMEN if ist_film else PERLEN_STIMMEN_TV
        grenze = f"{heute.year - PERLEN_MINDESTALTER_JAHRE}-12-31"
        date_to = min(date_to, grenze) if date_to else grenze

    return DiscoverFilters(
        date_from=date_from,
        date_to=date_to,
        genres_or="|".join(str(g) for g in genres),
        # Beides oder keines - allein hat "certification" bei TMDB keine
        # Wirkung, das Land muss dazu.
        certification_country=land if (grenze is not None and bezeichnungen) else None,
        certifications=bezeichnungen if grenze is not None else "",
        # Laufzeiten gibt es nur bei Filmen sinnvoll - bei Serien waere es die
        # Folgenlaenge, und "wir haben zwei Stunden" sagt darueber nichts.
        min_runtime=mindestens if ist_film else None,
        max_runtime=hoechstens if ist_film else None,
        min_votes=min_votes,
        max_votes=max_votes,
        # Nach Beliebtheit: Innerhalb einer so eng gefassten Auswahl ist die
        # Note kein guter Ordner mehr (es gewinnen Nischen-Dokumentationen),
        # und der Mensch will Titel sehen, die er einordnen kann.
        sort="popular",
        page=page,
    )


# --- Der eigene Sehstand ---------------------------------------------------


def gesehene_kennungen(db: Session, user_id: int, media_type: str) -> set[int]:
    """Alles, was diese Person laut Media-Server schon gesehen hat."""
    zeilen = db.scalars(
        select(UserWatched.tmdb_id).where(
            UserWatched.user_id == user_id,
            UserWatched.media_type == MediaType(media_type),
        )
    )
    return set(zeilen)


def hat_verlauf(db: Session, user_id: int, media_type: str) -> bool:
    """Gibt es ueberhaupt Seh-Eintraege - egal wie alt?

    Muss von ``lange_nicht_gesehen`` getrennt bleiben: "gar kein Verlauf" und
    "nichts, das lange genug her ist" sind **zwei verschiedene Auskuenfte**,
    und wer sie zusammenwirft, erzaehlt jemandem mit vollem Verlauf, er habe
    keinen. Genau das ist passiert.
    """
    return (
        db.scalar(
            select(UserWatched.id)
            .where(
                UserWatched.user_id == user_id,
                UserWatched.media_type == MediaType(media_type),
            )
            .limit(1)
        )
        is not None
    )


def lange_nicht_gesehen(
    db: Session, user_id: int, media_type: str, *, jetzt: datetime | None = None
) -> list[int]:
    """Titel, die mindestens ``LANGE_HER_TAGE`` her sind - aeltester zuerst.

    Eine **leere** Liste heisst hier zweierlei und das ist Absicht: Entweder
    gibt es keinen Verlauf, oder es liegt nichts weit genug zurueck. In beiden
    Faellen soll das Regal verschwinden statt etwas Unpassendes zu zeigen.

    Zeilen **ohne** Zeitstempel zaehlen mit und stehen ganz vorn: Ein fehlendes
    Datum heisst "irgendwann", und irgendwann ist laenger her als ein halbes
    Jahr. Sie wegzulassen waere der haeufigere Fehler - im gemessenen Bestand
    trugen 41 von 389 Zeilen kein Datum.
    """
    # ``watched_at`` liegt naiv in der Datenbank; die Zeitzone muss weg.
    jetzt = jetzt or utcnow().replace(tzinfo=None)
    grenze = jetzt - timedelta(days=LANGE_HER_TAGE)

    zeilen = db.execute(
        select(UserWatched.tmdb_id, UserWatched.watched_at).where(
            UserWatched.user_id == user_id,
            UserWatched.media_type == MediaType(media_type),
        )
    ).all()

    passend = [(kennung, wann) for kennung, wann in zeilen if wann is None or wann < grenze]
    # ``None`` zuerst, danach das aelteste Datum.
    passend.sort(key=lambda zeile: (zeile[1] is not None, zeile[1] or datetime.min))
    return [kennung for kennung, _ in passend]


def vorrat(kennungen: list[int]) -> list[int]:
    """Die aeltere Haelfte - daraus wird gewuerfelt.

    Nicht der ganze Verlauf: Sonst schluege der Assistent auch das vor, was
    man gestern gesehen hat, und "lange nicht gesehen" waere gelogen. Nicht
    nur die aeltesten zwoelf: Sonst kaeme bei jeder Runde dasselbe, und mit
    einem Genre-Filter davor bliebe oft gar nichts uebrig.
    """
    return kennungen[: max(VORRAT, len(kennungen) // 2)] or kennungen


def mischen(kennungen: list[int], antworten: dict[str, str], runde: int) -> list[int]:
    """Immer dieselbe Reihenfolge bei gleicher Runde, eine andere bei der naechsten.

    Kein Zufall: Ein echter Zufallsgenerator liefert bei jedem Neuladen der
    Seite eine andere Liste, und der Stapel, den man gerade ansah, waere weg.
    Der Startwert kommt deshalb aus den Antworten und der Rundennummer.
    """
    kern = ("|".join(f"{k}={v}" for k, v in sorted(antworten.items())) + f"#{runde}").encode()
    startwert = hashlib.sha256(kern).digest()

    def schluessel(kennung: int) -> bytes:
        return hashlib.sha256(startwert + str(kennung).encode()).digest()

    return sorted(kennungen, key=schluessel)
