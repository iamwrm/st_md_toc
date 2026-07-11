# Project context

This repository ships Markdown TOC extensions for two editors:

- `st4/`: Sublime Text 4 package, targeting Python 3.8.
- `vscode/`: VS Code extension with one browser-safe bundle for desktop VS
  Code and vscode.dev. Do not introduce Node-only runtime APIs.

## Cross-editor parity

Every user-facing feature must be implemented and tested for all three
supported surfaces: Sublime Text 4, desktop VS Code, and vscode.dev. The two
VS Code surfaces share one browser-safe implementation. If a platform imposes
a limitation (for example, browser CORS), preserve the same command semantics
and report the platform limitation clearly instead of silently omitting the
feature.

## Sublime Text invariants

- The installed package directory must be named `MarkdownTOC`.
- Never replace an existing window layout wholesale when opening the TOC.
  Add the TOC cell last so existing group indices stay stable.
- Closing the TOC must restore the exact saved layout without leaving a pane.
- Locate syntax resources dynamically with `sublime.find_resources`.
- Copy Code Block only recognizes unindented backtick or tilde fences.
- Section cutting includes nested headings and stops at the next peer/parent.
- Copy Link Contents supports local and HTTP(S) text resources, recursively
  expands inline, reference, and URI-autolink Markdown links, preserves
  non-resource links, detects cycles, stops at a depth of 20, and enforces
  per-resource link and aggregate output budgets.

Run:

```sh
python -m unittest discover -s st4/tests
python -m py_compile st4/md_toc.py
```

## VS Code invariants

- `src/markdown.js` is the pure parser and must remain independently testable.
- Runtime code may use the `vscode` API and browser globals, but not `fs`,
  `path`, `process`, or other Node-only APIs.
- Both `main` and `browser` point at the generated `dist/extension.js` bundle.
- Keep the outline live when the active editor, document, or selection changes.
- Keep copy-code-block, cut-section, and copy-link-contents behavior aligned
  with the ST4 package.

Run:

```sh
cd vscode
npm ci
npm run check
npm run package
```

## Releases

`.github/workflows/ci.yml` validates both targets. A `v*` tag runs
`.github/workflows/release.yml`, producing a Sublime Text zip whose top-level
folder is `MarkdownTOC` and a VS Code VSIX.
