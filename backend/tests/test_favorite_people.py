"""Personen mit dem Herz merken - anlegen, auflisten, entfernen.

Bewusst getrennt von den Titel-Favoriten: Personen haben keine Altersfreigabe
und keinen Bibliothekszustand, deshalb eine eigene Tabelle und eigene Wege.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import auth_headers, create_user

PFAD = "/api/favorites/people"


@pytest.fixture
def nutzer(admin_client: TestClient) -> dict[str, str]:
    create_user(admin_client, "lena")
    return auth_headers(admin_client, "lena", "passwort-1234")


def test_merken_und_auflisten(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    antwort = admin_client.post(
        PFAD,
        json={"person_id": 287, "name": "Brad Pitt", "department": "Acting"},
        headers=nutzer,
    )
    assert antwort.status_code == 201, antwort.text
    assert antwort.json()["person_id"] == 287

    liste = admin_client.get(PFAD, headers=nutzer).json()
    assert [p["person_id"] for p in liste] == [287]
    assert liste[0]["name"] == "Brad Pitt"


def test_zweimal_merken_ist_kein_fehler(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    admin_client.post(PFAD, json={"person_id": 287, "name": "Brad Pitt"}, headers=nutzer)
    zweite = admin_client.post(PFAD, json={"person_id": 287, "name": "Brad Pitt"}, headers=nutzer)
    assert zweite.status_code == 201
    assert len(admin_client.get(PFAD, headers=nutzer).json()) == 1


def test_entfernen(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    admin_client.post(PFAD, json={"person_id": 287, "name": "Brad Pitt"}, headers=nutzer)
    weg = admin_client.delete(f"{PFAD}/287", headers=nutzer)
    assert weg.status_code == 204
    assert admin_client.get(PFAD, headers=nutzer).json() == []


def test_entfernen_was_nicht_da_ist(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    assert admin_client.delete(f"{PFAD}/999", headers=nutzer).status_code == 404


def test_getrennt_von_titel_favoriten(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    """Eine gemerkte Person taucht nicht unter den Titel-Favoriten auf."""
    admin_client.post(PFAD, json={"person_id": 287, "name": "Brad Pitt"}, headers=nutzer)
    titel = admin_client.get("/api/favorites", headers=nutzer).json()
    assert titel == []


def test_jeder_sieht_nur_seine(admin_client: TestClient, nutzer: dict[str, str]) -> None:
    admin_client.post(PFAD, json={"person_id": 287, "name": "Brad Pitt"}, headers=nutzer)
    create_user(admin_client, "tom")
    tom = auth_headers(admin_client, "tom", "passwort-1234")
    assert admin_client.get(PFAD, headers=tom).json() == []


def test_nur_fuer_angemeldete(client: TestClient) -> None:
    assert client.get(PFAD, headers={"Authorization": ""}).status_code == 401
