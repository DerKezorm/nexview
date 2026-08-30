/**
 * Die Symbole der Navigation - an einer Stelle.
 *
 * Vorher zeichnete jedes Bauteil sein SVG selbst. Das ging, solange es eine
 * Handvoll waren; sobald aber jede Reiterreihe eines bekommt, laufen Groesse,
 * Strichstaerke und Rundung auseinander, und die Leiste sieht unruhig aus,
 * ohne dass man den Grund benennen koennte.
 *
 * ⚠️ **Alle mit demselben Raster: 24×24, Strich statt Flaeche, `currentColor`.**
 * Dadurch nimmt jedes Symbol die Farbe seines Knopfes an - auch die des
 * gewaehlten - und es gibt keine zweite Stelle, an der eine Farbe gepflegt
 * werden muesste.
 */

/**
 * ⚠️ **``punkt`` und ``flaeche`` sind nicht dasselbe.**
 *
 * ``punkt`` zeichnet einen Punkt ueber einen dicken, runden Strichabschluss -
 * der Pfad ist dabei nur ``M x y v0``, hat also gar keine Ausdehnung.
 * ``flaeche`` fuellt eine echte Form und darf **keinen** Strich bekommen, sonst
 * waechst sie nach aussen und wird plump.
 */
type Pfad = { d: string; punkt?: boolean; flaeche?: boolean; transform?: string }

/**
 * Die Zeichnungen. Bewusst schlicht gehalten: In 16 Pixeln neben einem Wort
 * traegt ohnehin nur die grobe Silhouette.
 */
const SYMBOLE = {
  /** Anlage, Betrieb - alles, was nicht den Alltag betrifft. */
  system: [
    { d: 'M4 6.5h16M4 12h16M4 17.5h16' },
    { d: 'M7.5 6.5v0M7.5 12v0M7.5 17.5v0', punkt: true },
  ],
  /** Verbundene Dienste - der Stecker. */
  dienste: [{ d: 'M9 3v6M15 3v6M6.5 9h11v3a5.5 5.5 0 0 1-11 0V9ZM12 17.5V21' }],
  /** Nachrichten hinaus - die Glocke. */
  glocke: [{ d: 'M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6M13.7 20a2 2 0 0 1-3.4 0' }],
  /** Konten. */
  benutzer: [
    { d: 'M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-4A3.5 3.5 0 0 0 5 18.5V20' },
    { d: 'M10.5 11.5a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5Z' },
    { d: 'M19 20v-1.5a3.5 3.5 0 0 0-2.6-3.4M15 5.2a3.25 3.25 0 0 1 0 6.1' },
  ],
  /**
   * Ein Kinderkonto - die kleine Figur.
   *
   * Bewusst nicht dasselbe wie ``benutzer``: Dort stehen mehrere Personen
   * nebeneinander, hier eine einzelne und kleiner. Nebeneinander in einer
   * Reihe muss man die beiden auf einen Blick auseinanderhalten koennen.
   */
  kind: [
    { d: 'M12 11.5a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5Z' },
    { d: 'M17 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-3A3.5 3.5 0 0 0 7 18.5V20' },
  ],
  /** Vorgemerkt - das Lesezeichen. */
  merkliste: [{ d: 'M6.5 4h11a1 1 0 0 1 1 1v15l-6.5-4-6.5 4V5a1 1 0 0 1 1-1Z' }],
  /** Wie viel darf jemand - die Anzeige. */
  kontingent: [
    { d: 'M4 18a8 8 0 1 1 16 0' },
    { d: 'M12 18l4.2-4.6' },
  ],
  /** Was nicht durchkommt. */
  sperre: [
    { d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' },
    { d: 'M6 6l12 12' },
  ],
  /** Erreichbarkeit von aussen - die Kugel. */
  adresse: [
    { d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' },
    { d: 'M3.5 12h17' },
    { d: 'M12 3.5c2.2 2.3 3.4 5.3 3.4 8.5s-1.2 6.2-3.4 8.5c-2.2-2.3-3.4-5.3-3.4-8.5S9.8 5.8 12 3.5Z' },
  ],
  /** Der Umschlag. */
  mail: [
    { d: 'M4 6.5h16v11H4z' },
    { d: 'M4.5 7.2l7.5 5.6 7.5-5.6' },
  ],
  /** Mitgeschriebene Zeilen. */
  protokoll: [
    { d: 'M5 4.5h14v15H5z' },
    { d: 'M8 9h8M8 12.5h8M8 16h5' },
  ],
  /** Weggelegte Staende - der Stapel. */
  sicherung: [
    { d: 'M12 8.2c3.9 0 7-1.05 7-2.35S15.9 3.5 12 3.5 5 4.55 5 5.85 8.1 8.2 12 8.2Z' },
    { d: 'M19 5.85v12.3c0 1.3-3.1 2.35-7 2.35s-7-1.05-7-2.35V5.85' },
    { d: 'M5 12c0 1.3 3.1 2.35 7 2.35s7-1.05 7-2.35' },
  ],
  /**
   * Zugang - der Schluessel.
   *
   * Fuer alles, was darueber entscheidet, **wer hereinkommt**: Passwort,
   * angemeldete Geraete, API-Token. Bewusst nicht das Schloss aus ``sperre``
   * - das steht fuer "kommt nicht durch" und meint das Gegenteil.
   */
  schluessel: [
    { d: 'M14.5 9.5a3.5 3.5 0 1 1 0 5 3.5 3.5 0 0 1 0-5Z' },
    { d: 'M11.4 12H3.5M6 12v3M9 12v2.5' },
  ],
  /**
   * Sprache und Region - die Kugel.
   *
   * Dieselbe Zeichnung wie ``adresse``, unter eigenem Namen: Dort steht sie
   * fuer Erreichbarkeit, hier fuer "wo bin ich, welche Sprache". Sie stehen
   * nie nebeneinander, und ein Name je Bedeutung haelt die Liste lesbar.
   */
  sprache: [
    { d: 'M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z' },
    { d: 'M3.5 12h17' },
    { d: 'M12 3.5c2.2 2.3 3.4 5.3 3.4 8.5s-1.2 6.2-3.4 8.5c-2.2-2.3-3.4-5.3-3.4-8.5S9.8 5.8 12 3.5Z' },
  ],
  /** Grundsaetzliches - die Regler. */
  allgemein: [
    { d: 'M5 7.5h9M17.5 7.5H19M5 16.5h1.5M10 16.5h9' },
    { d: 'M15.75 7.5a1.75 1.75 0 1 1-3.5 0 1.75 1.75 0 0 1 3.5 0ZM9.75 16.5a1.75 1.75 0 1 1-3.5 0 1.75 1.75 0 0 1 3.5 0Z' },
  ],
  /** Filme - die Filmrolle. */
  film: [
    { d: 'M4 5.5h16v13H4z' },
    { d: 'M4 9.5h16M4 14.5h16M8.5 5.5v13M15.5 5.5v13' },
  ],
  /**
   * Das Fernsehgeraet - fuer TMDB, die Quelle der Titeldaten.
   *
   * Antenne und Kasten, nicht die Filmrolle: TMDB liefert zu Filmen **und**
   * Serien, und die Rolle haette nur die eine Haelfte gemeint.
   */
  fernseher: [
    { d: 'M3.5 8.5h17v10.5h-17z' },
    { d: 'M8 4.5l4 4 4-4' },
  ],
  /**
   * Radarr - nach dem Logo gezeichnet: ein Dreieck aus kraeftigem Umriss mit
   * einem gefuellten Dreieck darin.
   *
   * ⚠️ Bewusst einfarbig ueber ``currentColor``. Das Original ist gelb; im
   * Reiter soll es aber die Farbe des Knopfes annehmen wie jedes andere Symbol,
   * sonst sticht ein einzelner bunter Fleck aus der Reihe.
   */
  radarr: [
    { d: 'M6.2 4.4a1.7 1.7 0 0 1 2.5-1.5l9.6 5.6a1.7 1.7 0 0 1 0 3l-9.6 5.6a1.7 1.7 0 0 1-2.5-1.5V4.4Z', transform: 'translate(0.6 2)' },
    { d: 'M10.2 8.1l4.9 2.9-4.9 2.9V8.1Z', flaeche: true, transform: 'translate(0.6 2)' },
  ],
  /**
   * Sonarr - nach dem Logo gezeichnet: vier Blaetter im Kreis, Punkt in der
   * Mitte. Ein Blatt, viermal gedreht - siehe ``transform``.
   */
  sonarr: [
    { d: 'M5.6 8.1a9 9 0 0 1 12.8 0 7.2 7.2 0 0 0-12.8 0Z', flaeche: true },
    { d: 'M5.6 8.1a9 9 0 0 1 12.8 0 7.2 7.2 0 0 0-12.8 0Z', flaeche: true, transform: 'rotate(90 12 12)' },
    { d: 'M5.6 8.1a9 9 0 0 1 12.8 0 7.2 7.2 0 0 0-12.8 0Z', flaeche: true, transform: 'rotate(180 12 12)' },
    { d: 'M5.6 8.1a9 9 0 0 1 12.8 0 7.2 7.2 0 0 0-12.8 0Z', flaeche: true, transform: 'rotate(270 12 12)' },
    { d: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z', flaeche: true },
  ],
  /** Serien - der Kasten mit Antenne. */
  serie: [
    { d: 'M4.5 8.5h15v10h-15z' },
    { d: 'M8.5 4.5L12 8.2l3.5-3.7' },
  ],
  /** Der Medienserver. */
  medienserver: [
    { d: 'M4.5 5h15v5h-15zM4.5 14h15v5h-15z' },
    { d: 'M7.75 7.5v0M7.75 16.5v0', punkt: true },
  ],
  /** Herunterladen. */
  herunterladen: [{ d: 'M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14' }],
  /** Wegwerfen. */
  loeschen: [
    { d: 'M5 7h14' },
    { d: 'M9.5 7V5h5v2' },
    { d: 'M6.5 7l.8 12.1a1 1 0 0 0 1 .9h7.4a1 1 0 0 0 1-.9L17.5 7' },
  ],
  /**
   * Qualitaetsprofile - die Schieberegler.
   *
   * Bewusst nicht ein Stern oder ein Haken: Ein Profil ist kein Urteil ueber
   * Gute und Schlechte, sondern eine Reihe Einstellungen, die man verschiebt.
   */
  qualitaet: [
    { d: 'M5 7.5h14M5 16.5h14' },
    { d: 'M10 5v5M15 14v5' },
  ],
  /** Auswertung, Verlauf - die Saeulen. */
  analyse: [
    { d: 'M4 20h16' },
    { d: 'M7 20v-6M12 20V6M17 20v-9' },
  ],
  /**
   * Ein Befund - das Ausrufezeichen im Kreis.
   *
   * Bewusst neutral und nicht als Warndreieck: Dasselbe Symbol steht auch
   * ueber einer leeren Liste ("nichts gefunden"), und ein Dreieck waere dort
   * ein Schreck ohne Anlass. Die Dringlichkeit traegt die Farbe, nicht die Form.
   */
  befund: [
    { d: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z' },
    { d: 'M12 8v5' },
    { d: 'M12 16.2v0', punkt: true },
  ],
} satisfies Record<string, Pfad[]>

export type SymbolName = keyof typeof SYMBOLE

export function Symbol({
  name,
  className = 'h-4 w-4',
}: {
  name: SymbolName
  className?: string
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      // Immer schmueckend: Die Bedeutung steht als Wort daneben, und wo sie das
      // nicht tut, traegt der Knopf ein `aria-label`.
      aria-hidden="true"
      focusable="false"
    >
      {/* Die Zuweisung ist nötig: `satisfies` behält oben die genauen
          Literaltypen, damit `SymbolName` alle Namen kennt - dabei geht
          aber `fill` als gemeinsames Feld verloren. */}
      {(SYMBOLE[name] as Pfad[]).map((pfad, i) => (
        <path
          key={i}
          d={pfad.d}
          transform={pfad.transform}
          fill={pfad.flaeche ? 'currentColor' : 'none'}
          stroke={pfad.flaeche ? 'none' : 'currentColor'}
          strokeWidth={pfad.punkt ? 2.5 : 1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  )
}
