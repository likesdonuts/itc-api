"""`python cli.py decide`: settling the analytics pairs that need a person."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from support import DataDirTestCase

import cli
from datalayer.analytics import build, decide, reference
from test_analytics import counsel_of, ids_case, rep


class DecideTestCase(DataDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.reference_path = self.root / "analytics_reference.json"
        self.reference_path.write_text(json.dumps({"firms": {}, "companies": {}, "attorneys": {}}), encoding="utf-8")
        patcher = mock.patch.object(reference, "REFERENCE_PATH", self.reference_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        store = self.store()
        store.investigations = {
            "337-1": ids_case(("Rohm and Haas Electronic Materials LLC", "Respondent"),
                              ("Rohm and Haas Electronic Materials CMP, LLC", "Respondent")),
        }
        store.counsel = counsel_of(**{
            "337-1": [rep("Lippes Mathias LLP", parties=["Rohm and Haas Electronic Materials LLC"]),
                      rep("Lipper Mathias LLP", parties=["Rohm and Haas Electronic Materials CMP, LLC"])],
        })
        store.save_cases()
        store.save_counsel()
        build.run(store, review=False, log=self.quiet)

    def reference(self):
        return json.loads(self.reference_path.read_text(encoding="utf-8"))

    def cli(self, *argv):
        printed = []
        with mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            code = cli.main(["--data-dir", str(self.data_dir), "--site-dir", str(self.site_dir), "decide", *argv])
        return code, "\n".join(printed)

    def number_of(self, kind):
        return next(n for n, item in enumerate(decide.pending(self.data_dir), 1) if item["kind"] == kind)


class TestListing(DecideTestCase):
    def test_it_lists_the_pairs_and_shows_one_in_full(self):
        code, listing = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("2 pair(s) need a person", listing)
        self.assertIn("Lipper Mathias LLP  vs  Lippes Mathias LLP", listing)

        code, detail = self.cli(str(self.number_of("firm")))
        self.assertIn("Is this the same law firm?", detail)
        self.assertIn("a-became-b", detail)
        self.assertIn("model: not asked", detail)

    def test_nothing_to_list_before_the_first_run(self):
        (self.data_dir / "analytics" / "needs_review.json").unlink()
        code, output = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("python cli.py analytics", output)


class TestAnswers(DecideTestCase):
    def test_same_merges_and_the_pair_leaves_the_list(self):
        code, output = self.cli(str(self.number_of("firm")), "same")
        self.assertEqual(code, 0)
        self.assertIn("firms.merge", output)
        self.assertEqual(self.reference()["firms"]["merge"], [["Lipper Mathias LLP", "Lippes Mathias LLP"]])
        firms = json.loads((self.data_dir / "analytics" / "firms.json").read_text(encoding="utf-8"))["firms"]
        self.assertEqual(len(firms), 1)
        self.assertEqual([i["kind"] for i in decide.pending(self.data_dir)], ["company"])

    def test_different_keeps_apart_and_clears_the_pair(self):
        self.cli(str(self.number_of("company")), "different")
        self.assertEqual(
            self.reference()["companies"]["keep_apart"],
            [["Rohm and Haas Electronic Materials LLC", "Rohm and Haas Electronic Materials CMP, LLC"]],
        )
        companies = json.loads((self.data_dir / "analytics" / "companies.json").read_text(encoding="utf-8"))
        self.assertEqual(len(companies["companies"]), 2)
        self.assertNotIn("company", [i["kind"] for i in decide.pending(self.data_dir)])

    def test_a_renamed_firm_is_linked_as_a_predecessor(self):
        self.cli(str(self.number_of("firm")), "a-became-b")
        self.assertEqual(self.reference()["firms"]["predecessors"], {"Lippes Mathias LLP": ["Lipper Mathias LLP"]})
        firms = json.loads((self.data_dir / "analytics" / "firms.json").read_text(encoding="utf-8"))["firms"]
        newer = next(f for f in firms if f["name"] == "Lippes Mathias LLP")
        self.assertEqual(len(newer["predecessors"]), 1)

    def test_an_answer_that_does_not_fit_the_pair_is_refused(self):
        code, output = self.cli(str(self.number_of("company")), "a-became-b")
        self.assertEqual(code, 1)
        self.assertIn("different, same", output)
        self.assertEqual(self.reference()["companies"], {})

    def test_the_same_answer_twice_is_written_once(self):
        item = decide.pending(self.data_dir)[self.number_of("firm") - 1]
        decide.record(item, "same")
        decide.record(item, "same")
        self.assertEqual(len(self.reference()["firms"]["merge"]), 1)


class TestSplits(DecideTestCase):
    def test_a_firm_field_split_by_hand(self):
        item = {"kind": "firm_split", "keys": ["smith jones et al"], "question": ""}
        with self.assertRaises(decide.DecideError):
            decide.record(item, "split", ["Smith LLP"])
        decide.record(item, "split", ["Smith LLP", "Jones PC"])
        self.assertEqual(self.reference()["firms"]["splits"], {"smith jones et al": ["Smith LLP", "Jones PC"]})

        store = self.store()
        store.counsel = counsel_of(**{"337-1": [rep("smith jones et al", parties=["Acme Inc."])]})
        from datalayer.analytics import firms

        result = firms.build(store.counsel, {}, reference.load_reference(), reference.Decisions(path=None))
        self.assertEqual(len(result.firms_of[("337-1", 0)]), 2)


if __name__ == "__main__":
    unittest.main()
