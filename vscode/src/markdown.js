"use strict";

const ATX_RE = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/;
const ATX_EMPTY_RE = /^ {0,3}(#{1,6})[ \t]*$/;
const SETEXT_RE = /^ {0,3}(=+|-+)[ \t]*$/;
const FENCE_OPEN_RE = /^ {0,3}(`{3,}|~{3,})/;
const FENCE_LINE_RE = /^(`{3,}|~{3,})/;

function cleanHeadingText(text) {
    return text
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/(\*{1,3}|_{1,3}|`+)(.+?)\1/g, "$2")
        .trim();
}

function isFenceClose(line, fenceCharacter, fenceLength) {
    const stripped = line.trim();
    return Boolean(
        stripped
        && stripped[0] === fenceCharacter
        && stripped.length >= fenceLength
        && stripped === stripped[0].repeat(stripped.length)
    );
}

function parseHeadings(text) {
    const lines = text.split(/\r?\n/);
    const headings = [];
    let inFence = false;
    let fenceCharacter = "";
    let fenceLength = 0;

    for (let line = 0; line < lines.length; line += 1) {
        const value = lines[line];
        if (inFence) {
            if (isFenceClose(value, fenceCharacter, fenceLength)) {
                inFence = false;
            }
            continue;
        }

        const fence = FENCE_OPEN_RE.exec(value);
        if (fence) {
            inFence = true;
            fenceCharacter = fence[1][0];
            fenceLength = fence[1].length;
            continue;
        }

        const atx = ATX_RE.exec(value);
        if (atx) {
            headings.push({
                line,
                level: atx[1].length,
                text: cleanHeadingText(atx[2]),
            });
            continue;
        }
        if (ATX_EMPTY_RE.test(value)) {
            continue;
        }

        const setext = SETEXT_RE.exec(value);
        if (setext && line > 0) {
            const previous = lines[line - 1].trim();
            if (
                previous
                && !previous.startsWith("#")
                && !SETEXT_RE.test(lines[line - 1])
                && (!headings.length || headings[headings.length - 1].line !== line - 1)
            ) {
                headings.push({
                    line: line - 1,
                    level: setext[1][0] === "=" ? 1 : 2,
                    text: cleanHeadingText(previous),
                });
            }
        }
    }

    return headings;
}

function fencedCodeBlocks(text) {
    const lines = text.split(/\r?\n/);
    const blocks = [];
    let openLine = null;
    let fence = "";

    lines.forEach((line, index) => {
        const match = FENCE_LINE_RE.exec(line);
        if (openLine === null) {
            if (match) {
                openLine = index;
                fence = match[1];
            }
            return;
        }
        if (!match) {
            return;
        }
        const run = match[1];
        const rest = line.slice(run.length).trim();
        if (run[0] === fence[0] && run.length >= fence.length && !rest) {
            blocks.push({ openLine, closeLine: index });
            openLine = null;
            fence = "";
        }
    });

    if (openLine !== null) {
        blocks.push({ openLine, closeLine: null });
    }
    return blocks;
}

function headingSection(text, selectedLine) {
    const headings = parseHeadings(text);
    const lines = text.split(/\r?\n/);
    const index = headings.findIndex((heading) => {
        if (heading.line === selectedLine) {
            return true;
        }
        return (
            selectedLine === heading.line + 1
            && !ATX_RE.test(lines[heading.line])
            && SETEXT_RE.test(lines[selectedLine] || "")
        );
    });
    if (index < 0) {
        return null;
    }

    const heading = headings[index];
    let endLine = lines.length;
    for (const following of headings.slice(index + 1)) {
        if (following.level <= heading.level) {
            endLine = following.line;
            break;
        }
    }
    return { startLine: heading.line, endLine };
}

module.exports = {
    cleanHeadingText,
    fencedCodeBlocks,
    headingSection,
    parseHeadings,
};
