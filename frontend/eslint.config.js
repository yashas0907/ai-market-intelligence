import js from '@eslint/js'
import globals from 'globals'

export default [
  js.configs.recommended,
  {
    files: ['src/**/*.{js,jsx}', 'scripts/*.cjs'],
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node }
    },
    rules: {
      'no-undef': 'error',
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-unused-vars': ['warn', { args: 'none' }]
    }
  },
  {
    ignores: ['dist/**', 'node_modules/**']
  }
]
