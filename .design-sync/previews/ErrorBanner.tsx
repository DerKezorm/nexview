import { ErrorBanner } from 'nexview-ui'

export const Standard = () => (
  <div className="bg-ink-950 p-6 max-w-lg">
    <ErrorBanner message="Radarr ist nicht erreichbar - bitte Adresse und Schlüssel prüfen." />
  </div>
)
