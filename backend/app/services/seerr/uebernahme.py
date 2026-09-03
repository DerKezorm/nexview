"""Seerrs Einstellungen auf Nexviews abbilden - in Bereichen, einzeln abwählbar.

⚠️ **Warum in Bereichen und nicht in einem Zug.** Ein Umzug ist keine
Entscheidung, sondern fünf: Wer schon einen Mailserver eingerichtet hat, will
den nicht überschrieben bekommen, und wer Radarr bewusst anders eingestellt
hat, erst recht nicht. Jeder Bereich steht deshalb für sich und lässt sich
überspringen.

⚠️ **Was hier durchgeht, sind Zugangsdaten.** An einer echten Installation
gemessen (03.09.2026): Seerr gibt das SMTP-Passwort und die Schlüssel von
Radarr und Sonarr im Klartext heraus. Nexview verschlüsselt sie beim Speichern
(``settings_service.SECRET_KEYS``), aber auf dem Weg dorthin liegen sie im
Arbeitsspeicher und dürfen **nirgends ins Protokoll**. Protokolliert werden
Bereichsnamen und Zählungen, nie Werte.

⚠️ **Zwei Dinge kommen nachweislich nicht mit.**

* **Das Plex-Token.** ``settings/plex`` liefert Name, Adresse, Port,
  Bibliotheken und die Maschinenkennung, aber kein Token; das hängt in Seerr am
  Konto und ist in der Schnittstelle ausgeblendet. Der Betreiber muss sich
  selbst bei Plex anmelden. Die Maschinenkennung ist trotzdem wertvoll: Sie
  sagt, **welchen** seiner Server er danach auswählen muss.
* **Der TMDB-Schlüssel.** Seerr hat gar keinen einstellbaren; er steckt dort
  fest im Programm. Nexview braucht einen eigenen, und das bleibt der eine
  Einrichtungsschritt, den dieser Umzug nicht abnehmen kann.

⚠️ **Bei Jellyfin und Emby hilft der Schlüssel auch nicht**, und der erste
Entwurf hat das Gegenteil behauptet. ``settings/jellyfin`` liefert zwar
``apiKey`` und ``serverId`` - nur nimmt Nexview für diese beiden Anbieter gar
keinen Schlüssel entgegen: Verbunden wird über ``connect/password`` mit
Benutzername und Passwort eines **Administrators** des Servers
(``mediaserver.login_with_password``, ``supports_password_login``). Einen Weg,
eine Verbindung aus einem fertigen Schlüssel zu bauen, gibt es nirgends. Was
mitkommt, ist also für alle drei Anbieter dasselbe: Art, Name und Adresse -
genug, um das Formular danach vorauszufüllen, nicht genug, um es zu ersparen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nexview.seerr")

#: Die Bereiche in der Reihenfolge, in der der Assistent sie zeigt.
#:
#: ⚠️ **Der Medienserver steht vorn, und das ist keine Kosmetik.** Eine
#: Plex-Verknüpfung am Konto nützt nichts, solange Nexview den Server nicht
#: kennt: Beim Anmelden fragt ``mediaserver_accounts.resolve`` nach Anbieter
#: und Kennung, und ohne Verbindung kommt der Weg gar nicht erst zustande.
#: Wer die Konten zuerst holt, baut Verknüpfungen ins Leere.
BEREICHE = ("medienserver", "dienste", "mail", "allgemein", "sperrliste", "kanaele")

#: Die Einzelposten, die es geben kann - Nexviews vier Arr-Plaetze.
#:
#: ⚠️ Sie stehen neben den Bereichen und nicht darin, weil sie einzeln
#: angehakt werden. Die Pruefung auf "unbekannter Bereich" muss beide Listen
#: kennen, sonst weist sie genau die Auswahl ab, die der Betreiber trifft.
POSTEN = (
    "vorgabe_region",
    "vorgabe_kontingent",
    "radarr",
    "radarr_uhd",
    "sonarr",
    "sonarr_uhd",
    "kanal_discord",
    "kanal_telegram",
    "kanal_gotify",
    "kanal_ntfy",
    "kanal_webhook",
)

#: Was der Betreiber ueberhaupt anhaken kann.
WAEHLBAR = BEREICHE + POSTEN


@dataclass
class Posten:
    """Ein einzeln anhakbarer Unterpunkt.

    ⚠️ **Gebraucht bei Radarr und Sonarr, und der Grund ist nicht Kosmetik.**
    Nexview hat vier Plaetze (Film und Serie, je normal und 4K), Seerr kann
    beliebig viele Instanzen fuehren. Ein einziger Haken fuer "die Dienste"
    zwaenge den Betreiber, alle vier zu nehmen oder keinen - und er saehe nicht,
    welche Seerr-Instanz auf welchem Nexview-Platz landet.
    """

    kennung: str
    beschriftung: str
    zeilen: list[tuple[str, str]] = field(default_factory=list)
    werte: dict[str, object] = field(default_factory=dict)
    #: Nur bei Meldewegen: was fuer ein ``ChannelTarget`` daraus wird.
    #:
    #: ⚠️ **Eigenes Feld, weil hier keine Einstellung geschrieben wird.** Ein
    #: Kanal ist eine Zeile in ``channel_targets``, bei Telegram und ntfy sogar
    #: zwei (Instanz und Postfach). Durch ``werte`` geschleust haette
    #: ``save_settings`` alles stillschweigend verworfen.
    kanal: dict[str, object] | None = None


@dataclass
class Bereich:
    """Ein abwählbarer Block der Übernahme."""

    kennung: str
    #: Was hier hineinkäme - Schlüssel aus ``settings_service.DEFAULTS``.
    werte: dict[str, object] = field(default_factory=dict)
    #: Was der Betreiber davon lesen soll, ohne die Werte selbst zu sehen.
    #: Je Zeile ein Paar aus Bezeichnung und einer **ungefährlichen** Anzeige.
    zeilen: list[tuple[str, str]] = field(default_factory=list)
    #: Was in diesem Bereich **nicht** mitkommt, mit Grund.
    luecken: list[str] = field(default_factory=list)
    #: Einzeln anhakbare Unterpunkte. Ist die Liste gefuellt, entscheidet der
    #: Betreiber je Posten statt fuer den ganzen Bereich.
    posten: list[Posten] = field(default_factory=list)
    #: Nur beim Medienserver: ``plex``, ``jellyfin`` oder ``emby``. Die
    #: Oberflaeche waehlt daran ihr Sinnbild, ohne Text auswerten zu muessen.
    anbieter: str = ""
    #: Nur bei der Sperrliste: die Zeilen, die angelegt wuerden.
    #:
    #: ⚠️ **Eigenes Feld, weil hier keine Einstellung geschrieben wird, sondern
    #: Datensaetze.** Sie durch ``werte`` zu schleusen haette geheissen, dass
    #: ``save_settings`` sie stillschweigend verwirft: Was nicht in ``DEFAULTS``
    #: steht, faellt dort heraus, ohne dass jemand etwas merkt.
    eintraege: list[dict[str, object]] = field(default_factory=list)
    #: Nur beim Medienserver: was das spaetere Verbinden vorausfuellen kann.
    #: ``{"art", "name", "adresse", "kennung"}``.
    #:
    #: ⚠️ **Getrennt von ``zeilen``, obwohl dieselben Angaben drinstehen.**
    #: ``zeilen`` ist Anzeigetext und darf sich jederzeit aendern - die
    #: Maschinenkennung steht dort abgekuerzt, die Adresse uebersetzt. Wer das
    #: Formular daraus fuellte, fuellte es irgendwann mit ``beispiel…``.
    verbindung: dict[str, str] = field(default_factory=dict)

    @property
    def leer(self) -> bool:
        """Gibt es hier überhaupt etwas zu sehen?

        ⚠️ **Auskunftszeilen zählen mit.** Der Medienserver-Bereich schreibt
        nichts (das Plex-Token gibt Seerr nicht heraus), trägt aber die
        Angaben, an denen der Betreiber seinen Server später wiedererkennt.
        Ohne ``zeilen`` in dieser Rechnung galt er als leer, und die Oberfläche
        meldete „hier ist nichts eingestellt" - direkt über dem Satz, der auf
        eine Kennung verweist, die sie gerade verschwiegen hatte.
        """
        return not self.werte and not self.posten and not self.eintraege and not self.zeilen


def _geheim(wert: object) -> str:
    """Ein Geheimnis so anzeigen, dass man sieht, **dass** es da ist."""
    text = str(wert or "")
    return f"gesetzt ({len(text)} Zeichen)" if text else "nicht gesetzt"


def _arr_adresse(eintrag: dict[str, Any]) -> str:
    """Aus Rechnername, Port und SSL eine Adresse bauen.

    ⚠️ Seerr führt die drei Teile getrennt und bei Sonarr zusätzlich einen
    Basispfad. Wer sie falsch zusammensetzt, bekommt eine Adresse, die
    plausibel aussieht und nicht antwortet.
    """
    schema = "https" if eintrag.get("useSsl") else "http"
    rechner = str(eintrag.get("hostname") or "").strip()
    port = eintrag.get("port")
    basis = str(eintrag.get("baseUrl") or "").strip("/")
    adresse = f"{schema}://{rechner}"
    if port:
        adresse = f"{adresse}:{int(port)}"
    return f"{adresse}/{basis}" if basis else adresse


def _server_adresse(daten: dict) -> str:
    """Aus Rechner, Port und SSL die Adresse, wie Seerr sie kennt.

    ⚠️ **Nur zur Anzeige.** Es ist die Adresse aus *Seerrs* Netz; ob Nexview
    denselben Rechner unter demselben Namen erreicht, weiss hier niemand.
    Uebernommen wird sie nicht.
    """
    rechner = str(daten.get("ip") or daten.get("hostname") or "").strip()
    if not rechner:
        return ""
    schema = "https" if daten.get("useSsl") else "http"
    port = daten.get("port")
    return f"{schema}://{rechner}:{int(port)}" if port else f"{schema}://{rechner}"


def _medienserver(plex: dict, jellyfin: dict, main: dict) -> Bereich:
    """Welcher Medienserver drüben eingetragen ist - und was davon mitkommt.

    ⚠️ **Es gibt drei Anbieter, nicht einen.** Seerr läuft genauso an Jellyfin
    und an Emby wie an Plex (``MediaServerType`` 1/2/3, ``NOT_CONFIGURED`` ist
    die 4). Emby hat dabei **keine eigene Adresse** in Seerrs Schnittstelle: Es
    liest und schreibt seine Einstellungen unter ``settings/jellyfin``. Wer
    danach sucht, findet nichts und hält Emby für unbedienbar.

    ⚠️ **Übernommen wird bei allen dreien dasselbe: nichts.** Und der Grund ist
    je Anbieter ein anderer, was leicht zu einem falschen Satz führt. Bei Plex
    fehlt das Token - es hängt dort am Konto und ist in der Schnittstelle
    ausgeblendet. Bei Jellyfin und Emby liegt zwar ein ``apiKey`` vor, aber
    Nexview kennt keinen Weg, daraus eine Verbindung zu bauen: ``connect``
    läuft über plex.tv, ``connect/password`` über Benutzername und Passwort
    eines Server-Administrators. Eine Adresse für einen fertigen Schlüssel gibt
    es nicht.

    ⚠️ **Und ein Knopf kann hier bei keinem stehen.** Beide Wege verlangen
    einen angemeldeten Administrator, und den gibt es erst, wenn der Abschluss
    den Besitzer angelegt hat. Deshalb Auskunft jetzt, Verbinden als letzter
    Schritt des Assistenten - mit der Sitzung aus dem Abschluss, und mit den
    Angaben aus ``verbindung`` vorausgefüllt.
    """
    art = {1: "plex", 2: "jellyfin", 3: "emby"}.get(main.get("mediaServerType"))
    b = Bereich("medienserver")
    b.anbieter = art or ""
    if art is None:
        b.luecken.append("Seerr hat gar keinen Medienserver eingetragen.")
        return b

    # Emby steht mit unter ``settings/jellyfin``; eine eigene Adresse dafuer
    # hat Seerr nicht (gepruefte Fassung 3.4.1, ``server/routes/settings``).
    quelle = plex if art == "plex" else jellyfin
    kennung = str((quelle.get("machineId") if art == "plex" else quelle.get("serverId")) or "")
    adresse = _server_adresse(quelle)
    name = str(quelle.get("name") or "")

    b.zeilen.append(("Server in Seerr", name or "ohne Namen"))
    if adresse:
        b.zeilen.append(("Adresse in Seerr", adresse))
    if kennung:
        # Nur der Anfang: Er genuegt zum Wiedererkennen und verraet weniger
        # als die ganze Kennung.
        b.zeilen.append(
            ("Maschinenkennung" if art == "plex" else "Server-Kennung", kennung[:8] + "…")
        )

    # Was das spaetere Verbinden vorausfuellen kann. Kein Geheimnis darin:
    # weder Plex-Token noch Jellyfin-Schluessel gehen hier durch.
    b.verbindung = {"art": art, "name": name, "adresse": adresse, "kennung": kennung}

    if art == "plex":
        b.luecken.append(
            "Das Plex-Token gibt Seerr nicht heraus. Du meldest dich am Ende "
            "des Assistenten selbst bei Plex an, sobald dein Konto besteht; "
            "der Server mit dieser Kennung wird dir dort vorgeschlagen."
        )
        return b

    beschriftung = "Jellyfin" if art == "jellyfin" else "Emby"
    b.luecken.append(
        f"Den Schlüssel aus Seerr kann Nexview nicht benutzen: Es verbindet "
        "sich mit Benutzername und Passwort eines Administrators deines "
        f"{beschriftung}-Servers. Das fragt der Assistent am Ende ab, sobald "
        "dein Konto besteht; die Adresse oben wird dort vorausgefüllt."
    )
    return b


def _dienste(radarr: list[dict], sonarr: list[dict]) -> Bereich:
    """Radarr und Sonarr auf Nexviews vier Plaetze - je Platz ein Haken.

    ⚠️ **Vier Plaetze, beliebig viele Instanzen.** Nexview fuehrt Film und
    Serie, je normal und 4K. Seerr kann mehr. Genommen wird je Platz die als
    Standard markierte Instanz, sonst die erste; **alles Weitere wird
    namentlich gemeldet** statt stillschweigend verworfen. Wer drei Radarr
    fuehrt, soll lesen, welches der dritte war.
    """
    b = Bereich("dienste")
    benutzt: set[int] = set()

    def waehlen(liste: list[dict], uhd: bool) -> dict | None:
        passend = [
            e for i, e in enumerate(liste)
            if bool(e.get("is4k")) == uhd and id(e) not in benutzt
        ]
        if not passend:
            return None
        gewaehlt = next((e for e in passend if e.get("isDefault")), passend[0])
        benutzt.add(id(gewaehlt))
        return gewaehlt

    for art, liste, praefix in (("Radarr", radarr, "radarr"), ("Sonarr", sonarr, "sonarr")):
        for uhd in (False, True):
            eintrag = waehlen(liste, uhd)
            if eintrag is None:
                continue
            schluessel = f"{praefix}_uhd" if uhd else praefix
            was = "Filme" if praefix == "radarr" else "Serien"
            platz = f"{was} in 4K" if uhd else was
            posten = Posten(kennung=schluessel, beschriftung=f"{platz} · {art}")
            posten.werte[f"{schluessel}_url"] = _arr_adresse(eintrag)
            posten.werte[f"{schluessel}_api_key"] = str(eintrag.get("apiKey") or "")
            posten.werte[f"{schluessel}_name"] = str(eintrag.get("name") or art)
            ordner = str(eintrag.get("activeDirectory") or "")
            if ordner:
                ziel = "movie" if praefix == "radarr" else "series"
                posten.werte[f"default_{ziel}{'_uhd' if uhd else ''}_root"] = ordner
            posten.zeilen.append(("In Seerr", str(eintrag.get("name") or art)))
            posten.zeilen.append(("Adresse", _arr_adresse(eintrag)))
            if eintrag.get("activeProfileName"):
                posten.zeilen.append(("Profil", str(eintrag["activeProfileName"])))
            if ordner:
                posten.zeilen.append(("Ordner", ordner))
            # ⚠️ Nicht "gesetzt (32 Zeichen)". Die Zeichenzahl beantwortet keine
            # Frage, die jemand hat; "inklusive Schluessel" schon.
            posten.zeilen.append(
                (
                    "Schlüssel",
                    "kommt mit" if eintrag.get("apiKey") else "in Seerr nicht hinterlegt",
                )
            )
            b.posten.append(posten)

    uebrig = [e for e in (radarr + sonarr) if id(e) not in benutzt]
    for eintrag in uebrig:
        b.luecken.append(
            f"„{eintrag.get('name') or 'ohne Namen'}“ bleibt draußen: Nexview hat "
            f"für {'4K-' if eintrag.get('is4k') else ''}Titel dieser Art nur einen "
            "Platz, und der ist vergeben."
        )
    return b


def _mail(email: dict) -> Bereich:
    """Der Mailserver.

    ⚠️ **Der wertvollste Bereich, und der heikelste.** Er bringt das
    SMTP-Passwort im Klartext mit. Ohne Mailserver kann Nexview niemanden
    einladen, und ohne Einladung kommt kein übernommenes Konto herein - dieser
    Bereich ist also die Voraussetzung dafür, dass der Rest überhaupt trägt.
    """
    b = Bereich("mail")
    o = email.get("options") or {}
    if not o.get("smtpHost"):
        b.luecken.append("In Seerr ist kein Mailserver eingetragen.")
        return b

    sicherheit = "ssl" if o.get("secure") else ("starttls" if o.get("requireTls") else "none")
    b.werte.update(
        {
            "smtp_host": str(o.get("smtpHost") or ""),
            "smtp_port": int(o.get("smtpPort") or 587),
            "smtp_security": sicherheit,
            "smtp_username": str(o.get("authUser") or ""),
            "smtp_password": str(o.get("authPass") or ""),
            "smtp_from_address": str(o.get("emailFrom") or ""),
            "smtp_from_name": str(o.get("senderName") or ""),
        }
    )
    b.zeilen.append(("Server", f"{o.get('smtpHost')}:{o.get('smtpPort')} · {sicherheit}"))
    b.zeilen.append(
        (
            "Anmeldung",
            "kommt mit, samt Passwort" if o.get("authPass") else "ohne Passwort",
        )
    )
    b.zeilen.append(("Absender", str(o.get("emailFrom") or "nicht gesetzt")))
    if not email.get("enabled"):
        b.luecken.append(
            "In Seerr ist der Mailversand abgeschaltet. Die Zugangsdaten kommen "
            "trotzdem mit; ob Nexview verschickt, entscheidest du selbst."
        )
    return b


def _allgemein(main: dict) -> Bereich:
    """Region, Sprache und die Kontingent-Vorgabe des Hauses.

    ⚠️ **Zwei Haken, nicht einer, und der erste Entwurf hatte nur einen.** Er
    hiess „Region und Sprache übernehmen" und schrieb dabei still die
    Kontingent-Vorgabe mit - im selben Kasten stand „Filme je Zeitraum 5", auf
    das der Haken sich dem Wortlaut nach gar nicht bezog. Wer Region und
    Sprache wollte, bekam ungefragt eine Mengengrenze für jedes künftige Konto.

    ⚠️ **Und es ist wirklich eine Hausvorgabe, keine Verwechslung.** Seerr
    führt beides: ``defaultQuotas`` in ``settings/main`` und je Konto eigene
    Werte. Welcher gilt, entscheidet ``server/entity/User.ts``:
    ``this.movieQuotaLimit ?? defaultQuotas.movie.quotaLimit`` - das Konto
    gewinnt, die Vorgabe füllt die Lücke. Die Konto-Werte kommen im
    Benutzer-Schritt mit; hier steht nur, was für **neue** Konten gälte.
    """
    b = Bereich("allgemein")

    ort = Posten(kennung="vorgabe_region", beschriftung="Region und Sprache")
    region = str(main.get("discoverRegion") or "").upper()
    if region:
        ort.werte["default_region"] = region
        ort.zeilen.append(("Region", region))

    sprache = str(main.get("locale") or "").split("-")[0].lower()
    # ⚠️ Nexview kennt genau zwei Sprachen. Alles andere faellt auf die
    # Hausvorgabe; das stillschweigend zu tun waere die Sorte Ueberraschung,
    # die man erst Wochen spaeter bemerkt.
    if sprache in ("de", "en"):
        ort.werte["default_language"] = sprache
        ort.zeilen.append(("Sprache", sprache))
    elif sprache:
        b.luecken.append(
            f"Seerr steht auf „{sprache}“. Nexview spricht Deutsch und Englisch; "
            "es bleibt bei der Hausvorgabe."
        )
    if ort.werte:
        b.posten.append(ort)

    # ⚠️ **Seerrs Adresse nach aussen wird bewusst NICHT uebernommen.** Sie
    # zeigt auf Seerr, nicht auf Nexview - die beiden laufen auf verschiedenen
    # Anschluessen und meist unter verschiedenen Namen. Sie zu uebernehmen
    # hiesse, jede Einladungsmail auf die alte Anwendung zu verlinken. Nexview
    # fragt seine eigene Adresse deshalb selbst.

    menge = Posten(
        kennung="vorgabe_kontingent",
        beschriftung="Kontingent-Vorgabe für neue Konten",
    )
    vorgaben = main.get("defaultQuotas") or {}
    for seerr_art, nexview_schluessel, beschriftung in (
        ("movie", "quota_default_movies", "Filme je Zeitraum"),
        ("tv", "quota_default_series", "Serien je Zeitraum"),
    ):
        grenze = (vorgaben.get(seerr_art) or {}).get("quotaLimit")
        if grenze is None:
            continue
        # ⚠️ Dieselbe umgedrehte Null wie beim Konto-Kontingent: In Seerr heisst
        # 0 "nicht zaehlen", in Nexview "darf nichts". Als Hausvorgabe ist der
        # ehrliche Gegenwert "keine Vorgabe", also leer.
        if int(grenze) == 0:
            b.luecken.append(
                f"{beschriftung}: In Seerr stand 0, was dort „ohne Grenze“ heißt. "
                "Als Hausvorgabe bleibt das Feld deshalb leer."
            )
            continue
        menge.werte[nexview_schluessel] = int(grenze)
        menge.zeilen.append((beschriftung, str(int(grenze))))
    if menge.werte:
        # ⚠️ **Zwei kurze Zeilen statt eines Satzes.** Die Wertspalte schneidet
        # ab; ein Satz, der genau an "nicht für die aus Seerr" abgeschnitten
        # wird, sagt dann das Gegenteil.
        menge.zeilen.append(("Gilt für", "neue Konten"))
        menge.zeilen.append(("Nicht für", "die aus Seerr"))
        b.posten.append(menge)

    if not b.posten:
        b.luecken.append("In Seerr ist hier nichts eingestellt, was Nexview kennt.")
    return b


def _kanaele(agenten: dict[str, dict]) -> Bereich:
    """Seerrs Meldewege auf Nexviews Kanaele.

    ⚠️ **Das sind die Wege des Hauses, nicht die der Benutzer.** Seerr fuehrt
    beides: je Konto eigene Adressen (Discord, Telegram, Pushover) und daneben
    Einstellungen fuer die ganze Installation. Nur die zweiten haben in Nexview
    ein Gegenstueck - dort gehoeren Kanaele grundsaetzlich dem Haus. Die
    persoenlichen Adressen bleiben also draussen, und das ist kein Versaeumnis,
    sondern ein Unterschied im Aufbau.

    ⚠️ **Was NICHT mitkommt: welche Meldungen ueber den Kanal gehen.** Seerr
    kennt zwoelf Arten (wartende Anfrage, freigegeben, verfuegbar,
    fehlgeschlagen, abgelehnt, automatisch freigegeben, dazu vier zu
    gemeldeten Problemen). Nexview kennt andere - Rueckmeldungen zur Qualitaet,
    das Ticketcenter, Speicher-Abgaben, Medienserver-Hinweise. Eine Abbildung
    waere zur Haelfte geraten, und geraten wird hier nichts. Der Betreiber
    stellt danach selbst ein, was wohin geht.

    Zwei Dienste haben zwei Ebenen: Bei Telegram traegt die Instanz das Token
    und das Postfach den Chat, bei ntfy traegt die Instanz die Adresse und das
    Postfach das Topic.
    """
    b = Bereich("kanaele")

    def opt(name: str) -> dict:
        return (agenten.get(name) or {}).get("options") or {}

    def dazu(
        kennung: str,
        beschriftung: str,
        eltern: dict[str, str],
        kind: dict[str, str] | None,
        zeilen: list[tuple[str, str]],
        an: bool,
    ) -> None:
        posten = Posten(kennung=f"kanal_{kennung}", beschriftung=beschriftung)
        posten.kanal = {"art": kennung, "eltern": eltern, "kind": kind or {}}
        posten.zeilen = list(zeilen)
        posten.zeilen.append(
            ("In Seerr", "eingeschaltet" if an else "eingerichtet, aber aus")
        )
        b.posten.append(posten)

    d = opt("discord")
    if d.get("webhookUrl"):
        dazu(
            "discord",
            "Discord",
            {"url": str(d["webhookUrl"]), "username": "Nexview"},
            None,
            [("Webhook", "kommt mit")],
            bool((agenten.get("discord") or {}).get("enabled")),
        )

    t = opt("telegram")
    if t.get("botAPI"):
        dazu(
            "telegram",
            "Telegram",
            {"token": str(t["botAPI"]), "username": "Nexview"},
            {
                "chat_id": str(t.get("chatId") or ""),
                "thread_id": str(t.get("messageThreadId") or ""),
            },
            [
                ("Token", "kommt mit"),
                ("Chat", str(t.get("chatId") or "nicht gesetzt")),
            ],
            bool((agenten.get("telegram") or {}).get("enabled")),
        )

    g = opt("gotify")
    if g.get("url") and g.get("token"):
        dazu(
            "gotify",
            "Gotify",
            {"url": str(g["url"]), "token": str(g["token"])},
            None,
            [("Server", str(g["url"])), ("Token", "kommt mit")],
            bool((agenten.get("gotify") or {}).get("enabled")),
        )

    n = opt("ntfy")
    if n.get("url"):
        dazu(
            "ntfy",
            "ntfy",
            {"url": str(n["url"]), "auth": "keine"},
            {"topic": str(n.get("topic") or "")},
            [("Server", str(n["url"])), ("Topic", str(n.get("topic") or "nicht gesetzt"))],
            bool((agenten.get("ntfy") or {}).get("enabled")),
        )

    w = opt("webhook")
    if w.get("webhookUrl"):
        dazu(
            "webhook",
            "Webhook",
            {"url": str(w["webhookUrl"])},
            None,
            [("Ziel", str(w["webhookUrl"]))],
            bool((agenten.get("webhook") or {}).get("enabled")),
        )

    if b.posten:
        b.luecken.append(
            "Übernommen wird der Weg, nicht was darüber geht: Nexview kennt "
            "andere Meldungen als Seerr (Rückmeldungen, Ticketcenter, "
            "Speicher). Was an welchen Kanal geschickt wird, stellst du danach "
            "unter Benachrichtigungen selbst ein."
        )
    else:
        b.luecken.append("In Seerr ist kein Meldeweg für das Haus eingerichtet.")

    ohne_gegenstueck = [
        name
        for name in ("slack", "pushover", "pushbullet", "webpush")
        if (agenten.get(name) or {}).get("options")
        and any((agenten.get(name) or {}).get("options", {}).values())
    ]
    if ohne_gegenstueck:
        b.luecken.append(
            "Kein Gegenstück in Nexview: " + ", ".join(sorted(ohne_gegenstueck)) + "."
        )
    return b


def _sperrliste(eintraege: list[dict[str, Any]]) -> Bereich:
    """Seerrs Sperrliste - der sauberste Fall des ganzen Umzugs.

    Fuenf von sechs Feldern passen eins zu eins: Art, TMDB-Nummer, Titel,
    Zeitpunkt und wer gesperrt hat. Fuer Seerrs gesperrte **Etiketten** hat
    Nexview kein Gegenstueck.

    ⚠️ **``blocked_by`` bleibt leer, und das ist kein Kompromiss.** Nexview
    laesst das Feld ausdruecklich zu, mit der Begruendung, die Sperre sei "eine
    Entscheidung ueber den Titel, nicht ueber die Person" (siehe
    ``models.Blocked``). Beim Umzug existiert ohnehin noch kein Konto, auf das
    man zeigen koennte.
    """
    b = Bereich("sperrliste")
    mit_etiketten = 0
    for eintrag in eintraege:
        art = str(eintrag.get("mediaType") or "")
        tmdb = eintrag.get("tmdbId")
        if art not in ("movie", "tv") or tmdb is None:
            continue
        if eintrag.get("blocklistedTags"):
            mit_etiketten += 1
        b.eintraege.append(
            {
                "media_type": art,
                "tmdb_id": int(tmdb),
                "title": str(eintrag.get("title") or ""),
            }
        )

    if b.eintraege:
        b.zeilen.append(("Gesperrte Titel", str(len(b.eintraege))))
        ohne_titel = sum(1 for e in b.eintraege if not e["title"])
        if ohne_titel:
            b.zeilen.append(("Davon ohne Namen", str(ohne_titel)))
    else:
        b.luecken.append("In Seerr ist nichts gesperrt.")

    if mit_etiketten:
        b.luecken.append(
            (
                "Ein Eintrag sperrt"
                if mit_etiketten == 1
                else f"{mit_etiketten} Einträge sperren"
            )
            + " in Seerr zusätzlich Etiketten. Dafür hat Nexview kein "
            "Gegenstück; die Titel kommen trotzdem mit."
        )
    return b


def bereiche_bauen(
    *,
    main: dict[str, Any],
    plex: dict[str, Any],
    jellyfin: dict[str, Any],
    radarr: list[dict[str, Any]],
    sonarr: list[dict[str, Any]],
    email: dict[str, Any],
    sperrliste: list[dict[str, Any]] | None = None,
    agenten: dict[str, dict] | None = None,
) -> list[Bereich]:
    """Alles Gelesene in abwählbare Blöcke schneiden.

    Reine Rechnung: keine Datenbank, kein Netz. Damit lässt sie sich gegen
    erfundene Antworten prüfen, und genau das ist nötig - eine echte kleine
    Installation hat weder 4K-Instanzen noch abweichende Sprachen noch
    Haus-Kontingente.
    """
    gebaut = [
        _medienserver(plex, jellyfin, main),
        _dienste(radarr, sonarr),
        _mail(email),
        _allgemein(main),
        _sperrliste(sperrliste or []),
        _kanaele(agenten or {}),
    ]
    logger.info(
        "Seerr takeover areas: %s",
        ", ".join(
            f"{b.kennung}={len(b.werte) + len(b.posten) + len(b.eintraege)}"
            for b in gebaut
        ),
    )
    return gebaut


#: Was dieser Umzug grundsätzlich nicht abnehmen kann, unabhängig vom Bestand.
NIE_DABEI = (
    (
        "Der TMDB-Schlüssel. Seerr hat gar keinen einstellbaren, er steckt dort "
        "fest im Programm. Den trägst du selbst ein."
    ),
    (
        "Passwörter der Benutzer. Über die Schnittstelle gibt Seerr sie nicht "
        "heraus; im Konto-Datensatz gibt es kein Passwortfeld."
    ),
    (
        "Die Anfragehistorie. Was in Radarr liegt, zeigt Nexview ohnehin als "
        "vorhanden an - verloren geht nur, wer es angefragt hat."
    ),
)


__all__ = [
    "BEREICHE",
    "NIE_DABEI",
    "POSTEN",
    "WAEHLBAR",
    "Bereich",
    "Posten",
    "bereiche_bauen",
]
