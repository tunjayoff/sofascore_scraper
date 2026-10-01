import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import globals from 'globals'

// eslint-plugin-vue's `recommended` also carries formatting rules (attribute per line, line
// breaks in elements, ...). This config lints, it does not format: rules the plugin marks as
// `layout` are off, so the existing templates are not reflowed and no format debate starts.
const vueLayoutOff = Object.fromEntries(
  Object.entries(pluginVue.rules)
    .filter(([, rule]) => rule.meta?.type === 'layout')
    .map(([name]) => [`vue/${name}`, 'off']),
)

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
      parserOptions: { parser: tseslint.parser, extraFileExtensions: ['.vue'] },
    },
    rules: {
      ...vueLayoutOff,
      // The match detail JSON is typed (lib/matchDetail.ts) and no `any` is left; keep it
      // that way. Shapes we don't know take `unknown` and a parser, as parseStatistics does.
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  {
    files: ['*.config.{js,ts}'],
    languageOptions: { globals: globals.node },
  },
)
