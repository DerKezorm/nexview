import { FilterBar } from 'nexview-ui'

/** Die vollstaendige Filterleiste des Entdecken-Bereichs. */
export const Filme = () => (
  <div className="bg-ink-950 p-6">
    <FilterBar
      mediaType="movie"
      view="grid"
      onViewChange={() => {}}
      showFeatureLength
      arrConfigured
      filters={{
        period: '7',
        language: '',
        region: 'DE',
        genreId: null,
        sort: 'newest',
        hideExisting: false,
        featureLength: true,
        minRating: 0,
        hideUnrated: false,
        releasedInRegion: false,
        hideWithoutOverview: false,
        knownOnly: false,
        studioId: null,
      }}
      onChange={() => {}}
      studios={[{ id: 41077, name: "A24" }, { id: 47, name: "Constantin Film" }]}
      genres={[
        { id: 28, name: 'Action' },
        { id: 12, name: 'Abenteuer' },
        { id: 18, name: 'Drama' },
      ]}
    />
  </div>
)
