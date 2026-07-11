"use strict";

const foldCase = require("@ar-nelson/foldcase");
const { decodeHTMLStrict } = require("entities/lib/decode.js");

const MAX_LINK_DEPTH = 20;
const MAX_RESOURCE_BYTES = 1024 * 1024;
const MAX_LINKS_PER_RESOURCE = 10000;
const MAX_EXPANDED_CHARACTERS = MAX_RESOURCE_BYTES * MAX_LINK_DEPTH;
const MAX_TOTAL_RESOURCES = 1000;
const MAX_TOTAL_LINKS = 10000;
const FENCE_OPEN_RE = /^ {0,3}(`{3,}|~{3,})/;
const ASCII_PUNCTUATION_RE = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;
const FETCHABLE_AUTOLINK_RE = /^(?:https?|file):/i;
const UNSUPPORTED_RESOURCE_SCHEMES = new Set([
    "command:",
    "data:",
    "ftp:",
    "ftps:",
    "javascript:",
    "mailto:",
    "sftp:",
    "ssh:",
    "telnet:",
    "untitled:",
    "ws:",
    "wss:",
]);

class LinkContentsError extends Error {
    constructor(code, message) {
        super(message);
        this.name = "LinkContentsError";
        this.code = code;
    }
}

function isEscaped(text, index) {
    let backslashes = 0;
    for (let cursor = index - 1; cursor >= 0 && text[cursor] === "\\"; cursor -= 1) {
        backslashes += 1;
    }
    return backslashes % 2 === 1;
}

function isMarkdownEscapeAt(text, index) {
    return (
        text[index] === "\\"
        && index + 1 < text.length
        && ASCII_PUNCTUATION_RE.test(text[index + 1])
    );
}

function decodeMarkdownDestination(value) {
    const unescaped = value.replace(
        /\\([!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])/g,
        "$1",
    );
    return decodeHTMLStrict(unescaped);
}

function lineRanges(text) {
    const ranges = [];
    let start = 0;
    for (let index = 0; index <= text.length; index += 1) {
        if (index !== text.length && text[index] !== "\n") {
            continue;
        }
        const contentEnd = index > start && text[index - 1] === "\r" ? index - 1 : index;
        ranges.push({
            start,
            contentEnd,
            end: index < text.length ? index + 1 : index,
        });
        start = index + 1;
    }
    return ranges;
}

function excludedBlockRanges(text) {
    const excluded = [];
    let fence;

    for (const line of lineRanges(text)) {
        const value = text.slice(line.start, line.contentEnd);
        if (fence) {
            let cursor = 0;
            while (cursor < value.length && value[cursor] === " " && cursor < 4) {
                cursor += 1;
            }
            const runStart = cursor;
            while (cursor < value.length && value[cursor] === fence.character) {
                cursor += 1;
            }
            if (
                runStart <= 3
                && cursor - runStart >= fence.length
                && /^[ \t]*$/.test(value.slice(cursor))
            ) {
                excluded.push({ start: fence.start, end: line.end });
                fence = undefined;
            }
            continue;
        }

        const opening = FENCE_OPEN_RE.exec(value);
        if (opening) {
            fence = {
                character: opening[1][0],
                length: opening[1].length,
                start: line.start,
            };
        } else if (/^(?: {4}|\t)/.test(value)) {
            excluded.push({ start: line.start, end: line.end });
        }
    }

    if (fence) {
        excluded.push({ start: fence.start, end: text.length });
    }
    return excluded;
}

function findClosingBackticks(text, start, runLength) {
    for (let cursor = start; cursor < text.length;) {
        if (text[cursor] !== "`") {
            cursor += 1;
            continue;
        }
        let end = cursor + 1;
        while (end < text.length && text[end] === "`") {
            end += 1;
        }
        if (end - cursor === runLength) {
            return end;
        }
        cursor = end;
    }
    return -1;
}

function buildBracketEndMap(text, excluded) {
    const bracketEnds = new Map();
    const stack = [];
    let excludedIndex = 0;

    for (let cursor = 0; cursor < text.length;) {
        while (excludedIndex < excluded.length && excluded[excludedIndex].end <= cursor) {
            excludedIndex += 1;
        }
        if (
            excludedIndex < excluded.length
            && excluded[excludedIndex].start <= cursor
            && cursor < excluded[excludedIndex].end
        ) {
            // Inline brackets cannot match across an excluded block.
            stack.length = 0;
            cursor = excluded[excludedIndex].end;
            continue;
        }

        if (isMarkdownEscapeAt(text, cursor)) {
            cursor += 2;
            continue;
        }
        if (text[cursor] === "`") {
            let end = cursor + 1;
            while (end < text.length && text[end] === "`") {
                end += 1;
            }
            const closing = findClosingBackticks(text, end, end - cursor);
            if (closing >= 0) {
                cursor = closing;
                continue;
            }
            cursor = end;
            continue;
        }
        if (text[cursor] === "[") {
            stack.push(cursor);
        } else if (text[cursor] === "]" && stack.length > 0) {
            const opening = stack.pop();
            bracketEnds.set(opening, cursor);
        }
        cursor += 1;
    }
    return bracketEnds;
}

function findLabelEnd(bracketEnds, start) {
    const end = bracketEnds.get(start);
    return end === undefined ? -1 : end;
}

function skipInlineWhitespace(text, start) {
    let cursor = start;
    let lineEndings = 0;
    while (cursor < text.length) {
        if (text[cursor] === " " || text[cursor] === "\t") {
            cursor += 1;
            continue;
        }
        if (text[cursor] === "\r" || text[cursor] === "\n") {
            lineEndings += 1;
            if (lineEndings > 1) {
                return null;
            }
            if (text[cursor] === "\r" && text[cursor + 1] === "\n") {
                cursor += 2;
            } else {
                cursor += 1;
            }
            continue;
        }
        break;
    }
    return { end: cursor, lineEndings };
}

function parseTitleAndClose(text, start) {
    const separator = skipInlineWhitespace(text, start);
    if (!separator) {
        return -1;
    }
    let cursor = separator.end;
    if (text[cursor] === ")") {
        return cursor + 1;
    }

    // A title must be separated from a destination by whitespace. This also
    // prevents `<target>"title"` from being accepted as a Markdown link.
    if (cursor === start) {
        return -1;
    }

    const opener = text[cursor];
    const closer = opener === "(" ? ")" : opener;
    if (opener !== "\"" && opener !== "'" && opener !== "(") {
        return -1;
    }
    const titleStart = cursor + 1;
    cursor += 1;
    while (cursor < text.length) {
        if (isMarkdownEscapeAt(text, cursor)) {
            cursor += 2;
            continue;
        }
        if (opener === "(" && text[cursor] === "(") {
            return -1;
        }
        if (text[cursor] === closer) {
            const title = text.slice(titleStart, cursor);
            if (/(?:\r\n?|\n)[ \t]*(?:\r\n?|\n)/.test(title)) {
                return -1;
            }
            const trailing = skipInlineWhitespace(text, cursor + 1);
            if (!trailing) {
                return -1;
            }
            return text[trailing.end] === ")" ? trailing.end + 1 : -1;
        }
        cursor += 1;
    }
    return -1;
}

function parseLinkDestination(text, start) {
    const leading = skipInlineWhitespace(text, start);
    if (!leading) {
        return null;
    }
    let cursor = leading.end;
    const destinationStart = cursor;

    if (text[cursor] === ")") {
        return { destination: "", end: cursor + 1 };
    }
    if (
        cursor > start
        && (text[cursor] === "\"" || text[cursor] === "'" || text[cursor] === "(")
    ) {
        const end = parseTitleAndClose(text, start);
        if (end >= 0) {
            return { destination: "", end };
        }
    }

    if (text[cursor] === "<") {
        cursor += 1;
        const valueStart = cursor;
        while (cursor < text.length && text[cursor] !== ">") {
            const code = text[cursor].charCodeAt(0);
            if (text[cursor] === "<" || code < 32 || code === 127) {
                return null;
            }
            if (isMarkdownEscapeAt(text, cursor)) {
                cursor += 2;
            } else {
                cursor += 1;
            }
        }
        if (text[cursor] !== ">") {
            return null;
        }
        const destination = decodeMarkdownDestination(text.slice(valueStart, cursor));
        const end = parseTitleAndClose(text, cursor + 1);
        return end < 0 ? null : { destination, end };
    }

    let parenthesisDepth = 0;
    while (cursor < text.length) {
        const character = text[cursor];
        if (isMarkdownEscapeAt(text, cursor)) {
            cursor += 2;
            continue;
        }
        if (character === "<") {
            return null;
        }
        if (character === "(") {
            parenthesisDepth += 1;
        } else if (character === ")") {
            if (parenthesisDepth === 0) {
                return {
                    destination: decodeMarkdownDestination(text.slice(destinationStart, cursor)),
                    end: cursor + 1,
                };
            }
            parenthesisDepth -= 1;
        } else if (
            character === " "
            || character === "\t"
            || character === "\r"
            || character === "\n"
        ) {
            if (parenthesisDepth !== 0) {
                return null;
            }
            const destination = decodeMarkdownDestination(text.slice(destinationStart, cursor));
            const end = parseTitleAndClose(text, cursor);
            return end < 0 ? null : { destination, end };
        } else if (character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127) {
            return null;
        }
        cursor += 1;
    }
    return null;
}

function normalizeReferenceLabel(label) {
    return foldCase(decodeMarkdownDestination(label)
        .replace(/[ \t\r\n]+/g, " ")
        .replace(/^ | $/g, ""));
}

function parseReferenceLabelAt(text, start, allowEmpty = false) {
    if (text[start] !== "[") {
        return null;
    }
    let cursor = start + 1;
    while (cursor < text.length) {
        if (cursor - start - 1 > 999) {
            return null;
        }
        if (isMarkdownEscapeAt(text, cursor)) {
            cursor += 2;
            continue;
        }
        if (text[cursor] === "[") {
            return null;
        }
        if (text[cursor] === "]") {
            const label = text.slice(start + 1, cursor);
            if ((!allowEmpty || label !== "") && !/[^ \t\r\n]/.test(label)) {
                return null;
            }
            return { label, end: cursor + 1 };
        }
        cursor += 1;
    }
    return null;
}

function parseReferenceDestination(text, start, end) {
    let cursor = start;
    if (text[cursor] === "<") {
        const valueStart = cursor + 1;
        cursor = valueStart;
        while (cursor < end) {
            if (isMarkdownEscapeAt(text, cursor)) {
                cursor += 2;
                continue;
            }
            const code = text[cursor].charCodeAt(0);
            if (text[cursor] === "<" || code < 32 || code === 127) {
                return null;
            }
            if (text[cursor] === ">") {
                return {
                    destination: decodeMarkdownDestination(text.slice(valueStart, cursor)),
                    end: cursor + 1,
                };
            }
            cursor += 1;
        }
        return null;
    }

    const valueStart = cursor;
    let parenthesisDepth = 0;
    while (cursor < end) {
        const character = text[cursor];
        if (isMarkdownEscapeAt(text, cursor)) {
            cursor += 2;
            continue;
        }
        if (character === " " || character === "\t") {
            break;
        }
        const code = character.charCodeAt(0);
        if (character === "<" || code < 32 || code === 127) {
            return null;
        }
        if (character === "(") {
            parenthesisDepth += 1;
        } else if (character === ")") {
            if (parenthesisDepth === 0) {
                return null;
            }
            parenthesisDepth -= 1;
        }
        cursor += 1;
    }
    if (cursor === valueStart || parenthesisDepth !== 0) {
        return null;
    }
    return {
        destination: decodeMarkdownDestination(text.slice(valueStart, cursor)),
        end: cursor,
    };
}

function referenceTitleLineIsValid(text, start, end) {
    let cursor = start;
    const opener = text[cursor];
    const closer = opener === "(" ? ")" : opener;
    if (opener !== "\"" && opener !== "'" && opener !== "(") {
        return false;
    }
    cursor += 1;
    while (cursor < end) {
        if (isMarkdownEscapeAt(text, cursor)) {
            cursor += 2;
            continue;
        }
        if (opener === "(" && text[cursor] === "(") {
            return false;
        }
        if (text[cursor] === closer) {
            cursor += 1;
            while (cursor < end && (text[cursor] === " " || text[cursor] === "\t")) {
                cursor += 1;
            }
            return cursor === end;
        }
        cursor += 1;
    }
    return false;
}

function referenceDefinitionTail(text, start, end) {
    let cursor = start;
    while (cursor < end && (text[cursor] === " " || text[cursor] === "\t")) {
        cursor += 1;
    }
    if (cursor === end) {
        return "none";
    }
    if (cursor === start) {
        return null;
    }
    return referenceTitleLineIsValid(text, cursor, end) ? "title" : null;
}

function rangeContains(ranges, position) {
    let low = 0;
    let high = ranges.length - 1;
    while (low <= high) {
        const middle = Math.floor((low + high) / 2);
        const range = ranges[middle];
        if (position < range.start) {
            high = middle - 1;
        } else if (position >= range.end) {
            low = middle + 1;
        } else {
            return true;
        }
    }
    return false;
}

function parseReferenceDefinitions(text, excluded) {
    const definitions = new Map();
    const ranges = [];
    const lines = lineRanges(text);

    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
        const line = lines[lineIndex];
        if (rangeContains(excluded, line.start)) {
            continue;
        }
        let cursor = line.start;
        let indentation = 0;
        while (cursor < line.contentEnd && text[cursor] === " " && indentation < 4) {
            cursor += 1;
            indentation += 1;
        }
        if (indentation > 3 || text[cursor] !== "[") {
            continue;
        }

        const parsedLabel = parseReferenceLabelAt(text, cursor);
        if (!parsedLabel || parsedLabel.end >= line.contentEnd || text[parsedLabel.end] !== ":") {
            continue;
        }
        cursor = parsedLabel.end + 1;
        while (cursor < line.contentEnd && (text[cursor] === " " || text[cursor] === "\t")) {
            cursor += 1;
        }

        let destinationLine = line;
        let continued = false;
        if (cursor >= line.contentEnd) {
            if (lineIndex + 1 >= lines.length) {
                continue;
            }
            const nextLine = lines[lineIndex + 1];
            if (rangeContains(excluded, nextLine.start)) {
                continue;
            }
            cursor = nextLine.start;
            let indentation = 0;
            while (cursor < nextLine.contentEnd && text[cursor] === " " && indentation < 4) {
                cursor += 1;
                indentation += 1;
            }
            if (indentation > 3 || cursor >= nextLine.contentEnd) {
                continue;
            }
            destinationLine = nextLine;
            continued = true;
        }

        const parsedDestination = parseReferenceDestination(
            text,
            cursor,
            destinationLine.contentEnd,
        );
        if (!parsedDestination) {
            continue;
        }
        const tail = referenceDefinitionTail(
            text,
            parsedDestination.end,
            destinationLine.contentEnd,
        );
        if (tail === null) {
            continue;
        }

        let lastDefinitionLineIndex = continued ? lineIndex + 1 : lineIndex;
        let definitionEnd = destinationLine.end;
        if (tail === "none" && lastDefinitionLineIndex + 1 < lines.length) {
            const titleLine = lines[lastDefinitionLineIndex + 1];
            if (!rangeContains(excluded, titleLine.start)) {
                let titleStart = titleLine.start;
                let titleIndentation = 0;
                while (
                    titleStart < titleLine.contentEnd
                    && text[titleStart] === " "
                    && titleIndentation < 4
                ) {
                    titleStart += 1;
                    titleIndentation += 1;
                }
                if (
                    titleIndentation <= 3
                    && titleStart < titleLine.contentEnd
                    && referenceTitleLineIsValid(text, titleStart, titleLine.contentEnd)
                ) {
                    lastDefinitionLineIndex += 1;
                    definitionEnd = titleLine.end;
                }
            }
        }

        const key = normalizeReferenceLabel(parsedLabel.label);
        if (!definitions.has(key)) {
            definitions.set(key, parsedDestination.destination);
        }
        ranges.push({ start: line.start, end: definitionEnd });
        lineIndex = lastDefinitionLineIndex;
    }
    return { definitions, ranges };
}

function parseInlineLinkAt(text, start, definitions, bracketEnds, instrumentation) {
    if (text[start] !== "[" || isEscaped(text, start)) {
        return null;
    }
    const labelEnd = findLabelEnd(bracketEnds, start);
    if (labelEnd < 0) {
        return null;
    }

    let destination;
    let end;

    function normalizeRawReference(rawStart, rawEnd) {
        if (rawEnd - rawStart > 999) {
            return undefined;
        }
        if (instrumentation) {
            instrumentation.referenceNormalizations = (
                instrumentation.referenceNormalizations || 0
            ) + 1;
        }
        return normalizeReferenceLabel(text.slice(rawStart, rawEnd));
    }

    function normalizeParsedReference(label) {
        if (instrumentation) {
            instrumentation.referenceNormalizations = (
                instrumentation.referenceNormalizations || 0
            ) + 1;
        }
        return normalizeReferenceLabel(label);
    }

    if (text[labelEnd + 1] === "(") {
        const parsed = parseLinkDestination(text, labelEnd + 2);
        if (parsed) {
            destination = parsed.destination;
            end = parsed.end;
        } else {
            if (definitions.size === 0) {
                return null;
            }
            const key = normalizeRawReference(start + 1, labelEnd);
            if (key === undefined) {
                return null;
            }
            const resolved = definitions.get(key);
            if (resolved === undefined) {
                return null;
            }
            destination = resolved;
            end = labelEnd + 1;
        }
    } else if (definitions.size === 0) {
        return null;
    } else if (text[labelEnd + 1] === "[") {
        const reference = parseReferenceLabelAt(text, labelEnd + 1, true);
        if (reference) {
            const key = reference.label === ""
                ? normalizeRawReference(start + 1, labelEnd)
                : normalizeParsedReference(reference.label);
            if (key === undefined) {
                return null;
            }
            const resolved = definitions.get(key);
            if (resolved === undefined) {
                return null;
            }
            destination = resolved;
            end = reference.end;
        }
    } else {
        const key = normalizeRawReference(start + 1, labelEnd);
        if (key === undefined) {
            return null;
        }
        const resolved = definitions.get(key);
        if (resolved !== undefined) {
            destination = resolved;
            end = labelEnd + 1;
        }
    }

    if (destination === undefined) {
        return null;
    }
    return {
        start,
        end,
        label: text.slice(start + 1, labelEnd),
        destination,
        image: start > 0 && text[start - 1] === "!" && !isEscaped(text, start - 1),
    };
}

function parseFetchableAutolinkAt(text, start) {
    if (text[start] !== "<" || isEscaped(text, start)) {
        return null;
    }
    const close = text.indexOf(">", start + 1);
    if (close < 0) {
        return null;
    }
    const value = text.slice(start + 1, close);
    if (!FETCHABLE_AUTOLINK_RE.test(value) || /[\u0000-\u0020\u007f<>]/.test(value)) {
        return null;
    }
    const destination = decodeHTMLStrict(value);
    return {
        start,
        end: close + 1,
        label: destination,
        destination,
        image: false,
    };
}

function inlineTextLinks(text, options = {}) {
    const links = [];
    const maxLinks = options.maxLinks ?? Number.POSITIVE_INFINITY;
    const instrumentation = options.instrumentation;

    function addLink(link) {
        if (links.length >= maxLinks) {
            throw new LinkContentsError(
                "too-many-links",
                `A linked resource contains more than ${maxLinks} Markdown links.`,
            );
        }
        links.push(link);
    }

    const blockRanges = excludedBlockRanges(text);
    const references = parseReferenceDefinitions(text, blockRanges);
    const excluded = blockRanges.concat(references.ranges).sort((left, right) => left.start - right.start);
    const bracketEnds = buildBracketEndMap(text, excluded);
    let excludedIndex = 0;

    for (let cursor = 0; cursor < text.length;) {
        while (excludedIndex < excluded.length && excluded[excludedIndex].end <= cursor) {
            excludedIndex += 1;
        }
        if (
            excludedIndex < excluded.length
            && excluded[excludedIndex].start <= cursor
            && cursor < excluded[excludedIndex].end
        ) {
            cursor = excluded[excludedIndex].end;
            continue;
        }

        if (text[cursor] === "`" && !isEscaped(text, cursor)) {
            if (instrumentation) {
                instrumentation.backtickRuns = (instrumentation.backtickRuns || 0) + 1;
            }
            let end = cursor + 1;
            while (end < text.length && text[end] === "`") {
                end += 1;
            }
            const closing = findClosingBackticks(text, end, end - cursor);
            if (closing >= 0) {
                cursor = closing;
                continue;
            }
            cursor = end;
            continue;
        }

        if (text[cursor] === "[") {
            if (instrumentation) {
                instrumentation.linkParseAttempts = (
                    instrumentation.linkParseAttempts || 0
                ) + 1;
            }
            const link = parseInlineLinkAt(
                text,
                cursor,
                references.definitions,
                bracketEnds,
                instrumentation,
            );
            if (link) {
                if (!link.image) {
                    addLink({
                        start: link.start,
                        end: link.end,
                        label: link.label,
                        destination: link.destination,
                    });
                }
                cursor = link.end;
                continue;
            }
        }
        if (text[cursor] === "<") {
            const link = parseFetchableAutolinkAt(text, cursor);
            if (link) {
                addLink({
                    start: link.start,
                    end: link.end,
                    label: link.label,
                    destination: link.destination,
                });
                cursor = link.end;
                continue;
            }
        }
        cursor += 1;
    }
    return links;
}

function linksAtSelection(text, selectionStart, selectionEnd = selectionStart) {
    const start = Math.max(0, Math.min(selectionStart, selectionEnd));
    const end = Math.max(0, Math.max(selectionStart, selectionEnd));
    return inlineTextLinks(text).filter((link) => (
        start === end
            ? (
                link.start <= start
                && (start < link.end || (start === text.length && start === link.end))
            )
            : link.start < end && start < link.end
    ));
}

function canonicalResource(resource) {
    let parsed;
    try {
        parsed = new URL(resource);
    } catch (error) {
        throw new LinkContentsError("invalid-resource", `Invalid resource URL: ${resource}`);
    }
    parsed.hash = "";
    return parsed.toString();
}

function resourceKind(resource) {
    let protocol;
    try {
        protocol = new URL(resource).protocol.toLowerCase();
    } catch (error) {
        throw new LinkContentsError("invalid-resource", `Invalid resource URL: ${resource}`);
    }
    if (protocol === "http:" || protocol === "https:") {
        return "http";
    }
    if (UNSUPPORTED_RESOURCE_SCHEMES.has(protocol)) {
        throw new LinkContentsError("unsupported-scheme", `Unsupported linked resource scheme: ${protocol}`);
    }
    return "workspace-fs";
}

function destinationCanUseProvider(destination, baseResource) {
    const match = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(destination.trim());
    if (!match) {
        return true;
    }

    const protocol = `${match[1].toLowerCase()}:`;
    if (protocol === "http:" || protocol === "https:" || protocol === "file:") {
        return true;
    }
    if (UNSUPPORTED_RESOURCE_SCHEMES.has(protocol)) {
        return false;
    }

    try {
        return new URL(baseResource).protocol.toLowerCase() === protocol;
    } catch (error) {
        return false;
    }
}

function resolveResource(destination, baseResource) {
    const value = destination.trim();
    if (!value) {
        throw new LinkContentsError("empty-target", "The Markdown link has an empty target.");
    }
    if (value.startsWith("#")) {
        throw new LinkContentsError(
            "fragment-target",
            "The Markdown link points to a document fragment, not a text resource.",
        );
    }
    try {
        return canonicalResource(new URL(value, baseResource).toString());
    } catch (error) {
        if (error instanceof LinkContentsError) {
            throw error;
        }
        throw new LinkContentsError("invalid-target", `The Markdown link target is invalid: ${value}`);
    }
}

function shouldLeaveLinkUnexpanded(error) {
    return (
        error instanceof LinkContentsError
        && [
            "empty-target",
            "fragment-target",
            "invalid-resource",
            "invalid-target",
            "unsupported-scheme",
        ].includes(error.code)
    );
}

function supportedTextMediaType(contentType) {
    if (!contentType) {
        return true;
    }
    const mediaType = contentType.split(";", 1)[0].trim().toLowerCase();
    return (
        mediaType.startsWith("text/")
        || mediaType === "application/json"
        || mediaType.endsWith("+json")
        || mediaType === "application/xml"
        || mediaType.endsWith("+xml")
        || mediaType === "application/javascript"
        || mediaType === "application/ecmascript"
        || mediaType === "application/yaml"
        || mediaType === "application/x-yaml"
        || mediaType === "application/toml"
    );
}

function decodeTextBytes(bytes, maxBytes = MAX_RESOURCE_BYTES) {
    const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    if (data.byteLength > maxBytes) {
        throw new LinkContentsError(
            "resource-too-large",
            `The linked resource exceeds the ${maxBytes}-byte size limit.`,
        );
    }
    if (data.includes(0)) {
        throw new LinkContentsError("binary-resource", "The linked resource appears to be binary.");
    }

    let text;
    try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(data);
    } catch (error) {
        throw new LinkContentsError(
            "unsupported-encoding",
            "The linked resource is not valid UTF-8 text.",
        );
    }

    let controls = 0;
    for (const character of text) {
        const code = character.charCodeAt(0);
        if (code < 32 && code !== 9 && code !== 10 && code !== 12 && code !== 13) {
            controls += 1;
        }
    }
    if (controls > Math.max(1, text.length / 100)) {
        throw new LinkContentsError("binary-resource", "The linked resource appears to be binary.");
    }
    return text;
}

async function expandLinkContents(destination, baseResource, loadResource, options = {}) {
    const maxDepth = options.maxDepth ?? MAX_LINK_DEPTH;
    const maxLinksPerResource = options.maxLinksPerResource ?? MAX_LINKS_PER_RESOURCE;
    const maxExpandedCharacters = options.maxExpandedCharacters ?? MAX_EXPANDED_CHARACTERS;
    const maxTotalResources = options.maxTotalResources ?? MAX_TOTAL_RESOURCES;
    const maxTotalLinks = options.maxTotalLinks ?? MAX_TOTAL_LINKS;
    const maxTotalSourceCharacters = (
        options.maxTotalSourceCharacters ?? MAX_EXPANDED_CHARACTERS
    );
    if (!Number.isInteger(maxDepth) || maxDepth < 1) {
        throw new LinkContentsError("invalid-depth", "The maximum link depth must be a positive integer.");
    }
    if (!Number.isInteger(maxLinksPerResource) || maxLinksPerResource < 1) {
        throw new LinkContentsError(
            "invalid-link-limit",
            "The per-resource Markdown link limit must be a positive integer.",
        );
    }
    if (!Number.isInteger(maxExpandedCharacters) || maxExpandedCharacters < 1) {
        throw new LinkContentsError(
            "invalid-expanded-limit",
            "The expanded character limit must be a positive integer.",
        );
    }
    if (!Number.isInteger(maxTotalResources) || maxTotalResources < 1) {
        throw new LinkContentsError(
            "invalid-total-resource-limit",
            "The total resource limit must be a positive integer.",
        );
    }
    if (!Number.isInteger(maxTotalLinks) || maxTotalLinks < 1) {
        throw new LinkContentsError(
            "invalid-total-link-limit",
            "The total parsed-link limit must be a positive integer.",
        );
    }
    if (!Number.isInteger(maxTotalSourceCharacters) || maxTotalSourceCharacters < 1) {
        throw new LinkContentsError(
            "invalid-total-source-limit",
            "The total fetched-source character limit must be a positive integer.",
        );
    }

    const cache = new Map();
    const root = resolveResource(destination, baseResource);
    let totalResources = 0;
    let totalParsedLinks = 0;
    let totalSourceCharacters = 0;

    function appendWithinLimit(parts, currentLength, value) {
        if (value.length > maxExpandedCharacters - currentLength) {
            throw new LinkContentsError(
                "expanded-too-large",
                `Expanded linked content exceeds the ${maxExpandedCharacters}-character limit.`,
            );
        }
        parts.push(value);
        return currentLength + value.length;
    }

    async function expand(resource, depth, ancestors) {
        const key = canonicalResource(resource);
        if (depth > maxDepth) {
            throw new LinkContentsError(
                "max-depth",
                `Linked resources exceed the maximum recursion depth of ${maxDepth}.`,
            );
        }
        if (ancestors.has(key)) {
            throw new LinkContentsError("link-cycle", `A recursive link cycle was detected at ${key}.`);
        }
        if (cache.has(key)) {
            return cache.get(key);
        }

        if (totalResources >= maxTotalResources) {
            throw new LinkContentsError(
                "too-many-resources",
                `Linked content exceeds the ${maxTotalResources}-resource operation limit.`,
            );
        }
        totalResources += 1;
        const loaded = await loadResource(key);
        if (!loaded || typeof loaded.text !== "string") {
            throw new LinkContentsError("invalid-loader", "The resource loader did not return text.");
        }
        const actualKey = canonicalResource(loaded.resource || key);
        if (actualKey !== key && ancestors.has(actualKey)) {
            throw new LinkContentsError("link-cycle", `A recursive link cycle was detected at ${actualKey}.`);
        }
        if (loaded.text.length > maxTotalSourceCharacters - totalSourceCharacters) {
            throw new LinkContentsError(
                "total-source-too-large",
                "Fetched linked sources exceed the "
                    + `${maxTotalSourceCharacters}-character operation limit.`,
            );
        }
        totalSourceCharacters += loaded.text.length;
        if (actualKey !== key && cache.has(actualKey)) {
            const expanded = cache.get(actualKey);
            cache.set(key, expanded);
            return expanded;
        }

        const nextAncestors = new Set(ancestors);
        nextAncestors.add(key);
        nextAncestors.add(actualKey);
        const links = inlineTextLinks(loaded.text, { maxLinks: maxLinksPerResource });
        if (links.length > maxLinksPerResource) {
            throw new LinkContentsError(
                "too-many-links",
                `A linked resource contains more than ${maxLinksPerResource} Markdown links.`,
            );
        }
        if (links.length > maxTotalLinks - totalParsedLinks) {
            throw new LinkContentsError(
                "too-many-total-links",
                `Linked content exceeds the ${maxTotalLinks}-parsed-link operation limit.`,
            );
        }
        totalParsedLinks += links.length;
        let cursor = 0;
        let expandedLength = 0;
        const parts = [];
        for (const link of links) {
            expandedLength = appendWithinLimit(
                parts,
                expandedLength,
                loaded.text.slice(cursor, link.start),
            );
            const literalLink = loaded.text.slice(link.start, link.end);
            if (!destinationCanUseProvider(link.destination, actualKey)) {
                expandedLength = appendWithinLimit(parts, expandedLength, literalLink);
                cursor = link.end;
                continue;
            }
            let target;
            try {
                target = resolveResource(link.destination, actualKey);
                // Validate schemes here so unsupported nested links remain
                // ordinary Markdown instead of aborting an otherwise useful
                // recursive expansion.
                resourceKind(target);
            } catch (error) {
                if (!shouldLeaveLinkUnexpanded(error)) {
                    throw error;
                }
                expandedLength = appendWithinLimit(parts, expandedLength, literalLink);
                cursor = link.end;
                continue;
            }
            const child = await expand(target, depth + 1, nextAncestors);
            expandedLength = appendWithinLimit(parts, expandedLength, child);
            cursor = link.end;
        }
        appendWithinLimit(parts, expandedLength, loaded.text.slice(cursor));
        const expanded = parts.join("");
        cache.set(key, expanded);
        cache.set(actualKey, expanded);
        return expanded;
    }

    return expand(root, 1, new Set());
}

module.exports = {
    LinkContentsError,
    MAX_EXPANDED_CHARACTERS,
    MAX_LINK_DEPTH,
    MAX_LINKS_PER_RESOURCE,
    MAX_RESOURCE_BYTES,
    MAX_TOTAL_LINKS,
    MAX_TOTAL_RESOURCES,
    canonicalResource,
    decodeTextBytes,
    expandLinkContents,
    inlineTextLinks,
    linksAtSelection,
    resourceKind,
    resolveResource,
    supportedTextMediaType,
};
