"""Tests for the shared date vocabulary.

The point of dates.py is that the day/month order comes from knowing the
source, never from inspecting a value, so most of these pin down values that
are ambiguous on their own.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dates  # noqa: E402


class TestParsing(unittest.TestCase):
    def test_edis_documents_are_year_first(self):
        self.assertEqual(dates.parse("2026/09/18 11:39:00"), datetime(2026, 9, 18, 11, 39))
        self.assertEqual(dates.parse("2026/01/05"), datetime(2026, 1, 5))

    def test_iso_values_keep_their_meaning(self):
        self.assertEqual(dates.parse("2026-09-18T12:00:00+00:00"), datetime(2026, 9, 18, 12, 0))
        self.assertEqual(dates.parse("2026-09-18"), datetime(2026, 9, 18))

    def test_ids_values_are_month_first(self):
        # Verified against the live feed: 886 of 886 Section 337 start dates
        # are month-first, so "04-05-2026" is 5 April, not 4 May.
        self.assertEqual(dates.parse("04-05-2026"), datetime(2026, 4, 5))
        self.assertEqual(dates.parse("01-13-2026"), datetime(2026, 1, 13))

    def test_a_month_above_twelve_is_treated_as_day_first(self):
        self.assertEqual(dates.parse("13-01-2026"), datetime(2026, 1, 13))

    def test_two_digit_years_are_this_century(self):
        self.assertEqual(dates.parse("09/18/26"), datetime(2026, 9, 18))

    def test_notes_and_blanks(self):
        self.assertEqual(
            dates.parse("2026/08/21 16:25:00  (approx., from earliest complaint filing)"),
            datetime(2026, 8, 21, 16, 25),
        )
        self.assertIsNone(dates.parse(None))
        self.assertIsNone(dates.parse(""))
        self.assertIsNone(dates.parse("sometime in 2026"))

    def test_impossible_dates_are_rejected(self):
        self.assertIsNone(dates.parse("2026/02/31"))
        self.assertIsNone(dates.parse("2026/13/13"))


class TestNormalizing(unittest.TestCase):
    def test_every_source_shape_becomes_iso(self):
        self.assertEqual(dates.to_iso("01-13-2026"), "2026-01-13")
        self.assertEqual(dates.to_iso("2026/09/18 11:39:00"), "2026-09-18T11:39:00")
        self.assertEqual(dates.to_iso("2026-09-18T12:00:00+00:00"), "2026-09-18T12:00:00")

    def test_normalizing_is_idempotent(self):
        once = dates.to_iso("2026/09/18 11:39:00")
        self.assertEqual(dates.to_iso(once), once)

    def test_unrecognized_values_are_kept_rather_than_dropped(self):
        self.assertEqual(dates.to_iso("sometime in 2026"), "sometime in 2026")
        self.assertIsNone(dates.to_iso(None))

    def test_the_approx_note_is_separated_from_the_date(self):
        stored = "2026/08/21 16:25:00  (approx., from earliest complaint filing)"
        self.assertEqual(dates.to_iso(stored), "2026-08-21T16:25:00")
        self.assertEqual(dates.note_of(stored), "approx., from earliest complaint filing")
        self.assertIsNone(dates.note_of("2026-08-21"))


class TestDisplay(unittest.TestCase):
    def test_dates_render_day_month_year_with_a_named_month(self):
        self.assertEqual(dates.format_ui("2026-01-13"), "13 Jan 2026")
        self.assertEqual(dates.format_ui("01-13-2026"), "13 Jan 2026")
        self.assertEqual(dates.format_ui("2026/09/03 14:56:00"), "03 Sep 2026")

    def test_the_same_day_from_different_sources_renders_identically(self):
        same_day = ["2026-04-05", "04-05-2026", "2026/04/05 09:00:00", "2026-04-05T09:00:00+00:00"]
        self.assertEqual({dates.format_ui(v) for v in same_day}, {"05 Apr 2026"})

    def test_missing_dates_say_unknown(self):
        self.assertEqual(dates.format_ui(None), "Unknown")
        self.assertEqual(dates.format_ui(""), "Unknown")

    def test_sorting_works_across_shapes(self):
        values = ["2026/08/21 16:25:00", "01-13-2026", "2026-09-18T12:00:00+00:00", "04-27-2026"]
        self.assertEqual(
            sorted(values, key=dates.sort_key),
            ["01-13-2026", "04-27-2026", "2026/08/21 16:25:00", "2026-09-18T12:00:00+00:00"],
        )


if __name__ == "__main__":
    unittest.main()
