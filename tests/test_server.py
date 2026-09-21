"""Tests for the local control panel that backs the index page buttons.

The data layer is faked out, so these check the wiring the browser depends
on: which process runs, whether PDFs were asked for, and what the page is
allowed to reach over HTTP.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
from datalayer.records import SyncResult  # noqa: E402
from datalayer.store import Store  # noqa: E402
from datalayer.update import UpdateReport  # noqa: E402


class ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.data_dir = root / "data"
        self.site_dir = root / "site"
        self.data_dir.mkdir()

        store = Store.load(self.data_dir)
        store.put(
            "337-1478",
            {
                "investigation_number": "337-1478",
                "title": "Certain Wearable Devices",
                "investigation_status": "Active",
                "date_initiated": "2026-01-13",
            },
            [],
        )
        store.save_investigations()

        self.calls: list[dict] = []
        self.controller = server.Controller(
            token="fake-token",
            data_dir=self.data_dir,
            site_dir=self.site_dir,
            log=lambda msg: None,
        )
        self.httpd = server.make_server(self.controller, port=0)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def fake_update(self, downloaded: int = 0, ok: bool = True, documents: int = 3):
        def _run(store, token, numbers, **kwargs):
            self.calls.append({"numbers": list(numbers), **kwargs})
            result = (
                SyncResult(
                    key=list(numbers)[0],
                    status="Active",
                    document_count=documents,
                    downloaded=downloaded,
                )
                if ok
                else SyncResult.skipped(list(numbers)[0], "EDIS said no")
            )
            return UpdateReport(requested=list(numbers), results=[result])

        return _run

    def post(self, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/update",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path: str) -> int:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code


class TestUpdateEndpoint(ServerTestCase):
    def test_update_button_skips_pdf_downloads(self):
        with mock.patch("datalayer.update.run", self.fake_update()):
            status, body = self.post({"number": "337-1478", "documents": False})

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.calls[0]["numbers"], ["337-1478"])
        self.assertFalse(self.calls[0]["download"])

    def test_fetch_docs_button_downloads_pdfs(self):
        with mock.patch("datalayer.update.run", self.fake_update(downloaded=2)):
            status, body = self.post({"number": "337-1478", "documents": True})

        self.assertEqual(status, 200)
        self.assertTrue(self.calls[0]["download"])
        self.assertIn("2 new file(s)", body["message"])

    def test_the_site_is_rebuilt_so_the_reload_shows_new_data(self):
        with mock.patch("datalayer.update.run", self.fake_update()):
            self.post({"number": "337-1478", "documents": False})

        index = (self.site_dir / "index.html").read_text()
        self.assertIn("Certain Wearable Devices", index)

    def test_loose_number_spellings_resolve(self):
        with mock.patch("datalayer.update.run", self.fake_update()):
            status, _ = self.post({"number": "337-TA-1478", "documents": False})

        self.assertEqual(status, 200)
        self.assertEqual(self.calls[0]["numbers"], ["337-1478"])

    def test_untracked_numbers_are_refused(self):
        with mock.patch("datalayer.update.run", self.fake_update()):
            status, body = self.post({"number": "337-9999", "documents": False})

        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(self.calls, [])

    def test_a_failed_fetch_is_reported_to_the_browser(self):
        with mock.patch("datalayer.update.run", self.fake_update(ok=False)):
            status, body = self.post({"number": "337-1478", "documents": False})

        self.assertEqual(status, 502)
        self.assertIn("EDIS said no", body["message"])

    def test_malformed_requests_get_a_message_not_a_stack_trace(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/update",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:
                status, body = response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status, body = exc.code, json.loads(exc.read())

        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


class TestStaticServing(ServerTestCase):
    def test_root_redirects_to_the_index(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(response.geturl().endswith("/site/index.html"))

    def test_only_the_site_and_documents_are_reachable(self):
        self.assertEqual(self.get("/.env"), 404)
        self.assertEqual(self.get("/data/investigations.json"), 404)
        self.assertEqual(self.get("/cli.py"), 404)


if __name__ == "__main__":
    unittest.main()
