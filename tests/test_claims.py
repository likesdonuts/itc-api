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

    def test_ocr_text_that_lost_the_apostrophe_still_names_the_patent(self):
        ocr = "claims 1 and 12-14 of the '511 patent or claims 1 and 3 of the 260 patent"
        patents = ["7,558,260", "7,333,511"]
        refs = find_claim_refs(ocr)
        self.assertEqual([patent_for(r, ocr, patents) for r in refs], ["7,333,511", "7,558,260"])

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


COMPLAINT_TEXT = (
    "VERIFIED COMPLAINT UNDER SECTION 337 OF THE TARIFF ACT OF 1930, AS AMENDED\n"
    "Complainant EPC asserts that Respondents infringe claims 1-3 of the '294 patent.\n"
    "The accused products are gallium nitride transistors.\n"
)
ORDER_TEXT = (
    "ORDER NO. 32: INITIAL DETERMINATION GRANTING COMPLAINANT'S MOTION FOR PARTIAL TERMINATION\n"
    "Complainant moves to terminate the investigation as to claims 1-3 of the '347 patent.\n"
    "Complainant's motion to terminate the investigation as to claims 1-3 of the '347 patent is GRANTED.\n"
)


class FakeModel:
    """Stands in for anthropic.Anthropic. `answer(user_message)` returns the
    events the model "finds"; every call is billed a fixed usage."""

    def __init__(self, answer=None, stop_reason="tool_use", usage=None):
        self.answer = answer or (lambda user: [])
        self.stop_reason = stop_reason
        self.usage = usage or {"input_tokens": 1000, "output_tokens": 200,
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4500}
        self.requests = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                user = kwargs["messages"][0]["content"]
                from types import SimpleNamespace

                reason = outer.stop_reason(user) if callable(outer.stop_reason) else outer.stop_reason
                tool = SimpleNamespace(type="tool_use", input={"events": outer.answer(user)})
                return SimpleNamespace(content=[tool], stop_reason=reason, usage=SimpleNamespace(**outer.usage))

        self.messages = Messages()


def answer_textbook(user):
    """What a good model says about the fixture documents."""
    events = []
    sid = re_sid(user, "asserts that Respondents infringe")
    if sid:
        events.append({"sentence_id": sid, "patent_number": "8,350,294", "claims_verbatim": "1-3",
                       "action": "asserted", "speaker": "party_argument", "respondents": ["ALL"],
                       "quote": "infringe claims 1-3 of the '294 patent"})
    sid = re_sid(user, "is GRANTED")
    if sid:
        events.append({"sentence_id": sid, "patent_number": "9,748,347", "claims_verbatim": "1-3",
                       "action": "withdrawn", "speaker": "tribunal_ruling", "respondents": ["ALL"],
                       "quote": "terminate the investigation as to claims 1-3 of the '347 patent is GRANTED"})
    return events


def re_sid(user, marker):
    """The id of the sentence in this request whose text has `marker`, if any."""
    import re

    for match in re.finditer(r'<sentence id="(S\d+)">(.*?)</sentence>', user, re.S):
        if marker in re.search(r"<text>(.*?)</text>", match.group(2), re.S).group(1):
            return match.group(1)
    return None


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
            {"id": "1", "document_type": "Complaint", "title": "Complaint", "security_level": "Public",
             "document_date": "2023-05-26"},
            {"id": "2", "document_type": "Order", "title": "Order No. 32", "security_level": "Public",
             "document_date": "2023-12-01"},
            {"id": "3", "document_type": "Motion", "title": "Motion to Compel", "security_level": "Public"},
            {"id": "4", "document_type": "Order", "title": "Order No. 33", "security_level": "Confidential"},
        ])
        self.put_text("1", COMPLAINT_TEXT)
        self.put_text("2", ORDER_TEXT)
        base = claims_config.load()
        from dataclasses import replace

        self.cfg = replace(base, costs_csv=self.data_dir / "claims_costs.csv")
        self.model = FakeModel()

    def put_text(self, doc_id, text):
        """A source document's PDF on disk, with its text already extracted."""
        pdf = self.data_dir / "documents" / "337-1366" / f"{doc_id}_1_1.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4")
        from datalayer.claims import candidates

        cached = candidates.cache_path(self.data_dir / "claims" / "text", pdf)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")

    def build(self, fetcher=None, **kwargs):
        kwargs.setdefault("model_client", self.model)
        return build.run(
            self.store_, "337-1366", fetcher=fetcher or FakeFedReg(), config=self.cfg, log=self.quiet, **kwargs
        )


class TestBuild(BuildTestCase):
    def test_a_build_stores_the_events_and_what_it_read(self):
        analysis = self.build()
        self.assertEqual(analysis["investigation_number"], "337-TA-1366")
        self.assertEqual(analysis["seen_documents"], ["1", "2", "3"])  # public only
        self.assertEqual([s["kind"] for s in analysis["source_documents"]], ["complaint", "alj_order"])
        self.assertEqual(analysis["processed_sources"], ["1", "2", "FR:2023-14091"])
        self.assertEqual(len(analysis["events"]), 4)  # the fake model found nothing
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
        # Two model calls (complaint, order) at 1,000 in + 4,500 cached + 200 out on Haiku.
        self.assertEqual(rows[0]["cost_usd"], "0.004900")
        self.assertEqual(rows[1]["cost_usd"], "0.000000")  # failed before any model call
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


class TestCandidates(unittest.TestCase):
    cfg = claims_config.load()

    def test_rulings_are_kept_arguments_and_claimless_sentences_are_not(self):
        text = (
            "Respondents argue that claims 2 and 3 of the '294 patent are obvious. "
            "The ALJ finds that claims 2 and 3 of the '294 patent are not invalid. "
            "The '294 patent issued in 2013. "
            "Complainant's motion to terminate claim 4 is GRANTED."
        )
        from datalayer.claims import candidates

        found = candidates.select(text, "9", kind="initial_determination", cfg=self.cfg)
        self.assertEqual(
            [c.sentence for c in found],
            ["The ALJ finds that claims 2 and 3 of the '294 patent are not invalid.",
             "Complainant's motion to terminate claim 4 is GRANTED."],
        )
        self.assertEqual(found[0].context, "Respondents argue that claims 2 and 3 of the '294 patent are obvious.")

    def test_capitalized_keywords_must_appear_in_capitals(self):
        from datalayer.claims import candidates

        text = "Complainant asks that its motion as to claim 4 be granted by the ALJ soon."
        self.assertEqual(candidates.select(text, "9", kind="alj_order", cfg=self.cfg), [])

    def test_the_parties_positions_are_skipped_until_the_analysis(self):
        from datalayer.claims import candidates

        text = "\n".join([
            "a) Respondents' Position",
            "Respondents contend the ALJ finds claims 1-3 invalid.",
            "b) Analysis",
            "The ALJ finds that claims 1-3 are not invalid.",
        ])
        found = candidates.select(text, "9", kind="initial_determination", cfg=self.cfg)
        self.assertEqual([c.sentence for c in found], ["The ALJ finds that claims 1-3 are not invalid."])

    def test_a_complaint_is_read_but_its_claim_charts_and_exhibits_are_not(self):
        from datalayer.claims import candidates

        self.assertTrue(candidates.is_complaint_body(COMPLAINT_TEXT))
        self.assertFalse(candidates.is_complaint_body("EXHIBIT 12\nClaim Chart for the '294 patent"))
        self.assertFalse(candidates.is_complaint_body("Infringement claim chart: claim 1 of the '294 patent"))


class TestCost(unittest.TestCase):
    cfg = claims_config.load()

    def test_every_kind_of_token_at_its_own_rate(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                 "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
        # Haiku 4.5: $1 input + $5 output + $1.25 5-minute cache write + $0.10 cache read.
        self.assertAlmostEqual(self.cfg.cost("claude-haiku-4-5-20251001", usage), 7.35)
        self.assertAlmostEqual(self.cfg.cost("claude-haiku-4-5-20251001", usage, batch=True), 3.675)
        # Sonnet 5 (the optional second pass): $2 + $10 + $2.50 + $0.20.
        self.assertAlmostEqual(self.cfg.cost("claude-sonnet-5", usage), 14.70)

    def test_one_hour_cache_writes_are_priced_as_such(self):
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 1_000_000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1_000_000}}
        self.assertAlmostEqual(self.cfg.cost("claude-haiku-4-5-20251001", usage), 2.00)

    def test_a_model_without_prices_is_an_error_not_free(self):
        with self.assertRaises(claims_config.ClaimsConfigError):
            self.cfg.cost("claude-unknown", {"input_tokens": 1})


class TestExtraction(BuildTestCase):
    def test_the_model_is_forced_to_the_tool_with_a_cached_system_prompt(self):
        model = FakeModel(answer_textbook)
        self.build(model_client=model)
        request = model.requests[0]
        self.assertEqual(request["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(request["tool_choice"], {"type": "tool", "name": "record_claim_events"})
        self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertTrue(request["tools"][0]["strict"])
        user = request["messages"][0]["content"]
        self.assertIn("Patents (write exactly as listed): 10,312,335; 8,350,294; 8,404,508; 9,748,347", user)
        self.assertIn("Respondents (write exactly as listed): Innoscience, Inc; Innoscience America, Inc.", user)

    def test_the_complaint_is_read_first_and_the_matrix_narrows(self):
        analysis = self.build(model_client=FakeModel(answer_textbook))
        self.assertEqual(analysis["model_calls"], 2)
        by_action = {e["action"]: e for e in analysis["events"] if e["method"] == "haiku"}
        self.assertEqual(by_action["asserted"]["stage"], "asserted")
        self.assertEqual(by_action["asserted"]["claims"], [1, 2, 3])
        self.assertEqual(by_action["withdrawn"]["stage"], "hearing")
        self.assertEqual(by_action["withdrawn"]["status"], "ok")

        built = matrix.build(analysis)
        rows = {g["patent"]: g for g in built["patents"]}
        self.assertEqual(rows["8,350,294"]["counts"]["asserted"], 3)
        self.assertEqual([r["cells"]["hearing"]["status"] for r in rows["9,748,347"]["rows"]], ["withdrawn"] * 3)

    def test_an_update_reads_only_documents_it_has_not_read(self):
        model = FakeModel(answer_textbook)
        first = self.build(model_client=model)
        calls = len(model.requests)
        again = self.build(model_client=model)
        self.assertEqual(len(model.requests), calls)  # nothing new, no model calls
        self.assertEqual(sorted(e["id"] for e in again["events"]), sorted(e["id"] for e in first["events"]))

        self.store_.documents["337-1366"].append(
            {"id": "5", "document_type": "Order", "title": "Order No. 40", "security_level": "Public"}
        )
        self.put_text("5", "The investigation is terminated as to claim 1 of the '508 patent. It is so ORDERED.")
        self.build(model_client=model)
        self.assertEqual(len(model.requests), calls + 1)

    def test_corrections_survive_an_update(self):
        self.build(model_client=FakeModel(answer_textbook))
        stored = build.load(self.data_dir, "337-1366")
        stored["corrections"] = [{"event": stored["events"][-1]["id"], "note": "checked by hand"}]
        from datalayer.store import save_json

        save_json(build.analysis_path(self.data_dir, "337-1366"), stored)
        self.assertEqual(self.build(model_client=FakeModel(answer_textbook))["corrections"], stored["corrections"])

    def test_missing_pdfs_are_fetched_and_unreadable_ones_left_for_later(self):
        self.store_.documents["337-1366"].append(
            {"id": "6", "document_type": "ID/RD - Final on Violation", "title": "Final ID", "security_level": "Public"}
        )
        asked = []
        analysis = self.build(fetch_pdfs=lambda store, key, ids: asked.append(sorted(ids)))
        self.assertEqual(asked, [["6"]])
        self.assertEqual(analysis["pending_sources"], ["6"])
        state = status.state(self.store_.investigations["337-1366"], self.store_.documents["337-1366"],
                             analysis, pipeline_version=self.cfg.pipeline_version)
        self.assertIn("1 source document not read yet", state["reasons"])

    def test_the_budget_stops_a_build_before_the_call_that_would_break_it(self):
        from dataclasses import replace

        self.cfg = replace(self.cfg, budget_usd=0.001)
        model = FakeModel(answer_textbook)
        analysis = self.build(model_client=model)
        self.assertEqual(model.requests, [])
        self.assertEqual(sorted(analysis["pending_sources"]), ["1", "2"])
        self.assertTrue(any("budget" in w for w in analysis["warnings"]))

    def test_a_failed_run_still_logs_what_its_calls_cost(self):
        class Boom(FakeModel):
            pass

        calls = {"n": 0}

        def answer(user):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("API overloaded")
            return []

        with self.assertRaises(RuntimeError):
            self.build(model_client=FakeModel(answer))
        with (self.data_dir / "claims_costs.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        # The first call's tokens were billed; the second raised before its reply.
        self.assertEqual(rows[-1]["cost_usd"], "0.002450")

    def test_a_reply_cut_off_at_the_output_limit_is_retried_in_halves(self):
        self.store_.documents["337-1366"] = [self.store_.documents["337-1366"][1]]  # the order only
        stops = iter(["max_tokens", "tool_use", "tool_use"])
        model = FakeModel(answer_textbook, stop_reason=lambda user: next(stops))
        self.put_text("2", ORDER_TEXT + "The investigation is terminated as to claim 1 of the '508 patent. It is ORDERED.\n")
        self.build(model_client=model)
        self.assertEqual(len(model.requests), 3)


class TestOutputChecks(unittest.TestCase):
    def check(self, raw, sentence="The ALJ finds that claims 2 and 3 of the ’294 patent are infringed.", context=""):
        from datalayer.claims import validate
        from datalayer.claims.candidates import Candidate

        candidate = Candidate("S1", "77", sentence, context)
        base = {"sentence_id": "S1", "patent_number": "8,350,294", "claims_verbatim": "2 and 3",
                "action": "found_infringed", "speaker": "tribunal_ruling", "respondents": ["ALL"],
                "quote": "claims 2 and 3 of the ’294 patent are infringed"}
        events, stats = validate.to_events(
            [{**base, **raw}], [candidate],
            document={"id": "77", "document_type": "ID/RD - Final on Violation", "title": "Final ID",
                      "document_date": "2024-07-09"},
            kind="initial_determination", patents=PATENTS_1366,
            respondents=["Innoscience, Inc", "Innoscience America, Inc."], model="claude-haiku-4-5-20251001",
        )
        return events, stats

    def test_a_clean_event_is_ok_and_its_claims_come_from_code(self):
        events, _ = self.check({})
        self.assertEqual(events[0]["status"], "ok")
        self.assertEqual(events[0]["claims"], [2, 3])
        self.assertEqual(events[0]["stage"], "final_id")
        self.assertEqual(events[0]["date"], "2024-07-09")

    def test_each_failed_check_sends_the_event_to_review(self):
        cases = {
            "quote": {"quote": "claims 2 and 3 are infringed by everything"},
            "claim list": {"claims_verbatim": "3-2"},
            "not written that way": {"claims_verbatim": "2-3"},
            "not on the record's list": {"patent_number": "9,999,999"},
            "respondent": {"respondents": ["Somebody Else Ltd."]},
        }
        for expected, raw in cases.items():
            events, stats = self.check(raw)
            self.assertEqual(events[0]["status"], "needs_review", expected)
            self.assertTrue(any(expected in note for note in events[0]["notes"]), (expected, events[0]["notes"]))
            self.assertEqual(stats["needs_review"], 1)

    def test_an_unknown_patent_is_resolved_by_rule_when_the_text_says(self):
        events, _ = self.check({"patent_number": "unknown"})
        self.assertEqual((events[0]["patent"], events[0]["status"]), ("8,350,294", "ok"))
        events, _ = self.check(
            {"patent_number": "unknown", "claims_verbatim": "1 through 4", "quote": "claims 1 through 4 are invalid"},
            sentence="Summary determination that claims 1 through 4 are invalid is GRANTED.",
            context="Respondents move for summary determination of invalidity of the '335 patent.",
        )
        self.assertEqual(events[0]["patent"], "10,312,335")

    def test_respondents_are_matched_to_the_record_and_mentions_are_dropped(self):
        events, _ = self.check({"respondents": ["innoscience america, inc."]})
        self.assertEqual(events[0]["respondents"], ["Innoscience America, Inc."])
        events, stats = self.check({"action": "mention_only"})
        self.assertEqual((events, stats["mention_only"]), ([], 1))


class TestNarrowing(unittest.TestCase):
    """337-TA-1366 as the record has it: the '347 and '335 claims terminated
    before the hearing, and a violation on '294 claims 2 and 3 but not 1."""

    def event(self, eid, action, stage, patent, claims, date, **kw):
        return {"id": eid, "action": action, "stage": stage, "patent": patent, "claims": claims,
                "respondents": ["ALL"], "status": "ok", "date": date,
                "speaker": "party_argument" if action == "asserted" else "tribunal_ruling", **kw}

    def analysis(self):
        return {
            "patents": ["8,350,294", "9,748,347"],
            "respondents": ["Innoscience, Inc", "Innoscience America, Inc."],
            "events": [
                self.event("a", "asserted", "asserted", "8,350,294", [1, 2, 3], "2023-05-26"),
                self.event("b", "instituted", "instituted", "8,350,294", [1, 2, 3], "2023-06-27"),
                self.event("c", "instituted", "instituted", "9,748,347", [1, 2, 3], "2023-06-27"),
                self.event("d", "withdrawn", "hearing", "9,748,347", [1, 2, 3], "2023-12-01"),
                self.event("e", "found_infringed", "final_id", "8,350,294", [2, 3], "2024-07-09"),
                self.event("f", "found_not_infringed", "final_id", "8,350,294", [1], "2024-07-09"),
                self.event("g", "not_reviewed", "commission", "8,350,294", [1, 2, 3], "2024-09-10"),
                # A later ruling cannot bring a withdrawn claim back.
                self.event("h", "found_infringed", "final_id", "9,748,347", [1], "2024-07-09"),
            ],
        }

    def test_the_claims_narrow_stage_by_stage(self):
        built = matrix.build(self.analysis())
        self.assertEqual(
            built["counts"],
            {"asserted": 3, "instituted": 6, "hearing": 3, "final_id": 2, "commission": 2, "appeal": 0},
        )
        rows = {(g["patent"], r["claim"]): r["cells"] for g in built["patents"] for r in g["rows"]}
        self.assertEqual(rows[("8,350,294", 1)]["final_id"]["status"], "not_infringed")
        self.assertEqual(rows[("8,350,294", 2)]["commission"]["status"], "violation")
        self.assertNotIn("final_id", rows[("9,748,347", 1)])

    def test_terminations_the_commission_declines_to_review_leave_before_the_hearing(self):
        """337-TA-1366: the '347 and '335 claims, as the Commission's notices put it."""
        from datalayer.claims import validate

        self.assertEqual(validate.stage_for("commission_notice", "Notice", "withdrawn"), "hearing")
        analysis = {"patents": ["10,312,335"], "respondents": [], "events": [
            self.event("i", "instituted", "instituted", "10,312,335", [1, 2], "2023-06-27"),
            self.event("n", "not_reviewed", "commission", "10,312,335", [1, 2], "2024-03-12",
                       sentence="All of the asserted claims (i.e., claims 1-7) of the '335 patent are terminated "
                                "from this investigation."),
        ]}
        cells = matrix.build(analysis)["patents"][0]["rows"][0]["cells"]
        self.assertEqual(cells["hearing"]["status"], "withdrawn")

    def test_a_flagged_finding_that_moves_no_chip_puts_no_dot_on_the_claim(self):
        analysis = self.analysis()
        analysis["events"].append(self.event("x", "found_not_invalid", "final_id", "8,350,294", [2], "2024-07-09",
                                             status="needs_review"))
        rows = matrix.build(analysis)["patents"][0]["rows"]
        self.assertFalse(any(r["needs_review"] for r in rows))

    def test_a_settlement_with_one_respondent_only_narrows_that_respondents_view(self):
        analysis = self.analysis()
        analysis["events"].append(self.event("s", "terminated_settlement", "hearing", "8,350,294", [1, 2, 3],
                                             "2024-01-15", respondents=["Innoscience America, Inc."]))
        everyone = matrix.build(analysis)
        settled = matrix.build(analysis, respondent="Innoscience, Inc")
        stayed = matrix.build(analysis, respondent="Innoscience America, Inc.")
        self.assertEqual(settled["counts"]["final_id"], 2)  # the other respondent went on to the Final ID
        self.assertEqual(stayed["counts"]["final_id"], 0)
        self.assertEqual(stayed["patents"][0]["rows"][0]["cells"]["hearing"]["status"], "settled")
        # For everyone together, the claims' respondents parted ways at the
        # settlement, so what follows varies by respondent.
        cells = everyone["patents"][0]["rows"][1]["cells"]
        self.assertEqual(cells["hearing"]["status"], "by_respondent")
        self.assertEqual(cells["final_id"]["status"], "by_respondent")


class TestPilotFindings(unittest.TestCase):
    """What the first live build (337-TA-1384) showed, kept from coming back."""

    cfg = claims_config.load()

    def test_only_tribunal_document_types_are_sources_and_id_notices_are_the_aljs(self):
        kind = lambda **d: self.cfg.source_kind({"security_level": "Public", **d})  # noqa: E731
        self.assertIsNone(kind(document_type="Petition for Review; and Response to",
                               title="Response of the Office of Unfair Import Investigations to the Final Initial Determination"))
        self.assertIsNone(kind(document_type="Correspondence", title="Staff's Letter on the Initial Determination"))
        self.assertEqual(kind(document_type="Notice", title="Initial Determination on Violation of Section 337"),
                         "initial_determination")
        self.assertEqual(kind(document_type="Notice", title="Commission Determination Not to Review an Initial Determination"),
                         "commission_notice")
        self.assertEqual(kind(document_type="Notice", title="Notice of Institution of Investigation"),
                         "notice_of_institution")
        from datalayer.claims import validate

        self.assertEqual(validate.stage_for("initial_determination", "Notice", "found_not_infringed",
                                            "Initial Determination on Violation of Section 337"), "final_id")

    def test_the_commissions_case_wide_finding_is_read_by_rule(self):
        from datalayer.claims import derive

        text = ("Having reviewed the record of the investigation, the Commission has found no violation of "
                "section 337. The investigation is terminated. On December 19, 2024, the ALJ issued a final "
                "initial determination finding no violation of section 337 with respect to claims 1 and 12-14 "
                "of the '511 patent.")
        doc = {"id": "849739", "title": "Commission's Final Determination Finding No Violation of Section 337",
               "document_type": "Notice", "document_date": "2025-04-25"}
        found = derive.commission_outcomes(text, doc, kind="commission_notice")
        self.assertEqual([(e["action"], e["case_wide"], e["method"]) for e in found], [("no_violation", True, "derived")])
        self.assertEqual(derive.commission_outcomes(text, {**doc, "title": "Request for Submissions"},
                                                    kind="commission_notice"), [])

    def test_case_wide_no_violation_reaches_the_claims_still_in_the_case(self):
        ev = lambda eid, action, stage, claims, **kw: {  # noqa: E731
            "id": eid, "action": action, "stage": stage, "patent": "7,333,511", "claims": claims,
            "respondents": ["ALL"], "status": "ok", "date": kw.pop("date", "2024-12-19"),
            "speaker": "tribunal_ruling", **kw}
        analysis = {"patents": ["7,333,511"], "respondents": [], "events": [
            ev("i", "instituted", "instituted", [1, 12], date="2024-01-01"),
            ev("w", "withdrawn", "hearing", [12], date="2024-05-01"),
            ev("f", "found_not_infringed", "final_id", [1]),
            ev("c", "no_violation", "commission", [], case_wide=True, patent="ALL", date="2025-04-25"),
        ]}
        rows = {r["claim"]: r["cells"] for r in matrix.build(analysis)["patents"][0]["rows"]}
        self.assertEqual(rows[1]["commission"]["status"], "no_violation")
        self.assertNotIn("commission", rows[12])  # withdrawn before the hearing

    def test_a_recital_of_institution_does_not_bring_a_withdrawn_claim_back(self):
        ev = lambda eid, action, stage, date, **kw: {  # noqa: E731
            "id": eid, "action": action, "stage": stage, "patent": "9,748,347", "claims": [1],
            "respondents": ["ALL"], "status": "ok", "date": date, "speaker": "tribunal_ruling", **kw}
        analysis = {"patents": ["9,748,347"], "respondents": [], "events": [
            ev("i", "instituted", "instituted", "2023-06-27"),
            ev("w", "withdrawn", "hearing", "2023-12-01"),
            ev("r", "instituted", "instituted", "2024-07-09"),  # the Final ID restating the notice
            ev("f", "found_infringed", "final_id", "2024-07-09"),
            ev("a", "asserted", "final_id", "2024-07-09", speaker="party_argument"),
        ]}
        cells = matrix.build(analysis)["patents"][0]["rows"][0]["cells"]
        self.assertEqual(cells["hearing"]["status"], "withdrawn")
        self.assertNotIn("final_id", cells)
        self.assertNotIn("asserted", cells)  # only the complaint's allegation counts

    def test_ocr_punctuation_noise_does_not_fail_the_quote_check(self):
        from datalayer.claims import validate

        sentence = "the Commission finding no violation of section 337 with respect to claims 1 and 12-14 of the “511patent"
        self.assertTrue(validate.contains(sentence, "findingno violation of section 337 with respect to claims 1 and 12-14 of the '511 patent"))
        self.assertFalse(validate.contains(sentence, "claims 1 and 3 of the '260 patent"))


RESPONDENTS_1417 = [
    "MIRAmedtech SP. Z.O.O.", "eMIRAmed USA, LLC", "MIRAmedtech UG", "Clarion Medical Technologies, Inc.",
    "Luvo Medical Technologies Inc.", "Healthcare Markets, Inc. d/b/a Powered by MRP", "Bio-Infusions USA Inc.",
    "Medical Purchasing Resource, LLC",
]


class TestRespondentScope(unittest.TestCase):
    """337-TA-1417: every respondent left by settlement or default, stated by
    name, never by claim."""

    def doc(self, doc_id, title, date, document_type="Notice"):
        return {"id": doc_id, "title": title, "document_date": date, "document_type": document_type}

    def test_settlements_and_defaults_are_read_by_rule_for_the_named_respondents(self):
        from datalayer.claims import derive

        settled = derive.respondent_events(
            "On April 24, 2025, the Commission issued a notice terminating the investigation as to Clarion. "
            "The investigation is terminated as to Clarion Medical Technologies, Inc., Luvo Medical Technologies, "
            "Inc., and Healthcare Markets, Inc. d/b/a Powered by MRP.",
            self.doc("849165", "Commission Determination Not to Review an Initial Determination Terminating the "
                     "Investigation as to Certain Respondents Based on Settlement", "2025-04-21"),
            kind="commission_notice", respondents=RESPONDENTS_1417,
        )
        self.assertEqual(len(settled), 1)  # the "On April 24 ..." recital is not a ruling
        self.assertEqual(settled[0]["action"], "terminated_settlement")
        self.assertEqual(sorted(settled[0]["respondents"]), sorted(RESPONDENTS_1417[3:6]))

        defaulted = derive.respondent_events(
            "It is my initial determination that Bio-Infusions USA Inc., MIRAmedtech UG, eMIRAmed USA, LLC, and "
            "MIRAmedtech SP. Z.O.O. are in default under 19 C.F.R. 210.16. The ALJ issued an order to show cause "
            "why Medical Purchasing Resource, LLC should not be found in default.",
            self.doc("841330", "Initial Determination Finding Respondents in Default", "2025-01-17",
                     "ID/RD - Other Than Final on Violation"),
            kind="initial_determination", respondents=RESPONDENTS_1417,
        )
        self.assertEqual([e["action"] for e in defaulted], ["default"])
        self.assertEqual(sorted(defaulted[0]["respondents"]),
                         sorted(["Bio-Infusions USA Inc.", "MIRAmedtech UG", "eMIRAmed USA, LLC", "MIRAmedtech SP. Z.O.O."]))

    def test_the_views_differ_by_respondent(self):
        instituted = {"id": "i", "action": "instituted", "stage": "instituted", "patent": "12,053,607",
                      "claims": [1, 2], "respondents": ["ALL"], "status": "ok", "speaker": "tribunal_ruling",
                      "date": "2024-09-09"}
        def case_wide(eid, action, stage, who, date):
            return {"id": eid, "action": action, "stage": stage, "patent": "ALL", "claims": [], "case_wide": True,
                    "respondents": who, "status": "ok", "speaker": "tribunal_ruling", "date": date}
        analysis = {"patents": ["12,053,607"], "respondents": RESPONDENTS_1417, "events": [
            instituted,
            case_wide("d", "default", "hearing", [RESPONDENTS_1417[7]], "2025-01-02"),
            case_wide("s", "terminated_settlement", "hearing", RESPONDENTS_1417[3:6], "2025-04-21"),
            case_wide("r", "violation", "commission", [RESPONDENTS_1417[7]], "2025-05-27"),
        ]}
        def row(respondent=None):
            return matrix.build(analysis, respondent=respondent)["patents"][0]["rows"][0]["cells"]
        self.assertEqual(row("Clarion Medical Technologies, Inc.")["hearing"]["status"], "settled")
        self.assertNotIn("commission", row("Clarion Medical Technologies, Inc."))
        self.assertEqual(row("Medical Purchasing Resource, LLC")["hearing"]["status"], "default")
        self.assertEqual(row("Medical Purchasing Resource, LLC")["commission"]["status"], "violation")
        everyone = row()
        self.assertEqual(everyone["hearing"]["status"], "by_respondent")
        self.assertEqual(everyone["commission"]["status"], "by_respondent")

    def test_the_page_offers_a_view_per_respondent_and_hides_withdrawn_claims(self):
        from ui import templates

        analysis = {"built_at": "2026-09-24T00:00:00+00:00", "outcome": "ok", "key": "337-1417",
                    "patents": ["12,053,607"], "respondents": RESPONDENTS_1417[:2], "events": [
                        {"id": "i", "action": "instituted", "stage": "instituted", "patent": "12,053,607",
                         "claims": [1], "respondents": ["ALL"], "status": "ok", "speaker": "tribunal_ruling",
                         "date": "2024-09-09", "method": "rule", "quote": "claim 1", "claims_verbatim": "1",
                         "source": {"id": "FR:1", "title": "Institution"}}]}
        html = templates._claims_section(analysis)
        self.assertIn('<option value="0">All respondents</option>', html)
        self.assertIn('<option value="2">eMIRAmed USA, LLC</option>', html)
        self.assertEqual(html.count('class="claims-view"'), 3)
        self.assertIn('id="claims-hide-withdrawn"', html)


class TestPrompt(unittest.TestCase):
    def test_the_system_prompt_is_long_enough_to_cache_on_haiku(self):
        from datalayer.claims import prompts

        # Haiku 4.5 caches prompts of 4,096 tokens or more. At no more than
        # ~4 characters a token, this length clears it with the tool schema.
        import json

        self.assertGreater(len(prompts.SYSTEM) + len(json.dumps(prompts.TOOL)), 4096 * 4)

    def test_the_tool_offers_exactly_the_specs_actions_and_speakers(self):
        from datalayer.claims import prompts

        item = prompts.TOOL["input_schema"]["properties"]["events"]["items"]["properties"]
        self.assertEqual(len(item["action"]["enum"]), 12)
        self.assertEqual(item["speaker"]["enum"], ["tribunal_ruling", "tribunal_recital", "party_argument", "other"])


if __name__ == "__main__":
    unittest.main()
