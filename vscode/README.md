# Markdown TOC for VS Code

Shows the active Markdown document as a live, hierarchical outline in the
activity bar. It runs in both desktop VS Code and the browser extension host
used by [vscode.dev](https://vscode.dev/).

## Commands

- `Markdown TOC: Show Outline` (`Ctrl+Alt+T`, or `Cmd+Alt+T` on macOS)
- `Markdown TOC: Refresh`
- `Markdown TOC: Copy Code Block`
- `Markdown TOC: Copy Link Contents`
- `Markdown TOC: Cut Whole Section`

The latter three commands are also available from the Markdown editor context
menu. Copy Code Block supports unindented backtick and tilde fences. Cut Whole
Section removes the selected heading, its body, and nested subsections.

Copy Link Contents loads the non-image Markdown text link under the cursor or
selection and copies the linked UTF-8 text to the clipboard. It understands
inline links, full/collapsed/shortcut reference links, angle-bracket
destinations, optional titles, and HTTP(S) URI autolinks. Local links must
be readable through the VS Code file-system API, which supports workspace files,
standalone desktop files, and file-system providers available to vscode.dev.
HTTP and HTTPS links use the extension host's fetch API, so vscode.dev requires
the remote server to allow CORS. Relative links are resolved from the file that
contains them, and Markdown text links inside loaded files are recursively replaced
with their contents. Fragment-only, email, and other non-resource links remain
unchanged. The command rejects cycles, recursion beyond 20 files, non-text data,
individual resources larger than 1 MiB, more than 10,000 expandable links in
one resource, or an operation exceeding 1,000 fetched resources, 10,000 parsed
links, or 20,971,520 fetched/expanded characters. HTTP requests time out after
15 seconds and can be cancelled from the progress notification. The clipboard
remains unchanged if any linked resource cannot be loaded safely.

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
