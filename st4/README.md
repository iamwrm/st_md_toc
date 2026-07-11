# Markdown TOC for Sublime Text 4

A Sublime Text 4 package that shows a live table of contents for the active
Markdown file in a docked side pane.

## Features

- Indented, color-coded TOC on the left or right.
- Single-click reveal; `Enter` or double-click to jump and focus the source.
- Current-heading highlight and debounced live refresh.
- ATX and Setext headings, fenced-code awareness, and cleaned inline markup.
- Copy a local or HTTP text link's contents, recursively expanding links in it.
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
| Copy linked text | Right-click a Markdown text link, or place the caret in it and use **Markdown TOC: Copy Link Contents at Caret** |
| Copy a code block | Right-click inside the fenced block |
| Cut a section | Right-click its heading |

Closing the TOC tab restores the previous window layout.

**Copy Link Contents** supports local paths, `file:` URLs, and `http:` or
`https:` URLs. Relative links are resolved from the Markdown file that contains
each link. Inline links, full/collapsed/shortcut reference links, angle-bracket
destinations, optional titles, and HTTP(S) URI autolinks use ordinary Markdown
syntax. Links inside fetched text are replaced recursively, up to 20 resource
levels; fragment-only, email, and other non-resource links remain unchanged.
Cycles, unreadable resources, non-text data, and a 21st level are
reported without changing the clipboard. Network and file reads run in the
background so Sublime Text remains responsive. Each linked resource is limited
to 1 MiB; HTTP resources must use a textual media type (including text, JSON,
XML, YAML, TOML, and JavaScript families) when the server supplies one. A
resource may contain at most 10,000 expandable links. One operation may fetch
at most 1,000 resources and parse 10,000 links, with both fetched source text
and expanded output limited to 20,971,520 characters. HTTP requests time out
after 15 seconds.

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
