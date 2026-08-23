import { KidsKatalog } from './KidsKatalog'

/**
 * Die Startseite der Kinderansicht.
 *
 * Der Inhalt steckt in `KidsKatalog`, weil die Eltern-Vorschau exakt dieselbe
 * Ansicht braucht - nur mit einer anderen Datenquelle.
 */
export function KidsHomePage() {
  return <KidsKatalog />
}
