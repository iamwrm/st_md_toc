#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""Download and serve Markdown TOC for sideloading on vscode.dev."""

import argparse
import errno
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import ssl
import stat
import subprocess
import sys
import urllib.parse
import urllib.error
import urllib.request
import webbrowser
import zipfile


REPOSITORY = "iamwrm/md_toc"
GITHUB_API = "https://api.github.com/repos/%s" % REPOSITORY
USER_AGENT = "markdown-toc-vscode-dev-server"


class FriendlyError(RuntimeError):
    """An expected failure with concrete recovery instructions."""

    def __init__(self, message, hints=None):
        super().__init__(message)
        self.hints = list(hints or [])


def print_friendly_error(error):
    print("error: %s" % error, file=sys.stderr)
    for hint in error.hints:
        print("  - %s" % hint, file=sys.stderr)
    print(
        "  - Show all options: uv run --script vscode/serve_vscode_dev.py --help",
        file=sys.stderr,
    )


def default_cache_dir():
    """Return a per-user cache location without requiring platformdirs."""
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "markdown-toc-vscode-dev"
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "markdown-toc-vscode-dev"
    return Path.home() / ".cache" / "markdown-toc-vscode-dev"


def github_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer %s" % token
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        hints = [
            "Check that the release exists: https://github.com/%s/releases" % REPOSITORY,
            "Skip GitHub with a local package: --vsix path/to/extension.vsix",
        ]
        if error.code == 403:
            hints.insert(0, "Set GITHUB_TOKEN if the GitHub API rate limit was exceeded.")
        raise FriendlyError("GitHub release lookup failed (HTTP %d)." % error.code, hints)
    except (urllib.error.URLError, TimeoutError) as error:
        raise FriendlyError(
            "Could not reach GitHub: %s" % getattr(error, "reason", error),
            [
                "Check the network or proxy and retry.",
                "Use an existing download: --vsix path/to/extension.vsix",
            ],
        )


def select_vsix_asset(release):
    """Return the VS Code VSIX asset from a GitHub release response."""
    matches = [
        asset
        for asset in release.get("assets", [])
        if asset.get("name", "").lower().endswith(".vsix")
        and "vscode" in asset.get("name", "").lower()
    ]
    if len(matches) != 1:
        raise FriendlyError(
            "Expected one VS Code VSIX in release %s; found %d."
            % (release.get("tag_name", "(unknown)"), len(matches)),
            [
                "Choose another tag with --release vX.Y.Z.",
                "Use a downloaded package with --vsix path/to/extension.vsix.",
            ],
        )
    return matches[0]


def fetch_release(tag):
    if tag == "latest":
        url = "%s/releases/latest" % GITHUB_API
    else:
        url = "%s/releases/tags/%s" % (GITHUB_API, urllib.parse.quote(tag))
    release = github_json(url)
    return release, select_vsix_asset(release)


def download_file(url, destination, refresh=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and not refresh:
        print("Using cached VSIX: %s" % destination)
        return

    print("Downloading %s" % url)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
        except urllib.error.HTTPError as error:
            raise FriendlyError(
                "VSIX download failed (HTTP %d)." % error.code,
                [
                    "Retry with --refresh.",
                    "Download it in a browser and use --vsix path/to/extension.vsix.",
                ],
            )
        except (urllib.error.URLError, TimeoutError) as error:
            raise FriendlyError(
                "VSIX download failed: %s" % getattr(error, "reason", error),
                [
                    "Check the network or proxy and retry.",
                    "Use an existing download with --vsix path/to/extension.vsix.",
                ],
            )
        temporary.replace(destination)
    except OSError as error:
        raise FriendlyError(
            "Could not write the downloaded VSIX: %s" % error,
            ["Choose a writable location with --cache-dir path/to/cache."],
        )
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def safe_extract_vsix(vsix_path, destination):
    """Extract a VSIX while rejecting traversal paths and symbolic links."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(str(vsix_path)) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                raise RuntimeError("Unsafe path in VSIX: %s" % info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise RuntimeError("Symbolic link in VSIX: %s" % info.filename)
        archive.extractall(str(destination))


def verify_extension(extension_dir):
    manifest_path = extension_dir / "package.json"
    if not manifest_path.is_file():
        raise RuntimeError("VSIX does not contain extension/package.json")
    with manifest_path.open(encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    browser = manifest.get("browser")
    if not isinstance(browser, str) or not browser:
        raise RuntimeError("Extension manifest has no browser entry point")
    relative_browser = browser[2:] if browser.startswith("./") else browser
    browser_entry = (extension_dir / relative_browser).resolve()
    try:
        browser_entry.relative_to(extension_dir.resolve())
    except ValueError:
        raise RuntimeError("Browser entry point escapes the extension directory")
    if not browser_entry.is_file():
        raise RuntimeError("Browser bundle is missing: %s" % browser_entry)
    return manifest


def extract_extension(vsix_path, destination, refresh=False):
    extension_dir = destination / "extension"
    if extension_dir.exists() and not refresh:
        try:
            return extension_dir, verify_extension(extension_dir)
        except (OSError, ValueError, RuntimeError):
            pass
    if destination.exists():
        shutil.rmtree(str(destination))
    safe_extract_vsix(vsix_path, destination)
    return extension_dir, verify_extension(extension_dir)


def find_mkcert():
    executable = shutil.which("mkcert")
    if executable:
        return Path(executable)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        packages = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            matches = list(packages.glob("FiloSottile.mkcert_*/mkcert.exe"))
            if matches:
                return matches[0]
    return None


def install_mkcert():
    """Install mkcert with a supported platform package manager."""
    if sys.platform == "win32" and shutil.which("winget"):
        command = [
            "winget", "install", "--id", "FiloSottile.mkcert", "--exact",
            "--accept-package-agreements", "--accept-source-agreements", "--silent",
        ]
    elif sys.platform == "darwin" and shutil.which("brew"):
        command = ["brew", "install", "mkcert"]
    elif sys.platform.startswith("linux") and shutil.which("apt-get"):
        prefix = [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo"]
        command = prefix + ["apt-get", "install", "-y", "mkcert", "libnss3-tools"]
    else:
        raise FriendlyError(
            "Cannot install mkcert automatically on this system. "
            "No supported package manager was found.",
            [
                "Install mkcert from https://github.com/FiloSottile/mkcert.",
                "Then rerun this command without --install-mkcert.",
            ],
        )
    print("Installing mkcert...")
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise FriendlyError(
            "Automatic mkcert installation failed: %s" % error,
            [
                "Install mkcert manually from https://github.com/FiloSottile/mkcert.",
                "Then rerun this command without --install-mkcert.",
            ],
        )


def ensure_certificate(cache_dir, allow_install=False):
    mkcert = find_mkcert()
    if mkcert is None and allow_install:
        install_mkcert()
        mkcert = find_mkcert()
    if mkcert is None:
        raise FriendlyError(
            "mkcert is required but was not found.",
            [
                "Install it automatically: uv run --script vscode/serve_vscode_dev.py --install-mkcert",
                "Or install it manually from https://github.com/FiloSottile/mkcert.",
            ],
        )

    cert_dir = cache_dir / "certs"
    try:
        cert_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise FriendlyError(
            "Cannot create the certificate directory %s: %s" % (cert_dir, error),
            ["Choose a writable path with --cache-dir path/to/cache."],
        )
    certificate = cert_dir / "localhost.pem"
    private_key = cert_dir / "localhost-key.pem"

    try:
        subprocess.run([str(mkcert), "-install"], check=True)
        if not certificate.is_file() or not private_key.is_file():
            subprocess.run(
                [
                    str(mkcert),
                    "-cert-file", str(certificate),
                    "-key-file", str(private_key),
                    "localhost", "127.0.0.1", "::1",
                ],
                check=True,
            )
    except (OSError, subprocess.CalledProcessError) as error:
        raise FriendlyError(
            "Could not create or trust the localhost certificate: %s" % error,
            [
                "Allow the operating-system trust-store prompt and retry.",
                "Run `mkcert -install` manually to see platform-specific details.",
                "If the cache is read-only, choose another path with --cache-dir.",
            ],
        )
    return certificate, private_key


class CorsRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Static-file handler suitable for vscode.dev sideloading."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(extension_dir, certificate, private_key, port, open_browser=False):
    handler = functools.partial(CorsRequestHandler, directory=str(extension_dir))
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        if (
            error.errno == errno.EADDRINUSE
            or getattr(error, "winerror", None) in (10013, 10048)
        ):
            raise FriendlyError(
                "Port %d is unavailable (usually because it is already in use)." % port,
                [
                    "Use another port: uv run --script vscode/serve_vscode_dev.py --port %d"
                    % (port + 1),
                    "Or stop the process currently listening on port %d." % port,
                ],
            )
        raise FriendlyError(
            "Could not start the HTTPS server on port %d: %s" % (port, error),
            ["Try a non-privileged port such as --port 5001."],
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certfile=str(certificate), keyfile=str(private_key))
    except (OSError, ssl.SSLError) as error:
        server.server_close()
        raise FriendlyError(
            "Could not load the HTTPS certificate: %s" % error,
            [
                "Remove the cached certs directory and retry.",
                "Or choose a fresh cache with --cache-dir path/to/cache.",
            ],
        )
    try:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    except (OSError, ssl.SSLError) as error:
        server.server_close()
        raise FriendlyError(
            "Could not enable HTTPS: %s" % error,
            ["Remove the cached certs directory or choose a fresh --cache-dir."],
        )
    actual_port = server.server_address[1]
    location = "https://localhost:%d" % actual_port

    print()
    print("Markdown TOC is ready for vscode.dev")
    print("  1. Open https://vscode.dev/")
    print("  2. Run: Developer: Install Extension From Location...")
    print("  3. Enter: %s" % location)
    print()
    print("Serving %s" % extension_dir)
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open("https://vscode.dev/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


def port_number(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be a number from 1 to 65535")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be from 1 to 65535")
    return port


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve Markdown TOC over trusted HTTPS for vscode.dev sideloading."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--vsix", type=Path, help="Use a local VSIX instead of downloading one.")
    source.add_argument(
        "--release",
        default="latest",
        help="GitHub release tag to download (default: latest).",
    )
    parser.add_argument(
        "--port", type=port_number, default=5000, help="HTTPS port (default: 5000)."
    )
    parser.add_argument("--cache-dir", type=Path, default=default_cache_dir())
    parser.add_argument("--refresh", action="store_true", help="Redownload and re-extract the VSIX.")
    parser.add_argument(
        "--install-mkcert",
        action="store_true",
        help="Install mkcert with the platform package manager if it is missing.",
    )
    parser.add_argument("--open", action="store_true", help="Open vscode.dev in the default browser.")
    return parser.parse_args(argv)


def run(args):
    cache_dir = args.cache_dir.expanduser().resolve()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise FriendlyError(
            "Cannot create the cache directory %s: %s" % (cache_dir, error),
            ["Choose a writable path with --cache-dir path/to/cache."],
        )
    if args.vsix:
        vsix_path = args.vsix.expanduser().resolve()
        if not vsix_path.is_file():
            raise FriendlyError(
                "VSIX not found: %s" % vsix_path,
                [
                    "Check the file path, or omit --vsix to download the latest release.",
                    "Example: --vsix path/to/markdown-toc-vscode-v0.1.0.vsix",
                ],
            )
        cache_key = vsix_path.stem
    else:
        release, asset = fetch_release(args.release)
        tag = release.get("tag_name") or args.release
        cache_key = tag.replace("/", "-").replace("\\", "-")
        vsix_path = cache_dir / "downloads" / asset["name"]
        download_file(asset["browser_download_url"], vsix_path, args.refresh)

    extracted_dir = cache_dir / "extracted" / cache_key
    try:
        extension_dir, manifest = extract_extension(vsix_path, extracted_dir, args.refresh)
    except FriendlyError:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        raise FriendlyError(
            "Could not extract a usable web extension: %s" % error,
            [
                "Redownload and replace the cache with --refresh.",
                "Or provide a known-good package with --vsix path/to/extension.vsix.",
            ],
        )
    certificate, private_key = ensure_certificate(cache_dir, args.install_mkcert)
    print(
        "Loaded %s.%s v%s"
        % (manifest.get("publisher"), manifest.get("name"), manifest.get("version"))
    )
    serve(extension_dir, certificate, private_key, args.port, args.open)


def main(argv=None):
    try:
        run(parse_args(argv))
    except KeyboardInterrupt:
        return 130
    except FriendlyError as error:
        print_friendly_error(error)
        return 1
    except Exception as error:
        print_friendly_error(
            FriendlyError(
                "Unexpected failure: %s" % error,
                [
                    "Retry with --refresh.",
                    "If it persists, use --vsix with a downloaded release package.",
                ],
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
