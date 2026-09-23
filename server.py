"""Local control panel for the site.

The generated pages are static, so the Update / Fetch docs buttons on the
index need something to call. This serves the repository over localhost and
exposes one endpoint that runs the EDIS documents process for one case,
re-renders the site, and reports what changed.

It is the only place the two layers are wired together at runtime; both
still work on their own from the command line.

    python cli.py serve
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from datalayer import counsel, docs
from datalayer.config import DATA_DIR, SCHEMA_PATH, SITE_DIR
from datalayer.runner import ProcessAborted
from datalayer.store import Store
from ui.render import render_site

Logger = Callable[[str], None]

MAX_BODY_BYTES = 8 * 1024


@dataclass
class Controller:
    """Runs one data-layer action at a time on behalf of the browser."""

    token: str
    data_dir: Path = DATA_DIR
    site_dir: Path = SITE_DIR
    schema_path: Path = SCHEMA_PATH
    log: Logger = print
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch_documents(self, number: str, *, documents: bool) -> tuple[int, dict[str, Any]]:
        if not self._lock.acquire(blocking=False):
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "message": "Another fetch is still running; wait for it to finish",
            }
        try:
            store = Store.load(self.data_dir)
            key = store.find_key(number)
            if key is None:
                return HTTPStatus.NOT_FOUND, {
                    "ok": False,
                    "message": f"{number} is not on disk; run 'python cli.py sync' first",
                }

            report = docs.run(store, self.token, [key], download=documents, log=self.log)
            result = report.results[0] if report.results else None
            if result is None or not result.ok:
                note = result.note if result else "no result"
                return HTTPStatus.BAD_GATEWAY, {"ok": False, "message": f"EDIS: {note}"}

            counsel.run(store, log=self.log)
            render_site(
                store,
                data_dir=self.data_dir,
                site_dir=self.site_dir,
                schema_path=self.schema_path,
                log=self.log,
            )

            if documents:
                message = (
                    f"{result.document_count} document(s), "
                    f"{result.downloaded} new file(s) downloaded"
                )
            else:
                message = f"{result.document_count} document(s) listed"
            return HTTPStatus.OK, {
                "ok": True,
                "key": result.key,
                "documents": result.document_count,
                "downloaded": result.downloaded,
                "message": message,
            }
        except ProcessAborted as exc:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "message": str(exc)}
        except Exception as exc:  # the browser should see why, not just a hang
            self.log(f"  ! {type(exc).__name__}: {exc}")
            return HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False,
                "message": f"{type(exc).__name__}: {exc}",
            }
        finally:
            self._lock.release()


class Handler(SimpleHTTPRequestHandler):
    controller: Controller

    def __init__(self, *args: Any, controller: Controller, **kwargs: Any) -> None:
        self.controller = controller
        # The document root is the directory holding site/ and data/, so that
        # the "../../data/documents/..." links on a case page resolve.
        super().__init__(*args, directory=str(controller.site_dir.parent), **kwargs)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"/{self.controller.site_dir.name}/index.html")
            self.end_headers()
            return
        if not self._is_servable(self.path):
            # Nothing else under the document root -- .env above all -- should
            # be reachable over HTTP.
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def _is_servable(self, path: str) -> bool:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        return clean.startswith((f"/{self.controller.site_dir.name}/", "/data/documents/"))

    def do_POST(self) -> None:
        endpoint = self.path.rstrip("/")
        if endpoint not in ("/api/update", f"/{self.controller.site_dir.name}/api/update"):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "unknown endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "bad request body"})
            return

        try:
            payload = json.loads(self.rfile.read(length))
            number = str(payload["number"])
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST, {"ok": False, "message": "expected {number, documents}"}
            )
            return

        documents = bool(payload.get("documents"))
        action = "fetch docs for" if documents else "update"
        self.controller.log(f"[browser] {action} {number}")
        status, body = self.controller.fetch_documents(number, documents=documents)
        self._send_json(status, body)

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def end_headers(self) -> None:
        # The page is re-rendered under the browser's feet; never serve it stale.
        if self.command == "GET":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def make_server(
    controller: Controller, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), partial(Handler, controller=controller))


def serve(
    token: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path = DATA_DIR,
    site_dir: Path = SITE_DIR,
    schema_path: Path = SCHEMA_PATH,
    open_browser: bool = True,
    log: Logger = print,
) -> None:
    controller = Controller(
        token=token,
        data_dir=data_dir,
        site_dir=site_dir,
        schema_path=schema_path,
        log=log,
    )
    httpd = make_server(controller, host, port)
    url = f"http://{host}:{httpd.server_address[1]}/site/index.html"

    log(f"Serving {url}")
    log("The Update and Fetch docs buttons on the list page work while this is running.")
    log("Press Ctrl+C to stop.")

    if open_browser:
        import webbrowser

        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\nStopped.")
    finally:
        httpd.server_close()
