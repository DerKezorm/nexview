import { Reiterreihe } from 'nexview-ui'

export const Hauptreihe = () => (
  <div className="bg-ink-950 p-6">
    <Reiterreihe
      eintraege={[
        { value: 'allgemein', label: 'Allgemein', symbol: 'allgemein' },
        { value: 'dienste', label: 'Dienste', symbol: 'dienste' },
        { value: 'benutzer', label: 'Benutzer', symbol: 'benutzer' },
        { value: 'protokoll', label: 'Protokoll', symbol: 'protokoll', abzeichen: 'neu' },
      ]}
      aktiv="dienste"
      onWechsel={() => {}}
      label="Einstellungen"
    />
  </div>
)

/**
 * Die untergeordnete Reihe bekommt den senkrechten Strich links. Ohne ihn
 * stehen zwei Reihen gleichrangig untereinander und man sieht nicht, dass die
 * untere zur oberen gehoert.
 */
export const MitUnterreihe = () => (
  <div className="bg-ink-950 p-6 flex flex-col gap-3">
    <Reiterreihe
      eintraege={[
        { value: 'dienste', label: 'Dienste', symbol: 'dienste' },
        { value: 'system', label: 'System', symbol: 'system' },
      ]}
      aktiv="dienste"
      onWechsel={() => {}}
    />
    <Reiterreihe
      unter
      eintraege={[
        { value: 'radarr', label: 'Radarr', symbol: 'radarr' },
        { value: 'sonarr', label: 'Sonarr', symbol: 'sonarr' },
        { value: 'medienserver', label: 'Medienserver', symbol: 'medienserver' },
      ]}
      aktiv="sonarr"
      onWechsel={() => {}}
    />
  </div>
)
