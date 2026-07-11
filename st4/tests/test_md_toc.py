"""Offline tests for MarkdownTOC's parser and section cutting."""

import importlib
import pathlib
import sys
import tempfile
import types
import unittest
from email.message import Message
from unittest import mock


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
    def __init__(self, text, caret=0, file_name="test.md"):
        self.text = text
        self._file_name = file_name
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
        return self._file_name

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
    sublime.error_message = lambda message: setattr(sublime, "error", message)
    sublime.set_timeout = lambda callback, _delay=0: callback()
    sublime.set_timeout_async = lambda callback, _delay=0: callback()
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


class InlineTextLinkTests(unittest.TestCase):
    def test_parser_handles_destinations_and_ignores_non_links(self):
        text = (
            "[local](folder/a%20b.txt) "
            "![image](image.txt) "
            "\\[escaped](escaped.txt) "
            "`[inline code](inline.txt)`\n"
            "```md\n[fenced](fenced.txt)\n```\n"
            "    [indented](indented.txt)\n"
            "[code label `[still a label]`](code-label.txt) "
            "[remote](https://example.test/a_(b).txt \"A title\") "
            "[angle](<file name.txt>)"
        )
        links = md_toc.parse_inline_text_links(text)
        self.assertEqual(
            [link.label for link in links],
            ["local", "code label `[still a label]`", "remote", "angle"],
        )
        self.assertEqual(
            [link.destination for link in links],
            [
                "folder/a%20b.txt",
                "code-label.txt",
                "https://example.test/a_(b).txt",
                "file name.txt",
            ],
        )

    def test_finds_link_containing_caret(self):
        text = "before [linked text](child.txt) after"
        link = md_toc.find_inline_text_link(text, text.index("text"))
        self.assertIsNotNone(link)
        self.assertEqual(link.destination, "child.txt")
        self.assertIsNone(md_toc.find_inline_text_link(text, 0))

    def test_finds_reference_and_autolink_containing_caret(self):
        text = (
            "[reference text][part] "
            "<https://example.test/remote.txt>\n\n"
            "[part]: local.txt"
        )

        reference = md_toc.find_inline_text_link(
            text, text.index("reference"))
        autolink = md_toc.find_inline_text_link(
            text, text.index("remote"))

        self.assertEqual("local.txt", reference.destination)
        self.assertEqual(
            "https://example.test/remote.txt", autolink.destination)

    def test_large_unmatched_bracket_run_uses_one_end_map(self):
        text = "[" * 20000 + "plain text"
        original_link_text_end = md_toc._link_text_end
        calls = []

        def checked_link_text_end(value, start, link_text_ends=None):
            self.assertIsNotNone(link_text_ends)
            calls.append(start)
            return original_link_text_end(
                value, start, link_text_ends)

        with mock.patch.object(
                md_toc, "_link_text_end", side_effect=checked_link_text_end):
            self.assertEqual([], md_toc.parse_inline_text_links(text))

        self.assertEqual([], calls)

    def test_reference_work_is_skipped_without_definitions(self):
        text = " ".join("[plain]" for _index in range(1000))

        with mock.patch.object(
                md_toc, "_parse_reference_link_at",
                side_effect=AssertionError("reference parser was called")):
            self.assertEqual([], md_toc.parse_inline_text_links(text))

    def test_oversized_shortcut_label_returns_before_normalizing(self):
        text = "[" + ("x" * 1000) + "]"
        link_text_ends = md_toc._build_link_text_end_map(text, [])

        with mock.patch.object(
                md_toc, "_normalize_reference_label",
                side_effect=AssertionError("oversized label was normalized")):
            self.assertIsNone(md_toc._parse_reference_link_at(
                text, 0, {"unused": "child.txt"}, link_text_ends))

    def test_unmatched_backtick_run_is_examined_once_by_bracket_map(self):
        text = ("`" * 20000) + "[plain]"
        original_match = md_toc._matching_backtick_end
        calls = []

        def checked_match(value, start, run_length):
            calls.append((start, run_length))
            return original_match(value, start, run_length)

        with mock.patch.object(
                md_toc, "_matching_backtick_end",
                side_effect=checked_match):
            link_text_ends = md_toc._build_link_text_end_map(text, [])

        self.assertEqual([(0, 20000)], calls)
        start = text.index("[")
        self.assertEqual(text.index("]"), link_text_ends[start])

    def test_resolves_full_collapsed_and_shortcut_reference_links(self):
        text = (
            "[full text][  RESOURCE ID ] "
            "[Collapsed Label][] "
            "[Shortcut Label] "
            "[entity][A&amp;B] "
            "[continued][next line] "
            "[titled][title next] "
            "![image][resource id] "
            "[undefined][missing]\n\n"
            "[resource   id]: <folder/file name.txt> \"first title\"\n"
            "[RESOURCE ID]: ignored.txt\n"
            "[collapsed label]: collapsed.txt 'title'\n"
            "[shortcut label]: shortcut.txt (title)\n"
            "[a&b]: entity.txt\n"
            "[next line]:\n"
            "  <https://example.test/continued.md>\n"
            "  \"third-line title [bad](evil.md)\"\n"
            "[title next]: https://example.test/title.md\n"
            "  'next-line title [worse](evil2.md)'\n"
            "[after definitions][resource id]\n"
        )

        links = md_toc.parse_inline_text_links(text)

        self.assertEqual(
            [link.label for link in links],
            [
                "full text",
                "Collapsed Label",
                "Shortcut Label",
                "entity",
                "continued",
                "titled",
                "after definitions",
            ],
        )
        self.assertEqual(
            [link.destination for link in links],
            [
                "folder/file name.txt",
                "collapsed.txt",
                "shortcut.txt",
                "entity.txt",
                "https://example.test/continued.md",
                "https://example.test/title.md",
                "folder/file name.txt",
            ],
        )
        self.assertEqual(
            (1, ""),
            md_toc._parse_reference_label_at("[]", 0, allow_empty=True),
        )
        self.assertIsNone(
            md_toc._parse_reference_label_at("[ ]", 0, allow_empty=True))

    def test_uri_autolinks_only_include_fetchable_schemes(self):
        text = (
            "<https://example.test/a.txt?raw=1&amp;mode=2#part> "
            "<file:///tmp/a%20b.txt> "
            "<mailto:person@example.test> "
            "<https://example.test/has space> "
            "https://example.test/bare.txt "
            "`<https://example.test/in-code.txt>`"
        )

        links = md_toc.parse_inline_text_links(text)

        self.assertEqual(
            [link.destination for link in links],
            [
                "https://example.test/a.txt?raw=1&mode=2#part",
                "file:///tmp/a%20b.txt",
            ],
        )

    def test_reference_definition_validation_and_shortcut_fallback(self):
        text = (
            "[fallback](not a link) "
            "[bad angle] [bad raw] [bad control] [escaped]\n\n"
            "[fallback]: child.txt\n"
            "[bad angle]: <a<b>\n"
            "[bad raw]: a<b.txt\n"
            "[bad control]: a\x01b.txt\n"
            "[escaped]: a\\<b.txt"
        )

        links = md_toc.parse_inline_text_links(text)

        self.assertEqual(
            [
                ("fallback", "child.txt"),
                ("escaped", "a<b.txt"),
            ],
            [(link.label, link.destination) for link in links],
        )

    def test_second_title_line_after_existing_title_is_ordinary_markdown(self):
        text = (
            "[reference]\n\n"
            "[reference]: child.md \"first title\"\n"
            "\"second title [visible](visible.md)\""
        )

        self.assertEqual(
            [
                ("reference", "child.md"),
                ("visible", "visible.md"),
            ],
            [
                (link.label, link.destination)
                for link in md_toc.parse_inline_text_links(text)
            ],
        )

    def test_destination_escapes_and_entities_follow_markdown_rules(self):
        text = (
            r"[escaped](folder/a\)\:.txt) "
            r"[nonpunct](foo\bar.txt) "
            "[entity](foo&amp;bar.txt) "
            "[unterminated](foo&copy) "
            "[invalid](foo&notit;)"
        )

        links = md_toc.parse_inline_text_links(text)

        self.assertEqual(
            [link.destination for link in links],
            [
                "folder/a):.txt",
                r"foo\bar.txt",
                "foo&bar.txt",
                "foo&copy",
                "foo&notit;",
            ],
        )

    def test_title_after_leading_space_uses_an_empty_destination(self):
        text = (
            "[double]( \"title\") "
            "[single]( 'title') "
            "[parenthesized]( (title))"
        )

        links = md_toc.parse_inline_text_links(text)

        self.assertEqual(
            ["double", "single", "parenthesized"],
            [link.label for link in links],
        )
        self.assertEqual(["", "", ""], [link.destination for link in links])

    def test_rejects_invalid_destination_and_title_whitespace(self):
        invalid_links = (
            "[space](foo( bar).txt)",
            "[control](foo\x07bar.txt)",
            "[two-lines](\n\nchild.txt)",
            "[blank-title](child.txt \"one\n\ntwo\")",
            "[paren-title](child.txt (ti(tle))",
        )
        for text in invalid_links:
            with self.subTest(text=text):
                self.assertEqual([], md_toc.parse_inline_text_links(text))

        unicode_space = md_toc.parse_inline_text_links(
            "[unicode](child.txt\u00a0\"title\")")
        self.assertEqual(
            'child.txt\u00a0"title"',
            unicode_space[0].destination,
        )

        one_line_ending = md_toc.parse_inline_text_links(
            "[valid](child.txt\n  \"title\")")
        self.assertEqual("child.txt", one_line_ending[0].destination)


class LinkContentsTests(unittest.TestCase):
    def test_expands_relative_local_links_from_each_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            nested = root / "nested"
            nested.mkdir()
            (root / "first.md").write_text(
                "before [child](nested/child.txt) after", encoding="utf-8")
            (nested / "child.txt").write_text("child text", encoding="utf-8")
            source = root / "source.md"

            result = md_toc.expand_link_contents(
                "first.md", str(source))

        self.assertEqual(result, "before child text after")

    def test_expands_relative_http_links_without_network(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        child = md_toc.ResourceRef(
            "http", "https://example.test/docs/nested/child.txt")
        resources = {
            first: "before [child](nested/child.txt) after",
            child: "HTTP child",
        }
        calls = []

        def fetch(resource):
            calls.append(resource)
            return resources[resource]

        result = md_toc.expand_link_contents(
            first.location, fetcher=fetch)

        self.assertEqual(result, "before HTTP child after")
        self.assertEqual(calls, [first, child])

    def test_expands_relative_reference_link_from_fetched_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            nested = root / "nested"
            nested.mkdir()
            (root / "first.md").write_bytes((
                "before [child][ include ] after\n\n"
                "[include]:\n"
                "  <nested/child file.md>\n"
                "  \"Child title [bad](evil.md)\""
            ).encode("utf-8"))
            (nested / "child file.md").write_bytes((
                "reference [leaf]\n\n[leaf]: ../leaf.txt"
            ).encode("utf-8"))
            (root / "leaf.txt").write_text("leaf text", encoding="utf-8")

            result = md_toc.expand_link_contents(
                "first.md", str(root / "source.md"))

        self.assertEqual(
            "before reference leaf text\n\n[leaf]: ../leaf.txt after\n\n"
            "[include]:\n  <nested/child file.md>\n"
            "  \"Child title [bad](evil.md)\"",
            result,
        )

    def test_expands_http_autolink_with_query_and_ignores_fragment(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        child = md_toc.ResourceRef(
            "http", "https://example.test/docs/child.txt?raw=1&mode=2")
        resources = {
            first: (
                "before "
                "<https://example.test/docs/child.txt?raw=1&amp;mode=2#part>"
                " after"
            ),
            child: "autolink child",
        }
        calls = []

        def fetch(resource):
            calls.append(resource)
            return resources[resource]

        result = md_toc.expand_link_contents(
            first.location, fetcher=fetch)

        self.assertEqual("before autolink child after", result)
        self.assertEqual([first, child], calls)

    def test_recursive_expansion_leaves_unsupported_links_unchanged(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        child = md_toc.ResourceRef(
            "http", "https://example.test/docs/child.txt")
        resources = {
            first: (
                "keep [section](#part), "
                "[mail](mailto:person@example.test), "
                "expand [child](child.txt)"
            ),
            child: "child text",
        }

        result = md_toc.expand_link_contents(
            first.location, fetcher=resources.__getitem__)

        self.assertEqual(
            "keep [section](#part), "
            "[mail](mailto:person@example.test), "
            "expand child text",
            result,
        )

    def test_resource_expansion_enforces_link_count_but_ui_parser_does_not(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        text = " ".join(
            "[fragment](#part-%d)" % number for number in range(3))

        self.assertEqual(3, len(md_toc.parse_inline_text_links(text)))
        with self.assertRaisesRegex(
                md_toc.LinkContentsError, "more than 2 Markdown links"):
            md_toc.expand_link_contents(
                first.location,
                fetcher=lambda _resource: text,
                max_links_per_resource=2,
            )

    def test_expanded_character_limit_is_checked_before_joining(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        left = md_toc.ResourceRef(
            "http", "https://example.test/docs/left.txt")
        right = md_toc.ResourceRef(
            "http", "https://example.test/docs/right.txt")
        resources = {
            first: "[left](left.txt)[right](right.txt)",
            left: "1234",
            right: "5678",
        }

        self.assertEqual(
            "12345678",
            md_toc.expand_link_contents(
                first.location,
                fetcher=resources.__getitem__,
                max_expanded_characters=8,
            ),
        )
        with self.assertRaisesRegex(
                md_toc.LinkContentsError, "7 character limit"):
            md_toc.expand_link_contents(
                first.location,
                fetcher=resources.__getitem__,
                max_expanded_characters=7,
            )
        self.assertEqual(
            md_toc.MAX_LINK_RESOURCE_BYTES * md_toc.MAX_LINK_CONTENT_DEPTH,
            md_toc.MAX_EXPANDED_CHARACTERS,
        )

    def test_operation_resource_and_source_budgets_ignore_cached_reuse(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        child = md_toc.ResourceRef(
            "http", "https://example.test/docs/child.txt")
        root_text = "[one](child.txt) [two](child.txt)"
        child_text = "[fragment](#part)"
        resources = {first: root_text, child: child_text}
        calls = []

        def fetch(resource):
            calls.append(resource)
            return resources[resource]

        source_characters = len(root_text) + len(child_text)
        result = md_toc.expand_link_contents(
            first.location,
            fetcher=fetch,
            max_total_resources=2,
            max_total_links=3,
            max_total_source_characters=source_characters,
        )

        self.assertEqual("%s %s" % (child_text, child_text), result)
        self.assertEqual([first, child], calls)
        with self.assertRaisesRegex(
                md_toc.LinkContentsError,
                "more than %d source characters" % (source_characters - 1)):
            md_toc.expand_link_contents(
                first.location,
                fetcher=resources.__getitem__,
                max_total_source_characters=source_characters - 1,
            )

    def test_operation_resource_and_link_budgets_cover_the_whole_tree(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/first.md")
        left = md_toc.ResourceRef(
            "http", "https://example.test/docs/left.md")
        right = md_toc.ResourceRef(
            "http", "https://example.test/docs/right.md")
        resources = {
            first: "[left](left.md) [right](right.md)",
            left: "left",
            right: "right",
        }

        with self.assertRaisesRegex(
                md_toc.LinkContentsError, "more than 2 resources"):
            md_toc.expand_link_contents(
                first.location,
                fetcher=resources.__getitem__,
                max_total_resources=2,
            )

        linked_child = md_toc.ResourceRef(
            "http", "https://example.test/docs/linked.md")
        linked_resources = {
            first: "[child](linked.md)",
            linked_child: "[one](#one) [two](#two)",
        }
        with self.assertRaisesRegex(
                md_toc.LinkContentsError,
                "more than 2 Markdown links"):
            md_toc.expand_link_contents(
                first.location,
                fetcher=linked_resources.__getitem__,
                max_total_links=2,
            )

        self.assertEqual(1000, md_toc.MAX_TOTAL_RESOURCES)
        self.assertEqual(md_toc.MAX_LINKS_PER_RESOURCE, md_toc.MAX_TOTAL_LINKS)
        self.assertEqual(
            md_toc.MAX_EXPANDED_CHARACTERS,
            md_toc.MAX_TOTAL_SOURCE_CHARACTERS,
        )

    def test_http_reader_honors_declared_charset_offline(self):
        class Response:
            def __init__(self):
                self.headers = Message()
                self.headers["Content-Type"] = "text/plain; charset=iso-8859-1"

            def read(self, _limit=-1):
                return b"caf\xe9"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        resource = md_toc.ResourceRef(
            "http", "https://example.test/note.txt")
        with mock.patch.object(
                md_toc.urllib_request, "urlopen", return_value=Response()) as opened:
            result = md_toc.read_text_resource(resource)

        self.assertEqual(result, "caf\u00e9")
        self.assertEqual(opened.call_args.args[0].full_url, resource.location)

    def test_http_redirect_effective_url_controls_base_cache_and_cycles(self):
        class Response:
            def __init__(self, effective_url, text):
                self.effective_url = effective_url
                self.data = text.encode("utf-8")
                self.headers = Message()
                self.headers["Content-Type"] = "text/markdown; charset=utf-8"

            def geturl(self):
                return self.effective_url

            def read(self, _limit=-1):
                return self.data

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        origin = "https://origin.test/entry.md"
        actual_root = "https://cdn.test/docs/root.md"
        alias_child = "https://cdn.test/docs/alias.txt"
        actual_child = "https://cdn.test/docs/actual.txt"
        responses = {
            origin: Response(
                actual_root,
                "[alias](alias.txt) [actual](actual.txt)",
            ),
            alias_child: Response(actual_child, "redirected child"),
        }
        calls = []

        def open_response(request, timeout=None):
            calls.append(request.full_url)
            return responses[request.full_url]

        with mock.patch.object(
                md_toc.urllib_request, "urlopen", side_effect=open_response):
            result = md_toc.expand_link_contents(origin)

        self.assertEqual("redirected child redirected child", result)
        self.assertEqual([origin, alias_child], calls)

        cycle_calls = []

        def open_cycle(request, timeout=None):
            cycle_calls.append(request.full_url)
            return Response(
                actual_root,
                "[self](https://cdn.test/docs/root.md)",
            )

        with mock.patch.object(
                md_toc.urllib_request, "urlopen", side_effect=open_cycle):
            with self.assertRaisesRegex(md_toc.LinkContentsError, "cycle"):
                md_toc.expand_link_contents(origin)

        self.assertEqual([origin], cycle_calls)

    def test_http_media_type_allows_text_families_and_missing_header(self):
        for content_type in (
                None,
                "text/markdown; charset=utf-8",
                "application/json",
                "application/problem+json",
                "application/xml",
                "application/atom+xml",
                "application/yaml",
                "application/x-yaml",
                "application/toml",
                "application/javascript",
                "application/ecmascript"):
            with self.subTest(content_type=content_type):
                self.assertTrue(
                    md_toc.supported_text_media_type(content_type))
        for content_type in ("image/png", "application/octet-stream"):
            with self.subTest(content_type=content_type):
                self.assertFalse(
                    md_toc.supported_text_media_type(content_type))

    def test_http_reader_rejects_non_text_media_type(self):
        class Response:
            def __init__(self):
                self.headers = Message()
                self.headers["Content-Type"] = "image/png"

            def read(self, _limit=-1):
                return b"valid UTF-8 that must still be rejected"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        resource = md_toc.ResourceRef(
            "http", "https://example.test/not-text")
        with mock.patch.object(
                md_toc.urllib_request, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(
                    md_toc.LinkContentsError, "content type"):
                md_toc.read_text_resource(resource)

    def test_local_reader_rejects_resource_larger_than_one_mib(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "large.txt"
            path.write_bytes(b"x" * (md_toc.MAX_LINK_RESOURCE_BYTES + 1))
            resource = md_toc.ResourceRef("local", str(path))

            with self.assertRaisesRegex(md_toc.LinkContentsError, "1 MiB"):
                md_toc.read_text_resource(resource)

    def test_cycle_is_reported(self):
        first = md_toc.ResourceRef(
            "http", "https://example.test/docs/a.md")
        second = md_toc.ResourceRef(
            "http", "https://example.test/docs/nested/b.md")
        resources = {
            first: "[B](nested/b.md)",
            second: "[A](../a.md)",
        }

        with self.assertRaisesRegex(md_toc.LinkContentsError, "cycle"):
            md_toc.expand_link_contents(
                first.location, fetcher=resources.__getitem__)

    def test_depth_20_is_allowed_but_21_is_rejected(self):
        resources = {}
        for number in range(1, 21):
            resource = md_toc.ResourceRef(
                "http", "https://example.test/%d.txt" % number)
            if number == 20:
                resources[resource] = "done"
            else:
                resources[resource] = "[next](%d.txt)" % (number + 1)

        fetch = resources.__getitem__
        self.assertEqual(
            md_toc.expand_link_contents(
                "https://example.test/1.txt", fetcher=fetch),
            "done",
        )

        twentieth = md_toc.ResourceRef(
            "http", "https://example.test/20.txt")
        twenty_first = md_toc.ResourceRef(
            "http", "https://example.test/21.txt")
        resources[twentieth] = "[too deep](21.txt)"
        resources[twenty_first] = "not reached"
        with self.assertRaisesRegex(
                md_toc.LinkContentsError, "maximum link depth of 20"):
            md_toc.expand_link_contents(
                "https://example.test/1.txt", fetcher=fetch)

    def test_command_schedules_fetch_off_ui_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "child.txt").write_text("from child", encoding="utf-8")
            text = "[child](child.txt)"
            view = FakeView(
                text, caret=text.index("child"),
                file_name=str(root / "source.md"),
            )
            command = md_toc.MarkdownCopyLinkContentsCommand(view)
            scheduled = []
            old_async = sublime.set_timeout_async
            sublime.set_timeout_async = (
                lambda callback, _delay=0: scheduled.append(callback))
            sublime.clipboard = "unchanged"
            try:
                command.run(edit=None)
                self.assertEqual(sublime.clipboard, "unchanged")
                self.assertEqual(len(scheduled), 1)
                scheduled[0]()
                self.assertEqual(sublime.clipboard, "from child")
            finally:
                sublime.set_timeout_async = old_async

    def test_command_palette_matches_one_overlapping_selection_or_caret(self):
        text = "before [one](one.txt) gap [two](two.txt) after"
        view = FakeView(text)
        command = md_toc.MarkdownCopyLinkContentsCommand(view)
        first_start = text.index("[one]")
        first_end = text.index(" gap")

        view._selection = Selection([
            Region(first_start - 2, first_end + 1),
        ])
        self.assertEqual("one.txt", command._link(None).destination)

        view._selection = Selection([Region(0, len(text))])
        self.assertIsNone(command._link(None))

        view._selection = Selection([
            Region(text.index("one")),
            Region(text.index("two")),
        ])
        self.assertIsNone(command._link(None))

        view._selection = Selection([Region(text.index("two"))])
        self.assertEqual("two.txt", command._link(None).destination)


if __name__ == "__main__":
    unittest.main()
