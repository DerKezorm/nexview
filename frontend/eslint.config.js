// ESLint-Aufbau nach der Vite-Vorlage für React + TypeScript.
//
// Absichtlich die Standardregeln und nichts Eigenes: Die Typprüfung läuft
// ohnehin im Build (`tsc -b`); ESLint fängt darüber hinaus die React-Fallen
// (Hook-Regeln, veraltete Abhängigkeits-Listen) und totes Zeug.
//
// ⚠️ **Die Schwelle steht auf null und wird beim Anschlagen NICHT
// hochgesetzt.** `npm run lint` ruft `eslint . --max-warnings 0` auf, und
// derselbe Aufruf steht im CI. Der Grund für die Null:
//
//   * Ohne Schwelle wäre der Schritt ein Prüfer, der niemals anschlägt.
//     ESLint gibt bei reinen Warnungen Rückgabecode 0 zurück; gemessen am
//     02.09.2026 waren das 20 Stück und der Lauf trotzdem grün.
//   * Eine Schwelle auf den Stand von heute (20) wäre keine Waage, sondern
//     ein Deckel auf einer **Summe über alle Regeln**. Wer eine Warnung
//     wegräumt und dabei eine andere einbaut, käme unbemerkt durch.
//
// Schlägt sie an, gehört die Meldung behoben oder die betroffene Regel
// benannt und begründet abgeschaltet, so wie die beiden react-hooks-Regeln
// weiter unten. Die Zahl bleibt null. Dieselbe Haltung wie bei der Waage in
// `tools/gewicht-pruefen.mjs`.
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // ⚠️ **Alle Ausgabeverzeichnisse, nicht nur das eine.** `'dist'` trifft in
  // der flachen Konfiguration nur den Ordner ganz oben. Damit prüfte ESLint
  // `packages/nexview-ui/dist` und den Behelfsbau `dist-dev` mit — erzeugte
  // Dateien, in denen Regeln stehen, die es hier gar nicht gibt. Der Lauf
  // endete deshalb auf jedem Rechner, der einmal gebaut hatte, mit vier
  // Fehlern, die nichts mit dem eigenen Code zu tun haben. Ein Werkzeug, das
  // immer rot ist, sieht sich niemand mehr an.
  { ignores: ['**/dist/**', '**/dist-dev/**'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Die Unterstrich-Konvention des Projekts: `_weg`, `_dito` beim
      // Destrukturieren heißen „bewusst ungenutzt".
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', destructuredArrayIgnorePattern: '^_' },
      ],
      // Zwei Regeln aus der React-Compiler-Familie, die hier etablierte und
      // bewusst gewählte Muster anschlagen: das Synchronisieren von
      // Entwurfs-State aus einer Abfrage (`if (daten) setEntwurf(...)` im
      // Effect, das Muster aller Einstellungsseiten) und der
      // Schnappschuss-Vergleich über eine Ref (`basis.current` in
      // AdminServicesSettings). Beides umzubauen wäre Churn ohne Fehlerbild –
      // für neuen Code bleibt die Leitlinie trotzdem: State aus Props/Query
      // ableiten statt spiegeln.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
    },
  },
)
