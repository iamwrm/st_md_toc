# Markdown TOC for Sublime Text 4

A Sublime Text 4 package that shows a live table of contents for the active
Markdown file in a docked side pane.

## Features

- Indented, color-coded TOC on the left or right.
- Single-click reveal; `Enter` or double-click to jump and focus the source.
- Current-heading highlight and debounced live refresh.
- ATX and Setext headings, fenced-code awareness, and cleaned inline markup.
- Copy the contents of a fenced code block from the context menu.
- Cut a heading together with its body and nested subsections.
- Exact layout restoration, including existing multi-column layouts.

## Usage

| Action | How |
| --- | --- |
| Toggle the TOC pane | `Ctrl+Alt+T`, **View > Markdown TOC**, or the Command Palette |
| Reveal a heading | Click its TOC entry |
| Jump to a heading | Press `Enter` or double-click its TOC entry |
| Refresh | **Markdown TOC: Refresh** in the Command Palette |
| Copy a code block | Right-click inside the fenced block |
| Cut a section | Right-click its heading |

Closing the TOC tab restores the previous window layout.

## Settings

Open **Preferences > Package Settings > Markdown TOC > Settings**.

```jsonc
{
    "side": "left",
    "width": 0.25,
    "navigate_on_click": true,
    "highlight_current": true,
    "scroll_toc_to_current": true,
    "refresh_on_edit": true,
    "refresh_delay_ms": 400,
    "focus_toc_on_open": false
}
```
