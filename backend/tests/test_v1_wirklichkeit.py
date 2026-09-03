"""Was die Beschreibung verspricht, kommt auch wirklich heraus.

⚠️ **Der Unterschied zu ``test_v1_zusage.py``.** Der haelt die Form fest, die
im OpenAPI-Dokument steht - also das, was der Code *behauptet*. Dieser hier
ruft die Adressen tatsaechlich auf und vergleicht, was zurueckkommt.

Warum das noetig ist, obwohl das Schema doch aus dem Code entsteht:

- Wo ein ``response_model`` steht, filtert FastAPI die Antwort danach. Dort
  koennen Beschreibung und Wirklichkeit gar nicht auseinanderlaufen.
- Wo **keines** steht - ``def offene_anzahl(...) -> dict[str, int]`` - sagt
  das Schema ueber die Schluesselnamen **nichts**. Das Dokument behauptet
  "irgendein Objekt", die Antwort hat aber einen konkreten Namen, und den
  erfaehrt niemand. Vier der fuenfzehn zugesagten Adressen sind so gebaut.

Und genau dort kann eine Zusage stillschweigend brechen: Wird aus
``{"count": 3}`` morgen ``{"offen": 3}``, merkt es kein Test - der Abdruck
haelt fuer diese vier eine **leere** Feldliste fest.

Der Nutzer hat darauf bestanden, dass die Richtigkeit bei jedem Release
geprueft wird und nicht bloss behauptet (26.08.2026). Dieser Test laeuft in
der Testreihe, die vor jedem Abbild-Bau durchlaeuft - also bei jedem Push und
jedem Tag.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routers.v1 import ZUGESAGT

#: Adressen, die sich ohne Platzhalter und ohne fremden Dienst aufrufen lassen.
#:
#: ⚠️ **Die uebrigen werden benannt, nicht verschwiegen.** Ein Test, der still
#: die Haelfte auslaesst, sieht gruen aus und prueft nichts. ``test_nichts_
#: wird_still_uebergangen`` haelt fest, welche fehlen und warum.
OHNE_VORAUSSETZUNG = {
    "/api/v1/about",
    "/api/v1/health",
    "/api/v1/requests/mine",
    "/api/v1/requests/quota",
    "/api/v1/home/recent",
    "/api/v1/tickets/open-count",
    "/api/v1/admin/requests/pending/count",
    "/api/v1/notifications/unread/count",
    "/api/v1/storage/me",
    "/api/v1/dashboard",
    "/api/v1/me",
}

#: Warum die uebrigen vier nicht aufgerufen werden.
BEGRUENDUNG = {
    "/api/v1/search/{media_type}": "braucht TMDB",
    "/api/v1/media/{media_type}/{tmdb_id}": "braucht TMDB",
    "/api/v1/requests": "POST - wuerde etwas anlegen",
    "/api/v1/requests/{request_id}/cancel": "braucht eine bestehende Anfrage",
}


def _dokumentierte_felder(pfad: str) -> set[str] | None:
    """Welche Felder das OpenAPI-Dokument fuer diese Antwort nennt.

    ``None`` heisst: Das Dokument nennt ueberhaupt keine - etwa bei einem
    blanken ``dict[str, int]``.
    """
    dokument = app.openapi()
    schemas = dokument.get("components", {}).get("schemas", {})
    operation = dokument["paths"][pfad]["get"]
    schema = (
        operation.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if schema.get("type") == "array":
        schema = schema.get("items", {})
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return set(schemas.get(name, {}).get("properties", {})) or None
    return set(schema.get("properties", {})) or None


def _echte_felder(antwort) -> set[str] | None:
    """Die Schluessel der tatsaechlichen Antwort - auch bei einer Liste."""
    if isinstance(antwort, list):
        return set(antwort[0]) if antwort and isinstance(antwort[0], dict) else None
    if isinstance(antwort, dict):
        return set(antwort)
    return None


class TestDieAntwortPasstZurBeschreibung:
    def test_kein_versprochenes_feld_fehlt(self, admin_client: TestClient) -> None:
        """⚠️ **Die Richtung, die weh tut.**

        Steht ein Feld in der Beschreibung und kommt es nicht, hat jemand
        dagegen gebaut und bekommt ``KeyError``. Andersherum - ein Feld mehr
        als beschrieben - ist unschoen, aber bricht nichts.
        """
        fehlend: list[str] = []
        for pfad in sorted(OHNE_VORAUSSETZUNG):
            beschrieben = _dokumentierte_felder(pfad)
            if beschrieben is None:
                continue

            antwort = admin_client.get(pfad)
            assert antwort.status_code == 200, f"{pfad}: {antwort.text[:160]}"
            echt = _echte_felder(antwort.json())
            if echt is None:
                # Leere Liste - daraus laesst sich nichts ablesen, und das ist
                # kein Fehler. Es wird nur nicht geprueft.
                continue

            for feld in sorted(beschrieben - echt):
                fehlend.append(f"{pfad}: '{feld}' ist beschrieben, kommt aber nicht")

        assert fehlend == [], (
            "Die Beschreibung verspricht Felder, die die Antwort nicht hat:\n  "
            + "\n  ".join(fehlend)
        )

    def test_die_zaehl_adressen_verraten_ihren_schluessel(
        self, admin_client: TestClient
    ) -> None:
        """⚠️ **Die vier Adressen, ueber die das Dokument schweigt.**

        ``dict[str, int]`` heisst im Schema "irgendein Objekt". Wer die
        Beschreibung liest, erfaehrt den Schluesselnamen nicht - er muss
        raten oder ausprobieren.

        Dieser Test haelt den Namen fest. Wird er umbenannt, schlaegt er an,
        und dann ist zu entscheiden: entweder zurueckbenennen, oder es ist ein
        bewusster Bruch der Zusage und braucht ein ``/api/v2``.
        """
        # ⚠️ **Die Namen sind hier nachgeschlagen, nicht geraten.** Beim ersten
        # Lauf standen hier ueberall "count" - und der Test hat sofort
        # widersprochen: Es sind "pending" und "unread". Genau deshalb gibt es
        # ihn: Diese Namen stehen in keinem Schema, in keiner Beschreibung und
        # in keinem Abdruck. Sie existierten bis eben nur im Quelltext.
        erwartet = {
            "/api/v1/tickets/open-count": "count",
            "/api/v1/admin/requests/pending/count": "pending",
            "/api/v1/notifications/unread/count": "unread",
            "/api/v1/health": "status",
        }
        abweichungen: list[str] = []
        for pfad, schluessel in sorted(erwartet.items()):
            antwort = admin_client.get(pfad)
            assert antwort.status_code == 200, f"{pfad}: {antwort.text[:160]}"
            daten = antwort.json()
            if schluessel not in daten:
                abweichungen.append(
                    f"{pfad}: erwartet '{schluessel}', bekommen {sorted(daten)}"
                )

        assert abweichungen == [], (
            "Eine zugesagte Adresse hat ihren Schluessel geaendert. Das Schema "
            "merkt es nicht - dort steht nur 'Objekt'.\n  "
            + "\n  ".join(abweichungen)
            + "\n\nEntweder zurueckbenennen, oder es ist ein bewusster Bruch "
            "und braucht /api/v2."
        )

    def test_nichts_wird_still_uebergangen(self) -> None:
        """Jede zugesagte Adresse ist entweder geprueft oder begruendet.

        ⚠️ Ohne diesen Test waere die Abdeckung eine Behauptung. Kommt eine
        weitere Adresse dazu und niemand traegt sie ein, sieht der Test oben
        weiter gruen aus und prueft sie einfach nicht mit.
        """
        offen = set(ZUGESAGT) - OHNE_VORAUSSETZUNG - set(BEGRUENDUNG)
        assert offen == set(), (
            f"Diese zugesagten Adressen werden weder aufgerufen noch begruendet "
            f"uebergangen: {sorted(offen)}.\n"
            "Entweder in OHNE_VORAUSSETZUNG aufnehmen oder in BEGRUENDUNG "
            "eintragen, warum nicht."
        )
