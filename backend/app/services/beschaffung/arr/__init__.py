"""Beschaffung ueber Radarr und Sonarr.

Der Weg selbst steht in ``weg.py`` (``ArrBeschaffung``), die festen Fassungen
in ``fassungen.py``. Diese Datei bleibt leer: Wer ``arr.fassungen`` laedt,
laedt sie mit, und sie darf dabei nicht den ganzen Weg samt Diensten ziehen -
``settings_service`` fragt die Fassungen schon beim Start.
"""
