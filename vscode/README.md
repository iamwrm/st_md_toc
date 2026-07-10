# Markdown TOC for VS Code

Shows the active Markdown document as a live, hierarchical outline in the
activity bar. It runs in both desktop VS Code and the browser extension host
used by [vscode.dev](https://vscode.dev/).

## Commands

- `Markdown TOC: Show Outline` (`Ctrl+Alt+T`, or `Cmd+Alt+T` on macOS)
- `Markdown TOC: Refresh`
- `Markdown TOC: Copy Code Block`
- `Markdown TOC: Cut Whole Section`

The latter two commands are also available from the Markdown editor context
menu. Copy Code Block supports unindented backtick and tilde fences. Cut Whole
Section removes the selected heading, its body, and nested subsections.

## Development

```sh
npm ci
npm run check
npm run package
```

Open this directory in VS Code and press `F5` to run the extension locally.
