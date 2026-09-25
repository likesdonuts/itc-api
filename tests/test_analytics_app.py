"""The analytics app: the page's data, and the server that rebuilds it."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from unittest import mock

from support import DataDirTestCase

import analytics_server
from analytics_ui import bundle, render
from datalayer.analytics import build
from test_analytics import counsel_of, ids_case, rep


class AppTestCase(DataDirTestCase):
    def seeded(self):
        store = self.store()
        store.investigations = {
            "337-1": {**ids_case(("Acme Inc.", "Complainant"), ("Globex Corp.", "Respondent"), ("Initech LLC", "Respondent")),
                      "title": "Certain Widgets", "date_initiated": "2024-02-01", "status": "Active"},
            "337-2": {**ids_case(("Globex Corp.", "Complainant"), ("Acme Inc.", "Respondent")),
                      "title": "Certain Gadgets", "date_initiated": "2019-05-01", "status": "Terminated"},
        }
        store.counsel = counsel_of(**{
            "337-1": [rep("Fish & Richardson P.C.", parties=["Acme Inc."], roles=("Complainant",), attorneys=["Ann Lee"]),
                      rep("Kirkland & Ellis LLP", parties=["Globex Corp."], attorneys=["Bo Park"])],
            "337-2": [rep("Kirkland & Ellis LLP", parties=["Globex Corp."], roles=("Complainant",), attorneys=["Bo Park"])],
        })
        for built in store.counsel.values():
            for r in built["representations"]:
                for party in r["parties"]:
                    party["role"] = r["roles"][0]
        store.save_cases()
        store.save_counsel()
        build.run(store, review=False, log=self.quiet)
        return store


class TestBundle(AppTestCase):
    def test_entities_refer_to_each_other_by_position(self):
        self.seeded()
        data = bundle.build(self.data_dir)

        names = [c[0] for c in data["cases"]]
        self.assertEqual(names, ["337-1", "337-2"])
        self.assertEqual(data["cases"][0][1:], ["Certain Widgets", 2024, "Active", 1, None])
        self.assertEqual(data["reps"][0][5:], [2024, 2024])  # the years of the representation's filings
        self.assertEqual(data["cases"][1][4], 0)

        firm = {f["name"]: i for i, f in enumerate(data["firms"])}
        kirkland = [r for r in data["reps"] if firm["Kirkland & Ellis LLP"] in r[1]]
        self.assertEqual(sorted(r[4] for r in kirkland), ["C", "R"])  # respondent in one case, complainant in the other
        globex = next(i for i, c in enumerate(data["companies"]) if c["name"] == "Globex Corp.")
        self.assertEqual(sorted(roles for _, roles in data["companies"][globex]["cases"]), ["C", "R"])
        self.assertEqual(data["meta"]["cases_with_counsel"], 2)

    def test_sides_and_roles(self):
        self.assertEqual(bundle.side_of(["Complainant"]), "C")
        self.assertEqual(bundle.side_of(["Respondent", "Intervenor"]), "R")
        self.assertEqual(bundle.side_of(["Non-Party"]), "N")
        self.assertEqual(bundle.side_of(["Complainant", "Respondent"]), "O")

    def test_no_analytics_yet_says_how_to_make_them(self):
        with self.assertRaises(bundle.NoAnalyticsError):
            bundle.build(self.data_dir)

    def test_render_writes_the_page_and_its_data(self):
        self.seeded()
        render.render(self.data_dir, self.root / "site_analytics", log=self.quiet)
        data_js = (self.root / "site_analytics" / "data.js").read_text(encoding="utf-8")
        self.assertTrue(data_js.startswith("window.ANALYTICS = {"))
        page = (self.root / "site_analytics" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="data.js">', page)
        # Every view the menu offers is one the page can draw.
        for route in ("#/firms", "#/attorneys", "#/companies", "#/cocounsel", "#/disputes", "#/review"):
            self.assertIn(f'href="{route}"', page)
            self.assertIn(f"kind === '{route[2:]}'", page)
        self.assertIn("kind === 'family'", page)


class TestServer(AppTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.site = self.root / "site_analytics"
        self.controller = analytics_server.AnalyticsController(
            data_dir=self.data_dir, site_dir=self.site, log=self.quiet, today=lambda: "2099-01-01"
        )
        self.httpd = analytics_server.make_server(self.controller, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
            return response.status, response.read().decode("utf-8")

    def post(self, path):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=b"", method="POST")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_rebuild_makes_the_analytics_and_the_page(self):
        self.seeded()
        with mock.patch("datalayer.analytics.review.ask", side_effect=AssertionError("asked")):
            status, body = self.post("/api/rebuild")
            self.controller.wait(30)
        # Nothing borderline in this data, so no model call was needed.
        self.assertEqual(status, 202)
        job = json.loads(self.get("/api/jobs/current")[1])["job"]
        self.assertEqual(job["level"], "ok", job["message"])
        self.assertIn("firms", job["message"])
        status, page = self.get("/index.html")
        self.assertIn("ITC Analytics", page)
        self.assertEqual(self.get("/data.js")[0], 200)

    def test_without_an_api_key_it_rebuilds_without_the_review(self):
        from datalayer.claims.extract import MissingApiKeyError

        self.seeded()
        real_run = build.run

        def run(store, *, review=True, **kwargs):
            if review:
                raise MissingApiKeyError("no key")
            return real_run(store, review=False, **kwargs)

        with mock.patch("datalayer.analytics.build.run", run):
            self.post("/api/rebuild")
            self.controller.wait(30)
        job = self.controller._job.to_dict()
        self.assertEqual(job["level"], "warn")
        self.assertIn("no API key", job["message"])

    def test_one_rebuild_at_a_time(self):
        self.seeded()
        release = threading.Event()
        with mock.patch("datalayer.analytics.build.run", lambda *a, **k: release.wait(5) or None):
            first, _ = self.post("/api/rebuild")
            second, body = self.post("/api/rebuild")
            release.set()
            self.controller.wait(10)
        self.assertEqual((first, second), (202, 409))
        self.assertIn("already running", body["message"])

    def test_status_says_when_it_was_built_and_what_waits(self):
        self.seeded()
        status = json.loads(self.get("/api/status")[1])
        self.assertTrue(status["built_at"])
        self.assertEqual(status["needs_review"], 0)
        self.assertFalse(status["stale"])

    def test_it_rebuilds_once_a_day_on_opening(self):
        self.seeded()
        with mock.patch.object(self.controller, "start_rebuild") as rebuild:
            analytics_server.start(self.controller)
        rebuild.assert_called_once()

        built_today = analytics_server.AnalyticsController(
            data_dir=self.data_dir, site_dir=self.site, log=self.quiet,
            today=lambda: analytics_server._local_day(self.controller.meta()["built_at"]),
        )
        with mock.patch.object(built_today, "start_rebuild") as rebuild:
            analytics_server.start(built_today)
        rebuild.assert_not_called()
        self.assertTrue((self.site / "data.js").exists())  # the page is made from what is there


if __name__ == "__main__":
    import unittest

    unittest.main()
