import { Slider, Card } from 'nexview-ui'

/** Waagerecht scrollbare Reihe - auf der Startseite fuer zuletzt Geladenes. */
export const Reihe = () => (
  <div className="bg-ink-950 p-6">
    <Slider>
      {['Tage des Donners', 'Alles steht Kopf 2', 'Philadelphia', 'WALL·E', '12 Monkeys'].map((titel) => (
        <Card key={titel} className="w-48 shrink-0">
          <p className="font-medium">{titel}</p>
          <p className="mt-1 text-xs text-mist-600">Bereits geladen</p>
        </Card>
      ))}
    </Slider>
  </div>
)
