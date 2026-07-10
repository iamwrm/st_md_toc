"""Tests for the uv-managed vscode.dev sideload server."""

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "serve_vscode_dev.py"
SPEC = importlib.util.spec_from_file_location("serve_vscode_dev", str(SCRIPT))
serve_vscode_dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serve_vscode_dev)


class ServeVscodeDevTests(unittest.TestCase):
    def test_selects_vscode_vsix(self):
        release = {
            "tag_name": "v0.1.0",
            "assets": [
                {"name": "MarkdownTOC-st4-v0.1.0.zip"},
                {
                    "name": "markdown-toc-vscode-v0.1.0.vsix",
                    "browser_download_url": "https://example.test/extension.vsix",
                },
            ],
        }
        self.assertEqual(
            serve_vscode_dev.select_vsix_asset(release)["browser_download_url"],
            "https://example.test/extension.vsix",
        )

    def test_rejects_ambiguous_vsix_assets(self):
        with self.assertRaises(RuntimeError):
            serve_vscode_dev.select_vsix_asset({"tag_name": "v1", "assets": []})

    def test_extracts_and_verifies_web_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vsix = root / "extension.vsix"
            with zipfile.ZipFile(str(vsix), "w") as archive:
                archive.writestr(
                    "extension/package.json",
                    json.dumps(
                        {
                            "name": "markdown-toc",
                            "publisher": "iamwrm",
                            "version": "1.0.0",
                            "browser": "./dist/extension.js",
                        }
                    ),
                )
                archive.writestr("extension/dist/extension.js", "exports.activate = () => {};\n")
            extension, manifest = serve_vscode_dev.extract_extension(
                vsix, root / "extracted"
            )
            self.assertEqual(manifest["browser"], "./dist/extension.js")
            self.assertTrue((extension / "dist" / "extension.js").is_file())

    def test_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vsix = root / "unsafe.vsix"
            with zipfile.ZipFile(str(vsix), "w") as archive:
                archive.writestr("../outside.txt", "unsafe")
            with self.assertRaises(RuntimeError):
                serve_vscode_dev.safe_extract_vsix(vsix, root / "extracted")

    def test_network_error_includes_local_vsix_recovery(self):
        with mock.patch.object(
            serve_vscode_dev.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaises(serve_vscode_dev.FriendlyError) as raised:
                serve_vscode_dev.github_json("https://example.test/release")
        self.assertTrue(any("--vsix" in hint for hint in raised.exception.hints))

    def test_occupied_port_suggests_another_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaises(serve_vscode_dev.FriendlyError) as raised:
                serve_vscode_dev.serve(
                    Path("extension"), Path("cert.pem"), Path("key.pem"), port
                )
        self.assertTrue(any("--port" in hint for hint in raised.exception.hints))

    def test_main_prints_recovery_hints(self):
        failure = serve_vscode_dev.FriendlyError(
            "something failed", ["Run this recovery command."]
        )
        stderr = io.StringIO()
        with mock.patch.object(serve_vscode_dev, "run", side_effect=failure):
            with contextlib.redirect_stderr(stderr):
                result = serve_vscode_dev.main([])
        self.assertEqual(result, 1)
        self.assertIn("Run this recovery command.", stderr.getvalue())
        self.assertIn("--help", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
