/**
 * Das Auffangnetz: eine Erklärung statt einer weißen Seite.
 *
 * ⚠️ **Ohne das räumt React bei jedem Fehler den ganzen Baum ab.** Wirft
 * irgendein Bestandteil beim Zeichnen, hängt React nicht etwa nur diesen einen
 * aus — es entfernt die komplette Anwendung aus `#root`. Übrig bleibt eine
 * leere Fläche: kein Text, kein Knopf, kein Hinweis, dass Neuladen hilft.
 *
 * ⚠️ **Der wahrscheinlichste Auslöser ist ein Update.** Die Seiten kommen
 * nachgeladen (die `lazy`-Liste in `App.tsx`), und ihre Dateinamen tragen eine
 * Prüfsumme. Wer beim Update einen Tab offen hatte und danach auf Profil,
 * Einstellungen oder die Statistik klickt, fragt nach einer Datei, die es
 * nicht mehr gibt. Für den Fall „die Sprachdatei kam nicht an" gibt es diese
 * Erklärung längst (`lib/startFehlgeschlagen.ts`); für „eine Seite kam nicht
 * an" fehlte sie.
 *
 * ⚠️ **Eine Klasse, kein Haken.** React bietet das Auffangen von Fehlern beim
 * Zeichnen ausschließlich über `componentDidCatch`/`getDerivedStateFromError`
 * an, und beides gibt es nur an einer Klasse. Ein zusätzliches Paket dafür
 * wäre Gewicht am ersten Laden für zwanzig Zeilen.
 *
 * Die Texte kommen aus den Sprachdateien: Anders als beim Start ist i18next
 * hier sicher geladen — `main.tsx` zeichnet erst danach.
 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

import i18n from '../i18n'

type Zustand = { gefallen: boolean }

export class Auffangnetz extends Component<{ children: ReactNode }, Zustand> {
  state: Zustand = { gefallen: false }

  static getDerivedStateFromError(): Zustand {
    return { gefallen: true }
  }

  componentDidCatch(fehler: Error, infos: ErrorInfo): void {
    // Der freundliche Satz hilft dem Besucher; wer die Konsole aufmacht,
    // braucht den echten Grund samt Stelle im Baum.
    console.error('Nexview: ein Bestandteil ist beim Zeichnen gescheitert.', fehler, infos)
  }

  render(): ReactNode {
    if (!this.state.gefallen) return this.props.children

    return (
      <div
        role="alert"
        className="flex min-h-dvh flex-col items-center justify-center gap-3 p-6 text-center"
      >
        <p className="text-lg font-semibold text-mist-100">{i18n.t('errors.crashTitle')}</p>
        <p className="max-w-lg text-sm leading-relaxed text-mist-500">
          {i18n.t('errors.crashHint')}
        </p>
        {/* Genau ein sichtbarer Ausgang - und der einzige, der hier wirklich
            hilft: ein neuer Versuch mit den Dateien von jetzt. */}
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-2 rounded-xl border border-ink-700 bg-ink-850 px-6 py-2 text-sm text-mist-100 hover:bg-ink-800"
        >
          {i18n.t('errors.crashReload')}
        </button>
      </div>
    )
  }
}
