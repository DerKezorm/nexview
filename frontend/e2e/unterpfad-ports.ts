/**
 * Ports und Datenverzeichnis des Unterpfad-Aufbaus.
 *
 * In einer eigenen Datei aus demselben Grund wie `konto.ts`: Konfiguration
 * und Test brauchen dieselben Werte, und zwei Fassungen laufen auseinander.
 *
 * Eigene Ports neben 8799/5599 des Haupt-Aufbaus, damit beide gleichzeitig
 * laufen können - und keiner davon dem gehört, der nebenher entwickelt.
 */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

const hier = path.dirname(fileURLToPath(import.meta.url))

/** Das Backend mit NEXVIEW_URL_BASE=/nexview und gebautem Frontend. */
export const UNTERPFAD_BACKEND_PORT = 8798

/** Pförtner, der /nexview/… mitsamt Vorbau weiterreicht. */
export const PROXY_DURCHREICHEN_PORT = 8797

/** Pförtner, der /nexview abschneidet und nur den Rest weiterreicht. */
export const PROXY_ABSCHNEIDEN_PORT = 8796

/** Frische Datenbank je Lauf - getrennt von .e2e-data des Haupt-Aufbaus. */
export const UNTERPFAD_DATEN = path.join(hier, '..', '.e2e-data-unterpfad')
