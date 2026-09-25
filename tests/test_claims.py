"""The claims analysis, phase 1: the rule-based parts, on real passages.

The notice text below is the 337-TA-1366 notice of institution (FR Doc.
2023-14091) as federalregister.gov serves it; the decision passages are in the
shape ALJ orders and Commission notices use, curly quotes included.
"""

from __future__ import annotations

import csv
import threading
import unittest
from pathlib import Path

from support import DataDirTestCase, ids_row, write_snapshot

from datalayer import ingest
from datalayer.claims import build, costs, fedreg, matrix, status
from datalayer.claims import config as claims_config
from datalayer.claims.text import (
    ClaimListError,
    expand_claims,
    find_claim_refs,
    patent_for,
    split_sentences,
)

PATENTS_1366 = ["10,312,335", "8,350,294", "8,404,508", "9,748,347"]

NOTICE_1366 = (
    "[Investigation No. 337-TA-1366] Certain Semiconductor Devices, and Methods of Manufacturing "
    "Same and Products Containing the Same; Institution of Investigation AGENCY: U.S. International "
    "Trade Commission. ACTION: Notice. SUMMARY: Notice is hereby given that a complaint was filed "
    "with the U.S. International Trade Commission on May 26, 2023, under section 337 of the Tariff "
    "Act of 1930, as amended, on behalf of Efficient Power Conversion Corporation of El Segundo, "
    "California. ORDER: (1) Pursuant to subsection (b) of section 337 of the Tariff Act of 1930, as "
    "amended, an investigation be instituted to determine whether there is a violation of subsection "
    "(a)(1)(B) of section 337 in the importation into the United States, or in the sale for "
    "importation, or the sale within the United States after importation of certain products "
    "identified in paragraph (2) by reason of infringement of one or more of claims 1-3 of the '294 "
    "patent; claim 1 of the '508 patent; claims 1-3 of the '347 patent; and claims 1-7 of the '335 "
    "patent, and whether an industry in the United States exists as required by subsection (a)(2) of "
    "section 337; (2) Pursuant to section 210.10(b)(1) of the Commission's Rules of Practice and "
    "Procedure, the plain language description of the accused products is ... By order of the "
    "Commission. Issued: June 27, 2023. Lisa Barton, Secretary to the Commission."
)


class TestSentences(unittest.TestCase):
    def test_legal_abbreviations_do_not_end_a_sentence(self):
        text = (
            "Order No. 32 (Dec. 1, 2023) granted EPC’s motion to terminate the investigation as to "
            "U.S. Patent No. 9,748,347. The Commission determined not to review the ID. See Innoscience, "
            "Inc. v. EPC, No. 24-1234 (Fed. Cir. 2024). John A. Smith testified."
        )
        self.assertEqual(
            split_sentences(text),
            [
                "Order No. 32 (Dec. 1, 2023) granted EPC’s motion to terminate the investigation as to "
                "U.S. Patent No. 9,748,347.",
                "The Commission determined not to review the ID.",
                "See Innoscience, Inc. v. EPC, No. 24-1234 (Fed. Cir. 2024).",
                "John A. Smith testified.",
            ],
        )

    def test_the_inv_abbreviation_and_quotes_after_a_full_stop(self):
        text = "In Inv. No. 337-TA-1366, the ALJ ruled. “The motion is GRANTED.” It is so ORDERED."
        self.assertEqual(
            split_sentences(text),
            ["In Inv. No. 337-TA-1366, the ALJ ruled.", "“The motion is GRANTED.”", "It is so ORDERED."],
        )


class TestClaimLists(unittest.TestCase):
    def test_every_way_a_list_is_written(self):
        self.assertEqual(expand_claims("1-5, 8, and 12"), [1, 2, 3, 4, 5, 8, 12])
        self.assertEqual(expand_claims("1 through 4"), [1, 2, 3, 4])
        self.assertEqual(expand_claims("2–4"), [2, 3, 4])  # en dash
        self.assertEqual(expand_claims("2—4 and 7"), [2, 3, 4, 7])  # em dash
        self.assertEqual(expand_claims("1 and 12-14"), [1, 12, 13, 14])
        self.assertEqual(expand_claims("claims 3"), [3])

    def test_nonsense_is_refused_not_guessed(self):
        for bad in ("5-2", "1-200", "see above", "", "0"):
            with self.assertRaises(ClaimListError, msg=bad):
                expand_claims(bad)

    def test_claim_references_in_a_sentence(self):
        sentence = "infringement of one or more of claims 1-3 of the '294 patent; claim 1 of the '508 patent"
        self.assertEqual([r.verbatim for r in find_claim_refs(sentence)], ["1-3", "1"])
        found = find_claim_refs("The ALJ finds claims 1, 3, and 5 through 7 invalid, but not claim 9.")
        self.assertEqual([r.verbatim for r in found], ["1, 3, and 5 through 7", "9"])


class TestPatents(unittest.TestCase):
    def test_each_reference_takes_the_patent_that_follows_it(self):
        sentence = (
            "infringement of one or more of claims 1-3 of the '294 patent; claim 1 of the '508 patent; "
            "claims 1-3 of the '347 patent; and claims 1-7 of the '335 patent"
        )
        refs = find_claim_refs(sentence)
        self.assertEqual(
            [patent_for(r, sentence, PATENTS_1366) for r in refs],
            ["8,350,294", "8,404,508", "9,748,347", "10,312,335"],
        )

    def test_full_numbers_curly_apostrophes_and_a_number_mid_sentence(self):
        curly = "The ALJ finds that claims 2 and 3 of the ’294 patent are infringed."
        self.assertEqual(patent_for(find_claim_refs(curly)[0], curly, PATENTS_1366), "8,350,294")
        full = "Respondents do not infringe U.S. Patent No. 8,350,294 (“the ’294 patent”) claim 1."
        self.assertEqual(patent_for(find_claim_refs(full)[0], full, PATENTS_1366), "8,350,294")

    def test_a_sentence_that_does_not_say_leaves_it_to_later(self):
        vague = "Claims 4 and 5 are terminated."
        self.assertIsNone(patent_for(find_claim_refs(vague)[0], vague, PATENTS_1366))
        # A short form two of the record's patents share is not guessed.
        clash = "claims 1-2 of the '294 patent"
        self.assertIsNone(patent_for(find_claim_refs(clash)[0], clash, ["8,350,294", "9,111,294"]))


class TestNoticeOfInstitution(unittest.TestCase):
    def notice(self, text=NOTICE_1366):
        return fedreg.Notice("2023-14091", "Certain Semiconductor Devices ...; Institution of Investigation",
                             "2023-07-03", "https://www.federalregister.gov/d/2023-14091", text)

    def test_the_instituted_claims_per_patent(self):
        found = fedreg.instituted_claims(self.notice(), PATENTS_1366)
        self.assertEqual(
            [(f.patent, f.claims) for f in found],
            [("8,350,294", (1, 2, 3)), ("8,404,508", (1,)), ("9,748,347", (1, 2, 3)),
             ("10,312,335", (1, 2, 3, 4, 5, 6, 7))],
        )
        self.assertEqual(found[0].quote, "claims 1-3 of the '294 patent")
        self.assertTrue(all(f.quote in f.sentence for f in found))

    def test_institution_takes_effect_when_issued_not_when_published(self):
        self.assertEqual(fedreg.issued_date(self.notice()), "2023-06-27")

    def test_a_notice_for_another_investigation_is_not_used(self):
        class Fake:
            def search(self, number):
                return [{"document_number": "1", "title": "Certain Widgets; Institution of Investigation"}]

            def text(self, document):
                return "Investigation No. 337-TA-9999 ... claims 1-3 of the '294 patent"

        self.assertEqual(fedreg.institution_notices(Fake(), "337-1366", "Certain Semiconductor Devices"), [])

    def test_investigation_numbers_in_the_form_notices_use(self):
        self.assertEqual(fedreg.ta_number("337-1366"), "337-TA-1366")
        self.assertEqual(fedreg.ta_number("337-TA-1366"), "337-TA-1366")


class FakeFedReg:
    def __init__(self, text=NOTICE_1366, fail=False):
        self.notice_text, self.fail = text, fail

    def search(self, number):
        if self.fail:
            raise fedreg.FedRegError("federalregister.gov returned HTTP 503")
        return [
            {"document_number": "2023-14091", "publication_date": "2023-07-03",
             "html_url": "https://www.federalregister.gov/d/2023-14091",
             "title": "Certain Semiconductor Devices, and Methods of Manufacturing Same and Products "
                      "Containing the Same; Institution of Investigation"},
            {"document_number": "2024-15414", "title": "Certain Semiconductor Devices; Notice of Request"},
        ]

    def text(self, document):
        return self.notice_text


class BuildTestCase(DataDirTestCase):
    def setUp(self):
        super().setUp()
        row = ids_row(
            "337-1366",
            topic="Certain Semiconductor Devices and Products Containing the Same",
            patents=tuple(PATENTS_1366),
            respondents=("Innoscience, Inc", "Innoscience America, Inc."),
        )
        self.store_ = self.store()
        ingest.parse_snapshot(self.store_, write_snapshot(self.ids_dir, [row]), log=self.quiet)
        self.store_.put_documents("337-1366", [
            {"id": "1", "document_type": "Complaint", "title": "Complaint", "security_level": "Public"},
            {"id": "2", "document_type": "Order", "title": "Order No. 32", "security_level": "Public"},
            {"id": "3", "document_type": "Motion", "title": "Motion to Compel", "security_level": "Public"},
            {"id": "4", "document_type": "Order", "title": "Order No. 33", "security_level": "Confidential"},
        ])
        base = claims_config.load()
        from dataclasses import replace

        self.cfg = replace(base, costs_csv=self.data_dir / "claims_costs.csv")

    def build(self, fetcher=None):
        return build.run(self.store_, "337-1366", fetcher=fetcher or FakeFedReg(), config=self.cfg, log=self.quiet)


class TestBuild(BuildTestCase):
    def test_a_build_stores_the_events_and_what_it_read(self):
        analysis = self.build()
        self.assertEqual(analysis["investigation_number"], "337-TA-1366")
        self.assertEqual(analysis["seen_documents"], ["1", "2", "3"])  # public only
        self.assertEqual([s["kind"] for s in analysis["source_documents"]], ["complaint", "alj_order"])
        self.assertEqual(analysis["processed_sources"], ["FR:2023-14091"])
        self.assertEqual(len(analysis["events"]), 4)
        event = analysis["events"][0]
        self.assertEqual((event["action"], event["speaker"], event["method"]), ("instituted", "tribunal_ruling", "rule"))
        self.assertEqual(event["date"], "2023-06-27")
        self.assertEqual(event["source"]["id"], "FR:2023-14091")
        self.assertEqual(build.load(self.data_dir, "337-1366")["ids_hash"], analysis["ids_hash"])

    def test_event_ids_survive_a_rebuild_so_corrections_can_find_them(self):
        first = [e["id"] for e in self.build()["events"]]
        self.assertEqual(first, [e["id"] for e in self.build()["events"]])

    def test_no_notice_means_no_claim_information_not_an_empty_matrix(self):
        analysis = self.build(FakeFedReg(text="Investigation No. 337-TA-1366 nothing about claims"))
        self.assertEqual(analysis["outcome"], "no_claims")

    def test_every_run_logs_its_cost_and_a_failure_keeps_the_last_good_analysis(self):
        good = self.build()
        with self.assertRaises(fedreg.FedRegError):
            self.build(FakeFedReg(fail=True))
        stored = build.load(self.data_dir, "337-1366")
        self.assertEqual(stored["built_at"], good["built_at"])
        self.assertIn("HTTP 503", stored["last_attempt"]["error"])

        with (self.data_dir / "claims_costs.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["investigation_number"] for r in rows], ["337-TA-1366", "337-TA-1366"])
        self.assertTrue(all(r["cost_usd"] == "0.000000" for r in rows))
        self.assertRegex(rows[0]["build_datetime"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class TestState(BuildTestCase):
    def state(self):
        return status.state(
            self.store_.investigations["337-1366"],
            self.store_.documents["337-1366"],
            build.load(self.data_dir, "337-1366"),
            pipeline_version=self.cfg.pipeline_version,
        )

    def test_create_then_up_to_date(self):
        self.assertEqual(self.state()["state"], "create")
        self.build()
        self.assertEqual(self.state()["state"], "up_to_date")

    def test_a_new_public_document_is_new_activity_a_confidential_one_is_not(self):
        self.build()
        docs = self.store_.documents["337-1366"]
        docs.append({"id": "9", "document_type": "Order", "title": "Order No. 40", "security_level": "Confidential"})
        self.assertEqual(self.state()["state"], "up_to_date")
        docs.append({"id": "10", "document_type": "Notice", "title": "Notice", "security_level": "Public"})
        state = self.state()
        self.assertEqual(state["state"], "new_activity")
        self.assertEqual(state["reasons"], ["1 new document"])

    def test_a_changed_ids_record_is_new_activity(self):
        self.build()
        case = self.store_.investigations["337-1366"]
        status.primary_stage(case)["fields"]["target_date"] = "2027-01-01"
        self.assertIn("the IDS record changed", self.state()["reasons"])

    def test_a_failed_attempt_shows_until_a_build_succeeds(self):
        with self.assertRaises(fedreg.FedRegError):
            self.build(FakeFedReg(fail=True))
        self.assertEqual(self.state()["state"], "failed")
        self.build()
        self.assertEqual(self.state()["state"], "up_to_date")


class TestMatrix(unittest.TestCase):
    def event(self, **kw):
        base = {"id": "e1", "action": "instituted", "speaker": "tribunal_ruling", "patent": "8,350,294",
                "claims": [1, 2, 3], "respondents": ["ALL"], "status": "ok", "date": "2023-06-27"}
        return {**base, **kw}

    def test_instituted_claims_fill_the_institution_column(self):
        built = matrix.build({"patents": ["8,350,294"], "events": [self.event()]})
        self.assertEqual(built["counts"]["instituted"], 3)
        self.assertEqual([r["claim"] for r in built["patents"][0]["rows"]], [1, 2, 3])

    def test_arguments_and_unchecked_events_change_nothing_but_still_show(self):
        built = matrix.build({"patents": ["8,350,294"], "events": [
            self.event(id="a", speaker="party_argument", claims=[4]),
            self.event(id="b", status="needs_review", claims=[5]),
        ]})
        rows = {r["claim"]: r for r in built["patents"][0]["rows"]}
        self.assertEqual(built["counts"]["instituted"], 0)
        self.assertTrue(rows[5]["needs_review"])
        self.assertEqual(rows[4]["cells"], {})

    def test_a_respondent_view_ignores_other_respondents_events(self):
        analysis = {"patents": ["8,350,294"], "events": [self.event(respondents=["Innoscience, Inc"])]}
        self.assertEqual(matrix.build(analysis, respondent="Other Co.")["counts"]["instituted"], 0)
        self.assertEqual(matrix.build(analysis, respondent="Innoscience, Inc")["counts"]["instituted"], 3)


class TestCostLog(unittest.TestCase):
    def test_rows_are_appended_never_rewritten_and_the_header_written_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "claims_costs.csv"
            costs.append(path, "337-TA-1366", 0.1842134)
            costs.append(path, "337-TA-1384", 0)
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines()[0], "investigation_number,build_datetime,cost_usd"
            )
            self.assertEqual(path.read_text(encoding="utf-8").count("investigation_number"), 1)
            self.assertAlmostEqual(costs.total(path), 0.184213)
            self.assertFalse(path.with_name("claims_costs.csv.lock").exists())

    def test_concurrent_builds_do_not_interleave_rows(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claims_costs.csv"
            threads = [
                threading.Thread(target=costs.append, args=(path, f"337-TA-{n}", n / 1000)) for n in range(40)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 40)
            self.assertTrue(all(len(r) == 3 and r["cost_usd"] for r in rows))


if __name__ == "__main__":
    unittest.main()
