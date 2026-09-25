"""Shared fixtures: IDS rows shaped like the real feed, and a fake EDIS API.

Everything in the test suite runs offline, so the feed's oddities have to be
reproduced here rather than downloaded: nested {"ID", "Name"} objects,
{"date", "isNa"} dates, month-first date strings, and one row per stage of an
investigation.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer import ids  # noqa: E402
from datalayer.store import Store  # noqa: E402


def ids_row(
    number: str = "337-1478",
    *,
    phase: str = "Violation",
    investigation_id: int = 9001,
    status: str = "Active",
    topic: str = "Certain Wearable Devices",
    start_date: str | None = "05-20-2026",
    end_date: str | None = None,
    docket: str | None = "3866",
    complainants: tuple[str, ...] = ("Acme Inc.",),
    respondents: tuple[str, ...] = ("Globex Corp.",),
    alj: tuple[str, str] | None = ("Monica", "Bhattacharyya"),
    patents: tuple[str, ...] = ("10,945,648",),
    **overrides: Any,
) -> dict[str, Any]:
    participants = [
        {
            "Participant Type": {"name": "Complainant", "ID": 6},
            "Participant": {"Name": name, "Country": {"name": "United States of America"}},
            "Active Date": "05-20-2026",
            "Is Petitioner?": False,
        }
        for name in complainants
    ] + [
        {
            "Participant Type": {"name": "Respondent", "ID": 8},
            "Participant": {"Name": name, "Country": {"name": "Japan"}},
            "Active Date": "05-20-2026",
        }
        for name in respondents
    ]

    staff = []
    if alj:
        staff.append(
            {
                "Staff Assigned Type": {"ID": 1, "Name": "ALJ"},
                "Staff Active Date": "05-21-2026",
                "Is Active?": True,
                "Staff Name": {
                    "Staff First Name": alj[0],
                    "Staff Last Name": alj[1],
                    "Staff Title": "Administrative Law Judge",
                    "Name": f"{alj[0]}.{alj[1]}",
                },
            }
        )

    row: dict[str, Any] = {
        "Investigation Number": number,
        "official_investigation_number": number,
        "Investigation ID": investigation_id,
        "Case ID": investigation_id,
        "Investigation Phase": {"Name": phase},
        "Phase Number": {"Name": 1},
        "Investigation Status": {"ID": 1, "Name": status},
        "Investigation Type": {"Name": "Unfair Imports"},
        "Investigation Categories": [{"ID": 6, "Name": "337 - Unfair Imports", "Is Active?": True}],
        "Topic": topic,
        "Full Title": f"{topic}; Inv. No. 337-TA-{number.split('-')[-1]} ({phase})",
        "Docket Number": docket,
        "Start Date": start_date,
        "Investigation End Date": end_date,
        "Initiating Document Received Date": "03-22-2026",
        "Target Date": "09-26-2027",
        "F.R. Citation for Notice of Institution": "91 FR 33548",
        "Date of Publication of FR Notice (NOI)": {
            "date": "2026-05-26T12:00:00.000+00:00",
            "isNa": False,
        },
        "Party Comments Due Date": {"date": None, "isNa": True},
        "Case Manager": {
            "Staff First Name": "Nathaniel",
            "Staff Last Name": "Gibson",
            "Staff Title": "Case Manager",
            "Email": "Nathaniel.Gibson@usitc.gov",
            "Name": "Nathaniel.Gibson",
        },
        "Final Determination Type": {
            "finalDeterminationTypeId": 1,
            "finalDeterminationType": "Violation",
            "isActive": True,
        },
        "Participants": participants,
        "Staff": staff,
        "Intellectual Property": [
            {
                "Intellectual Property ID": {
                    "Type": {"ID": 2, "Name": "Patent"},
                    "Number": patent,
                    "IP Expiration Date": "08-25-2028",
                }
            }
            for patent in patents
        ],
        "Unfair Act": [
            {
                "Unfair Act in Notice": {"name": "Patent Infringement", "ID": 1},
                "Is Instituted?": True,
                "Active Date": "05-20-2026",
            }
        ],
        "Is Active?": True,
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not None}


def payload(rows: list[dict[str, Any]], *, date: str = "2026-09-21T22:00:01.996+00:00") -> dict:
    return {"data": rows, "date": date, "count": len(rows)}


def write_snapshot(
    ids_dir: Path,
    rows: list[dict[str, Any]],
    *,
    day: str = "2026-09-22",
    at: str = "120000",
) -> ids.Snapshot:
    ids_dir = Path(ids_dir)
    ids_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{day}T{at}Z"
    path = ids_dir / f"{ids.PREFIX}{stamp}{ids.SUFFIX}"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload(rows), handle)
    return ids.Snapshot(path=path, stamp=stamp)


EDIS_DOCUMENTS = [
    {
        "id": "100",
        "documentType": "Complaint",
        "documentTitle": "Complaint of Acme",
        "securityLevel": "Public",
        "filedBy": "A. Lawyer",
        "onBehalfOf": "Acme Inc.",
        "firmOrganization": "Firm LLP",
        "documentDate": "2026/01/13 00:00:00",
        "officialReceivedDate": "2026/01/13 09:00:00",
    }
]


class FakeEdisClient:
    """Stands in for EdisClient and records what was asked of it."""

    def __init__(self, documents=None, attachments=None):
        self.documents = documents or {}
        self.attachments = attachments or {}
        self.document_calls: list[str] = []
        self.downloads: list[tuple[str, str]] = []

    def list_documents(self, number: str):
        self.document_calls.append(number)
        return self.documents.get(number, [])

    def list_attachments(self, document_id: str):
        return self.attachments.get(str(document_id), [])

    def download_attachment(self, document_id: str, attachment_id: str, dest: Path):
        self.downloads.append((str(document_id), str(attachment_id)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake")
        return dest


class DataDirTestCase(unittest.TestCase):
    """A throwaway data/, site/ and data/ids/ per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.site_dir = self.root / "site"
        self.ids_dir = self.data_dir / "ids"
        self.data_dir.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def store(self) -> Store:
        return Store.load(self.data_dir)

    def read_json(self, name: str):
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

    def read_documents(self) -> dict:
        """data/documents_index/, one file per case, as {case: documents}."""
        return {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((self.data_dir / "documents_index").glob("*.json"))
        }

    @staticmethod
    def quiet(_message: str) -> None:
        return None
