import { Fenster, Button } from 'nexview-ui'

/**
 * ⚠️ **Genau ein sichtbarer Ausgang.** Mit Fusszeile faellt der Schliessen-
 * Knopf oben weg - sonst stehen zwei Knoepfe mit derselben Wirkung im selben
 * Fenster. Escape und ein Klick daneben schliessen in beiden Faellen.
 */
export const MitFusszeile = () => (
  <Fenster
    offen
    titel="Ordner wählen"
    unterzeile="/media/filme"
    onSchliessen={() => {}}
    fuss={
      <>
        <Button variant="ghost" onClick={() => {}}>Abbrechen</Button>
        <Button onClick={() => {}}>Übernehmen</Button>
      </>
    }
  >
    <ul className="flex flex-col gap-1 text-sm text-mist-300">
      <li className="rounded-lg px-3 py-2 hover:bg-ink-800">4K</li>
      <li className="rounded-lg bg-ink-800 px-3 py-2 text-mist-100">Filme</li>
      <li className="rounded-lg px-3 py-2 hover:bg-ink-800">Serien</li>
    </ul>
  </Fenster>
)
