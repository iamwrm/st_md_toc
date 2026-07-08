# CLAUDE.md — MarkdownTOC (Sublime Text 4 package)

Project context for AI-assisted development. Keep this file updated when
requirements change or new lessons are learned.

## Project

A Sublime Text 4 package showing a live table of contents for the active
Markdown file in a docked side pane. ST's real sidebar cannot host custom
panels, so a side editor group (Outline-plugin style) is the standard
approach.

## User Requirements

1. **TOC in a "sidebar"** for Markdown files → implemented as a docked side
   pane (left/right column).
2. **Side is configurable** — user prefers **left** (`"side": "left"` is the
   current default in `Markdown TOC.sublime-settings`).
3. **Closing must fully restore the previous layout** — no empty pane may be
   left behind (was a reported bug; fixed).
4. **Must respect existing multi-column/grid layouts** — opening the TOC adds
   a column alongside the current layout instead of replacing it; closing
   restores the exact prior layout without reshuffling tabs.
5. Interaction model:
   - Single click on a TOC entry → scroll source there (focus stays in TOC).
   - `Enter` / double-click → jump **and** focus the source file.
   - `Ctrl+Alt+T` toggles the pane; closing the TOC tab directly also works.
6. Live behavior: refresh on edit (debounced), on save, retarget when
   switching Markdown tabs, underline the heading under the source caret.
7. Installed via **symlink** from this repo to
   `~/Library/Application Support/Sublime Text/Packages/MarkdownTOC`
   (folder name MUST be `MarkdownTOC` — syntax/settings paths reference it).

## File Map

| File | Role |
| --- | --- |
| `md_toc.py` | All plugin logic: parsing, layout, navigation, listeners |
| `Markdown TOC.sublime-settings` | Defaults: side, width, refresh, highlight |
| `Markdown TOC.sublime-syntax` | Colors TOC entries by depth (2-space indent = 1 level) |
| `Default.sublime-keymap` | `Ctrl+Alt+T` toggle; `Enter` (context `md_toc_view`) |
| `Default.sublime-mousemap` | Double-click jump (context-scoped!) |
| `Default.sublime-commands` / `Main.sublime-menu` | Palette + menus |
| `.python-version` | `3.8` (ST4 plugin host) |

## Lessons Learned

- **Never replace `window.set_layout()` wholesale.** Views belong to *group
  indices*, not cells. Replacing the layout mangles multi-column setups. To
  add a side pane: transform the current layout (squeeze `cols`, shift cell
  x-indices for the left side) and **append the new cell last** so every
  existing group keeps its index and no tabs move. See `add_toc_column()`.
- **Layout restore is racy.** `on_pre_close` fires before the view is gone;
  restoring with `set_timeout(..., 0)` plus a second restore path in the
  close command caused an empty leftover pane. Fix: single restore path
  (`restore_layout()` called only from `on_pre_close`), deferred ~50 ms, with
  a safety net that collapses empty groups — but the safety net must be
  **gated on "no saved layout"**, or it destroys intentionally-empty groups
  in user grids.
- **Mousemaps are global.** A `Default.sublime-mousemap` binding hijacks
  clicks everywhere unless given a `"context"` (same context system as
  keymaps; custom keys via `EventListener.on_query_context`).
- **Read-only scratch views** need a helper `TextCommand` that flips
  `set_read_only(False)` → replace → `set_read_only(True)`; edits require an
  `edit` token only commands can get.
- Prefer `window.get_layout()` (works on all ST builds) over the newer
  `window.layout()`.
- Markdown parsing gotchas handled in `parse_headings()`: fenced code blocks
  (``` and ~~~, matching fence length), Setext headings (`===`/`---`, only
  after a non-blank non-heading line), `##no-space` is NOT a heading, strip
  trailing `###`, strip inline markup/links from display text.
- `on_selection_modified` fires constantly — bail out with cheap settings
  checks first. Debounce `on_modified_async` with a token counter
  (`_pending_refresh`).
- TOC ↔ source wiring lives in view settings (`md_toc_rows` row mapping,
  `md_toc_source_id`) so it survives without global state.

## Dev Workflow

1. Edit files in this repo — the package is **symlinked** into `Packages/`,
   so Sublime hot-reloads `md_toc.py` on save (no restart).
2. Debug via the ST console: `View → Show Console` (`` Ctrl+` ``). Print
   tracebacks appear there; `sublime.log_commands(True)` helps trace commands.
3. Test parsing offline (no ST needed): stub `sublime`/`sublime_plugin`
   modules on `sys.path`, feed a `FakeView` wrapping a string into
   `parse_headings()`, assert on `(row, level, text)` tuples. Layout math can
   be tested as pure functions.
4. Always run `python3 -m py_compile md_toc.py` after edits (target Python
   3.8 — no `:=` in hot paths is fine, but avoid 3.9+ syntax). Remove
   `__pycache__` afterwards.
5. Validate JSON-ish resource files (they allow `//` comments — strip before
   `json.loads`).
6. Manual smoke test checklist:
   - Toggle open/close in a single-column window → layout restored.
   - Toggle with a pre-existing 2-column / grid layout → columns squeezed,
     restored exactly, no tabs moved.
   - Close via tab ×, `Cmd+W`, and toggle — no empty pane in any path.
   - Click / `Enter` / double-click navigation; caret-follow underline.
   - Edit + save + switch between two Markdown tabs → TOC retargets.

## Settings Reference (defaults)

```jsonc
{
    "side": "left",            // user preference
    "width": 0.25,             // clamped to 0.1–0.5
    "navigate_on_click": true,
    "highlight_current": true,
    "scroll_toc_to_current": true,
    "refresh_on_edit": true,
    "refresh_delay_ms": 400,
    "focus_toc_on_open": false
}
```
