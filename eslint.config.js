// Keep the CI lint scope intentionally narrow: parse the shipped browser scripts
// without imposing a new style policy on this legacy, globally wired bundle.
module.exports = [
  {
    files: ["pool/static/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
    },
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
    rules: {},
  },
];
