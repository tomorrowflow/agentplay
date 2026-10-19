"""Local viewer server: the bundled web viewer plus a small read-only JSON API.

Binds to 127.0.0.1 by default. Serves the static viewer from the package's
``viewer/`` directory and these endpoints over the local store:

``GET /api/status``            ``{available, mapCount, latestSessionDate}``
``GET /api/sessions``          paged session rows (``from``, ``to``, ``search``, ``pageSize``, ``cursor``)
``GET /api/map``               one entry per ``session_id`` param, each with a ``url``
``GET /api/map-doc?id=...``    the map document itself
``GET /api/prompt-images?id=...``  per-turn metadata for images pasted into Claude Code prompts (this machine only)
``GET /api/prompt-image?id=...&turn=...&i=...``  one such image, read from the transcript on demand (this machine only)
``GET /api/hook-detail?id=...&uuid=...``  the message and command of one hook failure, read from the transcript on demand (this machine only)
``GET /api/reviews?id=...``    the ``agentplay review`` docs that list this session (this machine only)

Every read needs a trusted request (see ``_Handler._trusted``): a loopback peer
naming this machine in ``Host``, or a ``Host`` the operator listed with
``--allow-host`` (``serve(allowed_hosts=...)``). Bound to another interface with no
allowed host, the server refuses every read. "This machine only" below means the
trusted viewer.

Pasted images are read from the original transcript when asked for and never
stored in a map, the index or an export; see ``prompt_images.py``. Hook failure
messages and commands are read the same way; see ``hook_detail.py``. Review docs
hold PR comment text and are local files written by ``agentplay review``; see
``review.py``.

The server writes nothing and sends nothing off the machine. Every ``POST`` is
answered 405.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from . import factory_digest, hook_detail, prompt_images, review
from .store import Store

MAX_PAGE = 200
DEFAULT_PAGE = 50
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
# Extra origins allowed to read (comma-separated), e.g. the viewer's Vite dev
# server at http://localhost:5173. Nothing else but this server's own origin.
DEV_ORIGINS_ENV = "AGENTPLAY_DEV_ORIGINS"
_FETCH_SITES = {"same-origin", "none"}
_INLINE_SCRIPT_RE = re.compile(rb"<script>(.*?)</script>", re.S)


def viewer_root() -> Path | None:
    """Directory holding the built viewer (``index.html``), or None if not bundled."""
    try:
        root = Path(str(resources.files("agentplay") / "viewer"))
    except (ModuleNotFoundError, TypeError):
        return None
    return root if (root / "index.html").is_file() else None


def _cursor_encode(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def _cursor_decode(raw: str | None) -> int | None:
    """The offset in a cursor from ``_cursor_encode``, 0 for none, None if it is not one."""
    if not raw:
        return 0
    try:
        pad = "=" * (-len(raw) % 4)
        return max(0, int(json.loads(base64.urlsafe_b64decode(raw + pad))["o"]))
    except (ValueError, KeyError, TypeError, OverflowError):  # OverflowError: {"o": 1e999}
        return None


_UUID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")


def _non_negative_int(raw: str) -> int | None:
    """``raw`` as an int if it is 1-9 plain ASCII digits, else None (no sign, space or underscore)."""
    return int(raw) if 0 < len(raw) <= 9 and raw.isascii() and raw.isdigit() else None


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in row.items()
        if k not in ("key", "sourcePath", "sourceMtime", "signals", "imported", "taskTitles")
    }


def _is_loopback(address: Any) -> bool:
    """Whether a peer address belongs to this machine's loopback interface.

    Fails closed: anything unparseable is treated as remote.

    Args:
        address: a ``(host, port, ...)`` tuple as ``client_address`` gives it,
            or a bare host string.

    Returns:
        True for ``127.0.0.0/8``, ``::1`` and IPv4-mapped forms of them.
    """
    host = address[0] if isinstance(address, tuple) and address else address
    try:
        ip = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return False
    return (getattr(ip, "ipv4_mapped", None) or ip).is_loopback


def _hostname(value: str | None) -> str | None:
    """The lowercase hostname a ``Host`` value names (any port), None if malformed.

    Any port, because the viewer's dev server proxies to this one with its own
    Host, and Tailscale Serve forwards its public port. ``Origin`` is held to a
    stricter rule: see ``_Handler._allowed_origin``.
    """
    if not value:
        return None
    try:
        parts = urlsplit(value if "//" in value else f"//{value}")
        host, _ = parts.hostname or "", parts.port  # `.port` raises on a bad or out-of-range port
    except ValueError:
        return None
    return (f"[{host}]" if ":" in host else host) or None


def _origin_authority(origin: str) -> tuple[str, int | None] | None:
    """``(hostname, port)`` of an ``http(s)://host[:port]`` origin, None for anything else (``null`` included)."""
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or parts.path or parts.query or parts.fragment:
        return None
    host = _hostname(parts.netloc)
    return (host, port) if host else None


def _host_authority(value: str | None) -> tuple[str, int | None] | None:
    """``(hostname, port)`` of a ``Host`` value, None if malformed."""
    host = _hostname(value)
    return (host, urlsplit(f"//{value}").port) if host else None


def _with_task_titles(doc: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Label a served map's tasks from the local index.

    Task labels are prompt text, so they live in the index and are merged here, at
    serve time, rather than being built into the map. `store.get` keeps returning a
    map with no prompt text in it, which is what `agentplay export` writes and what
    the format contract promises — see `discover.local_task_titles`.

    Callers must merge only for a trusted request (`_Handler._trusted`): the API
    is unauthenticated, so on a server bound to a non-loopback host this would
    hand the user's prompt text to anyone who can reach the port.

    Only fills a task whose name is empty, so a name the builder derived from a
    named span still wins.
    """
    titles = row.get("taskTitles")
    tasks = doc.get("tasks")
    if not titles or not tasks:
        return doc
    out = []
    for t in tasks:
        turn = t.get("firstTurn")
        if not t.get("name") and isinstance(turn, int) and 1 <= turn <= len(titles):
            label = titles[turn - 1]
            if label:
                t = {**t, "name": label}
        out.append(t)
    return {**doc, "tasks": out}


def _page_csp(html: bytes) -> str:
    """The viewer's Content-Security-Policy, allowing exactly the inline scripts ``html`` carries.

    The only inline script is the theme-before-first-paint snippet in
    ``index.html``; hashing it at serve time keeps the policy right when it changes.
    Styles stay ``'unsafe-inline'``: the UI libraries set ``style`` attributes and
    inject positioning styles. Fonts are bundled, some as ``data:`` URLs in the CSS.
    """
    hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(m).digest()).decode() + "'"
        for m in _INLINE_SCRIPT_RE.findall(html)
    )
    return (
        f"default-src 'self'; script-src 'self' {hashes}".rstrip() + "; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


class _Server(ThreadingHTTPServer):
    # The default backlog of 5 resets connections when the viewer fires a burst
    # of requests (about ten at once on a cold load).
    request_queue_size = 128


class _Handler(BaseHTTPRequestHandler):
    server_version = "agentplay"
    # A client that stalls mid-request gives up its thread instead of holding it forever.
    timeout = 15
    store: Store
    static: Path | None
    allowed_hosts: frozenset[str] = frozenset()
    lock = threading.Lock()

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        return

    # ------------------------------------------------------------------ io
    def end_headers(self) -> None:
        # On every response, http.server's own error pages included.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _json(self, body: Any, status: int = 200) -> None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, content_type: str, data: bytes) -> None:
        """Send a user's image: private, and inert if a browser is steered into rendering it as a page."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json({"success": False, "code": code, "error": message}, status)

    def _static(self, path: str) -> None:
        if self.static is None:
            self._error(404, "not_found", "The viewer is not bundled in this install. Build it with `make viewer`.")
            return
        rel = path.lstrip("/") or "index.html"
        root = self.static.resolve()
        try:
            target = (root / rel).resolve()
            found = target.is_relative_to(root) and target.is_file()
        except (OSError, ValueError):  # e.g. a name too long for the filesystem
            found = False
        if not found:
            target = root / "index.html"  # SPA fallback
        data = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Only an HTML page runs anything, so only it needs the script hashes.
        html = data if target.suffix == ".html" else b""
        self.send_header("Content-Security-Policy", _page_csp(html))
        self.end_headers()
        self.wfile.write(data)

    def _local_viewer(self) -> bool:
        """Whether the request comes from the viewer on this machine.

        A loopback peer is not enough: a DNS-rebinding page reaches 127.0.0.1 under
        its own hostname and reads the answer as same-origin. So the Host must name
        this machine, and so must the Origin whenever a browser sends one.
        """
        origin = self.headers.get("Origin")
        return (_is_loopback(self.client_address) and _hostname(self.headers.get("Host")) in _LOCAL_HOSTS
                and (origin is None or self._allowed_origin(origin)))

    def _trusted(self) -> bool:
        """Whether a read may include the user's own data (titles, task labels, transcripts).

        The local viewer, or a request whose Host is one the operator allowed. The
        peer is not checked for an allowed host: behind Docker it is the bridge
        gateway and behind Tailscale Serve a loopback proxy, so the boundary is
        where the port is published. A rebinding page still sends its own Host.
        """
        if self._local_viewer():
            return True
        if _hostname(self.headers.get("Host")) not in self.allowed_hosts:
            return False
        # Same origin means the port too: with `--allow-host localhost` behind a
        # tunnel, another local web app on another port must not count.
        origin = self.headers.get("Origin")
        return (origin is None or self._allowed_origin(origin)
                or _origin_authority(origin) == _host_authority(self.headers.get("Host")))

    def _allowed_origin(self, origin: str | None) -> bool:
        """Whether ``origin`` is this server's own, or one listed in ``AGENTPLAY_DEV_ORIGINS``.

        Not any local port: another local web app (or anything a page can make
        listen on localhost) would otherwise be trusted too.
        """
        if not origin:
            return False
        port = self.server.server_address[1]
        allowed = {f"http://{h}:{port}" for h in _LOCAL_HOSTS}
        allowed.update(o.strip().rstrip("/").lower() for o in os.environ.get(DEV_ORIGINS_ENV, "").split(","))
        allowed.discard("")
        return origin.strip().lower() in allowed

    # ------------------------------------------------------------------ routes
    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        # Every read is the user's own data (session titles are prompt text, maps
        # name private repos and files), so a DNS-rebinding page gets nothing.
        if not self._trusted():
            return self._error(403, "forbidden", "Only the local viewer can read this.")
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        # A browser labels every fetch with where it came from. The viewer's own
        # calls are same-origin; anything else (an <img> or <script> on another
        # site) gets nothing. Pages stay open to a typed URL or a link.
        site = self.headers.get("Sec-Fetch-Site")
        if url.path.startswith("/api/") and site is not None and site.lower() not in _FETCH_SITES:
            return self._error(403, "forbidden", "Only the local viewer can read this.")
        if url.path.startswith(("/api/", "/card/")):
            # `agentplay view` scans in the background, so the index can change
            # while we are serving. Cheap: one stat unless it actually moved.
            with self.lock:
                self.store.reload_if_changed()
        try:
            if url.path == "/api/status":
                return self._status()
            if url.path == "/api/sessions":
                return self._sessions(q)
            if url.path == "/api/map":
                return self._maps(q)
            if url.path == "/api/map-doc":
                return self._map_doc(q)
            if url.path == "/api/prompt-images":
                return self._prompt_images(q)
            if url.path == "/api/prompt-image":
                return self._prompt_image(q)
            if url.path == "/api/hook-detail":
                return self._hook_detail(q)
            if url.path == "/api/reviews":
                return self._reviews(q)
            if url.path.startswith("/api/"):
                return self._error(404, "not_found", "Unknown endpoint.")
            if url.path.startswith("/card/"):
                return self._card(url.path.removeprefix("/card/"))
            return self._static(url.path)
        except BrokenPipeError:
            return None
        except Exception:  # noqa: BLE001 - never leak a traceback to the page
            return self._error(500, "internal_error", "Something went wrong reading your local sessions.")

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self._error(405, "method_not_allowed", "This server is read-only.")

    def _rows(self) -> list[dict[str, Any]]:
        with self.lock:
            return [r for r in self.store.rows() if r.get("role") not in ("subagent", "helper")]

    def _card(self, card: str) -> None:
        """Redirect to the newest session a factory card ran, found by its ``…-wb-<cardId>`` project dir.

        The card's Claude session ids live only in the gateway, and a card can have
        several; the worktree name is in every one's transcript path.
        """
        ran = [r for r in self._rows() if card and factory_digest.card_id_of(r.get("sourcePath") or "") == card]
        if not ran:
            return self._error(404, "not_found", "No session of this card has been scanned yet.")
        newest = max(ran, key=lambda r: r.get("startedAt") or "")
        self.send_response(302)
        self.send_header("Location", f"/?session={quote(newest['sessionId'], safe='')}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _status(self) -> None:
        rows = self._rows()
        latest = max((r.get("sessionDate") or "" for r in rows), default="") or None
        self._json({"success": True, "available": bool(rows), "mapCount": len(rows), "latestSessionDate": latest})

    def _sessions(self, q: dict[str, list[str]]) -> None:
        rows = self._rows()
        lo = (q.get("from") or [""])[0]
        hi = (q.get("to") or [""])[0]
        search = (q.get("search") or [""])[0].strip().lower()
        try:
            size = min(MAX_PAGE, max(1, int((q.get("pageSize") or [DEFAULT_PAGE])[0])))
        except ValueError:
            size = DEFAULT_PAGE
        out = []
        for r in rows:
            day = r.get("sessionDate") or ""
            if lo and day and day < lo:
                continue
            if hi and day and day > hi:
                continue
            if search:
                hay = " ".join(
                    [str(r.get("sessionTitle") or ""), str(r.get("sessionId") or ""), str(r.get("agentTool") or ""),
                     " ".join(map(str, r.get("repos") or [])), " ".join(map(str, r.get("models") or []))]
                ).lower()
                if search not in hay:
                    continue
            out.append(r)
        start = _cursor_decode((q.get("cursor") or [None])[0])
        if start is None:
            return self._error(400, "invalid_input", "That page cursor is not valid.")
        page = out[start:start + size]
        more = start + size < len(out)
        self._json({
            "success": True,
            "sessions": [_public_row(r) for r in page],
            "nextCursor": _cursor_encode(start + size) if more else None,
            "hasMore": more,
        })

    def _maps(self, q: dict[str, list[str]]) -> None:
        ids = (q.get("session_id") or [])[:20]
        if not ids:
            return self._error(400, "invalid_input", "Pass at least one session_id.")
        index = self.store.index
        maps = []
        for sid in ids:
            row = index.get(sid)
            if row is None:
                maps.append({"sessionId": sid, "status": "not_found"})
                continue
            maps.append({
                "sessionId": sid,
                "url": f"/api/map-doc?id={quote(sid, safe='')}",
                "schemaVersion": 1,
                "contentHash": row.get("contentHash") or sid,
                "bytesGz": None,
            })
        self._json({"success": True, "expiresIn": 3600, "maps": maps})

    def _map_doc(self, q: dict[str, list[str]]) -> None:
        sid = (q.get("id") or [""])[0]
        doc = self.store.get(sid)
        if doc is None:
            return self._error(404, "not_found", "We couldn't find that session.")
        # Task labels are prompt text and the API is unauthenticated: a network
        # client gets the stored map, which carries none.
        if self._trusted():
            doc = _with_task_titles(doc, self.store.index.get(sid) or {})
        self._json(doc)

    def _image_source(self, sid: str) -> str | None:
        """Transcript path for a session whose transcript extras may be read, else None after replying.

        Pasted images and hook failure details are the user's own data, so only
        a trusted viewer gets them. Only a Claude Code session built from a local transcript has any:
        the path comes from the index row alone, never from the request.
        """
        if not self._trusted():
            self._error(403, "forbidden", "Only the local viewer can read this.")
            return None
        if not sid:
            self._error(400, "invalid_input", "Missing session id.")
            return None
        row = self.store.index.get(sid) or {}
        path = row.get("sourcePath")
        if (row.get("imported") or row.get("agentTool") != "claude-code"
                or not isinstance(path, str) or not path.endswith(".jsonl")):
            self._error(404, "not_found", "We couldn't find that session.")
            return None
        return path

    def _prompt_images(self, q: dict[str, list[str]]) -> None:
        path = self._image_source((q.get("id") or [""])[0])
        if path is None:
            return
        try:
            turns = prompt_images.list_images(path)
        except prompt_images.Unavailable as e:
            return self._error(e.status, e.code, e.message)
        self._json({"success": True, "turns": turns})

    def _prompt_image(self, q: dict[str, list[str]]) -> None:
        path = self._image_source((q.get("id") or [""])[0])
        if path is None:
            return
        turn, index = (_non_negative_int((q.get(k) or [""])[0]) for k in ("turn", "i"))
        if turn is None or index is None:
            return self._error(400, "invalid_input", "turn and i must be non-negative integers.")
        try:
            content_type, data = prompt_images.read_image(path, turn, index)
        except prompt_images.Unavailable as e:
            return self._error(e.status, e.code, e.message)
        self._bytes(content_type, data)

    def _hook_detail(self, q: dict[str, list[str]]) -> None:
        path = self._image_source((q.get("id") or [""])[0])
        if path is None:
            return
        uuid = (q.get("uuid") or [""])[0]
        if not _UUID_RE.fullmatch(uuid):
            return self._error(400, "invalid_input", "uuid must be a transcript record id.")
        try:
            message, command = hook_detail.read_hook_detail(path, uuid)
        except prompt_images.Unavailable as e:
            return self._error(e.status, e.code, e.message)
        self._json({"success": True, "message": message, "command": command})

    def _reviews(self, q: dict[str, list[str]]) -> None:
        # Review comments are the user's PR text: the same rule as pasted images
        # and hook details.
        if not self._trusted():
            return self._error(403, "forbidden", "Only the local viewer can read this.")
        sid = (q.get("id") or [""])[0]
        if not sid:
            return self._error(400, "invalid_input", "Missing session id.")
        self._json({"success": True, "reviews": review.reviews_for(self.store, sid)})


def serve(
    port: int = 7317,
    *,
    store: Store | None = None,
    host: str = "127.0.0.1",
    allowed_hosts: frozenset[str] = frozenset(),
    exact_port: bool = False,
) -> ThreadingHTTPServer:
    """Create (but do not start) the local server. Call ``serve_forever()`` on the result.

    Args:
        allowed_hosts: lowercase hostnames (no port) whose requests are trusted
            from any peer. Empty trusts only the viewer on this machine.
        exact_port: raise ``OSError`` when ``port`` is taken instead of picking a
            free one. A service must not drift to a port nothing forwards to.
    """
    handler = type("Handler", (_Handler,), {
        "store": store or Store(), "static": viewer_root(), "allowed_hosts": allowed_hosts,
    })
    try:
        return _Server((host, port), handler)
    except OSError:
        if port == 0 or exact_port:
            raise
        return _Server((host, 0), handler)  # port taken: pick a free one


__all__ = ["serve", "viewer_root", "HTTPStatus"]
