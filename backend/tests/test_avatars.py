"""Profilbilder: was hochgeladen werden darf - und was nicht."""

from __future__ import annotations

from fastapi.testclient import TestClient

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20


def _upload(client: TestClient, inhalt: bytes, name: str = "bild.png"):
    return client.post("/api/auth/me/avatar", files={"file": (name, inhalt, "image/png")})


def test_png_wird_angenommen(admin_client: TestClient) -> None:
    response = _upload(admin_client, PNG)
    assert response.status_code == 200
    assert response.json()["avatar_url"].startswith("/api/users/avatar/")


def test_jpeg_und_webp_werden_angenommen(admin_client: TestClient) -> None:
    assert _upload(admin_client, JPEG, "bild.jpg").status_code == 200
    assert _upload(admin_client, WEBP, "bild.webp").status_code == 200


def test_kein_bild_wird_abgelehnt(admin_client: TestClient) -> None:
    response = _upload(admin_client, b"<script>alert(1)</script>", "boese.png")
    assert response.status_code == 400
    assert "PNG" in response.json()["detail"]


def test_svg_wird_abgelehnt(admin_client: TestClient) -> None:
    """SVG darf Skripte enthalten - deshalb bewusst nicht erlaubt."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert _upload(admin_client, svg, "bild.svg").status_code == 400


def test_zu_grosses_bild_wird_abgelehnt(admin_client: TestClient) -> None:
    riesig = PNG + b"\x00" * (3 * 1024 * 1024)
    response = _upload(admin_client, riesig)
    assert response.status_code == 400
    assert "groß" in response.json()["detail"]


def test_leere_datei(admin_client: TestClient) -> None:
    assert _upload(admin_client, b"").status_code == 400


def test_bild_kann_abgerufen_werden(admin_client: TestClient) -> None:
    url = _upload(admin_client, PNG).json()["avatar_url"]

    # Ohne Anmeldung abrufbar - <img> schickt keinen Token mit.
    response = admin_client.get(url, headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_pfad_tricks_greifen_nicht(admin_client: TestClient) -> None:
    for versuch in (
        "..%2F..%2Fnexview.db",
        "%2E%2E%2Fsecret.key",
        "unbekannt.png",
    ):
        assert admin_client.get(f"/api/users/avatar/{versuch}").status_code == 404


def test_unbekannte_api_adresse_liefert_keine_webseite(admin_client: TestClient) -> None:
    """Sonst käme bei einem Tippfehler die HTML-Oberfläche mit Status 200."""
    response = admin_client.get("/api/gibtesnicht")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_neues_bild_ersetzt_das_alte(admin_client: TestClient) -> None:
    erste = _upload(admin_client, PNG).json()["avatar_url"]
    zweite = _upload(admin_client, JPEG, "neu.jpg").json()["avatar_url"]

    assert erste != zweite
    # Das alte Bild ist weg.
    assert admin_client.get(erste).status_code == 404
    assert admin_client.get(zweite).status_code == 200


def test_bild_entfernen(admin_client: TestClient) -> None:
    url = _upload(admin_client, PNG).json()["avatar_url"]

    response = admin_client.delete("/api/auth/me/avatar")
    assert response.status_code == 200
    assert response.json()["avatar_url"] is None
    assert admin_client.get(url).status_code == 404


def test_ohne_anmeldung_kein_upload(client: TestClient) -> None:
    assert client.post("/api/auth/me/avatar", files={"file": ("a.png", PNG, "image/png")}).status_code == 401
