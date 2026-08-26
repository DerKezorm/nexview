/**
 * Was **vor** allem anderen gerade gerückt werden muss.
 *
 * ⚠️ **`localStorage` funktioniert unter Node nicht von selbst.** Node bringt
 * seit Fassung 22 ein eigenes mit, das ohne `--localstorage-file` funktionslos
 * ist — jeder Aufruf endet in „getItem is not a function" — und es überdeckt
 * das von jsdom. Beides zu erkennen und das jeweils funktionierende zu wählen
 * hat sich als unzuverlässig erwiesen; deshalb steht hier ein eigener,
 * schlichter Ersatz.
 *
 * Er ist absichtlich dumm: eine `Map`, mehr nicht. Die Anwendung braucht von
 * `localStorage` nur `getItem`, `setItem` und `removeItem`, und ein
 * nachgebauter Speicher, der sich zwischen den Tests zurücksetzen lässt, ist
 * hier sogar das bessere Verhalten.
 *
 * Warum eine eigene Datei: `i18n` liest die gespeicherte Sprache **beim
 * Import**, also bevor ein Test beginnt. Innerhalb einer Datei werden Importe
 * nach oben gezogen — ein Zurechtrücken in `setup.ts` käme zu spät.
 * `setupFiles` arbeitet die Dateien dagegen der Reihe nach ab.
 */

class SpeicherAttrappe implements Storage {
  private daten = new Map<string, string>()

  get length(): number {
    return this.daten.size
  }

  clear(): void {
    this.daten.clear()
  }

  getItem(schluessel: string): string | null {
    return this.daten.get(schluessel) ?? null
  }

  key(index: number): string | null {
    return [...this.daten.keys()][index] ?? null
  }

  removeItem(schluessel: string): void {
    this.daten.delete(schluessel)
  }

  setItem(schluessel: string, wert: string): void {
    this.daten.set(schluessel, String(wert))
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  const ersatz = new SpeicherAttrappe()
  Object.defineProperty(globalThis, name, {
    value: ersatz,
    configurable: true,
    writable: true,
  })
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, name, {
      value: ersatz,
      configurable: true,
      writable: true,
    })
  }
}
