"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
    LinkContentsError,
    MAX_EXPANDED_CHARACTERS,
    MAX_LINK_DEPTH,
    MAX_LINKS_PER_RESOURCE,
    MAX_RESOURCE_BYTES,
    MAX_TOTAL_LINKS,
    MAX_TOTAL_RESOURCES,
    decodeTextBytes,
    expandLinkContents,
    inlineTextLinks,
    linksAtSelection,
    resourceKind,
    resolveResource,
    supportedTextMediaType,
} = require("../src/link-contents");

test("finds inline text links and ignores images, escapes, and code", () => {
    const source = [
        "[one](one.md) ![image](image.png) \\[escaped](no.md)",
        "`[inline](code.md)`",
        "~~~md",
        "[fenced](code.md)",
        "~~~",
        "    [indented](code.md)",
        "[two](<folder/two file.md> \"title\")",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "one", destination: "one.md" },
            { label: "two", destination: "folder/two file.md" },
        ],
    );
});

test("a fence closer may have at most three leading spaces", () => {
    const source = [
        "~~~md",
        "    ~~~",
        "[still fenced](no.md)",
        "   ~~~",
        "[after](yes.md)",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [{ label: "after", destination: "yes.md" }],
    );
});

test("handles a large run of unmatched opening brackets", () => {
    assert.deepEqual(inlineTextLinks("[".repeat(50000)), []);
});

test("keeps nested brackets and unmatched backticks on linear parser paths", () => {
    const nestedInstrumentation = {};
    const nested = "[".repeat(4000) + "]".repeat(4000);
    assert.deepEqual(inlineTextLinks(nested, { instrumentation: nestedInstrumentation }), []);
    assert.equal(nestedInstrumentation.linkParseAttempts, 4000);
    assert.equal(nestedInstrumentation.referenceNormalizations || 0, 0);

    const longShortcutInstrumentation = {};
    const longShortcut = `[${"x".repeat(1000)}]\n\n[x]: child.txt`;
    assert.deepEqual(
        inlineTextLinks(longShortcut, { instrumentation: longShortcutInstrumentation }),
        [],
    );
    assert.equal(longShortcutInstrumentation.referenceNormalizations || 0, 0);

    const backtickInstrumentation = {};
    assert.deepEqual(
        inlineTextLinks(`text ${"`".repeat(50000)}`, {
            instrumentation: backtickInstrumentation,
        }),
        [],
    );
    assert.equal(backtickInstrumentation.backtickRuns, 1);
});

test("parses balanced destinations, nested labels, and Markdown escapes", () => {
    const source = "See [the [nested] label](folder/a\\(1\\).md 'a title').";
    assert.deepEqual(inlineTextLinks(source), [{
        start: 4,
        end: source.length - 1,
        label: "the [nested] label",
        destination: "folder/a(1).md",
    }]);
});

test("decodes strict HTML entities and only treats punctuation as escaped", () => {
    const source = [
        "[named](dir/a&amp;b.txt)",
        "[numeric](dir&#x2F;b.txt)",
        "[unicode](f&ouml;&ouml;.txt)",
        "[missing-semicolon](a&amp.txt)",
        "[not-an-escape](a\\ b.txt)",
    ].join(" ");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "named", destination: "dir/a&b.txt" },
            { label: "numeric", destination: "dir/b.txt" },
            { label: "unicode", destination: "föö.txt" },
            { label: "missing-semicolon", destination: "a&amp.txt" },
        ],
    );
});

test("resolves full, collapsed, and shortcut reference links", () => {
    const source = [
        "[short]: first.txt",
        "[short]: ignored.txt",
        "[Before][  SHARED \t PART ] [Collapsed][] [SHORT] [entity][A&amp;B]",
        "![image][short] [missing][undefined]",
        "[shared part]: <parts/one file.md> \"title\"",
        "[collapsed]: parts/two.md 'title'",
        "[a&b]: entity.txt",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "Before", destination: "parts/one file.md" },
            { label: "Collapsed", destination: "parts/two.md" },
            { label: "SHORT", destination: "first.txt" },
            { label: "entity", destination: "entity.txt" },
        ],
    );
});

test("fully case-folds Unicode reference-label forms", () => {
    const source = [
        "[street one][Straße] [street two][STRAẞE] [sigma][οσ] "
            + "[ligature][oﬀice] [long s][ſource]",
        "",
        "[STRASSE]: street.txt",
        "[ΟΣ]: sigma.txt",
        "[office]: ligature.txt",
        "[source]: long-s.txt",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "street one", destination: "street.txt" },
            { label: "street two", destination: "street.txt" },
            { label: "sigma", destination: "sigma.txt" },
            { label: "ligature", destination: "ligature.txt" },
            { label: "long s", destination: "long-s.txt" },
        ],
    );
});

test("resolves and fully excludes continuation-line reference definitions", () => {
    const source = [
        "[local][part] [remote][remote]",
        "",
        "[part]:",
        "  child.md \"title\"",
        "[remote]:",
        "  <https://example.test/remote.txt>",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "local", destination: "child.md" },
            { label: "remote", destination: "https://example.test/remote.txt" },
        ],
    );
});

test("excludes next-line reference titles without consuming a second title", () => {
    const source = [
        "[one use][one] [two use][two]",
        "",
        "[one]: one.txt",
        "  \"title [bad](evil.md)\"",
        "[two]:",
        "  two.txt",
        "  'title [worse](evil-two.md)'",
        "[three]: three.txt \"inline title\"",
        "\"second [visible](visible.md)\"",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "one use", destination: "one.txt" },
            { label: "two use", destination: "two.txt" },
            { label: "visible", destination: "visible.md" },
        ],
    );
});

test("falls back to a shortcut reference when a following inline tail is invalid", () => {
    const source = "[fallback](not a link)\n\n[fallback]: child.txt";
    assert.deepEqual(inlineTextLinks(source), [{
        start: 0,
        end: "[fallback]".length,
        label: "fallback",
        destination: "child.txt",
    }]);
});

test("rejects invalid angle, raw-angle, and control characters in reference definitions", () => {
    const source = [
        "[bad-angle] [bad-raw] [bad-control] [escaped]",
        "[bad-angle]: <a<b>",
        "[bad-raw]: a<b.txt",
        "[bad-control]: a\u0001b.txt",
        "[escaped]: a\\<b.txt",
    ].join("\n");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [{ label: "escaped", destination: "a<b.txt" }],
    );
});

test("finds fetchable CommonMark URI autolinks but not bare or mail links", () => {
    const source = [
        "<https://example.test/a.txt?x=1&amp;y=2#part>",
        "<file:///workspace/a%20b.txt>",
        "<mailto:user@example.test>",
        "https://example.test/bare.txt",
    ].join(" ");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            {
                label: "https://example.test/a.txt?x=1&y=2#part",
                destination: "https://example.test/a.txt?x=1&y=2#part",
            },
            {
                label: "file:///workspace/a%20b.txt",
                destination: "file:///workspace/a%20b.txt",
            },
        ],
    );
});

test("enforces angle, control, title separator, and balanced-parenthesis rules", () => {
    const source = [
        "[balanced](dir/a_(b(c(d))).md?x=(y)&raw=1#part)",
        "[angle](<folder/a b.md> \"title\")",
        "[line-title](dir/a.md\n \"title\")",
        "[empty-title]( \"title\")",
        "[nested-angle](<a<b>)",
        "[raw-angle](a<b.md)",
        "[angle-control](<a\tb.md>)",
        "[joined-title](<a.md>\"title\")",
        "[control](a\u0001b.md)",
        "[blank-gap](a.md\n\n)",
        "[bad-title](a.md (ti(tle)))",
    ].join(" ");

    assert.deepEqual(
        inlineTextLinks(source).map(({ label, destination }) => ({ label, destination })),
        [
            { label: "balanced", destination: "dir/a_(b(c(d))).md?x=(y)&raw=1#part" },
            { label: "angle", destination: "folder/a b.md" },
            { label: "line-title", destination: "dir/a.md" },
            { label: "empty-title", destination: "" },
        ],
    );
});

test("matches a collapsed cursor or overlapping selection and rejects the gap", () => {
    const source = "[one](one.md) gap [two](two.md)";
    assert.equal(linksAtSelection(source, 2).length, 1);
    assert.equal(linksAtSelection(source, 0, 13)[0].destination, "one.md");
    assert.equal(linksAtSelection(source, 14).length, 0);
    assert.equal(linksAtSelection(source, 0, source.length).length, 2);
    const eofSource = "[one](one.md)";
    assert.equal(linksAtSelection(eofSource, eofSource.length).length, 1);
});

test("matches reference links and URI autolinks at the cursor", () => {
    const source = [
        "[reference text][part] <https://example.test/remote.txt>",
        "",
        "[part]: local.txt",
    ].join("\n");

    assert.equal(
        linksAtSelection(source, source.indexOf("reference"))[0].destination,
        "local.txt",
    );
    assert.equal(
        linksAtSelection(source, source.indexOf("remote"))[0].destination,
        "https://example.test/remote.txt",
    );
});

test("resolves relative URLs from each resource and removes fragments", () => {
    assert.equal(
        resolveResource("../shared/part.md#details", "https://example.test/docs/start/main.md"),
        "https://example.test/docs/shared/part.md",
    );
    assert.equal(
        resolveResource("next.md", "vscode-vfs://github/owner/repo/docs/main.md"),
        "vscode-vfs://github/owner/repo/docs/next.md",
    );
});

test("rejects empty and fragment-only targets", () => {
    assert.throws(
        () => resolveResource("", "file:///workspace/main.md"),
        (error) => error instanceof LinkContentsError && error.code === "empty-target",
    );
    assert.throws(
        () => resolveResource("#part", "file:///workspace/main.md"),
        (error) => error instanceof LinkContentsError && error.code === "fragment-target",
    );
});

test("classifies HTTP and file-system resources and rejects unsafe schemes", () => {
    assert.equal(resourceKind("https://example.test/file.md"), "http");
    assert.equal(resourceKind("file:///standalone/file.md"), "workspace-fs");
    assert.equal(resourceKind("vscode-vfs://github/owner/repo/file.md"), "workspace-fs");
    for (const resource of [
        "data:text/plain,no",
        "mailto:user@example.test",
        "command:workbench.action.closeWindow",
        "ftp://example.test/file.md",
        "ssh://example.test/file.md",
    ]) {
        assert.throws(
            () => resourceKind(resource),
            (error) => error instanceof LinkContentsError && error.code === "unsupported-scheme",
        );
    }
});

test("recursively replaces inline links relative to the containing resource", async () => {
    const files = new Map([
        ["file:///workspace/parts/first.md", "first + [second](nested/second.txt) + ![keep](image.png)"],
        ["file:///workspace/parts/nested/second.txt", "second"],
    ]);
    const loaded = [];

    const result = await expandLinkContents(
        "parts/first.md",
        "file:///workspace/main.md",
        async (resource) => {
            loaded.push(resource);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(result, "first + second + ![keep](image.png)");
    assert.deepEqual(loaded, [
        "file:///workspace/parts/first.md",
        "file:///workspace/parts/nested/second.txt",
    ]);
});

test("recursively resolves reference links relative to their containing resource", async () => {
    const files = new Map([
        [
            "file:///workspace/first.md",
            "before [child][target] after\n\n[target]:\n  nested/child.md \"Child\"",
        ],
        ["file:///workspace/nested/child.md", "child [leaf]\n\n[leaf]:\n  ../leaf.txt"],
        ["file:///workspace/leaf.txt", "text"],
    ]);
    const loaded = [];

    const result = await expandLinkContents(
        "first.md",
        "file:///workspace/main.md",
        async (resource) => {
            loaded.push(resource);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(
        result,
        "before child text\n\n[leaf]:\n  ../leaf.txt after\n\n[target]:\n  nested/child.md \"Child\"",
    );
    assert.deepEqual(loaded, [
        "file:///workspace/first.md",
        "file:///workspace/nested/child.md",
        "file:///workspace/leaf.txt",
    ]);
});

test("expands URI autolinks while preserving their query and dropping fragments", async () => {
    const files = new Map([
        [
            "file:///workspace/first.md",
            "before <https://example.test/part.txt?raw=1&amp;mode=md#section> after",
        ],
        ["https://example.test/part.txt?raw=1&mode=md", "remote"],
    ]);
    const loaded = [];

    const result = await expandLinkContents(
        "first.md",
        "file:///workspace/main.md",
        async (resource) => {
            loaded.push(resource);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(result, "before remote after");
    assert.deepEqual(loaded, [
        "file:///workspace/first.md",
        "https://example.test/part.txt?raw=1&mode=md",
    ]);
});

test("leaves unsupported nested links unchanged", async () => {
    const root = [
        "[fragment](#part)",
        "[mail](mailto:user@example.test)",
        "[data](data:text/plain,no)",
        "[ftp](ftp://example.test/file.txt)",
        "[ssh](ssh://example.test/file.txt)",
        "[ok](ok.txt)",
        "<mailto:user@example.test>",
    ].join(" ");
    const files = new Map([
        ["file:///workspace/root.md", root],
        ["file:///workspace/ok.txt", "value"],
    ]);
    const loaded = [];

    const result = await expandLinkContents(
        "root.md",
        "file:///workspace/main.md",
        async (resource) => {
            loaded.push(resource);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(
        result,
        "[fragment](#part) [mail](mailto:user@example.test) "
            + "[data](data:text/plain,no) [ftp](ftp://example.test/file.txt) "
            + "[ssh](ssh://example.test/file.txt) value <mailto:user@example.test>",
    );
    assert.deepEqual(loaded, ["file:///workspace/root.md", "file:///workspace/ok.txt"]);
});

test("expands relative links through a custom workspace provider", async () => {
    const files = new Map([
        ["vscode-vfs://github/owner/repo/root.md", "[child](nested/child.txt)"],
        ["vscode-vfs://github/owner/repo/nested/child.txt", "value"],
    ]);
    const loaded = [];

    const result = await expandLinkContents(
        "root.md",
        "vscode-vfs://github/owner/repo/main.md",
        async (resource) => {
            loaded.push(resource);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(result, "value");
    assert.deepEqual(loaded, [
        "vscode-vfs://github/owner/repo/root.md",
        "vscode-vfs://github/owner/repo/nested/child.txt",
    ]);
});

test("expands relative links from an HTTP resource", async () => {
    const files = new Map([
        ["https://example.test/docs/root.md", "[child](nested/child.txt)"],
        ["https://example.test/docs/nested/child.txt", "value"],
    ]);
    const loaded = [];

    const result = await expandLinkContents(
        "https://example.test/docs/root.md",
        "file:///workspace/main.md",
        async (resource) => {
            loaded.push(resource);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(result, "value");
    assert.deepEqual(loaded, [
        "https://example.test/docs/root.md",
        "https://example.test/docs/nested/child.txt",
    ]);
});

test("enforces per-resource link and incremental expanded-character limits", async () => {
    assert.equal(MAX_LINKS_PER_RESOURCE, 10000);
    assert.equal(MAX_EXPANDED_CHARACTERS, MAX_RESOURCE_BYTES * MAX_LINK_DEPTH);

    const tooMany = "[one](one.txt) [two](two.txt) [three](three.txt)";
    let linkLimitLoads = 0;
    await assert.rejects(
        expandLinkContents(
            "root.md",
            "file:///workspace/main.md",
            async (resource) => {
                linkLimitLoads += 1;
                return { text: tooMany, resource };
            },
            { maxLinksPerResource: 2 },
        ),
        (error) => error instanceof LinkContentsError && error.code === "too-many-links",
    );
    assert.equal(linkLimitLoads, 1);

    const files = new Map([
        ["file:///workspace/root.md", "A[first](first.txt)B[second](second.txt)C"],
        ["file:///workspace/first.txt", "1234"],
        ["file:///workspace/second.txt", "5678"],
    ]);
    const load = async (resource) => ({ text: files.get(resource), resource });

    await assert.rejects(
        expandLinkContents("root.md", "file:///workspace/main.md", load, {
            maxExpandedCharacters: 10,
        }),
        (error) => error instanceof LinkContentsError && error.code === "expanded-too-large",
    );
    assert.equal(
        await expandLinkContents("root.md", "file:///workspace/main.md", load, {
            maxExpandedCharacters: 11,
        }),
        "A1234B5678C",
    );
});

test("shares resource, parsed-link, and fetched-source budgets across the operation", async () => {
    assert.equal(MAX_TOTAL_RESOURCES, 1000);
    assert.equal(MAX_TOTAL_LINKS, 10000);

    const files = new Map([
        ["file:///workspace/root.md", "[first](part.md) / [again](part.md)"],
        ["file:///workspace/part.md", "[leaf](leaf.txt)"],
        ["file:///workspace/leaf.txt", "value"],
    ]);
    const sourceCharacters = Array.from(files.values())
        .reduce((total, value) => total + value.length, 0);
    const counts = new Map();
    const load = async (resource) => {
        counts.set(resource, (counts.get(resource) || 0) + 1);
        return { text: files.get(resource), resource };
    };

    assert.equal(
        await expandLinkContents("root.md", "file:///workspace/main.md", load, {
            maxTotalResources: 3,
            maxTotalLinks: 3,
            maxTotalSourceCharacters: sourceCharacters,
        }),
        "value / value",
    );
    assert.deepEqual(Array.from(counts.values()), [1, 1, 1]);

    const freshLoad = async (resource) => ({ text: files.get(resource), resource });
    await assert.rejects(
        expandLinkContents("root.md", "file:///workspace/main.md", freshLoad, {
            maxTotalResources: 2,
        }),
        (error) => error instanceof LinkContentsError && error.code === "too-many-resources",
    );
    await assert.rejects(
        expandLinkContents("root.md", "file:///workspace/main.md", freshLoad, {
            maxTotalLinks: 2,
        }),
        (error) => error instanceof LinkContentsError && error.code === "too-many-total-links",
    );
    await assert.rejects(
        expandLinkContents("root.md", "file:///workspace/main.md", freshLoad, {
            maxTotalSourceCharacters: sourceCharacters - 1,
        }),
        (error) => error instanceof LinkContentsError && error.code === "total-source-too-large",
    );
});

test("loads a repeated resource once while preserving repeated contents", async () => {
    const files = new Map([
        ["file:///workspace/root.md", "[first](part.txt) / [again](part.txt)"],
        ["file:///workspace/part.txt", "value"],
    ]);
    const counts = new Map();

    const result = await expandLinkContents(
        "root.md",
        "file:///workspace/index.md",
        async (resource) => {
            counts.set(resource, (counts.get(resource) || 0) + 1);
            return { text: files.get(resource), resource };
        },
    );

    assert.equal(result, "value / value");
    assert.equal(counts.get("file:///workspace/part.txt"), 1);
});

test("reuses an already-expanded redirect target without parsing the alias again", async () => {
    const root = "[actual](actual.md) [alias](alias.md)";
    const nested = "[leaf](leaf.txt)";
    const loaded = [];

    const result = await expandLinkContents(
        "root.md",
        "file:///workspace/main.md",
        async (resource) => {
            loaded.push(resource);
            if (resource.endsWith("root.md")) {
                return { text: root, resource };
            }
            if (resource.endsWith("alias.md")) {
                return { text: nested, resource: "file:///workspace/actual.md" };
            }
            if (resource.endsWith("actual.md")) {
                return { text: nested, resource };
            }
            return { text: "value", resource };
        },
        { maxTotalLinks: 3 },
    );

    assert.equal(result, "value value");
    assert.deepEqual(loaded, [
        "file:///workspace/root.md",
        "file:///workspace/actual.md",
        "file:///workspace/leaf.txt",
        "file:///workspace/alias.md",
    ]);
});

test("detects direct and redirected link cycles", async () => {
    const direct = new Map([
        ["file:///workspace/a.md", "[b](b.md)"],
        ["file:///workspace/b.md", "[a](a.md)"],
    ]);
    await assert.rejects(
        expandLinkContents("a.md", "file:///workspace/main.md", async (resource) => ({
            text: direct.get(resource),
            resource,
        })),
        (error) => error instanceof LinkContentsError && error.code === "link-cycle",
    );

    await assert.rejects(
        expandLinkContents("alias.md", "https://example.test/main.md", async (resource) => ({
            text: "[alias](alias.md)",
            resource: resource.endsWith("alias.md") ? "https://example.test/actual.md" : resource,
        })),
        (error) => error instanceof LinkContentsError && error.code === "link-cycle",
    );
});

test("allows 20 resources and rejects the 21st", async () => {
    const load = async (resource) => {
        const match = /level-(\d+)\.md$/.exec(resource);
        const level = Number(match[1]);
        return {
            text: level < 20 ? `[next](level-${level + 1}.md)` : "done",
            resource,
        };
    };

    assert.equal(
        await expandLinkContents("level-1.md", "file:///workspace/main.md", load, { maxDepth: 20 }),
        "done",
    );
    await assert.rejects(
        expandLinkContents("level-1.md", "file:///workspace/main.md", async (resource) => {
            const level = Number(/level-(\d+)\.md$/.exec(resource)[1]);
            return { text: `[next](level-${level + 1}.md)`, resource };
        }, { maxDepth: 20 }),
        (error) => error instanceof LinkContentsError && error.code === "max-depth",
    );
});

test("accepts textual media types and rejects binary media types", () => {
    assert.equal(supportedTextMediaType("text/markdown; charset=utf-8"), true);
    assert.equal(supportedTextMediaType("application/problem+json"), true);
    assert.equal(supportedTextMediaType("image/png"), false);
    assert.equal(supportedTextMediaType("application/octet-stream"), false);
});

test("decodes UTF-8 and rejects oversized, binary, and invalid UTF-8 data", () => {
    assert.equal(decodeTextBytes(new TextEncoder().encode("héllo")), "héllo");
    assert.throws(
        () => decodeTextBytes(new Uint8Array([1, 2, 3]), 2),
        (error) => error instanceof LinkContentsError && error.code === "resource-too-large",
    );
    assert.throws(
        () => decodeTextBytes(new Uint8Array([65, 0, 66])),
        (error) => error instanceof LinkContentsError && error.code === "binary-resource",
    );
    assert.throws(
        () => decodeTextBytes(new Uint8Array([0xc3, 0x28])),
        (error) => error instanceof LinkContentsError && error.code === "unsupported-encoding",
    );
});
