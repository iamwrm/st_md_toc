# Markdown TOC (side pane)

A Sublime Text 4 package that shows a live table of contents for the active
Markdown file in a docked side pane — the closest thing Sublime offers to a
custom sidebar panel.

![](https://img.shields.io/badge/Sublime%20Text-4-orange)

## Features

- **TOC side pane** on the left or right, indented by heading level and
  color-coded per depth.
- **Click to navigate** — clicking an entry scrolls the file to that heading
  (focus stays in the TOC). Press **Enter** or **double-click** to jump *and*
  focus the file.
- **Follows your caret** — the heading you're currently editing is underlined
  in the TOC.
- **Live refresh** — updates as you type (debounced), on save, and when you
  switch between Markdown tabs.
- Understands `#` ATX headings, Setext (`===` / `---`) headings, skips fenced
  code blocks, and strips inline markup (links, `code`, *emphasis*) from
  entries.

## Installation

Clone (or symlink) this repository into your `Packages` directory as
`MarkdownTOC`:

```sh
# macOS
git clone <repo-url> "$HOME/Library/Application Support/Sublime Text/Packages/MarkdownTOC"

# Linux
git clone <repo-url> "$HOME/.config/sublime-text/Packages/MarkdownTOC"

# Windows (PowerShell)
git clone <repo-url> "$env:APPDATA\Sublime Text\Packages\MarkdownTOC"
```

> The folder **must** be named `MarkdownTOC` — the syntax and settings files
> are referenced by that path.

## Usage

| Action | How |
| --- | --- |
| Toggle the TOC pane | `Ctrl+Alt+T`, **View ▸ Markdown TOC**, or Command Palette ▸ *Markdown TOC: Toggle Side Pane* |
| Reveal a heading | Click its TOC entry |
| Jump to a heading (and focus the file) | `Enter` or double-click in the TOC |
| Force a refresh | Command Palette ▸ *Markdown TOC: Refresh* |

Closing the TOC (toggle again, or just close its tab) restores your previous
window layout.

## Settings

**Preferences ▸ Package Settings ▸ Markdown TOC ▸ Settings**

```jsonc
{
    "side": "right",              // "left" or "right"
    "width": 0.25,                // pane width, fraction of the window (0.1–0.5)
    "navigate_on_click": true,    // single click scrolls source to heading
    "highlight_current": true,    // underline the heading under the caret
    "scroll_toc_to_current": true,
    "refresh_on_edit": true,      // rebuild while typing (debounced)
    "refresh_delay_ms": 400,
    "focus_toc_on_open": false
}
```

## License

MIT
