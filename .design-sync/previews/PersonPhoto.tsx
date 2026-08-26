import { PersonPhoto } from 'nexview-ui'

/**
 * Ohne Foto der erste Buchstabe - nie das kaputte Bildsymbol des Browsers.
 * TMDB kennt zu manchen Personen kein Bild, und selten fuehrt eine Adresse
 * ins Leere; beides landet auf demselben Ersatz.
 */
export const OhneFoto = () => (
  <div className="bg-ink-950 p-6 flex gap-4" style={{ width: '24rem' }}>
    {['Tom Hanks', 'Nicole Kidman', 'Robert Duvall'].map((name) => (
      <div
        key={name}
        className="overflow-hidden border border-ink-700"
        style={{ height: '6rem', width: '6rem', borderRadius: '9999px' }}
      >
        <PersonPhoto url={null} name={name} />
      </div>
    ))}
  </div>
)
