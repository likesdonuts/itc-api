"""The analytics app's own server, separate from the case tracker's.

    python cli.py analytics-serve        (what ITC Analytics.bat runs)

Serves site_analytics/ on http://127.0.0.1:8766 and runs one job: the
analytics rebuild (entity resolution, the model review of new borderline
pairs, and the page's data), from its Rebuild button:

    POST /api/rebuild        start a rebuild
    GET  /api/jobs/current   the running (or last) rebuild, with its log
    GET  /api/status         when the analytics were built, whether the
                             counsel data has changed since, pairs to review

Nothing in the tracker starts a rebuild. This app does it once a day, the
first time it is opened that day, and whenever the button is pressed. It
only reads the tracker's data (whose files are replaced whole, never
half-written) and writes data/analytics/ and site_analytics/.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from analytics_ui import render as analytics_render
from datalayer.analytics import build as analytics_build
from datalayer.analytics.reference import analytics_dir
from datalayer.config import DATA_DIR
from datalayer.store import COUNSEL_FILE, Store, load_json
from server import Job, JobRefused

Logger = Callable[[str], None]

PORT = 8766


def _local_day(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone().date().isoformat()
    except ValueError:
        return None


@dataclass
class AnalyticsController:
    data_dir: Path = DATA_DIR
    site_dir: Path = analytics_render.SITE_DIR
    log: Logger = print
    today: Callable[[], str] = lambda: datetime.now().astimezone().date().isoformat()
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _job: Job | None = None
    _thread: threading.Thread | None = None
    _next_id: int = 1

    # -- reading ------------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        return load_json(analytics_dir(self.data_dir) / "meta.json", {})

    def needs_daily_build(self) -> bool:
        return _local_day(self.meta().get("built_at")) != self.today()

    def status(self) -> dict[str, Any]:
        meta = self.meta()
        counsel = Path(self.data_dir) / COUNSEL_FILE
        # The tracker has rebuilt counsel since these analytics were made.
        stale = bool(meta.get("built_at")) and counsel.exists() and (
            meta.get("counsel_mtime") is None or counsel.stat().st_mtime > float(meta["counsel_mtime"])
        )
        pending = load_json(analytics_dir(self.data_dir) / "needs_review.json", {}).get("items") or []
        return {
            "ok": True,
            "built_at": meta.get("built_at"),
            "stale": stale,
            "needs_review": len(pending),
            "job": self._job.to_dict() if self._job else None,
        }

    # -- the one job --------------------------------------------------------

    def start_rebuild(self, label: str = "Rebuild analytics") -> Job:
        if not self._lock.acquire(blocking=False):
            raise JobRefused(HTTPStatus.CONFLICT, "A rebuild is already running")
        job = Job(id=self._next_id, kind="rebuild", label=label)
        self._next_id += 1
        self._job = job

        def target() -> None:
            try:
                self._rebuild(job)
            except Exception as exc:  # the page should see why, not a spinner forever
                job.finish(f"{type(exc).__name__}: {exc}", "error")
            finally:
                if job.state != "done":
                    job.finish("Stopped without a result", "error")
                self.log(f"[{job.label}] {job.message}")
                self._lock.release()

        self.log(f"[analytics] {label}")
        self._thread = threading.Thread(target=target, name=f"analytics-{job.id}", daemon=True)
        self._thread.start()
        return job

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _rebuild(self, job: Job) -> None:
        from datalayer.claims.extract import MissingApiKeyError

        def log(line: str) -> None:
            job.log(line)
            self.log(line)

        store = Store.load(self.data_dir)
        level = "ok"
        note = ""
        try:
            report = analytics_build.run(store, review=True, log=log)
        except MissingApiKeyError:
            log("  ! No Anthropic API key in .env: rebuilding without the model review.")
            report = analytics_build.run(store, review=False, log=log)
            level, note = "warn", " New borderline pairs were not reviewed (no API key)."
        analytics_render.render(self.data_dir, self.site_dir, log=log)

        message = (
            f"{report.firms} firms, {report.attorneys} attorneys, {report.companies} companies "
            f"from {report.representations} representations."
        )
        if report.review.calls:
            message += f" {report.review.answered} new pair(s) reviewed for ${report.review.cost:.4f}."
        if report.review.stopped:
            message += f" Review stopped: {report.review.stopped}."
            level = "warn"
        if report.needs_review:
            message += f" {report.needs_review} pair(s) need your review."
        job.finish(message + note, level)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, controller: AnalyticsController, **kwargs: Any) -> None:
        self.controller = controller
        super().__init__(*args, directory=str(controller.site_dir), **kwargs)

    def _endpoint(self) -> str:
        return self.path.split("?", 1)[0].split("#", 1)[0].rstrip("/")

    def do_GET(self) -> None:
        endpoint = self._endpoint()
        if endpoint == "":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return
        if endpoint == "/api/status":
            self._send_json(HTTPStatus.OK, self.controller.status())
            return
        if endpoint == "/api/jobs/current":
            job = self.controller._job
            self._send_json(HTTPStatus.OK, {"ok": True, "job": job.to_dict() if job else None})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self._endpoint() != "/api/rebuild":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "unknown endpoint"})
            return
        try:
            job = self.controller.start_rebuild()
        except JobRefused as exc:
            self._send_json(exc.status, {"ok": False, "message": str(exc)})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "job": job.to_dict()})

    def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def end_headers(self) -> None:
        # data.js changes with every rebuild; never serve a stale copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def make_server(controller: AnalyticsController, host: str = "127.0.0.1", port: int = PORT) -> ThreadingHTTPServer:
    Path(controller.site_dir).mkdir(parents=True, exist_ok=True)
    return ThreadingHTTPServer((host, port), partial(Handler, controller=controller))


def start(controller: AnalyticsController) -> Job | None:
    """What opening the app does: the day's rebuild the first time it is
    opened that day; otherwise just make sure the page exists.
    """
    if controller.needs_daily_build():
        return controller.start_rebuild("Daily rebuild")
    if not (Path(controller.site_dir) / "data.js").exists():
        analytics_render.render(controller.data_dir, controller.site_dir, log=controller.log)
    return None


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = PORT,
    data_dir: Path = DATA_DIR,
    site_dir: Path = analytics_render.SITE_DIR,
    open_browser: bool = True,
    log: Logger = print,
) -> None:
    controller = AnalyticsController(data_dir=data_dir, site_dir=site_dir, log=log)
    httpd = make_server(controller, host, port)
    url = f"http://{host}:{httpd.server_address[1]}/"
    job = start(controller)
    log(f"ITC Analytics running at {url}")
    if job:
        log("First visit today: rebuilding the analytics in the background (the page shows its progress).")
    log("Keep this window open while you use the app; close it (or press Ctrl+C) to stop.")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\nStopped.")
    finally:
        httpd.server_close()
