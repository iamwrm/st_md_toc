"use strict";

const vscode = require("vscode");
const {
    fencedCodeBlocks,
    headingSection,
    parseHeadings,
} = require("./markdown");
const {
    LinkContentsError,
    MAX_RESOURCE_BYTES,
    canonicalResource,
    decodeTextBytes,
    expandLinkContents,
    linksAtSelection,
    resourceKind,
    supportedTextMediaType,
} = require("./link-contents");

const VIEW_ID = "markdownToc.outline";
const HTTP_FETCH_TIMEOUT_MS = 15000;

function isMarkdownDocument(document) {
    return Boolean(document && document.languageId === "markdown");
}

function activeMarkdownEditor() {
    const editor = vscode.window.activeTextEditor;
    return editor && isMarkdownDocument(editor.document) ? editor : undefined;
}

class HeadingItem extends vscode.TreeItem {
    constructor(document, heading, parent) {
        super(heading.text, vscode.TreeItemCollapsibleState.None);
        this.document = document;
        this.heading = heading;
        this.parent = parent;
        this.children = [];
        this.description = `L${heading.line + 1}`;
        this.iconPath = new vscode.ThemeIcon("symbol-key");
        this.contextValue = "markdownHeading";
        this.command = {
            command: "markdownToc.openHeading",
            title: "Open heading",
            arguments: [document.uri, heading.line],
        };
    }
}

class MarkdownOutlineProvider {
    constructor() {
        this.document = undefined;
        this.roots = [];
        this.items = [];
        this.changeEmitter = new vscode.EventEmitter();
        this.onDidChangeTreeData = this.changeEmitter.event;
    }

    setDocument(document) {
        this.document = isMarkdownDocument(document) ? document : undefined;
        this.rebuild();
    }

    refresh() {
        this.rebuild();
    }

    rebuild() {
        this.roots = [];
        this.items = [];
        if (this.document) {
            const stack = [];
            for (const heading of parseHeadings(this.document.getText())) {
                while (stack.length && stack[stack.length - 1].heading.level >= heading.level) {
                    stack.pop();
                }
                const parent = stack.length ? stack[stack.length - 1] : undefined;
                const item = new HeadingItem(this.document, heading, parent);
                if (parent) {
                    parent.children.push(item);
                    parent.collapsibleState = vscode.TreeItemCollapsibleState.Expanded;
                } else {
                    this.roots.push(item);
                }
                this.items.push(item);
                stack.push(item);
            }
        }
        this.changeEmitter.fire(undefined);
    }

    getTreeItem(item) {
        return item;
    }

    getChildren(item) {
        return item ? item.children : this.roots;
    }

    getParent(item) {
        return item.parent;
    }

    currentItem(line) {
        let current;
        for (const item of this.items) {
            if (item.heading.line > line) {
                break;
            }
            current = item;
        }
        return current;
    }
}

function documentRange(document, startLine, endLine) {
    const start = new vscode.Position(startLine, 0);
    const end = endLine >= document.lineCount
        ? document.positionAt(document.getText().length)
        : new vscode.Position(endLine, 0);
    return new vscode.Range(start, end);
}

async function openHeading(uri, line) {
    const document = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(document);
    const position = new vscode.Position(line, 0);
    editor.selection = new vscode.Selection(position, position);
    editor.revealRange(
        new vscode.Range(position, position),
        vscode.TextEditorRevealType.AtTop,
    );
}

async function copyCodeBlock() {
    const editor = activeMarkdownEditor();
    if (!editor) {
        vscode.window.showInformationMessage("Markdown TOC: the active editor is not Markdown.");
        return;
    }
    const document = editor.document;
    const line = editor.selection.active.line;
    const block = fencedCodeBlocks(document.getText()).find(({ openLine, closeLine }) => (
        openLine <= line && line <= (closeLine === null ? document.lineCount - 1 : closeLine)
    ));
    if (!block) {
        vscode.window.showInformationMessage("Markdown TOC: no fenced code block at the cursor.");
        return;
    }

    const endLine = block.closeLine === null ? document.lineCount : block.closeLine;
    const content = [];
    for (let index = block.openLine + 1; index < endLine; index += 1) {
        content.push(document.lineAt(index).text);
    }
    await vscode.env.clipboard.writeText(content.length ? `${content.join("\n")}\n` : "");
    vscode.window.setStatusBarMessage(
        `Markdown TOC: copied ${content.length} line${content.length === 1 ? "" : "s"}`,
        2500,
    );
}

async function cutWholeSection() {
    const editor = activeMarkdownEditor();
    if (!editor) {
        vscode.window.showInformationMessage("Markdown TOC: the active editor is not Markdown.");
        return;
    }
    const document = editor.document;
    const section = headingSection(document.getText(), editor.selection.active.line);
    if (!section) {
        vscode.window.showInformationMessage("Markdown TOC: no heading at the cursor.");
        return;
    }

    const range = documentRange(document, section.startLine, section.endLine);
    const content = document.getText(range);
    await vscode.env.clipboard.writeText(content);
    const applied = await editor.edit((builder) => builder.delete(range));
    if (!applied) {
        vscode.window.showErrorMessage("Markdown TOC: the section could not be edited.");
    }
}

function resourceName(resource) {
    try {
        const parsed = new URL(resource);
        parsed.username = "";
        parsed.password = "";
        parsed.search = "";
        parsed.hash = "";
        return parsed.toString();
    } catch (error) {
        return resource;
    }
}

async function readHttpBytes(response) {
    const length = Number(response.headers.get("content-length"));
    if (Number.isFinite(length) && length > MAX_RESOURCE_BYTES) {
        throw new LinkContentsError(
            "resource-too-large",
            `The linked resource exceeds the ${MAX_RESOURCE_BYTES}-byte size limit.`,
        );
    }

    if (!response.body || typeof response.body.getReader !== "function") {
        return new Uint8Array(await response.arrayBuffer());
    }

    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                break;
            }
            total += value.byteLength;
            if (total > MAX_RESOURCE_BYTES) {
                await reader.cancel();
                throw new LinkContentsError(
                    "resource-too-large",
                    `The linked resource exceeds the ${MAX_RESOURCE_BYTES}-byte size limit.`,
                );
            }
            chunks.push(value);
        }
    } finally {
        reader.releaseLock();
    }

    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
    }
    return bytes;
}

async function loadHttpResource(resource, cancellationToken) {
    const controller = new globalThis.AbortController();
    let timedOut = false;
    const timeout = globalThis.setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, HTTP_FETCH_TIMEOUT_MS);
    const cancellation = cancellationToken?.onCancellationRequested(() => controller.abort());

    const throwAbortError = () => {
        if (cancellationToken?.isCancellationRequested) {
            throw new LinkContentsError("cancelled", "Copy Link Contents was cancelled.");
        }
        if (timedOut) {
            throw new LinkContentsError(
                "http-timeout",
                `Timed out after ${HTTP_FETCH_TIMEOUT_MS / 1000} seconds while fetching ${resourceName(resource)}.`,
            );
        }
    };

    try {
        throwAbortError();
        let response;
        try {
            response = await globalThis.fetch(resource, { signal: controller.signal });
        } catch (error) {
            throwAbortError();
            throw new LinkContentsError(
                "http-fetch",
                `Could not fetch ${resourceName(resource)}. Check the URL, network connection, and CORS policy.`,
            );
        }
        if (!response.ok) {
            throw new LinkContentsError(
                "http-status",
                `Could not fetch ${resourceName(resource)}: HTTP ${response.status}.`,
            );
        }

        const finalResource = canonicalResource(response.url || resource);
        if (resourceKind(finalResource) !== "http") {
            throw new LinkContentsError(
                "unsupported-redirect",
                "The HTTP request redirected to an unsupported resource scheme.",
            );
        }

        const contentType = response.headers.get("content-type") || "";
        if (!supportedTextMediaType(contentType)) {
            throw new LinkContentsError(
                "unsupported-media-type",
                `The linked resource is not a supported text type (${contentType.split(";", 1)[0]}).`,
            );
        }
        let bytes;
        try {
            bytes = await readHttpBytes(response);
        } catch (error) {
            throwAbortError();
            if (error instanceof LinkContentsError) {
                throw error;
            }
            throw new LinkContentsError(
                "http-read",
                `Could not read the response from ${resourceName(finalResource)}.`,
            );
        }
        return {
            text: decodeTextBytes(bytes),
            resource: finalResource,
        };
    } finally {
        globalThis.clearTimeout(timeout);
        cancellation?.dispose();
    }
}

async function loadWorkspaceResource(resource, cancellationToken) {
    if (cancellationToken?.isCancellationRequested) {
        throw new LinkContentsError("cancelled", "Copy Link Contents was cancelled.");
    }
    const uri = vscode.Uri.parse(resource, true);
    let stat;
    try {
        stat = await vscode.workspace.fs.stat(uri);
    } catch (error) {
        throw new LinkContentsError(
            "workspace-stat",
            `Could not find the linked workspace resource: ${resourceName(resource)}`,
        );
    }
    if ((stat.type & vscode.FileType.Directory) !== 0) {
        throw new LinkContentsError("directory-resource", "The Markdown link points to a directory, not a text file.");
    }
    if (stat.size > MAX_RESOURCE_BYTES) {
        throw new LinkContentsError(
            "resource-too-large",
            `The linked resource exceeds the ${MAX_RESOURCE_BYTES}-byte size limit.`,
        );
    }

    let bytes;
    try {
        bytes = await vscode.workspace.fs.readFile(uri);
    } catch (error) {
        throw new LinkContentsError(
            "workspace-read",
            `Could not read the linked workspace resource: ${resourceName(resource)}`,
        );
    }
    return { text: decodeTextBytes(bytes), resource };
}

async function loadTextResource(resource, cancellationToken) {
    if (resourceKind(resource) === "http") {
        if (typeof globalThis.fetch !== "function") {
            throw new LinkContentsError("fetch-unavailable", "HTTP fetching is unavailable in this extension host.");
        }
        return loadHttpResource(resource, cancellationToken);
    }
    return loadWorkspaceResource(resource, cancellationToken);
}

async function copyLinkContents() {
    const editor = activeMarkdownEditor();
    if (!editor) {
        vscode.window.showInformationMessage("Markdown TOC: the active editor is not Markdown.");
        return;
    }

    const document = editor.document;
    const start = document.offsetAt(editor.selection.start);
    const end = document.offsetAt(editor.selection.end);
    const links = linksAtSelection(document.getText(), start, end);
    if (links.length === 0) {
        vscode.window.showInformationMessage("Markdown TOC: no text-resource Markdown link at the cursor or selection.");
        return;
    }
    if (links.length > 1) {
        vscode.window.showInformationMessage("Markdown TOC: select only one text-resource Markdown link.");
        return;
    }

    try {
        const content = await vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: "Markdown TOC: Loading linked text",
                cancellable: true,
            },
            (_progress, cancellationToken) => expandLinkContents(
                links[0].destination,
                document.uri.toString(),
                (resource) => loadTextResource(resource, cancellationToken),
            ),
        );
        await vscode.env.clipboard.writeText(content);
        vscode.window.setStatusBarMessage(
            `Markdown TOC: copied ${content.length} character${content.length === 1 ? "" : "s"} from linked text`,
            2500,
        );
    } catch (error) {
        if (error instanceof LinkContentsError && error.code === "cancelled") {
            vscode.window.showInformationMessage(`Markdown TOC: ${error.message}`);
            return;
        }
        const message = error instanceof LinkContentsError
            ? error.message
            : "An unexpected error occurred while loading the linked text.";
        vscode.window.showErrorMessage(`Markdown TOC: ${message}`);
    }
}

function activate(context) {
    const provider = new MarkdownOutlineProvider();
    provider.setDocument(vscode.window.activeTextEditor?.document);
    const tree = vscode.window.createTreeView(VIEW_ID, {
        treeDataProvider: provider,
        showCollapseAll: true,
    });
    tree.message = provider.document ? undefined : "Open a Markdown file to show its headings.";

    let refreshTimer;
    const refresh = (document) => {
        if (document && provider.document && document.uri.toString() !== provider.document.uri.toString()) {
            return;
        }
        provider.refresh();
        tree.message = provider.document && provider.items.length === 0
            ? "This Markdown file has no headings."
            : undefined;
    };
    const revealCurrent = (editor) => {
        if (!editor || !provider.document || editor.document.uri.toString() !== provider.document.uri.toString()) {
            return;
        }
        if (!vscode.workspace.getConfiguration("markdownToc").get("revealCurrentHeading", true)) {
            return;
        }
        const item = provider.currentItem(editor.selection.active.line);
        if (item) {
            tree.reveal(item, { select: true, focus: false, expand: true }).then(
                undefined,
                () => {},
            );
        }
    };

    context.subscriptions.push(
        tree,
        provider.changeEmitter,
        vscode.commands.registerCommand("markdownToc.show", () => (
            vscode.commands.executeCommand(`${VIEW_ID}.focus`)
        )),
        vscode.commands.registerCommand("markdownToc.refresh", () => refresh()),
        vscode.commands.registerCommand("markdownToc.openHeading", openHeading),
        vscode.commands.registerCommand("markdownToc.copyCodeBlock", copyCodeBlock),
        vscode.commands.registerCommand("markdownToc.copyLinkContents", copyLinkContents),
        vscode.commands.registerCommand("markdownToc.cutWholeSection", cutWholeSection),
        vscode.window.onDidChangeActiveTextEditor((editor) => {
            provider.setDocument(editor?.document);
            tree.message = provider.document
                ? (provider.items.length ? undefined : "This Markdown file has no headings.")
                : "Open a Markdown file to show its headings.";
            revealCurrent(editor);
        }),
        vscode.window.onDidChangeTextEditorSelection(({ textEditor }) => revealCurrent(textEditor)),
        vscode.workspace.onDidSaveTextDocument((document) => refresh(document)),
        vscode.workspace.onDidChangeTextDocument(({ document }) => {
            if (!provider.document || document.uri.toString() !== provider.document.uri.toString()) {
                return;
            }
            clearTimeout(refreshTimer);
            const delay = vscode.workspace.getConfiguration("markdownToc").get("refreshDelay", 300);
            refreshTimer = setTimeout(() => refresh(document), Math.max(0, delay));
        }),
        { dispose: () => clearTimeout(refreshTimer) },
    );
}

function deactivate() {}

module.exports = { activate, deactivate };
