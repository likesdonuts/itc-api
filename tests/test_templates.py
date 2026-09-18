"""UI layer tests: page chrome and date presentation.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import templates  # noqa: E402


class TestDateFormatting(unittest.TestCase):
    def test_every_stored_date_shape_renders_as_ddmmyyyy(self):
        cases = {
            "2026/01/13 09:00:00": "13/01/2026",  # EDIS document date
            "2026-09-18T12:00:00+00:00": "18/09/2026",  # RSS-derived date
            "2026-01-13": "13/01/2026",  # plain ISO date
            "01-13-2026": "13/01/2026",  # IDS start date, US order
            "04-27-2026": "27/04/2026",
            "2026/08/21 16:25:00  (approx., from earliest complaint filing)": "21/08/2026",
        }
        for stored, expected in cases.items():
            with self.subTest(stored=stored):
                self.assertEqual(templates._format_ddmmyyyy(stored), expected)

    def test_missing_dates_say_unknown(self):
        self.assertEqual(templates._format_ddmmyyyy(None), "Unknown")
        self.assertEqual(templates._format_ddmmyyyy(""), "Unknown")

    def test_unparseable_dates_are_shown_as_stored(self):
        self.assertEqual(templates._format_ddmmyyyy("sometime in 2026"), "sometime in 2026")

    def test_day_first_input_is_not_swapped_into_an_impossible_month(self):
        self.assertEqual(templates._format_ddmmyyyy("27-04-2026"), "27/04/2026")


def investigation(number: str, date_initiated: str | None, **extra):
    return {
        "investigation_number": number,
        "title": f"Certain Things {number}",
        "investigation_status": "Active",
        "date_initiated": date_initiated,
        "last_refreshed": "2026-09-18T17:16:49.950756+00:00",
        **extra,
    }


class TestIndexPage(unittest.TestCase):
    def test_heading_and_title(self):
        html = templates.render_index([investigation("337-1478", "01-13-2026")])
        self.assertIn("<title>ITC 337 Investigations</title>", html)
        self.assertIn("<h1>ITC 337 Investigations</h1>", html)

    def test_strapline_is_gone(self):
        html = templates.render_index([investigation("337-1478", "01-13-2026")])
        self.assertNotIn("Newly filed complaints and their dockets", html)

    def test_dates_are_formatted(self):
        html = templates.render_index([investigation("337-1478", "01-13-2026")])
        self.assertIn("13/01/2026", html)
        self.assertNotIn("01-13-2026", html)

    def test_rows_are_ordered_by_real_date_not_by_string(self):
        html = templates.render_index(
            [
                investigation("337-1478", "01-13-2026"),  # IDS format, earliest
                investigation("337-3933", "2026/08/21 16:25:00"),  # EDIS format, latest
                investigation("337-1500", "04-27-2026"),
            ]
        )
        order = [html.index(f"Certain Things {n}") for n in ("337-3933", "337-1500", "337-1478")]
        self.assertEqual(order, sorted(order))


class TestDetailPage(unittest.TestCase):
    def page(self):
        return templates.render_detail(
            investigation("337-1478", "01-13-2026", investigation_phase="Violation"),
            [
                {
                    "id": "100",
                    "document_type": "Complaint",
                    "title": "Complaint of Acme",
                    "document_date": "2026/01/13 00:00:00",
                    "official_received_date": "2026/01/13 09:00:00",
                    "attachments": [],
                }
            ],
        )

    def test_date_initiated_is_formatted(self):
        self.assertIn("13/01/2026", self.page())

    def test_last_refreshed_is_formatted(self):
        html = self.page()
        self.assertIn("last refreshed 18/09/2026", html)
        self.assertNotIn("2026-09-18T17:16:49", html)

    def test_no_raw_date_shapes_survive_anywhere_on_the_page(self):
        html = self.page()
        for raw in ("01-13-2026", "2026/01/13", "2026-09-18T"):
            self.assertNotIn(raw, html)


if __name__ == "__main__":
    unittest.main()
