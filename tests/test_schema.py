"""The JSON-to-UI mapping: what a field spec resolves to, and what is refused."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import ids_row  # noqa: F401  (adds the repo root to sys.path)

import schema as ui_schema
from datalayer import cases
from datalayer.config import SCHEMA_PATH


def case(*rows) -> dict:
    return cases.build_cases(rows or [ids_row()])["337-1478"]


def spec(**kwargs) -> ui_schema.FieldSpec:
    return ui_schema.FieldSpec.from_dict(kwargs)


class TestFieldSpecs(unittest.TestCase):
    def test_a_field_needs_a_label_and_a_source(self):
        with self.assertRaises(ui_schema.SchemaError):
            spec(label="Target Date")

    def test_an_unknown_type_is_refused_with_the_known_ones_listed(self):
        with self.assertRaisesRegex(ui_schema.SchemaError, "unknown type"):
            spec(label="Target Date", source="target_date", type="calendar")

    def test_an_unknown_section_kind_is_refused(self):
        with self.assertRaisesRegex(ui_schema.SchemaError, "unknown kind"):
            ui_schema.Section.from_dict({"title": "Parties", "kind": "grid"})

    def test_a_schema_file_that_is_not_json_says_which_file(self):
        with self.assertRaisesRegex(ui_schema.SchemaError, "not valid JSON"):
            ui_schema.load(Path(__file__))


class TestResolution(unittest.TestCase):
    def test_case_fields_win_over_stage_fields(self):
        built = case()
        self.assertEqual(
            ui_schema.resolve(spec(label="Status", source="status"), built), "Active"
        )

    def test_stage_fields_are_reachable_by_their_ids_name(self):
        built = case()
        value = ui_schema.resolve(spec(label="FR", source="fr_citation_for_notice_of_institution"), built)
        self.assertEqual(value, "91 FR 33548")

    def test_a_list_can_be_filtered_to_one_role(self):
        built = case(
            ids_row(complainants=("Acme Inc.",), respondents=("Globex Corp.", "Initech")),
        )
        value = ui_schema.resolve(
            spec(
                label="Respondents",
                source="participants",
                where={"role": "Respondent"},
                item="name",
                type="list",
            ),
            built,
        )
        self.assertEqual(value, ["Globex Corp.", "Initech"])

    def test_where_accepts_several_values(self):
        built = case()
        value = ui_schema.resolve(
            spec(
                label="Judge",
                source="staff",
                where={"role": ["ALJ", "ALJ Attorney Advisor"]},
                item="name",
                type="list",
            ),
            built,
        )
        self.assertEqual(value, ["Monica Bhattacharyya"])

    def test_limit_caps_a_list(self):
        built = case(ids_row(respondents=("A Inc.", "B Inc.", "C Inc.")))
        value = ui_schema.resolve(
            spec(
                label="Respondents",
                source="participants",
                where={"role": "Respondent"},
                item="name",
                type="list",
                limit=2,
            ),
            built,
        )
        self.assertEqual(len(value), 2)

    def test_a_list_read_as_text_is_joined(self):
        built = case(ids_row(respondents=("A Inc.", "B Inc.")))
        value = ui_schema.resolve(
            spec(label="Respondents", source="participants", where={"role": "Respondent"}, item="name"),
            built,
        )
        self.assertEqual(value, "A Inc.; B Inc.")

    def test_a_field_can_ask_for_the_current_stage_instead_of_the_primary_one(self):
        built = cases.build_cases(
            [
                ids_row("337-1478", investigation_id=1, start_date="05-20-2026", **{"Target Date": "09-26-2027"}),
                ids_row("337-1478", phase="Remand", investigation_id=2, start_date="10-16-2028", **{"Target Date": "11-11-2029"}),
            ]
        )["337-1478"]

        primary = spec(label="Target", source="target_date", type="date")
        current = spec(label="Target", source="target_date", type="date", stage="current")
        self.assertEqual(ui_schema.resolve(primary, built), "2027-09-26")
        self.assertEqual(ui_schema.resolve(current, built), "2029-11-11")

    def test_stage_only_resolution_ignores_the_case_summary(self):
        built = case()
        stage = built["stages"][0]
        by_case = spec(label="Status", source="status")
        self.assertEqual(ui_schema.resolve(by_case, built, stage=stage, stage_only=True), None)

    def test_a_missing_source_is_empty_rather_than_an_error(self):
        built = case()
        self.assertIsNone(ui_schema.resolve(spec(label="Nope", source="nope"), built))
        self.assertEqual(ui_schema.resolve(spec(label="Nope", source="nope", type="list"), built), [])


class TestShippedSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = ui_schema.load(SCHEMA_PATH)

    def test_it_loads_and_covers_the_expected_sections(self):
        self.assertIn("Investigation Information", self.schema.section_titles())
        self.assertIn("Parties", self.schema.section_titles())
        self.assertEqual(
            [s.kind for s in self.schema.sections].count("documents"), 1
        )

    def test_the_fields_it_names_resolve_against_a_real_shaped_case(self):
        built = case()
        resolved = {
            spec.label: ui_schema.resolve(spec, built, extra={"document_count": 0})
            for section in self.schema.sections
            for spec in section.fields
        }
        self.assertEqual(resolved["Investigation Number"], "337-1478")
        self.assertEqual(resolved["Instituted"], "2026-05-20")
        self.assertEqual(resolved["Complainant(s)"], ["Acme Inc."])
        self.assertEqual(resolved["Administrative Law Judge"], ["Monica Bhattacharyya"])
        self.assertEqual(resolved["Notice of Institution"], "91 FR 33548")

    def test_a_source_nothing_can_answer_is_reported_but_render_extras_are_not(self):
        schema = ui_schema.from_dict(
            {
                "index_columns": [
                    {"label": "Docs", "source": "document_count", "type": "number"},
                    {"label": "Oops", "source": "targt_date", "type": "date"},
                ]
            }
        )
        self.assertEqual(ui_schema.unused_sources(schema, [case()]), ["targt_date"])

    def test_the_file_is_readable_json_with_a_version(self):
        raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)


if __name__ == "__main__":
    unittest.main()
