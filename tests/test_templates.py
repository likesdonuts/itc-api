"""UI layer tests: page chrome, dates as rendered, and the row action buttons.

Date parsing itself is covered in test_dates.py; here we only care that the
pages show one consistent format and nothing raw leaks through.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import templates  # noqa: E402

# Anything numeric like 13/01/2026 or 01-13-2026 means a raw feed value
# reached the page instead of going through the formatter.
RAW_DATE_RE = re.compile(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}")


def investigation(number: str, date_initiated: str | None, **extra):
    return {
        "investigation_number": number,
        "title": f"Certain Things {number}",
        "investigation_status": "Active",
        "date_initiated": date_initiated,
        "last_refreshed": "2026-09-18T17:16:49.950756+00:00",
        **extra,
    }


def body_of(html: str) -> str:
    """Strip the <style> and <script> blocks, which contain no page data."""
    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>.*?</script>", "", without_style, flags=re.S)


class TestIndexPage(unittest.TestCase):
    def test_heading_and_title(self):
        html = templates.render_index([investigation("337-1478", "2026-01-13")])
        self.assertIn("<title>ITC 337 Investigations</title>", html)
        self.assertIn("<h1>ITC 337 Investigations</h1>", html)

    def test_strapline_is_gone(self):
        html = templates.render_index([investigation("337-1478", "2026-01-13")])
        self.assertNotIn("Newly filed complaints and their dockets", html)

    def test_dates_render_day_month_year(self):
        html = templates.render_index([investigation("337-1478", "2026-01-13")])
        self.assertIn("13 Jan 2026", html)
        self.assertNotRegex(body_of(html), RAW_DATE_RE)

    def test_legacy_month_first_data_still_renders_correctly(self):
        # Records stored before dates were normalized keep the IDS spelling.
        html = templates.render_index([investigation("337-1478", "01-13-2026")])
        self.assertIn("13 Jan 2026", html)
        self.assertNotRegex(body_of(html), RAW_DATE_RE)

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


class TestRowActions(unittest.TestCase):
    def page(self):
        return templates.render_index(
            [investigation("337-1478", "2026-01-13"), investigation("337-3936", "2026-09-09")]
        )

    def test_each_row_offers_update_and_fetch_docs(self):
        html = self.page()
        for number in ("337-1478", "337-3936"):
            self.assertIn(f'data-action="update" data-number="{number}"', html)
            self.assertIn(f'data-action="fetch-docs" data-number="{number}"', html)

    def test_the_table_has_an_actions_column(self):
        self.assertIn("<th>Actions</th>", self.page())

    def test_a_notice_explains_the_disabled_buttons_when_opened_from_disk(self):
        html = self.page()
        # Hidden by default; the page script reveals it under file://, where
        # there is no server for the buttons to call.
        self.assertIn('<div class="notice" id="offline-notice" hidden>', html)
        self.assertIn("python cli.py serve", html)
        self.assertIn("if (notice) notice.hidden = false;", html)

    def test_the_detail_page_has_no_buttons(self):
        html = templates.render_detail(investigation("337-1478", "2026-01-13"), [])
        self.assertNotIn("data-action=", html)


class TestDetailPage(unittest.TestCase):
    def page(self):
        return templates.render_detail(
            investigation("337-1478", "2026-01-13", investigation_phase="Violation"),
            [
                {
                    "id": "100",
                    "document_type": "Complaint",
                    "title": "Complaint of Acme",
                    "document_date": "2026-01-13",
                    "official_received_date": "2026-01-13T09:00:00",
                    "attachments": [],
                }
            ],
        )

    def test_dates_render_day_month_year(self):
        html = self.page()
        self.assertIn("13 Jan 2026", html)
        self.assertIn("last refreshed 18 Sep 2026", html)

    def test_no_raw_date_shape_survives_anywhere_on_the_page(self):
        self.assertNotRegex(body_of(self.page()), RAW_DATE_RE)


if __name__ == "__main__":
    unittest.main()
