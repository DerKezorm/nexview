"""Eine neu eingetragene Instanz wartet nicht auf die volle Stunde.

Befund #note-10, zweiter Blickwinkel: An pv-live wurden Radarr, Radarr 4K und
erst danach Sonarr eingerichtet. Der Speicher-Abgleich lief einmal, sofort
nachdem Radarr stand (3586 Posten, nur Filme) - und dann sechs Minuten lang
nicht wieder, weil er stuendlich gilt und das Eintragen von Sonarr daran
nichts aenderte. Bis zur naechsten vollen Stunde behauptete die Uebersicht,
vollstaendig zu sein, ohne dass irgendwo stand, dass eine gerade erst
eingerichtete Instanz noch nie gemessen wurde.

Die Reparatur: Speichern einer Einstellung, die eine Beschaffungs-Instanz
betrifft (Radarr, Sonarr, deren 4K-Gegenstuecke oder nexcrate), macht den
stuendlichen Speicher-Abgleich sofort faellig - dieselbe Vorziehung, die eine
erkannte Aufwertung schon nutzt (``status_poller._speicher_vorziehen``).
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.services import status_poller


def test_eine_neu_eingerichtete_instanz_macht_den_speicher_abgleich_sofort_faellig(
    admin_client: TestClient,
) -> None:
    """Radarr steht schon, der Abgleich gilt als eben gelaufen - dann kommt Sonarr dazu."""
    admin_client.put(
        "/api/settings",
        json={"radarr_url": "http://127.0.0.1:9", "radarr_api_key": "test-radarr-key"},
    )
    # Als waere der stuendliche Speicher-Abgleich gerade eben gelaufen.
    status_poller._speicher_zuletzt = time.monotonic()

    response = admin_client.put(
        "/api/settings",
        json={"sonarr_url": "http://127.0.0.1:9", "sonarr_api_key": "test-sonarr-key"},
    )

    assert response.status_code == 200
    assert status_poller._speicher_zuletzt == 0.0, (
        "Eine neu eingetragene Instanz muss den Speicher-Abgleich vorziehen, "
        "sonst zaehlt sie bis zu eine Stunde lang nicht mit, ohne Hinweis"
    )


def test_eine_unbeteiligte_einstellung_zieht_nichts_vor(admin_client: TestClient) -> None:
    """Nur Einstellungen, die eine Beschaffungs-Instanz betreffen, sind eilig."""
    status_poller._speicher_zuletzt = time.monotonic()
    gesetzt = status_poller._speicher_zuletzt

    response = admin_client.put("/api/settings", json={"default_region": "AT"})

    assert response.status_code == 200
    assert status_poller._speicher_zuletzt == gesetzt
