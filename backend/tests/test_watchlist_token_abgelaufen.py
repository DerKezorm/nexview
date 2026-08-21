"""Ein abgelehntes Merklisten-Token sichtbar machen.

Der Fall: Plex nimmt den persoenlichen Zugang nicht mehr an - nach einem
Passwortwechsel oder einem "ueberall abmelden". Vorher blieb das unsichtbar,
weil das Token **nicht** geloescht wird und ``watchlist_connected`` deshalb
weiter wahr war. Die Oberflaeche konnte "abgelaufen" nicht von "verbunden"
unterscheiden.
"""

from __future__ import annotations

from app.models import User
from app.services import mediaserver_accounts as konten


def nutzer(**felder: object) -> User:
    grund = {"username": "user", "watchlist_token": "verschluesselt"}
    grund.update(felder)
    return User(**grund)


def test_abgelehntes_token_wird_vermerkt() -> None:
    person = nutzer()
    assert person.watchlist_token_invalid is False

    assert konten.token_abgelehnt(person) is True
    assert person.watchlist_token_invalid is True
    # Das Token bleibt stehen: Geloescht saehe es aus wie "nie verbunden".
    assert person.watchlist_token == "verschluesselt"


def test_zweite_ablehnung_schreibt_nicht_erneut() -> None:
    """Der Abgleich laeuft stuendlich - der Zeitpunkt soll der erste bleiben."""
    person = nutzer()
    konten.token_abgelehnt(person)
    zuerst = person.watchlist_token_invalid_at

    assert konten.token_abgelehnt(person) is False
    assert person.watchlist_token_invalid_at == zuerst


def test_ohne_token_kein_hinweis() -> None:
    """Wer Plex nie verbunden hat, darf keinen Balken bekommen.

    Einer der beiden Fallstricke: Der Hinweis darf nur bei Leuten erscheinen,
    die ueberhaupt einmal verbunden waren.
    """
    person = nutzer(watchlist_token=None)
    konten.token_abgelehnt(person)
    assert person.watchlist_token_invalid is False


def test_neues_token_raeumt_den_hinweis_weg() -> None:
    """Nach der Neuanmeldung muss der Balken sofort verschwinden.

    ``merke_token`` ist bewusst die Stelle dafuer: Durch sie laufen **alle**
    vier Wege, auf denen ein Token entsteht - Anmeldung mit Plex, Verknuepfen
    im Profil, Server-Anbindung und die Merklisten-Anmeldung. Nur im
    Merklisten-Endpunkt zurueckzusetzen liesse den Hinweis nach den anderen
    drei Wegen stehen.
    """
    person = nutzer()
    konten.token_abgelehnt(person)

    konten.merke_token(person, "neu-verschluesselt")

    assert person.watchlist_token_invalid is False
    assert person.watchlist_token == "neu-verschluesselt"


def test_leeres_token_raeumt_nichts_weg() -> None:
    """Ein Aufrufer ohne Token soll nichts ueberschreiben - auch den Hinweis nicht."""
    person = nutzer()
    konten.token_abgelehnt(person)

    konten.merke_token(person, None)

    assert person.watchlist_token_invalid is True


def test_trennen_raeumt_alles_weg() -> None:
    person = nutzer(
        password_hash="x", email="a@b.de", email_verified=True,
        mediaserver_provider="plex", mediaserver_account_id="1",
    )
    konten.token_abgelehnt(person)

    konten.unlink(person)

    assert person.watchlist_token is None
    assert person.watchlist_token_invalid_at is None


def test_protokollzeile_nennt_den_anzeigenamen() -> None:
    """Ein Konto namens "user" liest sich sonst wie ein Platzhalter."""
    from app.services.mediaserver_watched import _wer

    assert _wer(nutzer(display_name="Markus")) == "user (Markus)"


def test_ohne_anzeigenamen_bleibt_es_beim_benutzernamen() -> None:
    from app.services.mediaserver_watched import _wer

    assert _wer(nutzer(display_name=None)) == "user"
    # Gleicher Name doppelt waere nur Laerm.
    assert _wer(nutzer(display_name="user")) == "user"


def test_erfolg_nimmt_den_hinweis_zurueck() -> None:
    """Sonst bliebe der Balken stehen, obwohl die Ursache behoben ist.

    Genau das ist passiert: Der eigentliche Fehler lag gar nicht am Token
    (Plex verlangt von geteilten Konten ein server-eigenes Zugriffs-Token).
    Nach der Behebung lief der Abgleich wieder - der Hinweis blieb aber, weil
    ihn nichts zuruecknahm.
    """
    person = nutzer()
    konten.token_abgelehnt(person)
    assert person.watchlist_token_invalid is True

    assert konten.token_geht_wieder(person) is True
    assert person.watchlist_token_invalid is False
    # Beim zweiten Mal gibt es nichts mehr zu tun.
    assert konten.token_geht_wieder(person) is False
