/**
 * Was Ablage und Assistent gemeinsam brauchen.
 *
 * ⚠️ **Warum eine eigene Datei.** Beide Seiten müssen dasselbe unter einem
 * "Rezept" verstehen - sonst legt der Assistent etwas an, das die Ablage nicht
 * wiedererkennt, und die Doppelprüfung schlägt fehl, ohne dass es auffällt.
 */

/** Welcher Dienst - danach richtet sich, welche Instanzen infrage kommen. */
export type Typ = 'radarr' | 'sonarr'

/**
 * Der Zustand eines Profils **auf einer Instanz**.
 *
 * Die vier Fälle sind nicht frei erfunden, sondern das Kreuz aus zwei Fragen:
 * Hat sich die Quelle bewegt? Und hat jemand in Radarr von Hand daran gedreht?
 * Genau deshalb sind es vier und nicht drei oder fünf.
 */
export type Stand =
  | 'aktuell'
  | 'update'
  | 'angepasst'
  | 'konflikt'
  | 'nicht-installiert'
  /** In Radarr/Sonarr gelöscht, obwohl Nexview es dort verwaltet. */
  | 'fehlt'
  /** Instanz gerade nicht erreichbar - kein Urteil möglich. */
  | 'unerreichbar'
  /**
   * Der Abgleich läuft noch.
   *
   * ⚠️ Nur in der Oberfläche, nie vom Server. Ohne diesen Zustand müsste die
   * Liste während des Ladens etwas behaupten, das sie noch nicht weiß.
   */
  | 'pruefung'

export type Installation = { instanz: string; stand: Stand }

/**
 * Die Antworten aus dem Assistenten - das **Rezept**.
 *
 * ⚠️ Das Rezept ist das, was in Nexview liegt. Ein Profil in Radarr ist eine
 * *Kopie* davon auf einer Instanz. Deshalb hängen die Installationen am Profil
 * und nicht umgekehrt.
 */
/**
 * Wie ausführlich gefragt wird.
 *
 * ⚠️ **Der einfache Weg ist der ausführliche ohne Antworten.** Beide bauen
 * dasselbe Grundprofil; die zusätzlichen Fragen legen nur weitere
 * TRaSH-Gruppen obendrauf. Damit gibt es keine zwei Wege, die auseinander
 * driften können — und wer unterwegs umschaltet, verliert nichts.
 */
export type Modus = 'einfach' | 'ausfuehrlich'

export type Antworten = {
  modus: Modus
  /** Hochwertige Tonspuren bevorzugen (TrueHD ATMOS, DTS-X, …). */
  ton: 'egal' | 'bevorzugen'
  /** x265-Kodierungen meiden — TRaSHs „goldene Regel". */
  x265: 'egal' | 'meiden'
  /** SDR-Fassungen meiden. Nur bei 4K sinnvoll. */
  sdr: 'egal' | 'meiden'
  /** Besondere Schnittfassungen bevorzugen (IMAX, Hybrid, Remaster). Nur Filme. */
  fassungen: 'egal' | 'bevorzugen'
  /**
   * Fassungen mit Audiodeskription oder Gebärdensprache meiden.
   *
   * ⚠️ Voreinstellung ist **egal**, nicht „meiden": Wer sie braucht, soll sie
   * nicht wegwerfen, weil eine Voreinstellung es so wollte.
   */
  barrierefrei: 'egal' | 'meiden'
  /** Release-Gruppen der eigenen Sprache bevorzugen. */
  regionale_gruppen: 'egal' | 'bevorzugen'
  /** Asiatische Streaming-Dienste mitnehmen. */
  asiatische_dienste: 'egal' | 'dazu'

  name: string
  typ: Typ
  aufloesung: '2160p' | '1080p'
  /** Erst nehmen, was da ist, und später hochziehen? */
  sofortNehmen: boolean
  quelle: 'remux' | 'encodes' | 'web'
  /** Sprachcodes wie in SPRACHEN - mehrere sind ausdrücklich erlaubt. */
  sprachen: string[]
  /**
   * Die Rolle **je Sprache** - und das ist der Punkt.
   *
   * ⚠️ Eine gemeinsame Regel für alle gewählten Sprachen reicht nicht: Der
   * häufigste Fall ist "Deutsch, Englisch und Spanisch sind willkommen, aber
   * Deutsch **muss** dabei sein". Mit einem einzigen Schalter für alle wäre
   * genau das nicht ausdrückbar.
   */
  sprachRollen: Record<string, 'pflicht' | 'bevorzugt'>
  /**
   * Nur gefragt, wenn **mehr als eine** Sprache Pflicht ist - dann ist offen,
   * ob alle im selben Release stecken müssen oder eine davon genügt. Bei
   * höchstens einer Pflichtsprache gibt es nichts zu entscheiden.
   */
  mehrerePflicht: 'alle' | 'eine'
  hdr: 'netz' | 'frei' | 'egal'
  schlusspunkt: 'trash' | 'frueh'
}

export const LEERE_ANTWORTEN: Antworten = {
  modus: 'einfach',
  ton: 'egal',
  x265: 'egal',
  sdr: 'egal',
  fassungen: 'egal',
  barrierefrei: 'egal',
  regionale_gruppen: 'egal',
  asiatische_dienste: 'egal',
  name: '',
  typ: 'radarr',
  aufloesung: '2160p',
  sofortNehmen: true,
  quelle: 'encodes',
  sprachen: [],
  sprachRollen: {},
  mehrerePflicht: 'alle',
  hdr: 'netz',
  schlusspunkt: 'trash',
}

export type Profil = {
  id: string
  name: string
  typ: Typ
  /** Ein Satz in Alltagssprache - wofür das Profil gedacht ist. */
  zweck: string
  installationen: Installation[]
  /** Fehlt bei den Beispieldaten; echte Profile bringen ihr Rezept mit. */
  rezept?: Antworten
}

/**
 * Die Sprachen zur Auswahl.
 *
 * ⚠️ **Auszug, kein Vollbild.** Radarr kennt 60 Sprachen und liefert sie über
 * seine Schnittstelle - sobald es den Hinterbau gibt, kommt die Liste von dort.
 * ``ausgearbeitet`` sagt, ob es bei den TRaSH-Guides mehr gibt als die bloße
 * Spracherkennung: eigene Ränge und Schrott-Filter. Das gehört sichtbar dazu,
 * sonst erwartet jemand für Türkisch dieselbe Sorgfalt.
 *
 * ⚠️ **Französisch steht hier auf „einfach", obwohl TRaSH mehr hätte.** Deren
 * französische Familie gibt es in drei Fassungen — Synchronfassung, Originalton,
 * Untertitel —, und der Assistent stellt diese Frage nicht. Eine davon blind zu
 * nehmen wäre geraten; bis die Frage da ist, gilt die einfache Erkennung.
 */
export const SPRACHEN: { code: string; labelKey: string; ausgearbeitet: boolean }[] = [
  { code: 'de', labelKey: 'qualityWizard.langGerman', ausgearbeitet: true },
  { code: 'en', labelKey: 'qualityWizard.langEnglish', ausgearbeitet: false },
  { code: 'fr', labelKey: 'qualityWizard.langFrench', ausgearbeitet: false },
  { code: 'es', labelKey: 'qualityWizard.langSpanish', ausgearbeitet: false },
  { code: 'it', labelKey: 'qualityWizard.langItalian', ausgearbeitet: false },
  { code: 'tr', labelKey: 'qualityWizard.langTurkish', ausgearbeitet: false },
]

/**
 * Der Zweck eines Profils in einer Zeile - aus seinen Antworten erzeugt.
 *
 * ⚠️ **Warum nicht "Vom Assistenten angelegt".** Dieser Satz stand vorher unter
 * jedem Profil und sagte nichts: Woher es kommt, weiß man ohnehin, wonach man
 * sucht, ist *was es tut*. Aus den Antworten lässt sich das ableiten, also wird
 * es abgeleitet - dann stimmt es auch immer, statt zu veralten.
 */
export function kurzfassung(a: Antworten, t: (s: string) => string): string {
  const teile = [
    a.aufloesung === '2160p' ? '4K' : '1080p',
    t(`qualityWizard.src${a.quelle === 'remux' ? 'Remux' : a.quelle === 'web' ? 'Web' : 'Encodes'}`),
  ]
  const pflicht = a.sprachen.filter((c) => a.sprachRollen[c] === 'pflicht')
  const namen = (codes: string[]) =>
    codes.map((c) => t(SPRACHEN.find((s) => s.code === c)?.labelKey ?? c)).join(' + ')
  if (pflicht.length) {
    teile.push(`${namen(pflicht)} ${t('qualityProfiles.shortRequired')}`)
  } else if (a.sprachen.length) {
    teile.push(`${namen(a.sprachen)} ${t('qualityProfiles.shortPreferred')}`)
  }
  if (a.sofortNehmen) teile.push(t('qualityProfiles.shortUpgrade'))
  return teile.join(' · ')
}

/**
 * Der Fingerabdruck eines Rezepts - Grundlage der Doppelprüfung.
 *
 * ⚠️ **Ohne den Namen.** Zwei Profile mit verschiedenen Namen, aber gleichen
 * Antworten sind dasselbe Profil; sonst legte jemand fünfmal dasselbe an und
 * nennte es nur anders. Die Sprachen werden sortiert, weil die Reihenfolge des
 * Anklickens nichts bedeutet.
 */
export function fingerabdruck(a: Antworten): string {
  return JSON.stringify({
    typ: a.typ,
    aufloesung: a.aufloesung,
    sofortNehmen: a.sofortNehmen,
    quelle: a.quelle,
    // Rollen sortiert, damit die Reihenfolge des Anklickens nichts bedeutet.
    sprachRollen: [...a.sprachen].sort().map((c) => `${c}:${a.sprachRollen[c] ?? 'bevorzugt'}`),
    mehrerePflicht:
      a.sprachen.filter((c) => a.sprachRollen[c] === 'pflicht').length > 1
        ? a.mehrerePflicht
        : null,
    hdr: a.hdr,
    schlusspunkt: a.schlusspunkt,
    // ⚠️ Die ausführlichen Antworten gehören dazu: Ohne sie hielte die
    // Doppelprüfung ein Profil mit Ton-Vorliebe für dasselbe wie eines ohne.
    ton: a.ton,
    x265: a.x265,
    sdr: a.sdr,
    fassungen: a.fassungen,
    barrierefrei: a.barrierefrei,
    regionale_gruppen: a.regionale_gruppen,
    asiatische_dienste: a.asiatische_dienste,
  })
}
