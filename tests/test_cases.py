"""Flattening IDS rows, and folding a case's stages into one record."""

from __future__ import annotations

import unittest

from support import DataDirTestCase, ids_row, write_snapshot

from datalayer import cases, ingest
from datalayer.flatten import flatten_row, slug


class TestSlugs(unittest.TestCase):
    def test_labels_become_stable_field_names(self):
        self.assertEqual(slug("Start Date"), "start_date")
        self.assertEqual(slug("Hearing/Conf Start Date"), "hearing_conf_start_date")
        self.assertEqual(
            slug("F.R. Citation for Notice of Institution"),
            "fr_citation_for_notice_of_institution",
        )
        self.assertEqual(slug("Is Active?"), "is_active")
        self.assertEqual(slug("Date of Publication of FR Notice (NOI)"), "date_of_publication_of_fr_notice_noi")

    def test_camel_case_keys_split_the_same_way_as_labels(self):
        self.assertEqual(slug("finalDeterminationType"), slug("Final Determination Type"))


class TestFlatten(unittest.TestCase):
    def setUp(self) -> None:
        self.fields, self.lists = flatten_row(ids_row())

    def test_wrapped_values_become_their_name(self):
        self.assertEqual(self.fields["investigation_status"], "Active")
        self.assertEqual(self.fields["investigation_phase"], "Violation")
        self.assertEqual(self.fields["phase_number"], 1)

    def test_an_object_that_names_its_own_value_is_unwrapped(self):
        self.assertEqual(self.fields["final_determination_type"], "Violation")

    def test_dates_are_read_month_first_and_stored_as_iso(self):
        # "05-20-2026" in IDS is 20 May, never 5 December.
        self.assertEqual(self.fields["start_date"], "2026-05-20")
        self.assertEqual(self.fields["initiating_document_received_date"], "2026-03-22")

    def test_a_date_object_keeps_the_day_and_reports_not_applicable(self):
        self.assertEqual(self.fields["date_of_publication_of_fr_notice_noi"], "2026-05-26")
        self.assertEqual(self.fields["party_comments_due_date"], "N/A")

    def test_people_are_named_not_logged_in(self):
        self.assertEqual(self.fields["case_manager"], "Nathaniel Gibson")
        self.assertEqual(self.fields["case_manager_email"], "Nathaniel.Gibson@usitc.gov")

    def test_participants_carry_their_role(self):
        roles = {item["name"]: item["role"] for item in self.lists["participants"]}
        self.assertEqual(roles, {"Acme Inc.": "Complainant", "Globex Corp.": "Respondent"})

    def test_staff_and_ip_get_a_label_to_display(self):
        self.assertEqual(self.lists["staff"][0]["label"], "Monica Bhattacharyya")
        self.assertEqual(self.lists["staff"][0]["role"], "ALJ")
        self.assertEqual(self.lists["intellectual_property"][0]["label"], "Patent 10,945,648")

    def test_a_party_repeated_by_the_feed_is_stored_once(self):
        row = ids_row(complainants=("Acme Inc.", "Acme Inc."))
        _, lists = flatten_row(row)
        self.assertEqual(len([p for p in lists["participants"] if p["role"] == "Complainant"]), 1)

    def test_unknown_fields_come_through_under_their_own_name(self):
        fields, lists = flatten_row(ids_row(**{"Some New Field": "surprise"}))
        self.assertEqual(fields["some_new_field"], "surprise")
        self.assertNotIn("some_new_field", lists)


class TestCaseGrouping(unittest.TestCase):
    def rows(self):
        return [
            ids_row("337-1478", phase="Violation", investigation_id=1, start_date="05-20-2026",
                    end_date="07-28-2027", status="Terminated"),
            ids_row("337-1478", phase="Remand", investigation_id=2, start_date="10-16-2028",
                    status="Pending before the Commission", docket=None),
            ids_row("337-1478", phase="Bond Return", investigation_id=3, start_date="02-25-2027",
                    status="Terminated", docket=None),
        ]

    def test_every_row_for_a_number_becomes_one_case_with_its_stages(self):
        built = cases.build_cases(self.rows())
        self.assertEqual(list(built), ["337-1478"])
        case = built["337-1478"]
        self.assertEqual(case["stage_count"], 3)
        self.assertEqual([s["phase"] for s in case["stages"]], ["Violation", "Bond Return", "Remand"])

    def test_the_violation_phase_is_the_primary_stage(self):
        case = cases.build_cases(self.rows())["337-1478"]
        self.assertEqual(cases.primary_stage(case)["phase"], "Violation")
        self.assertEqual(case["primary_stage"], 1)
        self.assertEqual(case["date_initiated"], "2026-05-20")
        self.assertEqual(case["docket_number"], "3866")

    def test_status_and_phase_come_from_the_newest_stage(self):
        case = cases.build_cases(self.rows())["337-1478"]
        self.assertEqual(cases.current_stage(case)["phase"], "Remand")
        self.assertEqual(case["status"], "Pending before the Commission")
        self.assertEqual(case["phase"], "Remand")

    def test_the_title_drops_the_number_the_feed_repeats(self):
        case = cases.build_cases([ids_row(topic="Certain Vaporizer Cartridges")])["337-1478"]
        self.assertEqual(case["title"], "Certain Vaporizer Cartridges")

    def test_a_case_without_a_topic_falls_back_to_its_full_title(self):
        row = ids_row()
        row.pop("Topic")
        case = cases.build_cases([row])["337-1478"]
        self.assertEqual(case["title"], "Certain Wearable Devices")

    def test_a_pre_institution_docket_dates_from_its_complaint(self):
        row = ids_row("337-3936", status="Pre-institution", start_date=None, docket="3936")
        case = cases.build_cases([row])["337-3936"]
        self.assertEqual(case["date_initiated"], "2026-03-22")
        self.assertEqual(case["status"], "Pre-institution")

    def test_rows_are_grouped_by_investigation_number_not_the_official_one(self):
        row = ids_row("337-1432", **{"official_investigation_number": "337-1393"})
        built = cases.build_cases([row])
        self.assertEqual(list(built), ["337-1432"])
        self.assertEqual(built["337-1432"]["official_number"], "337-1393")


class TestIngest(DataDirTestCase):
    def parse(self, rows, *, day="2026-09-22", store=None, **kwargs):
        store = store or self.store()
        snapshot = write_snapshot(self.ids_dir, rows, day=day)
        report = ingest.parse_snapshot(store, snapshot, log=self.quiet, **kwargs)
        return store, report

    def test_the_snapshot_is_written_out_as_case_records(self):
        store, report = self.parse([ids_row(), ids_row("337-1479", investigation_id=2)])

        self.assertEqual(report.cases, 2)
        saved = self.read_json("investigations.json")
        self.assertEqual(sorted(saved), ["337-1478", "337-1479"])
        self.assertEqual(saved["337-1478"]["ids_snapshot"], "2026-09-22")
        self.assertEqual(saved["337-1478"]["source"], "ids")

    def test_parsing_again_reports_what_appeared_and_disappeared(self):
        store, _ = self.parse([ids_row()])
        store, report = self.parse(
            [ids_row("337-1479", investigation_id=2)], store=store, day="2026-09-23"
        )

        self.assertEqual(report.added, ["337-1479"])
        self.assertEqual(report.removed, ["337-1478"])
        self.assertEqual(sorted(store.investigations), ["337-1478", "337-1479"])

    def test_a_case_the_feed_stops_listing_is_kept_and_marked(self):
        store, _ = self.parse([ids_row(topic="Certain Wearable Devices")])
        store, report = self.parse(
            [ids_row("337-1479", investigation_id=2)], store=store, day="2026-09-23"
        )

        self.assertEqual(report.withdrawn, ["337-1478"])
        kept = store.investigations["337-1478"]
        self.assertTrue(kept["withdrawn"])
        # Still says what the last snapshot that listed it said.
        self.assertEqual(kept["title"], "Certain Wearable Devices")
        self.assertEqual(kept["last_listed_snapshot"], "2026-09-22")

    def test_a_case_that_stays_away_is_only_reported_the_first_time(self):
        store, _ = self.parse([ids_row()])
        rows = [ids_row("337-1479", investigation_id=2)]
        store, _ = self.parse(rows, store=store, day="2026-09-23")
        store, report = self.parse(rows, store=store, day="2026-09-24")

        self.assertEqual(report.removed, [])
        self.assertEqual(report.withdrawn, ["337-1478"])
        self.assertEqual(
            store.investigations["337-1478"]["last_listed_snapshot"], "2026-09-22"
        )

    def test_a_case_the_feed_lists_again_loses_the_mark(self):
        store, _ = self.parse([ids_row(status="Active")])
        store, _ = self.parse(
            [ids_row("337-1479", investigation_id=2)], store=store, day="2026-09-23"
        )
        store, _ = self.parse(
            [ids_row(status="Terminated"), ids_row("337-1479", investigation_id=2)],
            store=store,
            day="2026-09-24",
        )

        back = store.investigations["337-1478"]
        self.assertNotIn("withdrawn", back)
        self.assertNotIn("last_listed_snapshot", back)
        self.assertEqual(back["status"], "Terminated")

    def test_a_snapshot_that_drops_most_of_the_cases_is_refused(self):
        rows = [ids_row(f"337-{1400 + n}", investigation_id=n) for n in range(60)]
        store, _ = self.parse(rows)
        before = dict(store.investigations)

        with self.assertRaises(ingest.SuspectSnapshotError) as caught:
            self.parse(rows[:10], store=store, day="2026-09-23")

        self.assertIn("most likely incomplete", str(caught.exception))
        self.assertEqual(store.investigations, before)
        self.assertEqual(self.read_json("investigations.json").keys(), before.keys())

    def test_a_refused_snapshot_goes_through_with_allow_removals(self):
        rows = [ids_row(f"337-{1400 + n}", investigation_id=n) for n in range(60)]
        store, _ = self.parse(rows)
        store, report = self.parse(
            rows[:10], store=store, day="2026-09-23", allow_removals=True
        )

        self.assertEqual(len(report.removed), 50)
        self.assertEqual(len(report.withdrawn), 50)

    def test_a_handful_of_withdrawals_is_not_treated_as_suspect(self):
        rows = [ids_row(f"337-{1400 + n}", investigation_id=n) for n in range(60)]
        store, _ = self.parse(rows)
        store, report = self.parse(rows[:-3], store=store, day="2026-09-23")

        self.assertEqual(len(report.removed), 3)

    def test_documents_follow_a_docket_that_has_been_instituted(self):
        store = self.store()
        store.put_documents(
            "337-3866",
            [{"id": "700", "attachments": [{"href": "../../data/documents/337-3866/a.pdf"}]}],
        )
        pdf = self.data_dir / "documents" / "337-3866" / "a.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake")

        # IDS now lists the complaint as investigation 337-1478, docket 3866.
        store, report = self.parse([ids_row("337-1478", docket="3866")], store=store)

        self.assertEqual(report.migrated, [("337-3866", "337-1478")])
        self.assertNotIn("337-3866", store.documents)
        self.assertEqual(
            store.documents["337-1478"][0]["attachments"][0]["href"],
            "../../data/documents/337-1478/a.pdf",
        )
        self.assertTrue((self.data_dir / "documents" / "337-1478" / "a.pdf").exists())

    def test_parsing_offline_uses_the_newest_stored_snapshot(self):
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-21")
        write_snapshot(self.ids_dir, [ids_row("337-1479", investigation_id=2)], day="2026-09-22")
        store = self.store()
        report = ingest.run(store, ids_dir=self.ids_dir, offline=True, log=self.quiet)

        self.assertEqual(report.snapshot_day, "2026-09-22")
        self.assertEqual(list(store.investigations), ["337-1479"])

    def test_parsing_offline_without_a_snapshot_says_so(self):
        with self.assertRaises(ingest.ids.IdsError):
            ingest.run(self.store(), ids_dir=self.ids_dir, offline=True, log=self.quiet)


if __name__ == "__main__":
    unittest.main()
