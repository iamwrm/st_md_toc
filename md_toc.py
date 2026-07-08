"""Markdown TOC — show a table of contents for Markdown files in a side pane.

Sublime Text 4 package.
"""

import os
import re

import sublime
import sublime_plugin

SETTINGS_FILE = "Markdown TOC.sublime-settings"
TOC_SYNTAX_NAME = "Markdown TOC.sublime-syntax"

# view.settings() keys
S_IS_TOC = "md_toc"                  # marks the TOC view
S_ROWS = "md_toc_rows"               # TOC row -> source row mapping
S_SOURCE_ID = "md_toc_source_id"     # id() of the tracked source view

# window.settings() keys
W_PREV_LAYOUT = "md_toc_prev_layout"

_pending_refresh = {}  # source view id -> debounce token


def plugin_settings():
    return sublime.load_settings(SETTINGS_FILE)


def toc_syntax_path():
    """Locate the TOC syntax regardless of the installed folder name."""
    resources = sublime.find_resources(TOC_SYNTAX_NAME)
    if resources:
        return resources[0]
    return "scope:text.plain"  # graceful fallback: plain text


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

ATX_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
ATX_EMPTY_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]*$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_MD_RE = re.compile(r"(\*{1,3}|_{1,3}|`+)(.+?)\1")
LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def clean_heading_text(text):
    """Strip common inline markup from a heading for display."""
    text = LINK_RE.sub(r"\1", text)
    text = INLINE_MD_RE.sub(r"\2", text)
    return text.strip()


def parse_headings(view):
    """Return a list of (row, level, text) for every heading in the view."""
    content = view.substr(sublime.Region(0, view.size()))
    lines = content.split("\n")

    headings = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for row, line in enumerate(lines):
        if in_fence:
            stripped = line.strip()
            if (stripped and stripped[0] == fence_char
                    and len(stripped) >= fence_len
                    and stripped == stripped[0] * len(stripped)):
                in_fence = False
            continue

        m = FENCE_OPEN_RE.match(line)
        if m:
            in_fence = True
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            continue

        m = ATX_RE.match(line)
        if m:
            headings.append((row, len(m.group(1)), clean_heading_text(m.group(2))))
            continue
        if ATX_EMPTY_RE.match(line):
            continue

        # Setext headings: text line followed by === or ---
        m = SETEXT_RE.match(line)
        if m and row > 0:
            prev = lines[row - 1].strip()
            if prev and not prev.startswith("#") and not SETEXT_RE.match(lines[row - 1]):
                # avoid treating a heading we already recorded twice
                if not headings or headings[-1][0] != row - 1:
                    level = 1 if m.group(1)[0] == "=" else 2
                    headings.append((row - 1, level, clean_heading_text(prev)))

    return headings


FENCE_LINE_RE = re.compile(r"^(`{3,}|~{3,})")


def fenced_code_blocks(lines):
    """Return [(open_row, close_row_or_None)] for column-0 fenced blocks.

    Only plain, unindented ``` / ~~~ pairs are considered. An unclosed
    fence extends to EOF (close_row is None).
    """
    blocks = []
    open_row = None
    fence = ""
    for i, line in enumerate(lines):
        m = FENCE_LINE_RE.match(line)
        if open_row is None:
            if m:
                open_row, fence = i, m.group(1)
        elif m:
            run = m.group(1)
            rest = line[len(run):].strip()
            if run[0] == fence[0] and len(run) >= len(fence) and not rest:
                blocks.append((open_row, i))
                open_row = None
    if open_row is not None:
        blocks.append((open_row, None))
    return blocks


def is_markdown(view):
    if not view or view.settings().get(S_IS_TOC):
        return False
    syntax = (view.settings().get("syntax") or "").lower()
    if "markdown" in syntax or "multimarkdown" in syntax:
        return True
    name = (view.file_name() or "").lower()
    return name.endswith((".md", ".markdown", ".mdown", ".mkd", ".mkdn"))


# ---------------------------------------------------------------------------
# TOC view helpers
# ---------------------------------------------------------------------------

def find_toc_view(window):
    if not window:
        return None
    for view in window.views():
        if view.settings().get(S_IS_TOC):
            return view
    return None


def view_by_id(window, view_id):
    if not view_id:
        return None
    for view in window.views():
        if view.id() == view_id:
            return view
    return None


def render_toc(toc_view, source_view):
    """Fill the TOC view with the headings of source_view."""
    headings = parse_headings(source_view)
    indent = "  "
    lines = []
    rows = []
    for row, level, text in headings:
        lines.append(indent * (level - 1) + text)
        rows.append(row)

    content = "\n".join(lines) if lines else "(no headings)"

    toc_view.settings().set(S_ROWS, rows)
    toc_view.settings().set(S_SOURCE_ID, source_view.id())
    toc_view.run_command("md_toc_replace", {"content": content})

    name = os.path.basename(source_view.file_name() or "") or "untitled"
    toc_view.set_name("TOC \u2014 " + name)


def refresh_for_source(source_view):
    window = source_view.window()
    toc_view = find_toc_view(window)
    if toc_view and toc_view.settings().get(S_SOURCE_ID) == source_view.id():
        render_toc(toc_view, source_view)
        highlight_current_heading(toc_view, source_view)


def highlight_current_heading(toc_view, source_view):
    """Highlight the TOC entry for the heading containing the source caret."""
    rows = toc_view.settings().get(S_ROWS) or []
    if not rows or not source_view.sel():
        toc_view.erase_regions("md_toc_current")
        return

    caret_row = source_view.rowcol(source_view.sel()[0].begin())[0]
    current = -1
    for i, hrow in enumerate(rows):
        if hrow <= caret_row:
            current = i
        else:
            break

    if current < 0:
        toc_view.erase_regions("md_toc_current")
        return

    line = toc_view.line(toc_view.text_point(current, 0))
    toc_view.add_regions(
        "md_toc_current", [line], "region.bluish markup.heading",
        flags=sublime.DRAW_NO_FILL | sublime.DRAW_NO_OUTLINE
        | sublime.DRAW_SOLID_UNDERLINE | sublime.PERSISTENT,
    )
    if plugin_settings().get("scroll_toc_to_current", True):
        toc_view.show(line.begin(), False)


def navigate_from_toc(toc_view, focus_source):
    rows = toc_view.settings().get(S_ROWS) or []
    window = toc_view.window()
    if not window or not rows or not toc_view.sel():
        return
    source = view_by_id(window, toc_view.settings().get(S_SOURCE_ID))
    if not source:
        return

    toc_row = toc_view.rowcol(toc_view.sel()[0].begin())[0]
    if toc_row >= len(rows):
        return

    pt = source.text_point(rows[toc_row], 0)
    source.sel().clear()
    source.sel().add(sublime.Region(pt))
    source.show_at_center(pt)
    if focus_source:
        window.focus_view(source)


# ---------------------------------------------------------------------------
# Layout management
# ---------------------------------------------------------------------------

def add_toc_column(window, side, width):
    """Extend the CURRENT layout with an extra column for the TOC.

    The TOC cell is appended last, so every existing group keeps its index
    and no views have to be moved. Returns the TOC group index.
    """
    layout = window.get_layout()
    cols = layout["cols"]
    rows = layout["rows"]
    cells = [list(c) for c in layout["cells"]]
    full_height = len(rows) - 1

    if side == "left":
        # squeeze existing columns into [width, 1.0], new column at the front
        new_cols = [0.0] + [width + c * (1.0 - width) for c in cols]
        new_cells = [[x1 + 1, y1, x2 + 1, y2] for x1, y1, x2, y2 in cells]
        toc_cell = [0, 0, 1, full_height]
    else:
        # squeeze existing columns into [0.0, 1.0 - width], new column at the end
        new_cols = [c * (1.0 - width) for c in cols] + [1.0]
        new_cells = cells
        toc_cell = [len(cols) - 1, 0, len(cols), full_height]

    toc_group = len(new_cells)
    new_cells.append(toc_cell)
    window.set_layout({"cols": new_cols, "rows": rows, "cells": new_cells})
    return toc_group


def open_toc(window, source_view):
    settings = plugin_settings()
    side = settings.get("side", "right")
    width = float(settings.get("width", 0.25))
    width = min(max(width, 0.1), 0.5)

    window.settings().set(W_PREV_LAYOUT, window.get_layout())

    toc_group = add_toc_column(window, side, width)
    window.focus_group(toc_group)
    toc_view = window.new_file()
    toc_view.set_scratch(True)
    toc_view.set_read_only(True)
    try:
        toc_view.assign_syntax(toc_syntax_path())
    except Exception:
        pass  # syntax is cosmetic; never block the TOC on it

    vs = toc_view.settings()
    vs.set(S_IS_TOC, True)
    vs.set("gutter", False)
    vs.set("line_numbers", False)
    vs.set("word_wrap", False)
    vs.set("draw_indent_guides", False)
    vs.set("draw_white_space", "none")
    vs.set("scroll_past_end", False)
    vs.set("caret_extra_width", 0)
    vs.set("highlight_line", True)

    render_toc(toc_view, source_view)
    highlight_current_heading(toc_view, source_view)

    if settings.get("focus_toc_on_open", False):
        window.focus_view(toc_view)
    else:
        window.focus_view(source_view)


SINGLE_LAYOUT = {"cols": [0.0, 1.0], "rows": [0.0, 1.0], "cells": [[0, 0, 1, 1]]}


def restore_layout(window):
    """Restore the pre-TOC layout, never leaving an empty group behind."""
    prev = window.settings().get(W_PREV_LAYOUT)
    window.settings().erase(W_PREV_LAYOUT)

    def apply():
        if not window.is_valid():
            return
        restored = False
        try:
            if prev:
                window.set_layout(prev)
                restored = True
        except Exception:
            pass
        if restored:
            return
        # safety net (no saved layout): collapse any group left empty
        for group in range(window.num_groups()):
            if not window.views_in_group(group):
                window.set_layout(SINGLE_LAYOUT)
                break

    # defer until the closing view is actually gone
    sublime.set_timeout(apply, 50)


def close_toc(window, toc_view):
    # on_pre_close handles the layout restore
    toc_view.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

class MdTocReplaceCommand(sublime_plugin.TextCommand):
    """Internal: replace the full contents of the (read-only) TOC view."""

    def run(self, edit, content):
        view = self.view
        view.set_read_only(False)
        view.replace(edit, sublime.Region(0, view.size()), content)
        view.sel().clear()
        view.set_read_only(True)


class MarkdownTocToggleCommand(sublime_plugin.WindowCommand):
    """Toggle the TOC side pane for the active Markdown file."""

    def run(self):
        toc_view = find_toc_view(self.window)
        if toc_view:
            close_toc(self.window, toc_view)
            return
        source = self.window.active_view()
        if not is_markdown(source):
            sublime.status_message("Markdown TOC: active file is not Markdown")
            return
        open_toc(self.window, source)

    def is_enabled(self):
        return bool(find_toc_view(self.window)
                    or is_markdown(self.window.active_view()))


class MarkdownTocRefreshCommand(sublime_plugin.WindowCommand):
    """Re-parse the tracked source file and redraw the TOC."""

    def run(self):
        toc_view = find_toc_view(self.window)
        if not toc_view:
            return
        source = view_by_id(self.window,
                            toc_view.settings().get(S_SOURCE_ID))
        if source:
            render_toc(toc_view, source)
            highlight_current_heading(toc_view, source)

    def is_enabled(self):
        return find_toc_view(self.window) is not None


class MarkdownCopyCodeBlockCommand(sublime_plugin.TextCommand):
    """Copy the contents of the fenced code block under the mouse / caret.

    Invoked from the right-click context menu (uses the click position via
    want_event) or the Command Palette (falls back to the caret).
    """

    def want_event(self):
        return True

    def _point(self, event):
        if event is not None:
            return self.view.window_to_text((event["x"], event["y"]))
        sel = self.view.sel()
        return sel[0].begin() if sel else None

    def _block_content(self, event):
        """Content lines of the block at the event/caret, or None."""
        if not is_markdown(self.view):
            return None
        pt = self._point(event)
        if pt is None:
            return None
        row = self.view.rowcol(pt)[0]
        lines = self.view.substr(
            sublime.Region(0, self.view.size())).split("\n")
        for open_row, close_row in fenced_code_blocks(lines):
            end = close_row if close_row is not None else len(lines) - 1
            if open_row <= row <= end:
                last = close_row if close_row is not None else len(lines)
                return lines[open_row + 1:last]
        return None

    def run(self, edit, event=None):
        content = self._block_content(event)
        if content is None:
            sublime.status_message("Markdown TOC: no code block here")
            return
        text = "\n".join(content)
        sublime.set_clipboard(text + "\n" if text else "")
        n = len(content)
        sublime.status_message(
            "Markdown TOC: copied %d line%s" % (n, "" if n == 1 else "s"))

    def is_enabled(self, event=None):
        return self._block_content(event) is not None

    def is_visible(self, event=None):
        return is_markdown(self.view) and self._block_content(event) is not None


class MdTocFocusHeadingCommand(sublime_plugin.TextCommand):
    """Bound to Enter / double-click inside the TOC: jump and focus source."""

    def run(self, edit):
        navigate_from_toc(self.view, focus_source=True)


# ---------------------------------------------------------------------------
# Event listener
# ---------------------------------------------------------------------------

class MdTocListener(sublime_plugin.EventListener):

    # -- navigation: click in TOC reveals heading in the source ------------
    def on_selection_modified(self, view):
        if view.settings().get(S_IS_TOC):
            if plugin_settings().get("navigate_on_click", True):
                navigate_from_toc(view, focus_source=False)
            return

        # source caret moved -> underline current heading in TOC
        if is_markdown(view) and plugin_settings().get("highlight_current", True):
            toc_view = find_toc_view(view.window())
            if toc_view and toc_view.settings().get(S_SOURCE_ID) == view.id():
                highlight_current_heading(toc_view, view)

    # -- refresh ------------------------------------------------------------
    def on_post_save_async(self, view):
        if is_markdown(view):
            refresh_for_source(view)

    def on_modified_async(self, view):
        if not is_markdown(view):
            return
        if not plugin_settings().get("refresh_on_edit", True):
            return
        window = view.window()
        toc_view = find_toc_view(window)
        if not toc_view or toc_view.settings().get(S_SOURCE_ID) != view.id():
            return

        key = view.id()
        token = _pending_refresh.get(key, 0) + 1
        _pending_refresh[key] = token
        delay = int(plugin_settings().get("refresh_delay_ms", 400))

        def cb():
            if _pending_refresh.get(key) == token and view.is_valid():
                refresh_for_source(view)

        sublime.set_timeout_async(cb, delay)

    # -- retarget the TOC when switching between markdown files -------------
    def on_activated_async(self, view):
        if not is_markdown(view):
            return
        toc_view = find_toc_view(view.window())
        if toc_view and toc_view.settings().get(S_SOURCE_ID) != view.id():
            render_toc(toc_view, view)
            highlight_current_heading(toc_view, view)

    # -- restore layout when the TOC view is closed --------------------------
    def on_pre_close(self, view):
        if not view.settings().get(S_IS_TOC):
            return
        window = view.window()
        if window:
            restore_layout(window)

    # -- keybinding context ---------------------------------------------------
    def on_query_context(self, view, key, operator, operand, match_all):
        if key != "md_toc_view":
            return None
        value = bool(view.settings().get(S_IS_TOC))
        if operator == sublime.OP_EQUAL:
            return value == operand
        if operator == sublime.OP_NOT_EQUAL:
            return value != operand
        return None
