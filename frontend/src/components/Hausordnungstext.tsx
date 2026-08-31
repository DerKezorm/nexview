/**
 * Die Hausordnung anzeigen – aus der Datenstruktur, nie aus Markup.
 *
 * Den Text zerlegt `lib/auszeichnung`; hier entstehen daraus React-Elemente.
 * Kein `dangerouslySetInnerHTML`, kein `innerHTML`: Aus dem Betreibertext kann
 * damit nie Markup werden. Die Begründung steht ausführlich in
 * `lib/auszeichnung.ts` und in `services/csp.py`.
 */

import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { auszeichnung, type Block, type Teil } from '../lib/auszeichnung'
import { mitBasis } from '../lib/basis'

/** Die Adresse eines hinterlegten Bildes. Nur eigene – siehe Parser. */
export function bildAdresse(name: string): string {
  return mitBasis(`/api/hausordnung/bild/${encodeURIComponent(name)}`)
}

function teile(inhalt: Teil[]): ReactNode[] {
  return inhalt.map((teil, i) => {
    switch (teil.art) {
      case 'fett':
        return (
          <strong key={i} className="font-semibold text-mist-100">
            {teil.text}
          </strong>
        )
      case 'kursiv':
        return (
          <em key={i} className="italic">
            {teil.text}
          </em>
        )
      case 'code':
        return (
          <code
            key={i}
            className="rounded bg-ink-800 px-1 py-0.5 font-mono text-[0.9em] text-accent-400"
          >
            {teil.text}
          </code>
        )
      case 'verweis':
        return (
          <a
            key={i}
            href={teil.ziel}
            target="_blank"
            // `noopener` trennt die neue Seite von dieser – ohne das kann sie
            // über `window.opener` an unserer herumschreiben.
            rel="noopener noreferrer"
            className="text-accent-400 underline underline-offset-2 hover:text-accent-300"
          >
            {teil.text}
          </a>
        )
      default:
        return <span key={i}>{teil.text}</span>
    }
  })
}

/**
 * Ein Bild – oder ein ruhiger Platzhalter, wenn es fehlt.
 *
 * ⚠️ **Fehlende Bilder sind ein echter Fall, kein Randfall.** Eine Sicherung
 * gilt als gelungen, auch wenn eine einzelne Dateikopie scheitert
 * (`services/sicherung.py` schreibt dann nur eine Warnung ins Protokoll), und
 * Stände aus der Zeit vor dem Bilderordner bringen gar keine mit. Nach dem
 * Einspielen zeigt der Text dann auf Bilder, die es nicht gibt.
 *
 * Ein zerbrochenes Bildsymbol ließe den Leser rätseln, ob dort etwas Wichtiges
 * stand. Der Platzhalter sagt es ihm.
 */
function Bild({ name, text }: { name: string; text: string }) {
  const { t } = useTranslation()
  const [fehlt, setzeFehlt] = useState(false)

  if (fehlt) {
    return (
      <p className="rounded-lg border border-dashed border-ink-700 px-4 py-3 text-sm text-mist-600">
        {text ? t('hausordnung.bildFehltMitText', { text }) : t('hausordnung.bildFehlt')}
      </p>
    )
  }

  return (
    <img
      src={bildAdresse(name)}
      alt={text}
      loading="lazy"
      onError={() => setzeFehlt(true)}
      className="max-w-full rounded-lg border border-ink-700"
    />
  )
}

function BlockAnzeigen({ block }: { block: Block }) {
  switch (block.art) {
    case 'ueberschrift':
      return block.stufe === 2 ? (
        <h2 className="mt-6 text-lg font-semibold text-mist-100 first:mt-0">
          {teile(block.inhalt)}
        </h2>
      ) : (
        <h3 className="mt-5 font-semibold text-mist-200 first:mt-0">{teile(block.inhalt)}</h3>
      )
    case 'absatz':
      return <p className="text-sm leading-relaxed text-mist-400">{teile(block.inhalt)}</p>
    case 'liste':
      return block.nummeriert ? (
        <ol className="list-decimal space-y-1 pl-5 text-sm leading-relaxed text-mist-400">
          {block.punkte.map((punkt, i) => (
            <li key={i}>{teile(punkt)}</li>
          ))}
        </ol>
      ) : (
        <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-mist-400">
          {block.punkte.map((punkt, i) => (
            <li key={i}>{teile(punkt)}</li>
          ))}
        </ul>
      )
    case 'zitat':
      return (
        <blockquote className="border-l-2 border-accent-500/60 bg-ink-900/40 py-2 pl-4 text-sm leading-relaxed text-mist-300">
          {teile(block.inhalt)}
        </blockquote>
      )
    case 'bild':
      return <Bild name={block.name} text={block.text} />
    case 'trennlinie':
      return <hr className="border-ink-700/80" />
  }
}

/** Der fertige Text. */
export function Hausordnungstext({ text }: { text: string }) {
  const bloecke = auszeichnung(text)
  return (
    <div className="flex flex-col gap-3">
      {bloecke.map((block, i) => (
        <BlockAnzeigen key={i} block={block} />
      ))}
    </div>
  )
}
