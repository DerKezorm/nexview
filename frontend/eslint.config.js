// ESLint-Aufbau nach der Vite-Vorlage für React + TypeScript.
//
// Absichtlich die Standardregeln und nichts Eigenes: Die Typprüfung läuft
// ohnehin im Build (`tsc -b`); ESLint fängt darüber hinaus die React-Fallen
// (Hook-Regeln, veraltete Abhängigkeits-Listen) und totes Zeug.
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
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
