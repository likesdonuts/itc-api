"""Local control panel for the site.

The generated pages are static, so their buttons need something to call.
This serves the repository over localhost and runs the data layer on the
page's behalf:

    POST /api/jobs          start a job: the daily sync, documents for the
                            cases picked on the page, or one claims analysis
    GET  /api/jobs/current  the running (or last) job, with its progress
    GET  /api/status        when the last sync and fetch ran, the token's expiry

A job runs in the background, one at a time, while the page polls for its
progress; syncs and multi-case fetches take minutes, far longer than one
request should hang. The EDIS token is read when a job starts rather than
when the server does, so the site works without one and a renewed token in
.env is picked up without a restart.

It is the only place the layers are wired together at runtime; both still
work on their own from the command line.

    python cli.py serve
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from datalayer import counsel, docs, ingest, ids
from datalayer.client import decode_jwt_exp
from datalayer.config import DATA_DIR, SCHEMA_PATH, SITE_DIR, MissingTokenError, load_token
from datalayer.runner import ProcessAborted
from datalayer.store import STATE_FILE, Store, load_json
from ui.render import render_site

Logger = Callable[[str], None]

MAX_BODY_BYTES = 8 * 1024
MAX_CASES_PER_JOB = 100
# Only the tail of a job's log goes to the page; the console gets all of it.
LOG_LINES_KEPT = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """One run of the data layer on the page's behalf, and what it has said."""

    id: int
    kind: str
    label: str
    state: str = "running"  # running | done
    level: str = "ok"  # ok | warn | error, once done
    message: str = ""
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    lines: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, line: str) -> None:
        with self._lock:
            self.lines.append(str(line))
            del self.lines[:-LOG_LINES_KEPT]

    def finish(self, message: str, level: str = "ok") -> None:
        self.message, self.level = message, level
        self.finished_at = _now()
        self.state = "done"

    def to_dict(self, tail: int = 14) -> dict[str, Any]:
        with self._lock:
            lines = self.lines[-tail:]
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "level": self.level,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "lines": lines,
        }


class JobRefused(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Controller:
    """Runs one data-layer job at a time on behalf of the browser."""

    data_dir: Path = DATA_DIR
    site_dir: Path = SITE_DIR
    schema_path: Path = SCHEMA_PATH
    token_loader: Callable[[], str] = load_token
    log: Logger = print
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _job: Job | None = None
    _thread: threading.Thread | None = None
    _next_id: int = 1

    # -- reading ------------------------------------------------------------

    def current_job(self) -> Job | None:
        return self._job

    def token_status(self) -> dict[str, Any]:
        try:
            token = self.token_loader()
        except MissingTokenError:
            return {"state": "missing"}
        expires = decode_jwt_exp(token)
        if expires is None:
            return {"state": "ok", "expires_at": None}
        state = "expired" if expires <= datetime.now(timezone.utc) else "ok"
        return {"state": state, "expires_at": expires.isoformat()}

    def status(self) -> dict[str, Any]:
        """What the dashboard shows. Reads only state.json, so it stays quick."""
        runs = (load_json(Path(self.data_dir) / STATE_FILE, {}) or {}).get("runs") or {}
        sync = runs.get("ingest") or {}
        fetched = runs.get("documents") or {}
        job = self._job
        return {
            "ok": True,
            "sync": {
                "finished_at": sync.get("finished_at"),
                "snapshot": sync.get("snapshot"),
            },
            "documents": {
                "finished_at": fetched.get("finished_at"),
                "numbers": fetched.get("numbers") or [],
            },
            "token": self.token_status(),
            "job": job.to_dict() if job else None,
        }

    # -- starting -----------------------------------------------------------

    def start_daily(self) -> Job:
        return self._start("daily", "Daily sync", self._run_daily)

    def start_documents(self, numbers: list[str], *, download: bool) -> Job:
        numbers = [str(n).strip() for n in numbers if str(n).strip()]
        if not numbers:
            raise JobRefused(HTTPStatus.BAD_REQUEST, "Pick at least one case first")
        if len(numbers) > MAX_CASES_PER_JOB:
            raise JobRefused(
                HTTPStatus.BAD_REQUEST,
                f"That is {len(numbers)} cases; pick at most {MAX_CASES_PER_JOB} at a time",
            )
        store = Store.load(self.data_dir)
        keys, unknown = docs.resolve_targets(store, numbers)
        if unknown:
            raise JobRefused(
                HTTPStatus.NOT_FOUND,
                f"Not on disk: {', '.join(unknown)}. Run the daily sync first.",
            )
        try:
            token = self.token_loader()
        except MissingTokenError:
            raise JobRefused(
                HTTPStatus.BAD_REQUEST,
                "No EDIS token: add EDIS_TOKEN to the .env file (see the README)",
            ) from None
        if self.token_status()["state"] == "expired":
            raise JobRefused(
                HTTPStatus.UNAUTHORIZED,
                "The EDIS token has expired. Generate a new one at edis.usitc.gov "
                "-> profile -> API Token Generator and put it in .env",
            )

        what = "Fetch documents" if download else "Update document lists"
        label = f"{what}: {keys[0]}" if len(keys) == 1 else f"{what}: {len(keys)} cases"
        return self._start(
            "documents", label, lambda job: self._run_documents(job, token, keys, download)
        )

    def start_claims(self, number: str) -> Job:
        """Create, update or retry one investigation's claims analysis. One
        job runs at a time across the app, so a record never has two.
        """
        store = Store.load(self.data_dir)
        key = store.find_key(str(number or ""))
        if key is None:
            raise JobRefused(HTTPStatus.NOT_FOUND, f"{number} is not on disk. Run the daily sync first.")
        return self._start("claims", f"Claims analysis: {key}", lambda job: self._run_claims(job, key))

    def _start(self, kind: str, label: str, work: Callable[[Job], None]) -> Job:
        if not self._lock.acquire(blocking=False):
            raise JobRefused(HTTPStatus.CONFLICT, "Another job is still running; wait for it to finish")
        job = Job(id=self._next_id, kind=kind, label=label)
        self._next_id += 1
        self._job = job

        def target() -> None:
            try:
                work(job)
            except ProcessAborted as exc:
                job.finish(f"EDIS refused the token: {exc}", "error")
            except ids.IdsError as exc:
                job.finish(f"IDS: {exc}", "error")
            except Exception as exc:  # the page should see why, not a spinner forever
                job.finish(f"{type(exc).__name__}: {exc}", "error")
            finally:
                if job.state != "done":
                    job.finish("Stopped without a result", "error")
                self.log(f"[{job.label}] {job.message}")
                self._lock.release()

        self.log(f"[browser] {label}")
        self._thread = threading.Thread(target=target, name=f"job-{job.id}", daemon=True)
        self._thread.start()
        return job

    def wait(self, timeout: float | None = None) -> None:
        """Block until the current job is done (for tests and scripts)."""
        if self._thread is not None:
            self._thread.join(timeout)

    # -- the jobs -----------------------------------------------------------

    def _logger(self, job: Job) -> Logger:
        def log(line: str) -> None:
            job.log(line)
            self.log(line)

        return log

    def _rebuild(self, store: Store, log: Logger) -> None:
        counsel.run(store, log=log)
        render_site(
            store, data_dir=self.data_dir, site_dir=self.site_dir, schema_path=self.schema_path, log=log
        )

    def _run_daily(self, job: Job) -> None:
        """Today's case data, then the document lists and appearance notices
        of every case whose documents have been collected before.
        """
        log = self._logger(job)
        store = Store.load(self.data_dir)
        report = ingest.run(store, ids_dir=Path(self.data_dir) / "ids", log=log)
        message = (
            f"Case data from {report.snapshot_day}: {len(report.added)} new, "
            f"{len(report.changed)} changed."
        )
        level = "ok"

        targets = store.numbers_with_documents()
        if targets:
            try:
                token = self.token_loader()
            except MissingTokenError:
                token = None
                message += " Documents not refreshed: no EDIS token in .env."
                level = "warn"
            if token:
                log(f"Refreshing documents for {len(targets)} case(s) (appearance PDFs only)...")
                try:
                    fetched = docs.run(
                        store,
                        token,
                        targets,
                        download=True,
                        only_types=docs.APPEARANCE_TYPES,
                        log=log,
                    )
                except ProcessAborted as exc:
                    message += f" Documents not refreshed: {exc}"
                    level = "warn"
                else:
                    message += (
                        f" Documents refreshed for {len(fetched.fetched)} case(s), "
                        f"{fetched.downloaded} new file(s)."
                    )
                    if fetched.failed:
                        message += f" {len(fetched.failed)} skipped."
                        level = "warn"

        self._rebuild(store, log)
        job.finish(message, level)

    def _run_claims(self, job: Job, key: str) -> None:
        from datalayer.claims import build as claims_build

        log = self._logger(job)
        store = Store.load(self.data_dir)
        try:
            analysis = claims_build.run(
                store, key, fetch_pdfs=claims_build.edis_pdf_fetcher(self.token_loader, log), log=log
            )
        finally:
            # A failure is recorded on the analysis, so the page shows it too.
            render_site(
                store, data_dir=self.data_dir, site_dir=self.site_dir, schema_path=self.schema_path, log=log
            )
        events = len(analysis.get("events") or [])
        cost = f" Model cost ${float(analysis.get('cost_usd') or 0):.4f}."
        warnings = analysis.get("warnings") or []
        if analysis.get("outcome") == "no_claims":
            job.finish("Built. No claim information found in the available documents." + cost, "ok")
        elif warnings:
            job.finish(f"Built from {events} claim event(s), with warnings: {'; '.join(warnings)}.{cost}", "warn")
        else:
            job.finish(f"Built from {events} claim event(s).{cost}", "ok")

    def _run_documents(self, job: Job, token: str, keys: list[str], download: bool) -> None:
        log = self._logger(job)
        store = Store.load(self.data_dir)
        report = docs.run(store, token, keys, download=download, log=log)
        self._rebuild(store, log)

        message = f"{len(report.fetched)} of {len(keys)} case(s) updated"
        if download:
            message += f", {report.downloaded} new file(s) downloaded"
        message += "."
        level = "ok"
        if report.failed:
            skipped = "; ".join(f"{r.key}: {r.note}" for r in report.failed[:3])
            message += f" Skipped {skipped}"
            level = "error" if not report.fetched else "warn"
        job.finish(message, level)


class Handler(SimpleHTTPRequestHandler):
    controller: Controller

    def __init__(self, *args: Any, controller: Controller, **kwargs: Any) -> None:
        self.controller = controller
        # The document root is the directory holding site/ and data/, so that
        # the "../../data/documents/..." links on a case page resolve.
        super().__init__(*args, directory=str(controller.site_dir.parent), **kwargs)

    def _endpoint(self) -> str:
        return self.path.split("?", 1)[0].split("#", 1)[0].rstrip("/")

    def do_GET(self) -> None:
        endpoint = self._endpoint()
        if endpoint in ("", "/index.html"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", f"/{self.controller.site_dir.name}/index.html")
            self.end_headers()
            return
        if endpoint == "/api/status":
            self._send_json(HTTPStatus.OK, self.controller.status())
            return
        if endpoint == "/api/jobs/current":
            job = self.controller.current_job()
            self._send_json(HTTPStatus.OK, {"ok": True, "job": job.to_dict() if job else None})
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
        if self._endpoint() != "/api/jobs":
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
            kind = str(payload["kind"])
        except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "message": 'expected {"kind": "daily"} or {"kind": "documents", "numbers": [...]}'},
            )
            return

        try:
            if kind == "daily":
                job = self.controller.start_daily()
            elif kind == "documents":
                numbers = payload.get("numbers")
                if not isinstance(numbers, list):
                    raise JobRefused(HTTPStatus.BAD_REQUEST, "numbers must be a list")
                job = self.controller.start_documents(numbers, download=bool(payload.get("download")))
            elif kind == "claims":
                job = self.controller.start_claims(str(payload.get("number") or ""))
            else:
                raise JobRefused(HTTPStatus.BAD_REQUEST, f"unknown job kind {kind!r}")
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
        data_dir=data_dir,
        site_dir=site_dir,
        schema_path=schema_path,
        log=log,
    )
    httpd = make_server(controller, host, port)
    url = f"http://{host}:{httpd.server_address[1]}/site/index.html"

    log(f"ITC tracker running at {url}")
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
