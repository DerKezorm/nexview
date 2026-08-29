/// <reference types="vite/client" />

// Vite kennt *.svg, aber nicht die ?inline-Variante (data:-Adresse statt
// Datei unter /assets) - gebraucht vom Gotify-Logo, siehe AdminChannelSettings.
declare module '*.svg?inline' {
  const inhalt: string
  export default inhalt
}
