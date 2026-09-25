"""Whether a record's claims analysis needs building, from what is on disk.

The case page shows one of:

    create        no analysis yet                       "Create claims analysis"
    up_to_date    nothing new since the build           disabled, "Up to date as of ..."
    new_activity  new public documents, or the IDS      "Update claims analysis"
                  record changed, since the build
    failed        the last attempt failed               "Retry claims analysis"

It is worked out from the stored analysis, the documents index and the case
record -- all refreshed by the daily sync -- so loading a page never calls
EDIS.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def primary_stage(case: dict[str, Any]) -> dict[str, Any]:
    stages = case.get("stages") or []
    return next((s for s in stages if s.get("is_primary")), stages[0] if stages else {})


def ids_hash(case: dict[str, Any]) -> str:
    """A fingerprint of what IDS says about the investigation's Violation
    phase; bookkeeping IDS restates on every sync is left out.
    """
    stage = primary_stage(case)
    record = {"fields": stage.get("fields") or {}, "lists": stage.get("lists") or {}}
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def public_document_ids(documents: list[dict[str, Any]]) -> set[str]:
    return {
        str(d.get("id"))
        for d in documents or []
        if d.get("id") and str(d.get("security_level") or "").lower() == "public"
    }


def state(
    case: dict[str, Any],
    documents: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
    *,
    pipeline_version: str | None = None,
) -> dict[str, Any]:
    analysis = analysis or {}
    attempt = analysis.get("last_attempt") or {}
    built_at = analysis.get("built_at")

    if attempt.get("error") and (not built_at or str(attempt.get("at") or "") > str(built_at)):
        return {"state": "failed", "error": attempt["error"], "built_at": built_at}
    if not built_at:
        return {"state": "create"}

    new_documents = sorted(public_document_ids(documents) - set(analysis.get("seen_documents") or ()))
    ids_changed = bool(case) and ids_hash(case) != analysis.get("ids_hash")
    method_changed = bool(pipeline_version) and pipeline_version != analysis.get("pipeline_version")
    reasons = []
    if new_documents:
        reasons.append(f"{len(new_documents)} new document{'s' if len(new_documents) != 1 else ''}")
    if ids_changed:
        reasons.append("the IDS record changed")
    if method_changed:
        reasons.append("the analysis method was updated")
    return {
        "state": "new_activity" if reasons else "up_to_date",
        "built_at": built_at,
        "reasons": reasons,
        "new_documents": new_documents,
    }
