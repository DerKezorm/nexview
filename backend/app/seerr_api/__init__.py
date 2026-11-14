"""Seerr-kompatible Schnittstelle (Issue #17).

Viele Anwendungen koennen mit Seerr (frueher Overseerr/Jellyseerr) sprechen.
Diese Schicht antwortet so, wie Seerr antwortet, damit sie mit Nexview
sprechen koennen, ohne etwas von Nexview zu wissen.

⚠️ **Zugesagt ist sie nur je benanntem Client**, nicht "alles, was Seerr
spricht" (entschieden am 14.11.2026). Seerrs Oberflaeche hat weit ueber
hundert Adressen, und jeder Client nimmt sich einen anderen Teil davon
(Jellyfin Enhanced allein rund 55). Gebaut und mit Tests belegt ist, was diese
zwei aus ihrem Quelltext tatsaechlich aufrufen:

* **Silos Seerr-Plugin** (``Silo-Community/silo-plugins-requests-seerr``):
  ``auth/me``, Anfrage anlegen, lesen und auflisten, Film- und Seriendetails
  mit ``mediaInfo.requests``, ``service/radarr|sonarr`` und die Nutzerliste.
* **Das Litefin-Plugin fuer Jellyfin** (``MoazSalem/litefin-plugin``):
  Entdecken, Suche, Details, Personen, Sammlungen, Anfragen, Merkliste,
  "zuletzt hinzugefuegt" und die Nutzerliste mit ``jellyfinUserId``.

Wer einen weiteren Client will, misst dessen Aufrufe und traegt sie in
``tests/test_seerr_api_clients.py`` ein. Alles, was dort nicht steht, ist nicht
zugesagt, auch wenn es zufaellig antwortet.

⚠️ **Eigener Port, eigene App.** Die Schicht laeuft nicht unter ``/api/v1``
von Nexview (das ist zugesagt stabil und hat eine andere Form), sondern als
zweite ASGI-Anwendung auf eigenem Port, ab Werk aus (``lauscher.py``). Seerr
liegt auf 5055, und genau das tragen die Clients ab Werk ein.

⚠️ **Konten werden hier nie angelegt und nie ueber die Mailadresse
zugeordnet.** Siehe ``zugang.py``.
"""
