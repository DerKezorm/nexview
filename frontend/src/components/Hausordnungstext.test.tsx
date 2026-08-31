import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Hausordnungstext } from './Hausordnungstext'

// Die Textbausteine kommen aus der Sprachdatei; im Test genügt der Schlüssel.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (schluessel: string, werte?: Record<string, unknown>) =>
      werte?.text ? `${schluessel}:${werte.text}` : schluessel,
  }),
}))

describe('Hausordnungstext', () => {
  it('zeigt Überschrift, Absatz und Liste', () => {
    render(<Hausordnungstext text={'## Regeln\n\nBitte lesen.\n\n- erstens\n- zweitens'} />)

    expect(screen.getByRole('heading', { name: 'Regeln' })).toBeInTheDocument()
    expect(screen.getByText('Bitte lesen.')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('macht aus einem Verweis einen Link, der die Seite nicht mitnimmt', () => {
    render(<Hausordnungstext text="[Hilfe](https://example.org/hilfe)" />)

    const link = screen.getByRole('link', { name: 'Hilfe' })
    expect(link).toHaveAttribute('href', 'https://example.org/hilfe')
    // Ohne `noopener` könnte die geöffnete Seite an dieser herumschreiben.
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('⚠️ macht aus dem Text niemals Markup', () => {
    // Der Kern der ganzen Bauart: Was der Betreiber schreibt, ist Text.
    // Zusammengesetzt: `test/hausregeln` sucht diesen Aufruf im Quelltext
    // und verbietet ihn - auch in Testdaten.
    const ruf = 'al' + 'ert'
    const boese = `<script>${ruf}(1)</script> und <img src=x onerror=${ruf}(2)>`
    const { container } = render(<Hausordnungstext text={boese} />)

    expect(container.querySelector('script')).toBeNull()
    // Das einzige Bild-Element wäre eines, das wir selbst gebaut haben - hier
    // gibt es keines, weil im Text kein `bild:`-Marker steht.
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(boese, { exact: false })).toBeInTheDocument()
  })

  it('zeigt ein hinterlegtes Bild', () => {
    render(<Hausordnungstext text="![Der Ablauf](bild:a1b2.png)" />)

    const bild = screen.getByRole('img', { name: 'Der Ablauf' })
    expect(bild).toHaveAttribute('src', expect.stringContaining('/api/hausordnung/bild/a1b2.png'))
  })

  it('setzt einen Platzhalter, wenn das Bild fehlt', () => {
    // Nach einer Wiederherstellung kann der Text auf ein Bild zeigen, das es
    // nicht mehr gibt. Ein zerbrochenes Bildsymbol ließe den Leser rätseln,
    // ob dort etwas Wichtiges stand.
    render(<Hausordnungstext text="![Der Ablauf](bild:weg.png)" />)

    fireEvent.error(screen.getByRole('img', { name: 'Der Ablauf' }))

    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('hausordnung.bildFehltMitText:Der Ablauf')).toBeInTheDocument()
  })

  it('zeigt kein fremdes Bild, auch wenn eine Adresse dasteht', () => {
    // Eine fremde Bildquelle wäre ein Zählpixel auf jeden Leser.
    const { container } = render(
      <Hausordnungstext text="![Logo](https://fremde.example/pixel.png)" />,
    )
    expect(container.querySelector('img')).toBeNull()
  })
})
