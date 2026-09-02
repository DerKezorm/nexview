import { StorageDistribution } from 'nexview-ui'

/**
 * ⚠️ **Genau drei Farben, und das ist keine Willkuer.** Bei vier faellt jede
 * Kombination durch die Pruefung auf Farbfehlsichtigkeit, sobald - wie im
 * Kuchen unvermeidlich - *alle* Paare verglichen werden und nicht nur
 * benachbarte. Alles darueber faellt in „Andere"; in der Liste steht ohnehin
 * jeder einzeln. Der Hausbestand ist im Kuchen nicht dabei: er ist kein
 * Mitbewerber, sondern der Boden, auf dem alle stehen.
 */
export const FuenfPersonen = () => (
  <div className="bg-ink-950 p-6">
    <StorageDistribution
      houseBytes={38_428 * 1024 ** 3}
      shares={[
        { user_id: 1, username: 'nora', display_name: 'Nora', used_bytes: 34 * 1024 ** 3, items: 41, limit_bytes: 100 * 1024 ** 3 },
        { user_id: 2, username: 'lena', display_name: 'Lena', used_bytes: 19 * 1024 ** 3, items: 27, limit_bytes: 50 * 1024 ** 3 },
        { user_id: 3, username: 'jonas', display_name: 'Jonas', used_bytes: 9 * 1024 ** 3, items: 12, limit_bytes: null },
        { user_id: 4, username: 'mia', display_name: 'Mia', used_bytes: 3 * 1024 ** 3, items: 4, limit_bytes: 20 * 1024 ** 3 },
        { user_id: 5, username: 'ben', display_name: 'Ben', used_bytes: 1 * 1024 ** 3, items: 2, limit_bytes: 20 * 1024 ** 3 },
      ]}
    />
  </div>
)
