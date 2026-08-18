import { Card, Button } from 'nexview-ui'

export const Standard = () => (
  <div className="bg-ink-950 p-6">
    <Card className="max-w-md">
      <h2 className="text-lg font-semibold">Sperrliste</h2>
      <p className="mt-1 text-sm text-mist-500">
        Titel, die in dieser Bibliothek nicht landen sollen. Sie bleiben auffindbar,
        lassen sich aber nicht anfragen.
      </p>
    </Card>
  </div>
)

export const MitAktion = () => (
  <div className="bg-ink-950 p-6">
    <Card className="flex max-w-md flex-col gap-3">
      <h2 className="text-lg font-semibold">Kontingent zurücksetzen</h2>
      <p className="text-sm text-mist-500">
        Der Verbrauch beginnt von vorn. Die Anfragen selbst bleiben erhalten.
      </p>
      <div><Button>Zurücksetzen</Button></div>
    </Card>
  </div>
)
