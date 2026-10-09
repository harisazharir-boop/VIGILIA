/**
 * ESLint 9 flat config — the previous .eslintrc.cjs format is no longer read.
 *
 * `typescript-eslint` (the combined wrapper) is not a dependency, so the
 * plugin's own recommended flat config is spread directly. The base
 * `js.configs.recommended` rules that TypeScript handles better are turned
 * off for .ts, the same way typescript-eslint's configs do — otherwise the
 * base `no-unused-vars`/`no-redeclare` flag TS overloads and parameter
 * properties as errors.
 */
import js from '@eslint/js';
import globals from 'globals';
import tseslintPlugin from '@typescript-eslint/eslint-plugin';
import tsParser from '@typescript-eslint/parser';

const tsRecommended = tseslintPlugin.configs['flat/recommended'];

export default [
  {
    ignores: ['dist', 'node_modules', 'eslint.config.js', '**/*.cjs', '*.mjs'],
  },
  js.configs.recommended,
  {
    files: ['**/*.ts'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.node, ...globals.es2022 },
    },
    plugins: { '@typescript-eslint': tseslintPlugin },
    rules: {
      ...tsRecommended.rules,
      // Turned off in favour of the @typescript-eslint equivalents.
      'no-unused-vars': 'off',
      'no-redeclare': 'off',
      'no-undef': 'off',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/no-explicit-any': 'off',
      'no-case-declarations': 'off',
    },
  },
];
