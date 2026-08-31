"""Zielordner und Qualitätsprofil gehören zusammen.

Der Fehler dahinter: ``approver_picks_target`` verodert die beiden
Einstellungen — sobald **eine** auf „Entscheider" steht, wartet die ganze
Anfrage auf ihn, und *beide* Felder werden erst bei der Freigabe gesetzt. Die
andere Einstellung auf „der Benutzer wählt" stehen zu lassen war damit eine
Einstellung ohne jede Wirkung.

Nachgemessen an einer echten Installation: Dort stand für Filme „Zielordner:
der Benutzer wählt" — und eine Anfrage, die den Ordner ausdrücklich mitschickte,
bekam ihn kommentarlos entfernt.

Die Datenbank darf diese Kombination deshalb gar nicht erst enthalten.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _modi(client: TestClient) -> dict[str, str]:
    daten = client.get("/api/settings").json()
    return {
        schluessel: daten[schluessel]
        for schluessel in (
            "movie_root_folder_mode",
            "movie_profile_mode",
            "series_root_folder_mode",
            "series_profile_mode",
        )
    }


def test_ordner_auf_entscheider_zieht_das_profil_mit(admin_client: TestClient) -> None:
    antwort = admin_client.put("/api/settings", json={"movie_root_folder_mode": "approver"})
    assert antwort.status_code == 200, antwort.json()

    modi = _modi(admin_client)
    assert modi["movie_root_folder_mode"] == "approver"
    assert modi["movie_profile_mode"] == "approver", (
        "Das Profil muss mitziehen - sonst stünde dort eine Einstellung ohne Wirkung"
    )


def test_profil_auf_entscheider_zieht_den_ordner_mit(admin_client: TestClient) -> None:
    admin_client.put("/api/settings", json={"series_profile_mode": "approver"})

    modi = _modi(admin_client)
    assert modi["series_profile_mode"] == "approver"
    assert modi["series_root_folder_mode"] == "approver"


def test_zurueck_geht_auch(admin_client: TestClient) -> None:
    """Aus „Entscheider" muss man wieder herauskommen.

    Zöge nur die Hinrichtung, säße man für immer fest: Jeder Versuch, eines der
    beiden zurückzustellen, würde vom anderen sofort wieder eingefangen.
    """
    admin_client.put("/api/settings", json={"movie_root_folder_mode": "approver"})
    assert _modi(admin_client)["movie_profile_mode"] == "approver"

    admin_client.put("/api/settings", json={"movie_root_folder_mode": "user"})

    modi = _modi(admin_client)
    assert modi["movie_root_folder_mode"] == "user"
    assert modi["movie_profile_mode"] != "approver", (
        "Das Profil hängt sonst weiter beim Entscheider - und die neue "
        "Einstellung wäre erneut wirkungslos"
    )


def test_beide_zusammen_muessen_stimmig_sein(admin_client: TestClient) -> None:
    """Wer beide schickt, muss sie stimmig schicken.

    Hier still etwas anderes zu speichern als verlangt wäre genau der Fehler,
    den diese Regel behebt - also lieber eine klare Absage.
    """
    antwort = admin_client.put(
        "/api/settings",
        json={"movie_root_folder_mode": "user", "movie_profile_mode": "approver"},
    )
    assert antwort.status_code == 422
    detail = antwort.json()["detail"]
    assert detail["code"] == "target_and_profile_together"
    assert "zusammen" in detail["message"]


def test_stimmige_paare_gehen_durch(admin_client: TestClient) -> None:
    for ordner, profil in (("user", "user"), ("fixed", "user"), ("approver", "approver")):
        antwort = admin_client.put(
            "/api/settings",
            json={"movie_root_folder_mode": ordner, "movie_profile_mode": profil},
        )
        assert antwort.status_code == 200, f"{ordner}/{profil}: {antwort.json()}"
        modi = _modi(admin_client)
        assert modi["movie_root_folder_mode"] == ordner
        assert modi["movie_profile_mode"] == profil


def test_die_verbotene_kombination_kommt_nicht_in_die_datenbank(
    admin_client: TestClient,
) -> None:
    """Der eigentliche Zweck: Nach jedem Weg ist der Bestand stimmig."""
    for rumpf in (
        {"movie_root_folder_mode": "approver"},
        {"movie_profile_mode": "user"},
        {"series_profile_mode": "approver"},
        {"series_root_folder_mode": "fixed"},
    ):
        admin_client.put("/api/settings", json=rumpf)
        modi = _modi(admin_client)
        for art in ("movie", "series"):
            ordner = modi[f"{art}_root_folder_mode"] == "approver"
            profil = modi[f"{art}_profile_mode"] == "approver"
            assert ordner == profil, (
                f"nach {rumpf}: {art} steht auf Ordner={modi[f'{art}_root_folder_mode']}, "
                f"Profil={modi[f'{art}_profile_mode']} - eines davon wirkungslos"
            )
