"""Markdown TOC — show a table of contents for Markdown files in a side pane.

Sublime Text 4 package.
"""

import codecs
import os
import re
from collections import namedtuple
try:
    from html import unescape as _html_unescape
except ImportError:  # Sublime's legacy Python 3.3 host
    from html.parser import HTMLParser
    _html_parser = HTMLParser()
    _html_unescape = _html_parser.unescape
try:
    from html.entities import html5 as _html5_entities
except ImportError:  # pragma: no cover - Python 3.3 provides ``html5``
    from html.entities import name2codepoint as _html4_entities
    _html5_entities = dict(
        (name + ";", chr(codepoint))
        for name, codepoint in _html4_entities.items())
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

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

MAX_LINK_CONTENT_DEPTH = 20
MAX_LINK_RESOURCE_BYTES = 1024 * 1024
MAX_LINKS_PER_RESOURCE = 10000
MAX_EXPANDED_CHARACTERS = (
    MAX_LINK_RESOURCE_BYTES * MAX_LINK_CONTENT_DEPTH)
MAX_TOTAL_RESOURCES = 1000
MAX_TOTAL_LINKS = MAX_LINKS_PER_RESOURCE
MAX_TOTAL_SOURCE_CHARACTERS = MAX_EXPANDED_CHARACTERS
HTTP_FETCH_TIMEOUT_SECONDS = 15
HTTP_USER_AGENT = "MarkdownTOC-Sublime/1"


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


def heading_section_region(view, row):
    """Return the full section region for the heading at ``row``.

    A section ends immediately before the next heading at the same or a
    higher level. Lower-level headings are part of the section. For Setext
    headings, either the title row or its underline row identifies the
    section.
    """
    headings = parse_headings(view)
    if not headings:
        return None

    content = view.substr(sublime.Region(0, view.size()))
    lines = content.split("\n")
    heading_index = None
    for i, (heading_row, _level, _text) in enumerate(headings):
        if row == heading_row:
            heading_index = i
            break
        if (row == heading_row + 1 and row < len(lines)
                and not ATX_RE.match(lines[heading_row])
                and SETEXT_RE.match(lines[row])):
            heading_index = i
            break
    if heading_index is None:
        return None

    heading_row, level, _text = headings[heading_index]
    start = view.text_point(heading_row, 0)
    end = view.size()
    for next_row, next_level, _next_text in headings[heading_index + 1:]:
        if next_level <= level:
            end = view.text_point(next_row, 0)
            break
    return sublime.Region(start, end)


# ---------------------------------------------------------------------------
# Inline text links and recursive resource expansion
# ---------------------------------------------------------------------------

InlineTextLink = namedtuple(
    "InlineTextLink", "start end label destination")
ResourceRef = namedtuple("ResourceRef", "kind location")
FetchedTextResource = namedtuple("FetchedTextResource", "text resource")


class LinkContentsError(Exception):
    """A user-facing failure while loading or expanding a text link."""


def _is_escaped(text, index):
    slashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        slashes += 1
        index -= 1
    return slashes % 2 == 1


MARKDOWN_ENTITY_RE = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]*);")


def _is_ascii_punctuation(char):
    code = ord(char)
    return (33 <= code <= 47 or 58 <= code <= 64
            or 91 <= code <= 96 or 123 <= code <= 126)


def _is_ascii_control(char):
    code = ord(char)
    return code < 32 or code == 127


def _decode_markdown_entity(match):
    entity = match.group(0)
    if entity.startswith("&#"):
        return _html_unescape(entity)
    return _html5_entities.get(entity[1:], entity)


def _decode_markdown_entities(text):
    return MARKDOWN_ENTITY_RE.sub(_decode_markdown_entity, text)


def _markdown_unescape(text):
    """Apply CommonMark escapes and strict, terminated HTML entities."""
    result = []
    i = 0
    while i < len(text):
        char = text[i]
        if (char == "\\" and i + 1 < len(text)
                and _is_ascii_punctuation(text[i + 1])):
            result.append(text[i + 1])
            i += 2
            continue
        result.append(char)
        i += 1
    return _decode_markdown_entities("".join(result))


def _consume_link_spacing(text, position, required=False):
    """Consume CommonMark link spacing with at most one line ending."""
    start = position
    while position < len(text) and text[position] in " \t":
        position += 1
    if position < len(text) and text[position] in "\r\n":
        if (text[position] == "\r" and position + 1 < len(text)
                and text[position + 1] == "\n"):
            position += 2
        else:
            position += 1
        while position < len(text) and text[position] in " \t":
            position += 1
        if position < len(text) and text[position] in "\r\n":
            return None
    if required and position == start:
        return None
    return position


def _parse_link_tail(text, position):
    """Return the outer closing-parenthesis index after a destination."""
    if position >= len(text):
        return None
    if text[position] == ")":
        return position
    position = _consume_link_spacing(text, position, required=True)
    if position is None or position >= len(text):
        return None
    if text[position] == ")":
        return position

    opener = text[position]
    closer = {"\"": "\"", "'": "'", "(": ")"}.get(opener)
    if closer is None:
        return None
    position += 1
    title_start = position
    while position < len(text):
        if (text[position] == "\\" and position + 1 < len(text)
                and _is_ascii_punctuation(text[position + 1])):
            position += 2
            continue
        if opener == "(" and text[position] == "(":
            return None
        if text[position] == closer:
            title = text[title_start:position]
            if re.search(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)", title):
                return None
            position += 1
            break
        position += 1
    else:
        return None

    position = _consume_link_spacing(text, position)
    if (position is not None and position < len(text)
            and text[position] == ")"):
        return position
    return None


def _link_text_end(text, start, link_text_ends=None):
    """Return the closing bracket for balanced Markdown link text."""
    if link_text_ends is not None:
        return link_text_ends.get(start)
    depth = 1
    position = start + 1
    while position < len(text):
        char = text[position]
        if (char == "\\" and position + 1 < len(text)
                and _is_ascii_punctuation(text[position + 1])):
            position += 2
            continue
        if char == "`":
            end = position + 1
            while end < len(text) and text[end] == "`":
                end += 1
            code_end = _matching_backtick_end(text, position, end - position)
            position = code_end if code_end is not None else end
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    return None


def _parse_inline_link_at(text, start, include_image=False,
                          link_text_ends=None):
    """Parse a non-image inline Markdown link beginning at ``start``."""
    if text[start] != "[" or _is_escaped(text, start):
        return None
    if (start > 0 and text[start - 1] == "!"
            and not _is_escaped(text, start - 1) and not include_image):
        return None

    label_end = _link_text_end(text, start, link_text_ends)
    if label_end is None or label_end + 1 >= len(text):
        return None
    if text[label_end + 1] != "(":
        return None

    content_start = label_end + 2
    position = _consume_link_spacing(text, content_start)
    if position is None or position >= len(text):
        return None
    if (position > content_start and text[position] in "\"'("):
        close = _parse_link_tail(text, content_start)
        if close is not None:
            label = text[start + 1:label_end]
            return InlineTextLink(start, close + 1, label, "")

    if text[position] == "<":
        destination_start = position + 1
        position = destination_start
        while position < len(text):
            if (text[position] == "\\" and position + 1 < len(text)
                    and _is_ascii_punctuation(text[position + 1])):
                position += 2
                continue
            if text[position] == ">":
                break
            if (text[position] == "<"
                    or _is_ascii_control(text[position])):
                return None
            position += 1
        if position >= len(text):
            return None
        destination_end = position
        close = _parse_link_tail(text, position + 1)
    else:
        destination_start = position
        parens = 0
        close = None
        destination_end = None
        while position < len(text):
            char = text[position]
            if (char == "\\" and position + 1 < len(text)
                    and _is_ascii_punctuation(text[position + 1])):
                position += 2
                continue
            if char == "<":
                return None
            if char == "(":
                parens += 1
            elif char == ")":
                if parens == 0:
                    destination_end = position
                    close = position
                    break
                parens -= 1
            elif char == " " or _is_ascii_control(char):
                if parens != 0:
                    return None
                if char not in " \t\r\n":
                    return None
                destination_end = position
                close = _parse_link_tail(text, position)
                break
            position += 1

    if close is None or destination_end is None:
        return None
    label = text[start + 1:label_end]
    destination = _markdown_unescape(
        text[destination_start:destination_end])
    return InlineTextLink(start, close + 1, label, destination)


def _fenced_markdown_ranges(text):
    """Return character ranges occupied by fenced/indented Markdown code."""
    ranges = []
    opening = None
    fence_char = None
    fence_len = 0
    offset = 0
    for raw_line in text.splitlines(True):
        line = raw_line.rstrip("\r\n")
        if opening is None:
            match = FENCE_OPEN_RE.match(line)
            if match:
                opening = offset
                fence_char = match.group(1)[0]
                fence_len = len(match.group(1))
            elif re.match(r"^(?: {4}|\t)", line):
                ranges.append((offset, offset + len(raw_line)))
        else:
            close_re = r"^ {0,3}%s{%d,}[ \t]*$" % (
                re.escape(fence_char), fence_len)
            if re.match(close_re, line):
                ranges.append((opening, offset + len(raw_line)))
                opening = None
                fence_char = None
                fence_len = 0
        offset += len(raw_line)
    if opening is not None:
        ranges.append((opening, len(text)))
    return ranges


def _matching_backtick_end(text, start, run_length):
    position = start + run_length
    while position < len(text):
        candidate = text.find("`", position)
        if candidate < 0:
            return None
        end = candidate
        while end < len(text) and text[end] == "`":
            end += 1
        if end - candidate == run_length:
            return end
        position = end
    return None


def _build_link_text_end_map(text, excluded_ranges):
    """Match brackets once so malformed input cannot trigger rescans."""
    ends = {}
    stack = []
    excluded_index = 0
    position = 0
    while position < len(text):
        while (excluded_index < len(excluded_ranges)
               and position >= excluded_ranges[excluded_index][1]):
            excluded_index += 1
        if (excluded_index < len(excluded_ranges)
                and excluded_ranges[excluded_index][0] <= position
                < excluded_ranges[excluded_index][1]):
            stack = []
            position = excluded_ranges[excluded_index][1]
            continue

        char = text[position]
        if (char == "\\" and position + 1 < len(text)
                and _is_ascii_punctuation(text[position + 1])):
            position += 2
            continue
        if char == "`":
            end = position + 1
            while end < len(text) and text[end] == "`":
                end += 1
            code_end = _matching_backtick_end(text, position, end - position)
            position = code_end if code_end is not None else end
            continue
        if char == "[":
            stack.append(position)
        elif char == "]" and stack:
            ends[stack.pop()] = position
        position += 1
    return ends


def _parse_reference_label_at(text, start, allow_empty=False):
    """Return ``(end, contents)`` for one CommonMark reference label."""
    if start >= len(text) or text[start] != "[":
        return None
    position = start + 1
    while position < len(text):
        char = text[position]
        if (char == "\\" and position + 1 < len(text)
                and _is_ascii_punctuation(text[position + 1])):
            position += 2
            continue
        if char == "[":
            return None
        if char == "]":
            contents = text[start + 1:position]
            if len(contents) > 999:
                return None
            if (not contents.strip(" \t\r\n")
                    and not (allow_empty and contents == "")):
                return None
            return position, contents
        position += 1
    return None


def _normalize_reference_label(label):
    label = _markdown_unescape(label)
    label = re.sub(r"[ \t\r\n]+", " ", label.strip(" \t\r\n"))
    return label.casefold()


def _parse_reference_destination(line, position):
    """Return ``(destination, end)`` for a same-line definition target."""
    if position >= len(line):
        return None
    if line[position] == "<":
        start = position + 1
        position = start
        while position < len(line):
            char = line[position]
            if (char == "\\" and position + 1 < len(line)
                    and _is_ascii_punctuation(line[position + 1])):
                position += 2
                continue
            if char == ">":
                return _markdown_unescape(line[start:position]), position + 1
            if char == "<" or _is_ascii_control(char):
                return None
            position += 1
        return None

    start = position
    parens = 0
    while position < len(line):
        char = line[position]
        if (char == "\\" and position + 1 < len(line)
                and _is_ascii_punctuation(line[position + 1])):
            position += 2
            continue
        if char in " \t":
            break
        if char == "<" or _is_ascii_control(char):
            return None
        if char == "(":
            parens += 1
        elif char == ")":
            if parens == 0:
                return None
            parens -= 1
        position += 1
    if position == start or parens != 0:
        return None
    return _markdown_unescape(line[start:position]), position


def _parse_reference_title(line, position):
    opener = line[position] if position < len(line) else None
    closer = {"\"": "\"", "'": "'", "(": ")"}.get(opener)
    if closer is None:
        return None
    position += 1
    while position < len(line):
        char = line[position]
        if (char == "\\" and position + 1 < len(line)
                and _is_ascii_punctuation(line[position + 1])):
            position += 2
            continue
        if opener == "(" and char == "(":
            return None
        if char == closer:
            return position + 1
        position += 1
    return None


def _parse_reference_definition_start(line):
    """Return a top-level definition label and destination position."""
    position = 0
    while position < len(line) and line[position] == " ":
        position += 1
    if position > 3:
        return None
    parsed_label = _parse_reference_label_at(line, position)
    if parsed_label is None:
        return None
    label_end, label = parsed_label
    position = label_end + 1
    if position >= len(line) or line[position] != ":":
        return None
    position += 1
    while position < len(line) and line[position] in " \t":
        position += 1
    return label, position


def _parse_reference_definition_tail(line, position):
    parsed_destination = _parse_reference_destination(line, position)
    if parsed_destination is None:
        return None
    destination, position = parsed_destination
    separator_start = position
    while position < len(line) and line[position] in " \t":
        position += 1
    if position == len(line):
        return destination, False
    if position == separator_start:
        return None

    title_end = _parse_reference_title(line, position)
    if title_end is None:
        return None
    position = title_end
    while position < len(line) and line[position] in " \t":
        position += 1
    if position != len(line):
        return None
    return destination, True


def _is_reference_title_line(line):
    position = 0
    while position < len(line) and line[position] == " ":
        position += 1
    if position > 3:
        return False
    title_end = _parse_reference_title(line, position)
    if title_end is None:
        return False
    position = title_end
    while position < len(line) and line[position] in " \t":
        position += 1
    return position == len(line)


def _parse_reference_definition_line(line):
    """Parse a practical top-level, same-line reference definition."""
    parsed_start = _parse_reference_definition_start(line)
    if parsed_start is None:
        return None
    label, position = parsed_start
    parsed_tail = _parse_reference_definition_tail(line, position)
    if parsed_tail is None:
        return None
    destination, _has_title = parsed_tail
    return label, destination


def _range_containing(ranges, position):
    for start, end in ranges:
        if start <= position < end:
            return start, end
        if start > position:
            break
    return None


def _parse_reference_definitions(text, fenced_ranges):
    definitions = {}
    definition_ranges = []
    raw_lines = text.splitlines(True)
    offsets = []
    offset = 0
    for raw_line in raw_lines:
        offsets.append(offset)
        offset += len(raw_line)

    index = 0
    while index < len(raw_lines):
        raw_line = raw_lines[index]
        offset = offsets[index]
        consumed_lines = 1
        if _range_containing(fenced_ranges, offset) is None:
            line = raw_line.rstrip("\r\n")
            parsed_start = _parse_reference_definition_start(line)
            parsed = None
            has_title = False
            if parsed_start is not None:
                label, position = parsed_start
                parsed_tail = _parse_reference_definition_tail(
                    line, position)
                if parsed_tail is not None:
                    destination, has_title = parsed_tail
                    parsed = label, destination
                elif position == len(line) and index + 1 < len(raw_lines):
                    next_offset = offsets[index + 1]
                    if _range_containing(
                            fenced_ranges, next_offset) is None:
                        continuation = raw_lines[index + 1].rstrip("\r\n")
                        continuation_position = 0
                        while (continuation_position < len(continuation)
                               and continuation[continuation_position] == " "):
                            continuation_position += 1
                        if continuation_position <= 3:
                            parsed_tail = _parse_reference_definition_tail(
                                continuation, continuation_position)
                            if parsed_tail is not None:
                                destination, has_title = parsed_tail
                                parsed = label, destination
                                consumed_lines = 2
            if parsed is not None and not has_title:
                title_index = index + consumed_lines
                if title_index < len(raw_lines):
                    title_offset = offsets[title_index]
                    if _range_containing(
                            fenced_ranges, title_offset) is None:
                        title_line = raw_lines[title_index].rstrip("\r\n")
                        if _is_reference_title_line(title_line):
                            consumed_lines += 1
            if parsed is not None:
                label, destination = parsed
                normalized = _normalize_reference_label(label)
                if normalized not in definitions:
                    definitions[normalized] = destination
                final_line = index + consumed_lines - 1
                definition_ranges.append((
                    offset,
                    offsets[final_line] + len(raw_lines[final_line]),
                ))
        index += consumed_lines
    return definitions, definition_ranges


def _parse_reference_link_at(text, start, definitions,
                             link_text_ends=None):
    if (start > 0 and text[start - 1] == "!"
            and not _is_escaped(text, start - 1)):
        return None
    label_end = _link_text_end(text, start, link_text_ends)
    if label_end is None:
        return None
    position = label_end + 1
    label_length = label_end - start - 1

    if position < len(text) and text[position] == "[":
        parsed_reference = _parse_reference_label_at(
            text, position, allow_empty=True)
        if parsed_reference is None:
            return None
        reference_end, reference = parsed_reference
        if reference == "":
            if label_length > 999:
                return None
            key = _normalize_reference_label(
                text[start + 1:label_end])
        else:
            key = _normalize_reference_label(reference)
        end = reference_end + 1
    else:
        if label_length > 999:
            return None
        key = _normalize_reference_label(text[start + 1:label_end])
        end = label_end + 1

    destination = definitions.get(key)
    if destination is None:
        return None
    label = text[start + 1:label_end]
    return InlineTextLink(start, end, label, destination)


def _image_link_end(text, start, link_text_ends=None):
    inline = _parse_inline_link_at(
        text, start, include_image=True,
        link_text_ends=link_text_ends)
    if inline is not None:
        return inline.end
    label_end = _link_text_end(text, start, link_text_ends)
    if label_end is None:
        return start + 1
    position = label_end + 1
    if position < len(text) and text[position] == "[":
        reference = _parse_reference_label_at(
            text, position, allow_empty=True)
        if reference is not None:
            return reference[0] + 1
    return label_end + 1


AUTOLINK_SCHEME_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9+.-]{1,31}:")


def _parse_uri_autolink_at(text, start):
    if text[start] != "<" or _is_escaped(text, start):
        return None
    position = start + 1
    while position < len(text):
        char = text[position]
        if char == ">":
            raw_destination = text[start + 1:position]
            match = AUTOLINK_SCHEME_RE.match(raw_destination)
            if match is None:
                return None
            scheme = raw_destination.split(":", 1)[0].lower()
            if scheme not in ("http", "https", "file"):
                return None
            destination = _decode_markdown_entities(raw_destination)
            return InlineTextLink(
                start, position + 1, destination, destination)
        if char == "<" or char == " " or _is_ascii_control(char):
            return None
        position += 1
    return None


def parse_inline_text_links(text, max_links=None):
    """Return inline, reference, and URI-autolink text resources."""
    if (max_links is not None
            and (not isinstance(max_links, int)
                 or isinstance(max_links, bool) or max_links < 1)):
        raise LinkContentsError("maximum links per resource must be at least 1")
    links = []

    def add_link(link):
        if max_links is not None and len(links) >= max_links:
            raise LinkContentsError(
                "resource contains more than %d Markdown links" % max_links)
        links.append(link)

    fences = _fenced_markdown_ranges(text)
    definitions, definition_ranges = _parse_reference_definitions(
        text, fences)
    excluded_ranges = sorted(fences + definition_ranges)
    link_text_ends = _build_link_text_end_map(text, excluded_ranges)
    fence_index = 0
    position = 0
    while position < len(text):
        while (fence_index < len(excluded_ranges)
               and position >= excluded_ranges[fence_index][1]):
            fence_index += 1
        if (fence_index < len(excluded_ranges)
                and excluded_ranges[fence_index][0] <= position
                < excluded_ranges[fence_index][1]):
            position = excluded_ranges[fence_index][1]
            continue

        if text[position] == "`" and not _is_escaped(text, position):
            end = position + 1
            while end < len(text) and text[end] == "`":
                end += 1
            code_end = _matching_backtick_end(text, position, end - position)
            position = code_end if code_end is not None else end
            continue

        if text[position] == "<":
            link = _parse_uri_autolink_at(text, position)
            if link is not None:
                add_link(link)
                position = link.end
                continue

        if text[position] == "[":
            if position not in link_text_ends:
                position += 1
                continue
            if (position > 0 and text[position - 1] == "!"
                    and not _is_escaped(text, position - 1)):
                position = _image_link_end(
                    text, position, link_text_ends)
                continue
            link = _parse_inline_link_at(
                text, position, link_text_ends=link_text_ends)
            if link is None and definitions:
                link = _parse_reference_link_at(
                    text, position, definitions, link_text_ends)
            if link is not None:
                add_link(link)
                position = link.end
                continue
        position += 1
    return links


def find_inline_text_link(text, point):
    """Return the inline text link containing a character/caret point."""
    for link in parse_inline_text_links(text):
        if link.start <= point < link.end:
            return link
        # A caret at EOF sits just after the final character of the link.
        if point == len(text) and point == link.end:
            return link
    return None


def find_inline_text_link_for_selection(text, start, end):
    """Return one link overlapped by a selection, or the caret link."""
    start, end = min(start, end), max(start, end)
    if start == end:
        return find_inline_text_link(text, start)
    matches = [
        link for link in parse_inline_text_links(text)
        if link.start < end and start < link.end
    ]
    return matches[0] if len(matches) == 1 else None


def is_supported_resource_target(destination):
    destination = destination.strip()
    if not destination or destination.startswith("#"):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", destination):
        return True
    try:
        scheme = urllib_parse.urlsplit(destination).scheme.lower()
    except ValueError:
        return False
    return scheme in ("", "file", "http", "https")


def _local_resource(path):
    path = os.path.expanduser(path)
    return ResourceRef("local", os.path.realpath(os.path.abspath(path)))


def _http_resource(location):
    """Canonicalize an HTTP URL enough for fetching and relative links."""
    location = urllib_parse.urldefrag(location)[0]
    parts = urllib_parse.urlsplit(location)
    path = urllib_parse.quote(
        parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib_parse.quote(
        parts.query, safe="=&?/:;+,%@[]!$'()*-._~")
    return ResourceRef("http", urllib_parse.urlunsplit((
        parts.scheme.lower(), parts.netloc, path, query, "")))


def _coerce_base_resource(base):
    if base is None or isinstance(base, ResourceRef):
        return base
    base = os.fspath(base) if hasattr(os, "fspath") else str(base)
    if re.match(r"^https?://", base, re.IGNORECASE):
        return _http_resource(base)
    if base.lower().startswith("file:"):
        return resolve_link_target(base)
    return _local_resource(base)


def resolve_link_target(destination, base=None):
    """Resolve a Markdown destination against a local or HTTP resource."""
    destination = destination.strip()
    if not is_supported_resource_target(destination):
        raise LinkContentsError(
            "link is not a local, file, HTTP, or HTTPS text resource")
    base = _coerce_base_resource(base)

    if re.match(r"^[A-Za-z]:[\\/]", destination):
        return _local_resource(urllib_parse.unquote(destination))

    parts = urllib_parse.urlsplit(destination)
    scheme = parts.scheme.lower()
    if scheme in ("http", "https"):
        return _http_resource(destination)

    if scheme == "file":
        path = urllib_request.url2pathname(urllib_parse.unquote(parts.path))
        if parts.netloc and parts.netloc.lower() != "localhost":
            if os.name == "nt":
                path = "\\\\" + parts.netloc + path.replace("/", "\\")
            else:
                path = "//" + parts.netloc + path
        if not os.path.isabs(path):
            if base is None or base.kind != "local":
                raise LinkContentsError(
                    "relative file link needs a saved local Markdown file")
            path = os.path.join(os.path.dirname(base.location), path)
        return _local_resource(path)

    if base is not None and base.kind == "http":
        location = urllib_parse.urljoin(base.location, destination)
        return _http_resource(location)

    if parts.netloc:
        path = "//" + parts.netloc + urllib_parse.unquote(parts.path)
    else:
        path = urllib_parse.unquote(parts.path)
    if not os.path.isabs(path):
        if base is None or base.kind != "local":
            raise LinkContentsError(
                "relative link needs a saved local Markdown file")
        path = os.path.join(os.path.dirname(base.location), path)
    return _local_resource(path)


def _resource_key(resource):
    if resource.kind == "local":
        return (resource.kind, os.path.normcase(resource.location))
    parts = urllib_parse.urlsplit(resource.location)
    normalized = urllib_parse.urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), parts.path,
        parts.query, ""))
    return (resource.kind, normalized)


def supported_text_media_type(content_type):
    """Whether an HTTP Content-Type represents a supported text resource."""
    if not content_type:
        return True
    media_type = content_type.split(";", 1)[0].strip().lower()
    return (
        media_type.startswith("text/")
        or media_type == "application/json"
        or media_type.endswith("+json")
        or media_type == "application/xml"
        or media_type.endswith("+xml")
        or media_type == "application/javascript"
        or media_type == "application/ecmascript"
        or media_type == "application/yaml"
        or media_type == "application/x-yaml"
        or media_type == "application/toml"
    )


def _decode_text_bytes(data, charset, location):
    if len(data) > MAX_LINK_RESOURCE_BYTES:
        raise LinkContentsError(
            "resource exceeds the 1 MiB size limit: %s" % location)
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    else:
        encoding = charset or "utf-8"
    try:
        text = data.decode(encoding)
    except (LookupError, UnicodeError) as error:
        raise LinkContentsError(
            "resource is not decodable text (%s): %s" % (location, error))
    if "\x00" in text:
        raise LinkContentsError(
            "resource appears to be binary, not text: %s" % location)
    return text


def _read_text_resource_result(resource):
    """Read text and retain the effective resource after HTTP redirects."""
    if resource.kind == "local":
        try:
            with open(resource.location, "rb") as handle:
                data = handle.read(MAX_LINK_RESOURCE_BYTES + 1)
        except OSError as error:
            raise LinkContentsError(
                "could not read %s: %s" % (resource.location, error))
        return FetchedTextResource(
            _decode_text_bytes(data, None, resource.location), resource)

    request = urllib_request.Request(
        resource.location,
        headers={
            "Accept": "text/*, application/json, application/xml;q=0.9, */*;q=0.1",
            "User-Agent": HTTP_USER_AGENT,
        },
    )
    try:
        with urllib_request.urlopen(
                request, timeout=HTTP_FETCH_TIMEOUT_SECONDS) as response:
            effective_location = (response.geturl()
                                  if hasattr(response, "geturl") else None)
            effective_location = effective_location or resource.location
            try:
                effective_parts = urllib_parse.urlsplit(effective_location)
            except ValueError as error:
                raise LinkContentsError(
                    "HTTP redirect returned an invalid URL: %s" % error)
            if effective_parts.scheme.lower() not in ("http", "https"):
                raise LinkContentsError(
                    "HTTP redirect used an unsupported scheme: %s"
                    % effective_parts.scheme)
            effective_resource = _http_resource(effective_location)
            headers = response.headers
            content_type = (headers.get("Content-Type")
                            if hasattr(headers, "get") else None)
            if not supported_text_media_type(content_type):
                raise LinkContentsError(
                    "HTTP resource is not a supported text content type "
                    "(%s): %s"
                    % (content_type.split(";", 1)[0], resource.location))
            data = response.read(MAX_LINK_RESOURCE_BYTES + 1)
            charset = (headers.get_content_charset()
                       if hasattr(headers, "get_content_charset") else None)
    except urllib_error.HTTPError as error:
        raise LinkContentsError(
            "HTTP %s while fetching %s" % (error.code, resource.location))
    except urllib_error.URLError as error:
        raise LinkContentsError(
            "could not fetch %s: %s" % (resource.location, error.reason))
    except OSError as error:
        raise LinkContentsError(
            "could not fetch %s: %s" % (resource.location, error))
    return FetchedTextResource(
        _decode_text_bytes(data, charset, effective_resource.location),
        effective_resource,
    )


def read_text_resource(resource):
    """Read a local or HTTP resource as Unicode text."""
    return _read_text_resource_result(resource).text


def expand_link_contents(
        destination, source_path=None, fetcher=None,
        max_depth=MAX_LINK_CONTENT_DEPTH,
        max_links_per_resource=MAX_LINKS_PER_RESOURCE,
        max_expanded_characters=MAX_EXPANDED_CHARACTERS,
        max_total_resources=MAX_TOTAL_RESOURCES,
        max_total_links=MAX_TOTAL_LINKS,
        max_total_source_characters=MAX_TOTAL_SOURCE_CHARACTERS):
    """Fetch one link and recursively replace resource links in its text.

    ``source_path`` is the Markdown document containing the initial link.
    The first fetched resource is depth 1, so a max depth of 20 permits a
    chain of 20 resources and rejects the 21st.
    """
    if (not isinstance(max_depth, int) or isinstance(max_depth, bool)
            or max_depth < 1):
        raise LinkContentsError("maximum link depth must be at least 1")
    if (not isinstance(max_links_per_resource, int)
            or isinstance(max_links_per_resource, bool)
            or max_links_per_resource < 1):
        raise LinkContentsError(
            "maximum links per resource must be at least 1")
    if (not isinstance(max_expanded_characters, int)
            or isinstance(max_expanded_characters, bool)
            or max_expanded_characters < 1):
        raise LinkContentsError(
            "maximum expanded characters must be at least 1")
    if (not isinstance(max_total_resources, int)
            or isinstance(max_total_resources, bool)
            or max_total_resources < 1):
        raise LinkContentsError(
            "maximum total resources must be at least 1")
    if (not isinstance(max_total_links, int)
            or isinstance(max_total_links, bool) or max_total_links < 1):
        raise LinkContentsError(
            "maximum total links must be at least 1")
    if (not isinstance(max_total_source_characters, int)
            or isinstance(max_total_source_characters, bool)
            or max_total_source_characters < 1):
        raise LinkContentsError(
            "maximum total source characters must be at least 1")
    fetcher = fetcher or _read_text_resource_result
    base = _coerce_base_resource(source_path)
    first = resolve_link_target(destination, base)
    cache = {}
    stack = []
    totals = {"resources": 0, "links": 0, "source_characters": 0}

    def expand(resource, depth):
        if depth > max_depth:
            raise LinkContentsError(
                "maximum link depth of %d exceeded at %s"
                % (max_depth, resource.location))

        requested_key = _resource_key(resource)
        stack_keys = [_resource_key(item) for item in stack]
        if requested_key in stack_keys:
            cycle_start = stack_keys.index(requested_key)
            cycle = stack[cycle_start:] + [resource]
            raise LinkContentsError(
                "link cycle detected: %s"
                % " -> ".join(item.location for item in cycle))
        if requested_key in cache:
            return cache[requested_key]

        totals["resources"] += 1
        if totals["resources"] > max_total_resources:
            raise LinkContentsError(
                "operation fetched more than %d resources"
                % max_total_resources)
        stack.append(resource)
        try:
            fetched = fetcher(resource)
            actual_resource = resource
            if isinstance(fetched, FetchedTextResource):
                text = fetched.text
                actual_resource = fetched.resource
                if not isinstance(actual_resource, ResourceRef):
                    raise LinkContentsError(
                        "resource reader returned an invalid effective resource")
            else:
                text = fetched
            if not isinstance(text, str):
                raise LinkContentsError(
                    "resource reader did not return text: %s"
                    % resource.location)
            totals["source_characters"] += len(text)
            if (totals["source_characters"]
                    > max_total_source_characters):
                raise LinkContentsError(
                    "operation fetched more than %d source characters"
                    % max_total_source_characters)
            actual_key = _resource_key(actual_resource)
            if actual_key in stack_keys:
                cycle_start = stack_keys.index(actual_key)
                cycle = stack[cycle_start:] + [actual_resource]
                raise LinkContentsError(
                    "link cycle detected: %s"
                    % " -> ".join(item.location for item in cycle))
            if actual_key in cache:
                cache[requested_key] = cache[actual_key]
                return cache[actual_key]
            stack[-1] = actual_resource

            parsed_links = parse_inline_text_links(
                text, max_links=max_links_per_resource)
            totals["links"] += len(parsed_links)
            if totals["links"] > max_total_links:
                raise LinkContentsError(
                    "operation parsed more than %d Markdown links"
                    % max_total_links)
            links = [
                link for link in parsed_links
                if is_supported_resource_target(link.destination)
            ]
            if not links:
                if len(text) > max_expanded_characters:
                    raise LinkContentsError(
                        "expanded content exceeds the %d character limit"
                        % max_expanded_characters)
                expanded = text
            else:
                pieces = []
                previous = 0
                expanded_length = 0
                for link in links:
                    literal = text[previous:link.start]
                    expanded_length += len(literal)
                    if expanded_length > max_expanded_characters:
                        raise LinkContentsError(
                            "expanded content exceeds the %d character limit"
                            % max_expanded_characters)
                    pieces.append(literal)
                    nested = resolve_link_target(
                        link.destination, actual_resource)
                    nested_text = expand(nested, depth + 1)
                    expanded_length += len(nested_text)
                    if expanded_length > max_expanded_characters:
                        raise LinkContentsError(
                            "expanded content exceeds the %d character limit"
                            % max_expanded_characters)
                    pieces.append(nested_text)
                    previous = link.end
                literal = text[previous:]
                expanded_length += len(literal)
                if expanded_length > max_expanded_characters:
                    raise LinkContentsError(
                        "expanded content exceeds the %d character limit"
                        % max_expanded_characters)
                pieces.append(literal)
                expanded = "".join(pieces)
            cache[actual_key] = expanded
            cache[requested_key] = expanded
            return expanded
        finally:
            stack.pop()

    return expand(first, 1)


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


class MarkdownCopyLinkContentsCommand(sublime_plugin.TextCommand):
    """Asynchronously copy the expanded text resource under mouse/caret."""

    def want_event(self):
        return True

    def _point(self, event):
        if event is not None:
            return self.view.window_to_text((event["x"], event["y"]))
        selection = self.view.sel()
        return selection[0].begin() if selection else None

    def _link(self, event):
        if not is_markdown(self.view):
            return None
        text = self.view.substr(sublime.Region(0, self.view.size()))
        if event is not None:
            point = self._point(event)
            if point is None:
                return None
            link = find_inline_text_link(text, point)
        else:
            selections = self.view.sel()
            if len(selections) != 1:
                return None
            selection = selections[0]
            link = find_inline_text_link_for_selection(
                text, selection.begin(), selection.end())
        if (link is None
                or not is_supported_resource_target(link.destination)):
            return None
        return link

    def run(self, edit, event=None):
        link = self._link(event)
        if link is None:
            sublime.status_message("Markdown TOC: no text resource link here")
            return

        destination = link.destination
        source_path = self.view.file_name()
        sublime.status_message("Markdown TOC: loading link contents...")

        def work():
            try:
                content = expand_link_contents(destination, source_path)
            except LinkContentsError as error:
                message = "Markdown TOC: %s" % error

                def report_expected(message=message):
                    sublime.error_message(message)

                sublime.set_timeout(report_expected, 0)
                return
            except Exception as error:
                message = "Markdown TOC: could not copy link contents: %s" % error

                def report_unexpected(message=message):
                    sublime.error_message(message)

                sublime.set_timeout(report_unexpected, 0)
                return

            def copy(content=content):
                sublime.set_clipboard(content)
                sublime.status_message(
                    "Markdown TOC: copied %d character%s from link"
                    % (len(content), "" if len(content) == 1 else "s"))

            sublime.set_timeout(copy, 0)

        sublime.set_timeout_async(work, 0)

    def is_enabled(self, event=None):
        return self._link(event) is not None

    def is_visible(self, event=None):
        return self._link(event) is not None


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


class MarkdownCutWholeSectionCommand(sublime_plugin.TextCommand):
    """Cut the heading under the mouse / caret and its complete section."""

    def want_event(self):
        return True

    def _point(self, event):
        if event is not None:
            return self.view.window_to_text((event["x"], event["y"]))
        sel = self.view.sel()
        return sel[0].begin() if sel else None

    def _section(self, event):
        if not is_markdown(self.view):
            return None
        pt = self._point(event)
        if pt is None:
            return None
        return heading_section_region(self.view, self.view.rowcol(pt)[0])

    def run(self, edit, event=None):
        section = self._section(event)
        if section is None:
            sublime.status_message("Markdown TOC: no heading here")
            return
        text = self.view.substr(section)
        sublime.set_clipboard(text)
        self.view.erase(edit, section)
        line_count = len(text.splitlines())
        sublime.status_message(
            "Markdown TOC: cut %d line%s"
            % (line_count, "" if line_count == 1 else "s"))

    def is_enabled(self, event=None):
        return (not self.view.is_read_only()
                and self._section(event) is not None)

    def is_visible(self, event=None):
        return is_markdown(self.view) and self._section(event) is not None


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
