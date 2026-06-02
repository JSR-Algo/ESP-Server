module.exports = {
  root: true,
  env: { node: true, browser: true, es2021: true },
  extends: ['eslint:recommended', 'plugin:vue/recommended'],
  parserOptions: { ecmaVersion: 2020, sourceType: 'module' },
  rules: {
    'no-console': process.env.NODE_ENV === 'production' ? 'warn' : 'off',
    'no-unused-vars': 'warn',
    'vue/no-unused-vars': 'warn',
    'vue/multi-word-component-names': 'off'
  }
};
