"use strict";

const vscode = require("vscode");
const {
    fencedCodeBlocks,
    headingSection,
    parseHeadings,
} = require("./markdown");

const VIEW_ID = "markdownToc.outline";

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
