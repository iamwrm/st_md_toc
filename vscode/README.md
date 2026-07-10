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

## Sideload on vscode.dev

The repository includes a `uv`-managed Python script that downloads the latest
VSIX, extracts it, creates a trusted localhost certificate with `mkcert`, and
serves the extension over HTTPS with the CORS headers vscode.dev requires:

Install `uv` first if the command is not available:

```sh
# Windows
winget install --id=astral-sh.uv -e

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```sh
uv run --script vscode/serve_vscode_dev.py --install-mkcert --open
```

Run that command from the repository root. The `--install-mkcert` flag is only
needed the first time. After the server starts, open the Command Palette on
vscode.dev, run **Developer: Install Extension From Location...**, and enter
`https://localhost:5000`.

Later runs are simply:

```sh
uv run --script vscode/serve_vscode_dev.py
```

Use `--release v0.1.0` to pin a release, `--vsix path/to/file.vsix` for a local
package, or `--refresh` to replace the cached download. Press `Ctrl+C` to stop.
Expected first-run failures print a specific recovery command, such as choosing
another port, using a local VSIX, refreshing the cache, or installing `mkcert`.

## Development

```sh
npm ci
npm run check
npm run package
```

Open this directory in VS Code and press `F5` to run the extension locally.
