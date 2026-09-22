"""UI layer tests: page chrome, schema-driven sections, dates, row buttons.

Date parsing itself is covered in test_dates.py; here we only care that the
pages show one consistent format and nothing raw leaks through.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import re
import unittest

from support import ids_row

import schema as ui_schema
from datalayer import cases
from datalayer.config import SCHEMA_PATH
from ui import templates

# Anything numeric like 13/01/2026 or 01-13-2026 means a raw feed value
# reached the page instead of going through the formatter.
RAW_DATE_RE = re.compile(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}")

SCHEMA = ui_schema.load(SCHEMA_PATH)


def case(*rows, number: str = "337-1478") -> dict:
    return cases.build_cases(rows or [ids_row()])[number]


def body_of(html: str) -> str:
    """Strip the <style> and <script> blocks, which contain no page data."""
    without_style = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    return re.sub(r"<script>.*?</script>", "", without_style, flags=re.S)


def stage_block(html: str, anchor: str) -> str:
    """The collapsed <details> block for one stage of a case page."""
    start = html.index(f'<details class="stage" id="{anchor}">')
    return html[start : html.index("</details>", start)]


class TestIndexPage(unittest.TestCase):
    def page(self, *cases_, **kwargs):
        return templates.render_index(list(cases_) or [case()], SCHEMA, **kwargs)

    def test_heading_and_title(self):
        html = self.page()
        self.assertIn("<title>ITC 337 Investigations</title>", html)
        self.assertIn("<h1>ITC 337 Investigations</h1>", html)
        self.assertNotIn("Newly filed complaints and their dockets", html)

    def test_columns_come_from_the_schema(self):
        html = self.page()
        for label in ("Case", "Number", "Instituted", "Stages", "Status"):
            self.assertIn(f"<th>{label}</th>", html)

    def test_a_row_links_to_the_case_page_and_shows_its_status(self):
        html = self.page()
        self.assertIn('href="investigations/337-1478.html"', html)
        self.assertIn("Certain Wearable Devices", html)
        self.assertIn(">Active<", html)

    def test_dates_render_day_month_year(self):
        html = self.page()
        self.assertIn("20 May 2026", html)
        self.assertNotRegex(body_of(html), RAW_DATE_RE)

    def test_rows_are_ordered_newest_first_by_real_date(self):
        html = self.page(
            case(ids_row("337-1478", start_date="01-13-2026")),
            case(ids_row("337-1500", start_date="04-27-2026"), number="337-1500"),
            case(ids_row("337-1521", start_date="09-09-2026"), number="337-1521"),
        )
        order = [html.index(number) for number in ("337-1521", "337-1500", "337-1478")]
        self.assertEqual(order, sorted(order))

    def test_the_document_count_comes_from_the_ui_layer_not_the_case(self):
        html = self.page(case(), document_counts={"337-1478": 7})
        self.assertIn("<th>Docs</th>", html)
        self.assertIn(">7<", html)

    def test_it_says_which_snapshot_the_page_was_built_from(self):
        html = self.page(case(), meta={"snapshot_day": "2026-09-22"})
        self.assertIn("IDS snapshot 22 Sep 2026", html)

    def test_parties_are_searchable_from_the_list(self):
        html = self.page()
        row = re.search(r'data-search="([^"]*)"', html).group(1)
        self.assertIn("acme inc.", row)
        self.assertIn("certain wearable devices", row)

    def test_a_withdrawn_case_is_flagged_searchable_and_filterable(self):
        html = self.page({**case(), "withdrawn": True, "last_listed_snapshot": "2026-09-22"})

        self.assertIn(templates.WITHDRAWN_PILL, html)
        self.assertIn(f'data-status="{templates.WITHDRAWN_LABEL}"', html)
        self.assertIn(f'<option value="{templates.WITHDRAWN_LABEL}">', html)
        self.assertIn(templates.WITHDRAWN_LABEL.lower(), re.search(r'data-search="([^"]*)"', html).group(1))
        # The last known status still shows in the Status column.
        self.assertIn(">Active<", html)


class TestRowActions(unittest.TestCase):
    def page(self):
        return templates.render_index(
            [case(), case(ids_row("337-3936", docket="3936"), number="337-3936")], SCHEMA
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
        self.assertNotIn("data-action=", templates.render_detail(case(), [], SCHEMA))


class TestDetailPage(unittest.TestCase):
    def page(self, built=None, documents=()):
        return templates.render_detail(built or case(), list(documents), SCHEMA)

    def test_case_information_and_parties_come_from_the_schema(self):
        html = self.page()
        self.assertIn("Investigation Information", html)
        self.assertIn(">Certain Wearable Devices</h1>", html)
        self.assertIn("Acme Inc.", html)
        self.assertIn("Globex Corp.", html)
        self.assertIn("Monica Bhattacharyya", html)
        self.assertIn("Patent 10,945,648", html)

    def test_a_field_with_nothing_in_it_is_left_off(self):
        html = self.page()
        self.assertNotIn("Intervenor(s)", html)

    def test_dates_render_day_month_year_and_nothing_raw_survives(self):
        html = self.page()
        self.assertIn("20 May 2026", html)
        self.assertNotRegex(body_of(html), RAW_DATE_RE)

    def test_a_withdrawn_case_says_when_it_was_last_listed(self):
        html = self.page(
            {**case(), "withdrawn": True, "last_listed_snapshot": "2026-09-22"}
        )
        self.assertIn(templates.WITHDRAWN_LABEL, html)
        self.assertIn("last listed\n  this case on 22 Sep 2026", html)

    def test_a_current_case_has_no_withdrawal_notice(self):
        self.assertNotIn(templates.WITHDRAWN_LABEL, self.page())

    def test_a_single_stage_case_has_no_stage_table(self):
        self.assertNotIn("Stages (1)", self.page())

    def test_a_multi_stage_case_lists_and_links_its_stages(self):
        built = cases.build_cases(
            [
                ids_row("337-1478", investigation_id=1, start_date="05-20-2026"),
                ids_row("337-1478", phase="Remand", investigation_id=2, start_date="10-16-2028"),
            ]
        )["337-1478"]
        html = self.page(built)

        self.assertIn("Stages (2)", html)
        self.assertIn('href="#stage-2"', html)
        self.assertIn('<details class="stage" id="stage-1">', html)
        self.assertIn("2 stages", html)

    def test_a_stage_block_shows_only_what_that_stage_says(self):
        built = cases.build_cases(
            [
                ids_row("337-1478", investigation_id=1, start_date="05-20-2026"),
                ids_row(
                    "337-1478",
                    phase="Remand",
                    investigation_id=2,
                    start_date="10-16-2028",
                    respondents=("Initech LLC",),
                ),
            ]
        )["337-1478"]
        remand = stage_block(self.page(built), "stage-2")

        self.assertIn("Initech LLC", remand)
        # Shared with the investigation, so the sections above already say it.
        self.assertNotIn("337-1478", remand)
        self.assertNotIn("Acme Inc.", remand)
        self.assertNotIn("Globex Corp.", remand)

    def test_the_primary_stage_block_says_the_page_above_describes_it(self):
        built = cases.build_cases(
            [
                ids_row("337-1478", investigation_id=1, start_date="05-20-2026"),
                ids_row("337-1478", phase="Remand", investigation_id=2, start_date="10-16-2028"),
            ]
        )["337-1478"]
        primary = stage_block(self.page(built), "stage-1")

        self.assertIn("the sections above describe this stage", primary)
        self.assertNotIn("field-label", primary)

    def test_documents_are_listed_with_their_attachments(self):
        html = self.page(
            documents=[
                {
                    "id": "100",
                    "document_type": "Complaint",
                    "title": "Complaint of Acme",
                    "document_date": "2026-01-13",
                    "official_received_date": "2026-01-13T09:00:00",
                    "filed_by": "A. Lawyer",
                    "on_behalf_of": "Acme Inc.",
                    "attachments": [{"href": "../../data/documents/337-1478/c.pdf", "label": "c.pdf"}],
                }
            ]
        )
        self.assertIn("Documents (1)", html)
        self.assertIn("13 Jan 2026", html)
        self.assertIn('href="../../data/documents/337-1478/c.pdf"', html)

    def test_a_case_with_no_documents_says_how_to_fetch_them(self):
        html = self.page()
        self.assertIn("Documents (0)", html)
        self.assertIn("Fetch docs", html)
        self.assertIn("python cli.py docs", html)


if __name__ == "__main__":
    unittest.main()
