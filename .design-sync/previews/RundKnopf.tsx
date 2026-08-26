import { RundKnopf } from 'nexview-ui'

/** Der runde Symbolknopf in der Fusszeile einer Kachel. Drei Rollen. */
export const DreiRollen = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-3">
    <RundKnopf label="Bearbeiten" onClick={() => {}}>
      <path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4Z" strokeLinecap="round" strokeLinejoin="round" />
    </RundKnopf>
    <RundKnopf label="Benachrichtigungen an" an onClick={() => {}}>
      <path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6" strokeLinecap="round" strokeLinejoin="round" />
    </RundKnopf>
    <RundKnopf label="Löschen" gefahr onClick={() => {}}>
      <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" strokeLinecap="round" strokeLinejoin="round" />
    </RundKnopf>
  </div>
)
