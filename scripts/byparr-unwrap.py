#!/usr/bin/env python3
"""
byparr-unwrap: tiny HTTP proxy that fixes a known Byparr bug.

When Byparr's Camoufox (Firefox) fetches a non-HTML response (JSON, XML,
plain text, etc.), Firefox renders it inside its built-in plaintext viewer
and Byparr returns that wrapper HTML in solution.response instead of the
raw bytes. Prowlarr (and other FlareSolverr-API consumers) then fail to
parse the response.

See upstream issues:
  https://github.com/ThePhaseless/Byparr/issues/353
  https://github.com/ThePhaseless/Byparr/issues/333
  https://github.com/ThePhaseless/Byparr/issues/303

This sidecar sits in front of Byparr. It forwards every request through
unchanged, and on the way back inspects solution.response. If the body
matches the Firefox plaintext-viewer wrapper, it extracts the inner
content and HTML-unescapes it, replacing solution.response with the
unwrapped bytes. All other responses (real HTML, errors) pass through
untouched.

Configuration via env vars:
  UNWRAP_LISTEN_HOST   default 0.0.0.0
  UNWRAP_LISTEN_PORT   default 8193
  UNWRAP_UPSTREAM_URL  default http://127.0.0.1:8192
  UNWRAP_TIMEOUT       default 300 (seconds; Byparr solves can be slow)
  UNWRAP_LOG_LEVEL     default INFO
"""

from __future__ import annotations

import gzip
import html
import json
import logging
import os
import re
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

LISTEN_HOST = os.environ.get("UNWRAP_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("UNWRAP_LISTEN_PORT", "8193"))
UPSTREAM_URL = os.environ.get("UNWRAP_UPSTREAM_URL", "http://127.0.0.1:8192").rstrip(
    "/"
)
TIMEOUT = int(os.environ.get("UNWRAP_TIMEOUT", "300"))
LOG_LEVEL = os.environ.get("UNWRAP_LOG_LEVEL", "INFO").upper()

# Headers we always strip from the upstream response; we recompute
# Content-Length ourselves, and Transfer-Encoding is hop-by-hop.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}

# Signature for the Firefox plaintext viewer wrapper. Camoufox emits it for
# any response Firefox renders in its built-in viewer (JSON, XML, plain
# text, etc.). The stylesheet href is the stable bit.
WRAPPER_RE = re.compile(
    r'^\s*<html><head><link rel="stylesheet" href="resource://content-accessible/plaintext\.css"></head>'
    r"<body><pre>(?P<body>.*)</pre></body></html>\s*$",
    re.DOTALL,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("byparr-unwrap")


def maybe_unwrap(response_text: str) -> tuple[str, bool]:
    """Return (possibly-unwrapped text, did_we_unwrap)."""
    m = WRAPPER_RE.match(response_text)
    if not m:
        return response_text, False
    inner = m.group("body")
    # The wrapper HTML-escapes the original bytes (& -> &amp;, etc.).
    return html.unescape(inner), True


def _decode_body(raw: bytes, encoding: str | None) -> bytes | None:
    """Return decoded bytes, or None if we can't decode."""
    if not encoding or encoding.lower() == "identity":
        return raw
    enc = encoding.lower().strip()
    try:
        if enc == "gzip":
            return gzip.decompress(raw)
        if enc == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        return None
    # br / zstd / unknown: don't try.
    return None


def rewrite_payload(raw: bytes, content_encoding: str | None) -> tuple[bytes, bool]:
    """Parse the upstream JSON, unwrap solution.response if needed.

    Returns (body, rewritten). When rewritten is True the caller must drop
    Content-Encoding because we return identity bytes.
    """
    decoded = _decode_body(raw, content_encoding)
    if decoded is None:
        # Compressed with something we don't support; pass through untouched.
        return raw, False

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw, False

    solution = payload.get("solution") if isinstance(payload, dict) else None
    if not isinstance(solution, dict):
        return raw, False

    response = solution.get("response")
    if not isinstance(response, str):
        return raw, False

    unwrapped, did = maybe_unwrap(response)
    if not did:
        return raw, False

    solution["response"] = unwrapped
    log.info(
        "unwrapped Firefox viewer wrapper for url=%s (%d -> %d bytes)",
        solution.get("url", "?"),
        len(response),
        len(unwrapped),
    )
    return json.dumps(payload).encode("utf-8"), True


def _header(headers: list[tuple[str, str]], name: str) -> str | None:
    name_l = name.lower()
    for k, v in headers:
        if k.lower() == name_l:
            return v
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "byparr-unwrap/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib name
        log.info("%s - %s", self.address_string(), fmt % args)

    def _proxy(self, method: str) -> None:
        url = UPSTREAM_URL + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None

        # Forward request headers minus hop-by-hop / Host.
        fwd_headers = {}
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP or k.lower() == "host":
                continue
            fwd_headers[k] = v

        req = urlrequest.Request(url=url, data=body, method=method, headers=fwd_headers)

        try:
            with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
                status = resp.status
                resp_headers = list(resp.getheaders())
                resp_body = resp.read()
        except HTTPError as e:
            status = e.code
            resp_headers = list(e.headers.items()) if e.headers else []
            resp_body = e.read() or b""
        except URLError as e:
            log.error("upstream connection failed: %s", e)
            self.send_error(502, f"Upstream unreachable: {e.reason}")
            return
        except Exception as e:  # noqa: BLE001
            log.exception("proxy error")
            self.send_error(500, f"Proxy error: {e}")
            return

        body_out, rewritten = rewrite_payload(
            resp_body, _header(resp_headers, "content-encoding")
        )

        self.send_response(status)
        for k, v in resp_headers:
            kl = k.lower()
            if kl in HOP_BY_HOP:
                continue
            if rewritten and kl == "content-encoding":
                # We returned identity bytes; drop the upstream encoding header.
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        if body_out:
            self.wfile.write(body_out)

    def do_GET(self) -> None:  # noqa: N802
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy("DELETE")


def main() -> int:
    log.info(
        "byparr-unwrap listening on %s:%d, forwarding to %s (timeout=%ds)",
        LISTEN_HOST,
        LISTEN_PORT,
        UPSTREAM_URL,
        TIMEOUT,
    )
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
