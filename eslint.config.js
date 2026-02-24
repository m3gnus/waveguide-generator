// ESLint flat config — warning-only baseline.
// Purpose: surface issues incrementally without breaking the build.
// Do NOT run --fix sweepingly; fix issues per-session as appropriate.
import js from '@eslint/js';
import globals from 'globals';

export default [
  {
    ignores: [
      'dist/**',
      'bundle.js',
      'node_modules/**',
      'server/**',
      'scripts/diagnostics/**',
    ],
  },
  {
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      // Downgrade all recommended errors to warnings so lint never blocks CI.
      // Tighten individual rules to 'error' only after the codebase is clean.
      ...Object.fromEntries(
        Object.keys(js.configs.recommended.rules).map((rule) => [rule, 'warn'])
      ),

      // Rules that are too noisy or conflict with current style — silenced.
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
];
