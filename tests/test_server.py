"""Tests for the local control panel that backs the page buttons.

The data layer is faked out, so these check the wiring the browser depends
on: which process a job runs, whether PDFs were asked for, how the page
learns a job's progress, and what the page is allowed to reach over HTTP.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from support import ids_row, write_snapshot

import server  # noqa: E402
from datalayer import ingest  # noqa: E402
from datalayer.config import MissingTokenError  # noqa: E402
from datalayer.docs import DocsReport, DocsResult  # noqa: E402
from datalayer.runner import ProcessAborted  # noqa: E402
from datalayer.store import Store  # noqa: E402
from ui.render import render_site  # noqa: E402


def jwt(expires: datetime) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(expires.timestamp())}).encode())
    return f"e30.{claims.decode().rstrip('=')}.sig"


def no_token() -> str:
    raise MissingTokenError("no token")


class ServerTestCase(unittest.TestCase):
    token = "fake-token"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.data_dir = root / "data"
        self.site_dir = root / "site"
        self.data_dir.mkdir()
        # The document root holds the token file, so the 404 below is the
        # check that nothing outside site/ and data/documents/ is served.
        (root / ".env").write_text("EDIS_TOKEN=secret", encoding="utf-8")

        store = Store.load(self.data_dir)
        snapshot = write_snapshot(self.data_dir / "ids", [ids_row()])
        ingest.parse_snapshot(store, snapshot, log=lambda msg: None)
        # cli.py serve renders before it listens, so the site exists here too.
        render_site(store, site_dir=self.site_dir, log=lambda msg: None)

        self.calls: list[dict] = []
        self.controller = server.Controller(
            data_dir=self.data_dir,
            site_dir=self.site_dir,
            token_loader=self.load_token,
            log=lambda msg: None,
        )
        self.httpd = server.make_server(self.controller, port=0)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def load_token(self) -> str:
        if self.token is None:
            raise MissingTokenError("no token")
        return self.token

    def fake_docs(self, downloaded: int = 0, ok: bool = True, documents: int = 3):
        def _run(store, token, numbers, **kwargs):
            self.calls.append({"numbers": list(numbers), "token": token, **kwargs})
            results = [
                DocsResult(key=n, document_count=documents, downloaded=downloaded)
                if ok
                else DocsResult.skipped(n, "EDIS said no")
                for n in numbers
            ]
            return DocsReport(requested=list(numbers), results=results)

        return _run

    def fake_ingest(self, *, fail: Exception | None = None):
        def _run(store, **kwargs):
            self.calls.append({"ingest": True, **kwargs})
            if fail:
                raise fail
            store.record_run("ingest", snapshot="2026-09-23")
            store.save_state()
            return ingest.IngestReport(cases=1, added=["337-1478"])

        return _run

    def post(self, payload) -> tuple[int, dict]:
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/jobs",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
            return json.loads(response.read())

    def get(self, path: str) -> int:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def run_job(self, payload) -> dict:
        """Start a job, wait for it, and return what the page would poll."""
        status, body = self.post(payload)
        self.assertEqual(status, 202, body)
        self.controller.wait(10)
        return self.get_json("/api/jobs/current")["job"]


class TestDocumentJobs(ServerTestCase):
    def test_fetch_documents_downloads_pdfs_for_every_ticked_case(self):
        with mock.patch("datalayer.docs.run", self.fake_docs(downloaded=2)):
            job = self.run_job({"kind": "documents", "numbers": ["337-1478"], "download": True})

        self.assertEqual(job["state"], "done")
        self.assertEqual(job["level"], "ok")
        self.assertEqual(self.calls[0]["numbers"], ["337-1478"])
        self.assertTrue(self.calls[0]["download"])
        self.assertIn("2 new file(s)", job["message"])

    def test_update_lists_skips_pdf_downloads(self):
        with mock.patch("datalayer.docs.run", self.fake_docs()):
            job = self.run_job({"kind": "documents", "numbers": ["337-1478"], "download": False})

        self.assertFalse(self.calls[0]["download"])
        self.assertEqual(job["label"], "Update document lists: 337-1478")

    def test_the_site_and_counsel_are_rebuilt_so_the_reload_shows_new_data(self):
        with mock.patch("datalayer.docs.run", self.fake_docs()):
            self.run_job({"kind": "documents", "numbers": ["337-1478"]})

        self.assertIn("Certain Wearable Devices", (self.site_dir / "index.html").read_text())
        self.assertTrue((self.data_dir / "counsel.json").exists())

    def test_loose_number_spellings_resolve(self):
        with mock.patch("datalayer.docs.run", self.fake_docs()):
            self.run_job({"kind": "documents", "numbers": ["337-TA-1478"]})

        self.assertEqual(self.calls[0]["numbers"], ["337-1478"])

    def test_untracked_numbers_are_refused_before_anything_runs(self):
        with mock.patch("datalayer.docs.run", self.fake_docs()):
            status, body = self.post({"kind": "documents", "numbers": ["337-9999"]})

        self.assertEqual(status, 404)
        self.assertIn("337-9999", body["message"])
        self.assertEqual(self.calls, [])

    def test_nothing_ticked_is_refused(self):
        status, body = self.post({"kind": "documents", "numbers": []})
        self.assertEqual(status, 400)
        self.assertIn("Pick at least one case", body["message"])

    def test_a_missing_or_expired_token_is_refused_with_a_way_to_fix_it(self):
        self.token = None
        status, body = self.post({"kind": "documents", "numbers": ["337-1478"]})
        self.assertEqual(status, 400)
        self.assertIn("EDIS_TOKEN", body["message"])

        self.token = jwt(datetime.now(timezone.utc) - timedelta(hours=1))
        status, body = self.post({"kind": "documents", "numbers": ["337-1478"]})
        self.assertEqual(status, 401)
        self.assertIn("expired", body["message"])

    def test_a_failed_fetch_is_reported_to_the_page(self):
        with mock.patch("datalayer.docs.run", self.fake_docs(ok=False)):
            job = self.run_job({"kind": "documents", "numbers": ["337-1478"]})

        self.assertEqual(job["level"], "error")
        self.assertIn("EDIS said no", job["message"])

    def test_the_token_is_read_when_the_job_starts_not_when_the_server_did(self):
        self.token = "renewed-token"
        with mock.patch("datalayer.docs.run", self.fake_docs()):
            self.run_job({"kind": "documents", "numbers": ["337-1478"]})
        self.assertEqual(self.calls[0]["token"], "renewed-token")


class TestDailyJob(ServerTestCase):
    def test_it_syncs_then_refreshes_collected_cases_appearances_only(self):
        store = Store.load(self.data_dir)
        store.put_documents("337-1478", [{"id": "1", "title": "Complaint"}])
        store.save_documents()

        with mock.patch("datalayer.ingest.run", self.fake_ingest()), mock.patch(
            "datalayer.docs.run", self.fake_docs()
        ):
            job = self.run_job({"kind": "daily"})

        self.assertEqual(job["level"], "ok", job["message"])
        self.assertTrue(self.calls[0]["ingest"])
        self.assertEqual(self.calls[1]["numbers"], ["337-1478"])
        self.assertTrue(self.calls[1]["download"])
        self.assertEqual(self.calls[1]["only_types"], {"Notice of Appearance"})

    def test_without_a_token_the_case_data_still_updates(self):
        self.token = None
        store = Store.load(self.data_dir)
        store.put_documents("337-1478", [{"id": "1"}])
        store.save_documents()

        with mock.patch("datalayer.ingest.run", self.fake_ingest()), mock.patch(
            "datalayer.docs.run", self.fake_docs()
        ):
            job = self.run_job({"kind": "daily"})

        self.assertEqual(job["level"], "warn")
        self.assertIn("no EDIS token", job["message"])
        self.assertEqual(len(self.calls), 1)

    def test_a_rejected_token_is_a_warning_not_a_lost_sync(self):
        store = Store.load(self.data_dir)
        store.put_documents("337-1478", [{"id": "1"}])
        store.save_documents()

        def refused(*args, **kwargs):
            raise ProcessAborted("EDIS returned 401")

        with mock.patch("datalayer.ingest.run", self.fake_ingest()), mock.patch(
            "datalayer.docs.run", refused
        ):
            job = self.run_job({"kind": "daily"})

        self.assertEqual(job["level"], "warn")
        self.assertIn("401", job["message"])

    def test_a_failed_case_download_still_fetches_the_appearances(self):
        from datalayer.ids import IdsError

        store = Store.load(self.data_dir)
        store.put_documents("337-1478", [{"id": "1", "title": "Complaint"}])
        store.save_documents()

        with mock.patch(
            "datalayer.ingest.run", self.fake_ingest(fail=IdsError("IDS download failed: 502"))
        ), mock.patch("datalayer.docs.run", self.fake_docs()):
            job = self.run_job({"kind": "daily"})

        self.assertEqual(job["level"], "warn")
        self.assertIn("Case data not updated", job["message"])
        self.assertIn("IDS download failed: 502", job["message"])
        docs_calls = [call for call in self.calls if "numbers" in call]
        self.assertEqual(len(docs_calls), 1)
        self.assertEqual(docs_calls[0]["only_types"], {"Notice of Appearance"})

    def test_one_job_at_a_time(self):
        release = threading.Event()

        def slow(store, **kwargs):
            release.wait(5)
            return ingest.IngestReport()

        with mock.patch("datalayer.ingest.run", slow):
            first, _ = self.post({"kind": "daily"})
            second, body = self.post({"kind": "daily"})
            running = self.get_json("/api/jobs/current")["job"]
            release.set()
            self.controller.wait(10)

        self.assertEqual(first, 202)
        self.assertEqual(second, 409)
        self.assertIn("still running", body["message"])
        self.assertEqual(running["state"], "running")


class TestClaimsJob(ServerTestCase):
    def test_it_builds_one_record_and_rebuilds_its_page(self):
        calls = []

        def fake_build(store, key, **kwargs):
            calls.append(key)
            return {"events": [{"id": "e1"}], "outcome": "ok"}

        with mock.patch("datalayer.claims.build.run", fake_build):
            job = self.run_job({"kind": "claims", "number": "337-TA-1478"})

        self.assertEqual(calls, ["337-1478"])
        self.assertEqual(job["label"], "Claims analysis: 337-1478")
        self.assertEqual(job["level"], "ok")
        self.assertIn("1 claim event", job["message"])

    def test_a_failed_build_is_reported_and_the_page_still_rebuilt(self):
        def failing(store, key, **kwargs):
            raise RuntimeError("federalregister.gov returned HTTP 503")

        before = (self.site_dir / "index.html").stat().st_mtime_ns
        with mock.patch("datalayer.claims.build.run", failing):
            job = self.run_job({"kind": "claims", "number": "337-1478"})
        self.assertEqual(job["level"], "error")
        self.assertIn("HTTP 503", job["message"])
        self.assertGreaterEqual((self.site_dir / "index.html").stat().st_mtime_ns, before)

    def test_an_unknown_record_is_refused(self):
        status, body = self.post({"kind": "claims", "number": "337-9999"})
        self.assertEqual(status, 404)


class TestStatus(ServerTestCase):
    def test_it_reports_the_last_sync_and_the_token_expiry(self):
        store = Store.load(self.data_dir)
        store.record_run("ingest", snapshot="2026-09-22")
        store.save_state()
        expires = datetime.now(timezone.utc) + timedelta(days=3)
        self.token = jwt(expires)
        status = self.get_json("/api/status")

        self.assertTrue(status["ok"])
        self.assertEqual(status["sync"]["snapshot"], "2026-09-22")
        self.assertTrue(status["sync"]["finished_at"])
        self.assertEqual(status["token"]["state"], "ok")
        self.assertEqual(status["token"]["expires_at"][:16], expires.isoformat()[:16])
        self.assertIsNone(status["job"])

    def test_a_missing_token_is_reported_not_fatal(self):
        self.token = None
        self.assertEqual(self.get_json("/api/status")["token"]["state"], "missing")


class TestRequests(ServerTestCase):
    def test_malformed_requests_get_a_message_not_a_stack_trace(self):
        status, body = self.post(b"not json")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

        status, body = self.post({"kind": "explode"})
        self.assertEqual(status, 400)
        self.assertIn("unknown job kind", body["message"])


class TestStaticServing(ServerTestCase):
    def test_root_redirects_to_the_index(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.geturl().endswith("/site/index.html"))

    def test_only_the_site_documents_and_api_are_reachable(self):
        self.assertEqual(self.get("/site/index.html"), 200)
        self.assertEqual(self.get("/api/status"), 200)
        self.assertEqual(self.get("/.env"), 404)
        self.assertEqual(self.get("/data/investigations.json"), 404)
        self.assertEqual(self.get("/data/state.json"), 404)
        self.assertEqual(self.get("/data/ids/investigations-2026-09-22.json.gz"), 404)


if __name__ == "__main__":
    unittest.main()
