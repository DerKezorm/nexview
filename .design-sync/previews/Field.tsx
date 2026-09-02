import { Field } from 'nexview-ui'

export const Standard = () => (
  <div className="bg-ink-950 p-6 max-w-sm">
    <Field label="Anzeigename" defaultValue="Jonas" />
  </div>
)

export const MitHinweis = () => (
  <div className="bg-ink-950 p-6 max-w-sm">
    <Field
      label="Öffentliche Adresse"
      defaultValue="https://nexview.example.de"
      hint="Steckt in jedem Link, den Nexview verschickt."
    />
  </div>
)
