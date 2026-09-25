"""Representation analytics, phase 1: turning names into entities.

The cases are the real ones the matching was tuned on (the spellings come
from EDIS and IDS as filed), so a change that breaks one of them breaks
something that happens in the data.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from support import DataDirTestCase

from datalayer.analytics import attorneys, build, cluster, companies, firms, names, review
from datalayer.analytics.reference import Decisions, Reference


def decisions(tmp=None, items=None):
    return Decisions(path=(tmp / "decisions.json") if tmp else None, items=dict(items or {}))


class TestFirmNames(unittest.TestCase):
    def test_one_firm_many_spellings_one_key(self):
        spellings = [
            "Finnegan, Henderson, Farabow, Garrett & Dunner, L.L.P.",
            "finnegan henderson farabow garrett and dunner",
            "Finnegan, Henderson, Farabow, Garrett & Dunner LLP",
        ]
        self.assertEqual({names.firm_key(s) for s in spellings}, {"finnegan henderson farabow garrett dunner"})
        self.assertEqual(names.firm_key("Sterne, Kessler, Goldstein & Fox P. L. L.C."), "sterne kessler goldstein fox")
        self.assertEqual(names.firm_key("Blank Rome LLP (DC)"), "blank rome")
        self.assertEqual(names.firm_key("Kirkland &; Ellis LLP"), "kirkland ellis")
        self.assertEqual(names.firm_key("Law Offices of Ye E. Huang"), "ye e huang")

    def test_a_list_of_firms_splits_after_each_suffix(self):
        split = names.split_firm_field("Winston Taylor LLP, DLA Piper LLP (US), Hueston Hennigan LLP, and Wilmerhale")
        self.assertEqual(split.parts, ["Winston Taylor LLP", "DLA Piper LLP (US)", "Hueston Hennigan LLP", "Wilmerhale"])
        self.assertEqual(
            names.split_firm_field("Sterne, Kessler, Goldstein & Fox, P.L.L.C.and Latham & Watkins LLP").parts,
            ["Sterne, Kessler, Goldstein & Fox, P.L.L.C", "Latham & Watkins LLP"],
        )
        self.assertEqual(names.split_firm_field("Kirkland & Ellis LLP; Covington & Burling LLP").rule, "separator")

    def test_commas_inside_one_firm_never_split_it(self):
        for firm in ("Finnegan, Henderson, Farabow, Garrett & Dunner, LLP", "Wolf, Greenfield & Sacks, P.C.",
                     "Foster, Murphy, Altman & Nickel, PC"):
            self.assertEqual(names.split_firm_field(firm).parts, [firm])

    def test_run_together_old_fields_split_by_firms_seen_elsewhere(self):
        known = names.KnownFirms(tuple(names.firm_core(f)) for f in (
            "Fenwick & West LLP", "Finnegan, Henderson, Farabow, Garrett & Dunner LLP",
            "Sonnenschein Nath & Rosenthal LLP", "Morrison & Foerster LLP", "Heller Ehrman White & McAuliffe LLP",
        ))
        split = names.split_firm_field(
            "fenwick and west finnegan henderson farabow garrett and dunner sonnenschein nath and rosenthal "
            "and morrison and foerster", known)
        self.assertEqual(split.parts, ["fenwick west", "finnegan henderson farabow garrett dunner",
                                       "sonnenschein nath rosenthal", "morrison foerster"])
        # Short forms and "et al" too.
        self.assertEqual(
            names.split_firm_field("morrison foerster finnegan henderson et al heller ehrman white et al", known).parts,
            ["morrison foerster", "finnegan henderson farabow garrett dunner", "heller ehrman white mcauliffe"],
        )
        # A firm with its own suffix is one firm, whatever it contains.
        self.assertEqual(len(names.split_firm_field("Morrison & Foerster LLP", known).parts), 1)

    def test_what_cannot_be_split_cleanly_is_flagged(self):
        self.assertTrue(names.split_firm_field("smith jones et al").review)


class TestCompanyNames(unittest.TestCase):
    def test_former_names_and_trade_names(self):
        hydra = names.parse_company("HydraFacial LLC f/k/a Edge Systems LLC")
        self.assertEqual((hydra.primary, hydra.former), ("HydraFacial LLC", ["Edge Systems LLC"]))
        sams = names.parse_company("Sam’s East, Inc. (d/b/a Sam’s Club)")
        self.assertEqual((sams.primary, sams.trade, sams.former), ("Sam’s East, Inc.", ["Sam’s Club"], []))
        now = names.parse_company("X Corp n/k/a Y Inc.")
        self.assertEqual((now.primary, now.former), ("Y Inc.", ["X Corp"]))
        self.assertEqual(names.parse_company("Airwheel (2)").primary, "Airwheel")

    def test_corporate_forms(self):
        self.assertEqual(names.company_form("Samsung Electronics, Co., Ltd."), (["samsung", "electronics"], "co ltd"))
        self.assertEqual(names.company_form("Kokadi GmbH & Co. KG"), (["kokadi"], "gmbh co kg"))
        self.assertEqual(names.company_form("LG Electronics"), (["lg", "electronics"], ""))

    def test_families_skip_places(self):
        self.assertEqual(names.family_key(["shenzhen", "topworld", "technology"]), "topworld")
        self.assertEqual(names.family_key(["zhuhai", "ninestar", "image"]), "ninestar")
        self.assertIsNone(names.family_key(["advanced", "silicon"]))


class TestPeople(unittest.TestCase):
    def test_parsing_and_compatibility(self):
        self.assertEqual(names.parse_person("James A. Fussell, III").full(), "james a fussell")
        self.assertEqual(names.parse_person("Bas de Blank").last, "de blank")
        self.assertEqual(names.parse_person("Séké G. Godo").first, "seke")
        full, initial, other = (names.parse_person(n) for n in ("Deanna Tanner Okun", "Deanna T. Okun", "Deanna S. Okun"))
        self.assertTrue(names.compatible_people(full, initial))
        self.assertFalse(names.compatible_people(initial, other))


class TestCompare(unittest.TestCase):
    def test_typos_merge_lookalikes_do_not(self):
        self.assertEqual(cluster.compare("wiitgen group holding".split(), "wirtgen group holding".split()).verdict, "typo")
        self.assertEqual(cluster.compare("shenzhen carku technology".split(), "shenzhen yark technology".split()).verdict,
                         "different")
        self.assertEqual(
            cluster.compare("sams east".split(), "sams west".split(), protected=companies.PROTECTED_WORDS).verdict,
            "different",
        )
        self.assertEqual(cluster.compare("hong kong boze".split(), "hongkong boze".split()).verdict, "typo")

    def test_a_country_added_is_a_different_entity(self):
        similarity = cluster.compare("ingrasys technology".split(), "ingrasys technology usa".split(),
                                     protected=companies.PROTECTED_WORDS)
        self.assertEqual(similarity.verdict, "different")

    def test_keep_apart_vetoes_any_merge(self):
        clusters = cluster.Clusters()
        clusters.keep_apart("a", "c")
        clusters.union("a", "b", "typo")
        self.assertFalse(clusters.union("b", "c", "typo"))
        self.assertTrue(clusters.kept_apart("b", "c"))


def rep(firm, *, parties, attorneys=(), first="2024-01-01", last="2024-06-01", roles=("Respondent",)):
    return {
        "firm": firm,
        "firm_key": firm.lower(),
        "roles": list(roles),
        "parties": [{"name": p, "role": roles[0]} for p in parties],
        "attorneys": [{"name": a} for a in attorneys],
        "first_filed": first,
        "last_filed": last,
        "filings": 3,
    }


def counsel_of(**cases):
    return {case: {"representations": reps, "non_parties": []} for case, reps in cases.items()}


class TestFirms(unittest.TestCase):
    def build(self, counsel, reference=None, decided=None):
        return firms.build(counsel, {}, reference or Reference(), decided or decisions())

    def test_typos_merge_and_the_best_spelling_names_the_firm(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Kirkland & Ellis LLP", parties=["Acme Inc."])],
            "337-2": [rep("Kikland & Ellis LLP", parties=["Acme Inc."])],
            "337-3": [rep("Kirkland & Ellis LLP", parties=["Globex Corp."])],
        }))
        [firm] = [e for e in result.entities.values() if "ellis" in e.id]
        self.assertEqual(firm.name, "Kirkland & Ellis LLP")
        self.assertEqual(sorted(firm.cases), ["337-1", "337-2", "337-3"])

    def test_a_split_field_gives_each_firm_the_representation(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Oliff PLC and Husch Blackwell LLP", parties=["Acme Inc."])],
        }))
        self.assertEqual(len(result.firms_of[("337-1", 0)]), 2)

    def test_short_forms_merge_but_a_merger_is_asked_about(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Pillsbury Winthrop", parties=["A Inc."]), rep("Hogan Lovells US LLP", parties=["B Inc."])],
            "337-2": [rep("Pillsbury Winthrop Shaw Pittman LLP", parties=["A Inc."]),
                      rep("Hogan Lovells Cadwalader US LLP", parties=["B Inc."])],
        }))
        self.assertEqual(result.firms_of[("337-1", 0)], result.firms_of[("337-2", 0)])
        self.assertNotEqual(result.firms_of[("337-1", 1)], result.firms_of[("337-2", 1)])
        self.assertIn(("hogan lovells", "hogan lovells cadwalader"), [item.keys for item in result.review])

    def test_predecessors_are_linked_not_merged(self):
        reference = Reference(raw={"firms": {"predecessors": {"Hogan Lovells Cadwalader US LLP": ["Hogan Lovells US LLP"]}}})
        result = self.build(counsel_of(**{
            "337-1": [rep("Hogan Lovells US LLP", parties=["B Inc."])],
            "337-2": [rep("Hogan Lovells Cadwalader US LLP", parties=["B Inc."])],
        }), reference)
        before, after = result.firms_of[("337-1", 0)][0], result.firms_of[("337-2", 0)][0]
        self.assertEqual(result.entities[after].predecessors, [before])
        self.assertEqual(result.review, [])

    def test_a_company_filing_for_itself_is_not_a_law_firm(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Optimum Communications Services, Inc.", parties=["Optimum Communications Services, Inc."]),
                      rep("Leydig, Voit & Mayer, Ltd.", parties=["Acme Inc."]),
                      rep("Law Office of Li Liu", parties=["Acme Inc."])],
        }))
        kinds = {e.name: e.kind for e in result.entities.values()}
        self.assertEqual(kinds["Optimum Communications Services, Inc."], "self_represented")
        self.assertEqual(kinds["Leydig, Voit & Mayer, Ltd."], "law_firm")
        self.assertEqual(kinds["Law Office of Li Liu"], "law_firm")

    def test_a_wrapped_signature_fragment_folds_into_its_firm(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Foster, Murphy, Altman & Nickel, PC", parties=["A Inc."]),
                      rep("Nickel, PC", parties=["A Inc."])],
        }))
        self.assertEqual(result.firms_of[("337-1", 0)], result.firms_of[("337-1", 1)])


def ids_case(*parties):
    return {"stages": [{"lists": {"participants": [{"name": n, "role": r} for n, r in parties]}}]}


class TestCompanies(unittest.TestCase):
    def build(self, investigations, decided=None):
        return companies.build(investigations, {}, Reference(), decided or decisions())

    def entity(self, result, name):
        return result.entities[result.ids_of_name[name][0]]

    def test_a_former_name_joins_the_cases_filed_under_it(self):
        result = self.build({
            "337-1": ids_case(("Edge Systems LLC", "Complainant")),
            "337-2": ids_case(("HydraFacial LLC f/k/a Edge Systems LLC", "Complainant")),
        })
        hydra = self.entity(result, "HydraFacial LLC f/k/a Edge Systems LLC")
        self.assertEqual(hydra.name, "HydraFacial LLC")
        self.assertEqual(sorted(hydra.cases), ["337-1", "337-2"])

    def test_a_shared_trade_name_does_not_merge(self):
        result = self.build({"337-1": ids_case(("Sam’s East, Inc. (d/b/a Sam’s Club)", "Respondent"),
                                               ("Sam’s West, Inc. (d/b/a Sam’s Club)", "Respondent"))})
        self.assertEqual(len(result.entities), 2)

    def test_forms_different_entities_but_a_missing_form_is_the_one_there_is(self):
        result = self.build({"337-1": ids_case(("Sony Interactive Entertainment Inc.", "Respondent"),
                                               ("Sony Interactive Entertainment LLC", "Respondent"),
                                               ("LG Electronics", "Respondent"),
                                               ("LG Electronics, Inc.", "Respondent"))})
        self.assertEqual(len(result.entities), 3)
        self.assertEqual(self.entity(result, "LG Electronics").name, "LG Electronics, Inc.")

    def test_two_companies_in_one_name(self):
        result = self.build({"337-1": ids_case(("NuRich, LLC and NuRich Accounting, LLC", "Respondent"),
                                               ("San Antonio Sam's Spa and Nail Supply, Inc.", "Respondent"))})
        self.assertEqual(len(result.ids_of_name["NuRich, LLC and NuRich Accounting, LLC"]), 2)
        self.assertEqual(len(result.ids_of_name["San Antonio Sam's Spa and Nail Supply, Inc."]), 1)

    def test_families_group_brands_seen_twice(self):
        result = self.build({"337-1": ids_case(("Samsung Electronics Co., Ltd.", "Respondent"),
                                               ("Samsung SDI Co., Ltd.", "Respondent"),
                                               ("Acme Widgets Inc.", "Respondent"))})
        families = {e.name: e.family for e in result.entities.values()}
        self.assertEqual(families["Samsung SDI Co., Ltd."], "samsung")
        self.assertIsNone(families["Acme Widgets Inc."])


class TestAttorneys(unittest.TestCase):
    def build(self, counsel):
        firm_result = firms.build(counsel, {}, Reference(), decisions())
        return attorneys.build(counsel, firm_result.firms_of, Reference(), decisions())

    def test_compatible_names_at_one_firm_are_one_person(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Cooley LLP", parties=["A Inc."], attorneys=["Stephen R. Smith"])],
            "337-2": [rep("Cooley LLP", parties=["B Inc."], attorneys=["Stephen Smith"])],
        }))
        [person] = result.entities.values()
        self.assertEqual(person.name, "Stephen R. Smith")

    def test_a_lateral_move_is_one_person_with_a_history(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Perkins Coie LLP", parties=["A Inc."], attorneys=["Kevin J. Patariu"], first="2023-01-01")],
            "337-2": [rep("Foley & Lardner LLP", parties=["B Inc."], attorneys=["Kevin Patariu"], first="2025-01-01")],
        }))
        [person] = result.entities.values()
        self.assertEqual(len(person.firms), 2)

    def test_a_clashing_name_sends_the_pair_to_review(self):
        result = self.build(counsel_of(**{
            "337-1": [rep("Firm A LLP", parties=["A Inc."], attorneys=["John A. Smith"])],
            "337-2": [rep("Firm B LLP", parties=["B Inc."], attorneys=["John B. Smith"])],
            "337-3": [rep("Firm C LLP", parties=["C Inc."], attorneys=["John Smith"])],
        }))
        self.assertEqual(len(result.entities), 3)
        self.assertEqual({item.kind for item in result.review}, {"attorney"})


class TestReviewDecisions(unittest.TestCase):
    def test_cached_answers_settle_pairs(self):
        clusters = cluster.Clusters()
        similarity = cluster.Similarity("review", 88.0, "")
        item_id = review.ReviewItem(kind="firm", keys=("a", "b"), question="").id
        queue: list = []
        review.settle("firm", "a", "b", similarity, clusters, decisions(items={item_id: {"decision": "same", "confidence": 0.9}}),
                      queue, context={})
        self.assertTrue(clusters.same("a", "b"))

        clusters = cluster.Clusters()
        review.settle("firm", "a", "b", similarity, clusters, decisions(items={item_id: {"decision": "same", "confidence": 0.9}}),
                      queue, context={}, min_same=0.95)
        self.assertFalse(clusters.same("a", "b"))
        self.assertEqual([item.id for item in queue], [item_id])


def _fake_client(answer):
    """An Anthropic client stand-in that answers every item with `answer`."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        items = [json.loads(line) for line in kwargs["messages"][0]["content"].splitlines() if line.startswith("{")]
        decisions_out = [{"id": item["id"], "decision": answer, "confidence": 0.99, "reason": "test", "parts": []}
                         for item in items]
        block = SimpleNamespace(type="tool_use", input={"decisions": decisions_out})
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0, cache_creation=None)
        return SimpleNamespace(content=[block], usage=usage, stop_reason="tool_use")

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


class TestTheRun(DataDirTestCase):
    def seeded(self):
        store = self.store()
        store.investigations = {"337-1": ids_case(("Acme Inc.", "Complainant"), ("Globex Corp.", "Respondent"))}
        store.counsel = counsel_of(**{"337-1": [
            rep("Lippes Mathias LLP", parties=["Globex Corp."], attorneys=["Jane Roe"]),
            rep("Lipper Mathias LLP", parties=["Globex Corp."], attorneys=["Jane Roe"]),
        ]})
        return store

    def config(self):
        from datalayer.claims import config as claims_config

        return dataclasses.replace(claims_config.load(), costs_csv=self.data_dir / "costs.csv")

    def test_it_writes_the_entities_and_the_report_and_asks_only_once(self):
        store = self.seeded()
        client, calls = _fake_client("same")
        with mock.patch("datalayer.claims.config.load", return_value=self.config()):
            first = build.run(store, client=client, log=self.quiet)
            second = build.run(store, client=client, log=self.quiet)

        out = self.data_dir / "analytics"
        for name in ("firms.json", "attorneys.json", "companies.json", "representations.json", "report.md",
                     "needs_review.json", "review_decisions.json", "meta.json"):
            self.assertTrue((out / name).exists(), name)
        # Nothing outside data/analytics/ is written.
        self.assertFalse((self.data_dir / "state.json").exists())
        self.assertEqual(len(calls), 1)
        self.assertEqual(first.review.answered, 1)
        self.assertEqual(second.review.calls, 0)
        self.assertEqual(second.firms, 1)  # the model's "same" applied
        rows = (self.data_dir / "costs.csv").read_text(encoding="utf-8")
        self.assertIn("analytics-review", rows)
        reps = json.loads((out / "representations.json").read_text(encoding="utf-8"))["representations"]
        self.assertEqual(reps[0]["parties"][0]["role"], "Respondent")

    def test_without_review_no_model_is_called(self):
        store = self.seeded()
        with mock.patch("datalayer.analytics.review.ask", side_effect=AssertionError("asked")):
            result = build.run(store, review=False, log=self.quiet)
        self.assertEqual(result.firms, 2)
        self.assertEqual(result.needs_review, 1)

    def test_the_budget_stops_the_review(self):
        store = self.seeded()
        client, calls = _fake_client("same")
        broke = dataclasses.replace(self.config(), budget_usd=0.0)
        with mock.patch("datalayer.claims.config.load", return_value=broke):
            result = build.run(store, client=client, log=self.quiet)
        self.assertEqual(calls, [])
        self.assertIn("budget", result.review.stopped)


if __name__ == "__main__":
    unittest.main()
