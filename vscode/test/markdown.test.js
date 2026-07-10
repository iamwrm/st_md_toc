"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
    cleanHeadingText,
    fencedCodeBlocks,
    headingSection,
    parseHeadings,
} = require("../src/markdown");

test("parses ATX and Setext headings and skips fenced content", () => {
    const source = [
        "# Intro",
        "## A [link](https://example.com) and `code` ##",
        "```md",
        "# hidden",
        "```",
        "Setext title",
        "------------",
    ].join("\n");
    assert.deepEqual(parseHeadings(source), [
        { line: 0, level: 1, text: "Intro" },
        { line: 1, level: 2, text: "A link and code" },
        { line: 5, level: 2, text: "Setext title" },
    ]);
});

test("requires whitespace after an ATX marker", () => {
    assert.deepEqual(parseHeadings("#valid?\n## valid"), [
        { line: 1, level: 2, text: "valid" },
    ]);
});

test("finds closed and unclosed column-zero code fences", () => {
    const source = "```js\none\n```\n  ```\nignored\n  ```\n~~~\ntwo";
    assert.deepEqual(fencedCodeBlocks(source), [
        { openLine: 0, closeLine: 2 },
        { openLine: 6, closeLine: null },
    ]);
});

test("section includes nested headings and stops at a peer", () => {
    const source = "# Root\n## Section\nbody\n### Nested\nmore\n## Keep\nrest\n";
    assert.deepEqual(headingSection(source, 1), { startLine: 1, endLine: 5 });
});

test("Setext underline also identifies its section", () => {
    const source = "Title\n=====\nbody\nNext\n=====\nkeep\n";
    assert.deepEqual(headingSection(source, 1), { startLine: 0, endLine: 3 });
});

test("cleans common inline Markdown", () => {
    assert.equal(cleanHeadingText("*hello* ![alt](image.png)"), "hello alt");
});
