"""The document lists on disk: one file per case under data/documents_index/."""

from __future__ import annotations

import json
import unittest

from support import DataDirTestCase

from datalayer.store import Store


class TestPerCaseFiles(DataDirTestCase):
    def test_the_old_single_file_is_read_and_replaced_on_the_next_save(self):
        legacy = {"337-1478": [{"id": "1"}], "337-1479": [{"id": "2"}]}
        (self.data_dir / "documents_index.json").write_text(json.dumps(legacy), encoding="utf-8")

        store = Store.load(self.data_dir)
        self.assertEqual(store.documents, legacy)
        store.save_documents()

        self.assertFalse((self.data_dir / "documents_index.json").exists())
        self.assertEqual(self.read_documents(), legacy)
        self.assertEqual(Store.load(self.data_dir).documents, legacy)

    def test_a_save_rewrites_only_the_cases_that_changed(self):
        store = self.store()
        store.put_documents("337-1478", [{"id": "1"}])
        store.put_documents("337-1479", [{"id": "2"}])
        store.save_documents()
        untouched = self.data_dir / "documents_index" / "337-1479.json"
        untouched.write_text(untouched.read_text(encoding="utf-8"), encoding="utf-8")
        before = untouched.stat().st_mtime_ns

        store = Store.load(self.data_dir)
        store.put_documents("337-1478", [{"id": "1"}, {"id": "3"}])
        store.save_documents()

        self.assertEqual(untouched.stat().st_mtime_ns, before)
        self.assertEqual(len(self.read_documents()["337-1478"]), 2)

    def test_a_renumbered_docket_leaves_no_file_behind(self):
        store = self.store()
        store.put_documents("337-3866", [{"id": "700", "attachments": []}])
        store.save_documents()

        store = Store.load(self.data_dir)
        store.rename("337-3866", "337-1478")
        store.save_documents()

        self.assertEqual(list(self.read_documents()), ["337-1478"])


if __name__ == "__main__":
    unittest.main()
