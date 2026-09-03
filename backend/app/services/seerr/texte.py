"""Die Saetze des Umzugs: Kennung und Zahlen hier, der Wortlaut in der Oberflaeche.

⚠️ **Warum es diese Datei gibt.** Bis 0.29.0 baute das Backend jeden Hinweis
des Umzugs als fertigen deutschen Satz - "In Seerr stand 0, und das heisst
dort nicht zaehlen" - und die Oberflaeche zeigte ihn unveraendert, auch auf
Englisch. Auf der Projektseite standen dadurch englische Bildschirmfotos mit
deutschen Zeilen darin. Das verletzt die Regel, dass die Anwendung die
eingestellte Sprache spricht.

Deshalb liefert das Backend seit 0.29.1 nur noch **Kennung und Zahlen**
(:class:`Satz`), und die Oberflaeche baut den Satz aus ``setup.seerr.saetze``
in ihrer Sprache. Der deutsche Wortlaut steht trotzdem hier, als ``text``:
Er ist der Rueckfall fuer alles, was die Schnittstelle ohne Nexviews
Oberflaeche liest - dieselbe Bauweise wie bei den Fehlermeldungen
(``code`` plus deutscher Rueckfall).

⚠️ **Jede Kennung hier braucht einen Text in beiden Sprachdateien**, und die
Platzhalter muessen dieselben sein (``{grenze}`` hier, ``{{grenze}}`` dort).
``tests/test_seerr_saetze.py`` haelt das; wer eine Kennung nur hier anlegt,
sieht auf dem Bildschirm den deutschen Rueckfall in der englischen
Oberflaeche - genau den Fehler, den diese Datei beheben soll.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Der deutsche Wortlaut je Kennung. Platzhalter in ``str.format``-Schreibweise.
VORLAGEN: dict[str, str] = {
    # ---- Fassung ----------------------------------------------------------
    "fassung_unlesbar": "Diese Installation nennt keine brauchbare Fassung.",
    "fassung_zu_alt": (
        "Seerr {fassung} ist älter als die älteste geprüfte Fassung {mindestens}."
    ),
    "fassung_zu_neu": (
        "Seerr {fassung} ist neuer als alles, wogegen dieser Umzug geprüft wurde "
        "({geprueft})."
    ),
    # ---- Rollen -----------------------------------------------------------
    "rolle_verlust_teilweise": (
        "Durfte in Seerr auch Einstellungen oder Konten verwalten. Dafür gibt es in "
        "Nexview nur die Administrator-Rolle, und die vergibst du besser selbst."
    ),
    "rolle_verlust_kombination": (
        "Durfte in Seerr Einstellungen oder Konten verwalten, aber keine Anfragen "
        "entscheiden. Diese Kombination kennt Nexview nicht."
    ),
    # ---- Kontingente ------------------------------------------------------
    "kontingent_null": (
        "In Seerr stand 0, und das heißt dort „nicht zählen“, also ohne Grenze. "
        "Hier hieße die 0 „darf nichts“, deshalb wird daraus ausdrücklich „ohne Grenze“."
    ),
    "kontingent_staffeln": (
        "Seerr zählte {grenze} Staffeln, Nexview zählt {grenze} Anfragen. Eine Anfrage "
        "kann mehrere Staffeln umfassen, die Grenze ist hier also lockerer als drüben."
    ),
    "kontingent_zeitraum": (
        "Seerr rechnete {tage} Tage rückwärts ab jetzt, Nexview rechnet am Kalender. "
        "Der Zeitraum ist nicht derselbe."
    ),
    # ---- Anfragen (nur in der Vorlage, nicht in der Oberflaeche) ----------
    "zustand_fehlgeschlagen": (
        "In Seerr fehlgeschlagen. Nexview kennt dafür keinen Zustand, der dasselbe "
        "bedeutet."
    ),
    "zustand_unbekannt": "Unbekannter Zustand in Seerr: {status}",
    "anfrage_konto_weg": "Das Konto dazu gibt es in Seerr nicht mehr.",
    "anfrage_ohne_tvdb": (
        "Seerr kennt zu dieser Serie keine TVDB-Nummer. Ohne sie findet Nexview sie "
        "in Sonarr nicht wieder."
    ),
    "anfrage_ohne_tmdb": "Seerr kennt zu diesem Titel keine TMDB-Nummer.",
    # ---- Treffer ----------------------------------------------------------
    "treffer_plex": "Dieselbe Plex-Kennung, aus derselben Quelle.",
    "treffer_unsicher": (
        "Gleiche Kennung. Ob beide Installationen denselben Server meinen, kann "
        "Nexview nicht feststellen - Seerr schreibt es nicht auf."
    ),
    # ---- Was Nexview nicht aufnehmen kann ---------------------------------
    "nicht_dabei_watchlist": (
        "Merklisten. Nexview führt keine eigene, es zeigt die deines Medienservers "
        "live an. Es gibt hier keinen Ort dafür."
    ),
    "nicht_dabei_notification_targets": (
        "Persönliche Meldeadressen (Discord, Telegram, Pushover). In Seerr hängen sie "
        "am Konto, in Nexview gehören die Kanäle dem Haus."
    ),
    "nicht_dabei_override_rules": (
        "Seerrs Zielregeln. Sie steuern Server, Profil und Ordner. Nexviews Regeln "
        "entscheiden über freigeben oder ablehnen - gleicher Name, anderer Zweck."
    ),
    "nicht_dabei_discover_sliders": (
        "Die Reihen auf Seerrs Startseite. Nexviews Regale stehen fest."
    ),
    "nicht_dabei_passwords": (
        "Passwörter. Über die Schnittstelle gibt Seerr sie nicht heraus - im "
        "Konto-Datensatz gibt es kein Passwortfeld."
    ),
    "nie_tmdb": (
        "Der TMDB-Schlüssel. Seerr hat gar keinen einstellbaren, er steckt dort fest "
        "im Programm. Den trägst du selbst ein."
    ),
    "nie_passwoerter": (
        "Passwörter der Benutzer. Über die Schnittstelle gibt Seerr sie nicht heraus; "
        "im Konto-Datensatz gibt es kein Passwortfeld."
    ),
    "nie_historie": (
        "Die Anfragehistorie. Was in Radarr liegt, zeigt Nexview ohnehin als vorhanden "
        "an - verloren geht nur, wer es angefragt hat."
    ),
    # ---- Bereiche: Beschriftungen der Zeilen (l_) und Werte (w_) ----------
    "l_server_in_seerr": "Server in Seerr",
    "l_adresse_in_seerr": "Adresse in Seerr",
    "l_maschinenkennung": "Maschinenkennung",
    "l_server_kennung": "Server-Kennung",
    "l_in_seerr": "In Seerr",
    "l_adresse": "Adresse",
    "l_profil": "Profil",
    "l_ordner": "Ordner",
    "l_schluessel": "Schlüssel",
    "l_server": "Server",
    "l_anmeldung": "Anmeldung",
    "l_absender": "Absender",
    "l_region": "Region",
    "l_sprache": "Sprache",
    "l_filme_je_zeitraum": "Filme je Zeitraum",
    "l_serien_je_zeitraum": "Serien je Zeitraum",
    "l_gilt_fuer": "Gilt für",
    "l_nicht_fuer": "Nicht für",
    "l_gesperrte_titel": "Gesperrte Titel",
    "l_davon_ohne_namen": "Davon ohne Namen",
    "l_webhook": "Webhook",
    "l_token": "Token",
    "l_chat": "Chat",
    "l_topic": "Topic",
    "l_ziel": "Ziel",
    "w_ohne_namen": "ohne Namen",
    "w_kommt_mit": "kommt mit",
    "w_nicht_hinterlegt": "in Seerr nicht hinterlegt",
    "w_kommt_mit_passwort": "kommt mit, samt Passwort",
    "w_ohne_passwort": "ohne Passwort",
    "w_nicht_gesetzt": "nicht gesetzt",
    "w_neue_konten": "neue Konten",
    "w_die_aus_seerr": "die aus Seerr",
    "w_eingeschaltet": "eingeschaltet",
    "w_eingerichtet_aus": "eingerichtet, aber aus",
    # ---- Bereiche: Posten -------------------------------------------------
    "platz_radarr": "Filme · Radarr",
    "platz_radarr_uhd": "Filme in 4K · Radarr",
    "platz_sonarr": "Serien · Sonarr",
    "platz_sonarr_uhd": "Serien in 4K · Sonarr",
    "region_und_sprache": "Region und Sprache",
    "kontingent_vorgabe": "Kontingent-Vorgabe für neue Konten",
    "kanal": "{name}",
    # ---- Bereiche: Luecken ------------------------------------------------
    "kein_medienserver": "Seerr hat gar keinen Medienserver eingetragen.",
    "plex_token": (
        "Das Plex-Token gibt Seerr nicht heraus. Du meldest dich am Ende des "
        "Assistenten selbst bei Plex an, sobald dein Konto besteht; der Server mit "
        "dieser Kennung wird dir dort vorgeschlagen."
    ),
    "jf_schluessel": (
        "Den Schlüssel aus Seerr kann Nexview nicht benutzen: Es verbindet sich mit "
        "Benutzername und Passwort eines Administrators deines {dienst}-Servers. Das "
        "fragt der Assistent am Ende ab, sobald dein Konto besteht; die Adresse oben "
        "wird dort vorausgefüllt."
    ),
    "arr_uebrig": (
        "„{name}“ bleibt draußen: Nexview hat für Titel dieser Art nur einen Platz, "
        "und der ist vergeben."
    ),
    "arr_uebrig_uhd": (
        "„{name}“ bleibt draußen: Nexview hat für 4K-Titel dieser Art nur einen Platz, "
        "und der ist vergeben."
    ),
    "kein_mailserver": "In Seerr ist kein Mailserver eingetragen.",
    "mail_aus": (
        "In Seerr ist der Mailversand abgeschaltet. Die Zugangsdaten kommen trotzdem "
        "mit; ob Nexview verschickt, entscheidest du selbst."
    ),
    "sprache_unbekannt": (
        "Seerr steht auf „{sprache}“. Nexview spricht Deutsch und Englisch; es bleibt "
        "bei der Hausvorgabe."
    ),
    "vorgabe_null_filme": (
        "Filme je Zeitraum: In Seerr stand 0, was dort „ohne Grenze“ heißt. Als "
        "Hausvorgabe bleibt das Feld deshalb leer."
    ),
    "vorgabe_null_serien": (
        "Serien je Zeitraum: In Seerr stand 0, was dort „ohne Grenze“ heißt. Als "
        "Hausvorgabe bleibt das Feld deshalb leer."
    ),
    "allgemein_nichts": "In Seerr ist hier nichts eingestellt, was Nexview kennt.",
    "kanaele_abos": (
        "Übernommen wird der Weg, nicht was darüber geht: Nexview kennt andere "
        "Meldungen als Seerr (Rückmeldungen, Ticketcenter, Speicher). Was an welchen "
        "Kanal geschickt wird, stellst du danach unter Benachrichtigungen selbst ein."
    ),
    "kein_meldeweg": "In Seerr ist kein Meldeweg für das Haus eingerichtet.",
    "kanaele_ohne_gegenstueck": "Kein Gegenstück in Nexview: {namen}.",
    "sperrliste_leer": "In Seerr ist nichts gesperrt.",
    "sperrliste_etiketten_eins": (
        "Ein Eintrag sperrt in Seerr zusätzlich Etiketten. Dafür hat Nexview kein "
        "Gegenstück; die Titel kommen trotzdem mit."
    ),
    "sperrliste_etiketten": (
        "{anzahl} Einträge sperren in Seerr zusätzlich Etiketten. Dafür hat Nexview "
        "kein Gegenstück; die Titel kommen trotzdem mit."
    ),
    # ---- Abschluss --------------------------------------------------------
    "adresse_vergeben": "Diese Adresse gehört schon einem anderen Konto.",
}


@dataclass(frozen=True)
class Satz:
    """Ein Hinweis, wie er zur Oberflaeche geht: Kennung, Zahlen, Rueckfall."""

    kennung: str
    zahlen: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    # ``zahlen`` ist ein dict, und ein frozen dataclass wuerde daraus einen
    # unbrauchbaren Hash bauen. Gehasht wird, was den Satz ausmacht.
    def __hash__(self) -> int:
        return hash((self.kennung, self.text))

    def als_dict(self) -> dict[str, Any]:
        return {"kennung": self.kennung, "zahlen": dict(self.zahlen), "text": self.text}

    def __str__(self) -> str:
        return self.text


def satz(kennung: str, **zahlen: Any) -> Satz:
    """Einen Satz bauen. Eine unbekannte Kennung ist ein Programmierfehler."""
    return Satz(kennung=kennung, zahlen=dict(zahlen), text=VORLAGEN[kennung].format(**zahlen))


def anzeige(wert: object) -> str:
    """Ein Zeilenwert als Text - Rohwert oder der Rueckfall eines Satzes."""
    return wert.text if isinstance(wert, Satz) else str(wert)


__all__ = ["VORLAGEN", "Satz", "anzeige", "satz"]
