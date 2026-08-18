import { CastStrip } from 'nexview-ui'

export const Besetzung = () => (
  <div className="bg-ink-950 p-6">
    <CastStrip
      cast={[
        { person_id: 31, name: 'Tom Hanks', character: 'Andrew Beckett', photo_url: null },
        { person_id: 2, name: 'Denzel Washington', character: 'Joe Miller', photo_url: null },
        { person_id: 3, name: 'Jason Robards', character: 'Charles Wheeler', photo_url: null },
        { person_id: 4, name: 'Mary Steenburgen', character: 'Belinda Conine', photo_url: null },
      ]}
    />
  </div>
)
