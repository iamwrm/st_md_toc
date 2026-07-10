"""Offline tests for MarkdownTOC's parser and section cutting."""

import importlib
import pathlib
import sys
import types
import unittest


class Region:
    def __init__(self, a, b=None):
        self.a = a
        self.b = a if b is None else b

    def begin(self):
        return min(self.a, self.b)

    def end(self):
        return max(self.a, self.b)


class Settings:
    def __init__(self, syntax="Packages/Markdown/Markdown.sublime-syntax"):
        self.values = {"syntax": syntax}

    def get(self, key, default=None):
        return self.values.get(key, default)


class Selection(list):
    pass


class FakeView:
    def __init__(self, text, caret=0):
        self.text = text
        self._settings = Settings()
        self._selection = Selection([Region(caret)])

    def size(self):
        return len(self.text)

    def substr(self, region):
        return self.text[region.begin():region.end()]

    def text_point(self, row, col):
        starts = [0]
        for i, char in enumerate(self.text):
            if char == "\n":
                starts.append(i + 1)
        return min(starts[row] + col, len(self.text))

    def rowcol(self, point):
        before = self.text[:point]
        row = before.count("\n")
        last_newline = before.rfind("\n")
        return row, point if last_newline < 0 else point - last_newline - 1

    def settings(self):
        return self._settings

    def file_name(self):
        return "test.md"

    def sel(self):
        return self._selection

    def is_read_only(self):
        return False

    def erase(self, _edit, region):
        self.text = self.text[:region.begin()] + self.text[region.end():]


def load_plugin():
    sublime = types.ModuleType("sublime")
    sublime.Region = Region
    sublime.set_clipboard = lambda text: setattr(sublime, "clipboard", text)
    sublime.status_message = lambda _message: None
    sublime.DRAW_NO_FILL = 0
    sublime.DRAW_NO_OUTLINE = 0
    sublime.DRAW_SOLID_UNDERLINE = 0
    sublime.PERSISTENT = 0

    sublime_plugin = types.ModuleType("sublime_plugin")

    class TextCommand:
        def __init__(self, view):
            self.view = view

    sublime_plugin.TextCommand = TextCommand
    sublime_plugin.WindowCommand = object
    sublime_plugin.EventListener = object

    sys.modules["sublime"] = sublime
    sys.modules["sublime_plugin"] = sublime_plugin
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    module = importlib.import_module("md_toc")
    return module, sublime


md_toc, sublime = load_plugin()


class HeadingSectionTests(unittest.TestCase):
    def test_section_includes_nested_headings_only(self):
        text = (
            "# Intro\nintro\n"
            "## Section 1\nbody\n"
            "### Section 1.1\nnested\n"
            "#### Section 1.1.1\ndeep\n"
            "## Section 2\nkeep\n"
            "# Outro\nkeep too\n"
        )
        view = FakeView(text)
        region = md_toc.heading_section_region(view, 2)
        self.assertEqual(
            view.substr(region),
            "## Section 1\nbody\n### Section 1.1\nnested\n"
            "#### Section 1.1.1\ndeep\n",
        )

    def test_deep_section_stops_at_parent_heading(self):
        text = "## Parent\n### Child\nbody\n## Next\n"
        view = FakeView(text)
        region = md_toc.heading_section_region(view, 1)
        self.assertEqual(view.substr(region), "### Child\nbody\n")

    def test_last_section_extends_to_eof_without_changing_text(self):
        text = "# First\nbody\n## Last\nno final newline"
        view = FakeView(text)
        region = md_toc.heading_section_region(view, 2)
        self.assertEqual(view.substr(region), "## Last\nno final newline")

    def test_setext_underline_identifies_section(self):
        text = "Title\n=====\nbody\nNext\n=====\nkeep\n"
        view = FakeView(text)
        region = md_toc.heading_section_region(view, 1)
        self.assertEqual(view.substr(region), "Title\n=====\nbody\n")

    def test_fenced_heading_does_not_end_section(self):
        text = "## Keep\n```md\n## not a heading\n```\nbody\n## Next\n"
        view = FakeView(text)
        region = md_toc.heading_section_region(view, 0)
        self.assertEqual(
            view.substr(region),
            "## Keep\n```md\n## not a heading\n```\nbody\n",
        )

    def test_non_heading_row_has_no_section(self):
        view = FakeView("# Heading\nbody\n")
        self.assertIsNone(md_toc.heading_section_region(view, 1))

    def test_rule_after_atx_heading_is_not_treated_as_its_underline(self):
        view = FakeView("# Heading\n---\nbody\n")
        self.assertIsNone(md_toc.heading_section_region(view, 1))

    def test_command_copies_then_erases_exact_section(self):
        text = "## Cut\nbody\n### Nested\nmore\n## Keep\nrest\n"
        view = FakeView(text)
        command = md_toc.MarkdownCutWholeSectionCommand(view)
        command.run(edit=None)
        self.assertEqual(
            sublime.clipboard,
            "## Cut\nbody\n### Nested\nmore\n",
        )
        self.assertEqual(view.text, "## Keep\nrest\n")


if __name__ == "__main__":
    unittest.main()
