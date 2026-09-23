"""Reduce an IDS investigation row to plain named values.

IDS writes its fields for humans ("Hearing/Conf Start Date") and wraps almost
every value in a small object:

    "Investigation Status": {"ID": 11, "Name": "Terminated"}
    "Hearing/Conf Start Date": {"date": "2021-05-19T12:00:00.000+00:00", "isNa": false}
    "Phase Number": {"Name": 1}
    "Participants": [{"Participant": {"Name": "Acme Inc.", ...}, ...}]

Flattening that into scalars under stable names, plus lists of small uniform
dicts, is what lets `ui_schema.json` name any field in the feed without a
code change: every scalar in the row becomes `slug(label)` (so
"Hearing/Conf Start Date" is `hearing_conf_start_date`) and every list gets
one entry per item with a `label` to display.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import dates

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Values are only run through the date parser when the field name says it is a
# date; IDS dates are month-first ("07-10-2020"), which is only safe to assume
# because we know the source (see dates.py).
_DATE_NAME = re.compile(r"(^|_)(date|dates)($|_)")

_DISPLAY_KEYS = ("Name", "name", "Description", "description")

# Per-item bookkeeping that carries no meaning outside IDS's own database.
_NOISE = {"id", "investigation_id", "case_id", "staff_department_id"}


def slug(label: Any) -> str:
    """Field name for a IDS label, stable enough to write into a schema file.

    "F.R. Citation for Notice of Institution" -> fr_citation_for_notice_of_institution
    "finalDeterminationType"                  -> final_determination_type
    """
    text = str(label or "").replace(".", "")
    text = _CAMEL_BOUNDARY.sub("_", text)
    return _NON_ALNUM.sub("_", text.lower()).strip("_")


def _is_date_name(name: str) -> bool:
    return bool(_DATE_NAME.search(name))


def _clean(value: Any, name: str = "") -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return dates.to_iso(text) if _is_date_name(name) else text
    return value


def _date_object(value: dict[str, Any]) -> str | None:
    """{"date": ..., "isNa": ...} -- IDS's way of saying "this date, or it does
    not apply". The timestamps in these are always noon UTC placeholders, so
    only the calendar day is kept.
    """
    raw = value.get("date")
    if raw:
        iso = dates.to_iso(raw)
        return iso[:10] if iso else None
    return "N/A" if value.get("isNa") else None


def _is_staff(value: dict[str, Any]) -> bool:
    return "Staff Last Name" in value or "Staff First Name" in value


def _staff_name(value: dict[str, Any]) -> str | None:
    """IDS's "Name" for a person is their login ("Monica.Bhattacharyya")."""
    parts = [value.get("Staff First Name"), value.get("Staff Last Name")]
    name = " ".join(str(part).strip() for part in parts if part)
    return name or (str(value.get("Name")) if value.get("Name") else None)


def _display_of(name: str, value: dict[str, Any]) -> Any:
    for key in _DISPLAY_KEYS:
        if key in value:
            return value[key]
    # Some objects name their own value after themselves, e.g.
    # "Final Determination Type": {"finalDeterminationType": "Violation", ...}
    for key, child in value.items():
        if not isinstance(child, (dict, list)) and slug(key) == name:
            return child
    return None


def _flatten_object(name: str, value: dict[str, Any], into: dict[str, Any]) -> None:
    if set(value) <= {"date", "isNa"}:
        into[name] = _date_object(value)
        return
    if _is_staff(value):
        into[name] = _staff_name(value)
        for label, key in (("Staff Title", "title"), ("Email", "email"), ("Phone Number", "phone")):
            if value.get(label):
                into[f"{name}_{key}"] = _clean(value[label])
        return

    into[name] = _clean(_display_of(name, value), name)
    for key, child in value.items():
        child_name = slug(key)
        if isinstance(child, (dict, list)) or child_name in _NOISE:
            continue
        if child_name.endswith("_id") or child_name == name or child_name in _DISPLAY_KEYS:
            continue
        combined = f"{name}_{child_name}"
        if combined not in into:
            into[combined] = _clean(child, combined)


def scalars(item: Any) -> dict[str, Any]:
    """Flatten one object's own scalar fields, ignoring nested lists."""
    if not isinstance(item, dict):
        return {"label": str(item)} if item is not None else {}
    flat: dict[str, Any] = {}
    for label, value in item.items():
        name = slug(label)
        if name in _NOISE or isinstance(value, list):
            continue
        if isinstance(value, dict):
            _flatten_object(name, value, flat)
        else:
            flat[name] = _clean(value, name)
    return {key: value for key, value in flat.items() if value is not None}


def _labelled(flat: dict[str, Any], *preferred: str) -> dict[str, Any]:
    for key in preferred:
        if flat.get(key):
            flat["label"] = str(flat[key])
            return flat
    for value in flat.values():
        if isinstance(value, str):
            flat["label"] = value
            return flat
    return flat


def _participants(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        party = item.get("Participant") or {}
        name = str(party.get("Name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "label": str(name),
                "name": str(name),
                # IDS's own ID for the party, the same in every case it is in.
                "participant_id": party.get("ID"),
                "role": (item.get("Participant Type") or {}).get("name"),
                "country": (party.get("Country") or {}).get("name"),
                "disposition": (item.get("Participant Disposition") or {}).get("name"),
                "disposition_date": dates.to_iso(item.get("Participant Disposition Date")),
                "active_date": dates.to_iso(item.get("Active Date")),
                "inactive_date": dates.to_iso(item.get("Inactive Date")),
                "is_petitioner": bool(item.get("Is Petitioner?")),
            }
        )
    return _dedupe(out, ("name", "role"))


def _staff(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _staff_name(item.get("Staff Name") or {})
        if not name:
            continue
        out.append(
            {
                "label": name,
                "name": name,
                "role": (item.get("Staff Assigned Type") or {}).get("Name"),
                "title": (item.get("Staff Name") or {}).get("Staff Title"),
                "is_active": bool(item.get("Is Active?")),
                "active_date": dates.to_iso(item.get("Staff Active Date")),
            }
        )
    return _dedupe(out, ("name", "role"))


def _intellectual_property(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ip = item.get("Intellectual Property ID") or {}
        number = ip.get("Number")
        kind = (ip.get("Type") or {}).get("Name")
        if not number and not kind:
            continue
        out.append(
            {
                "label": " ".join(str(part) for part in (kind, number) if part),
                "number": number,
                "type": kind,
                "expiration_date": dates.to_iso(ip.get("IP Expiration Date")),
                "active_date": dates.to_iso(item.get("Active Date")),
                "inactive_date": dates.to_iso(item.get("Inactive Date")),
            }
        )
    return _dedupe(out, ("type", "number"))


def _unfair_acts(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("Unfair Act in Notice") or {}).get("name")
        if not name:
            continue
        out.append(
            {
                "label": str(name),
                "name": str(name),
                "is_instituted": bool(item.get("Is Instituted?")),
                "active_date": dates.to_iso(item.get("Active Date")),
                "inactive_date": dates.to_iso(item.get("Inactive Date")),
            }
        )
    return _dedupe(out, ("name",))


def _hts_numbers(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        hts = item.get("HTS Number & Description") or {}
        number = hts.get("Name")
        if not number:
            continue
        out.append(
            {
                "label": str(number),
                "number": str(number),
                "description": hts.get("Description"),
                "is_active": bool(item.get("Active")),
            }
        )
    return _dedupe(out, ("number",))


def _historic_hts(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        number = item.get("Historic HTS Number") if isinstance(item, dict) else None
        if number:
            out.append({"label": str(number), "number": str(number)})
    return _dedupe(out, ("number",))


def _associated_litigation(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        case_number = item.get("Case Number")
        case_name = item.get("Case Name")
        court = (item.get("Court Forum") or {}).get("Name")
        heading = " ".join(str(part) for part in (court, case_number) if part)
        out.append(
            {
                "label": f"{heading}: {case_name}" if case_name else heading,
                "case_number": case_number,
                "case_name": case_name,
                "court": court,
                "status": (item.get("Proceeding Status") or {}).get("Name"),
                "docketing_date": dates.to_iso(item.get("Docketing Date")),
            }
        )
    return _dedupe(out, ("court", "case_number"))


def _unfair_import_orders(items: list[Any]) -> list[dict[str, Any]]:
    """Only the order titles: the rest of this object restates participants and
    IP that are already on the row.
    """
    out = []
    for item in items:
        title = item.get("Order Title") if isinstance(item, dict) else None
        if title:
            out.append({"label": str(title), "title": str(title)})
    return _dedupe(out, ("title",))


def _names(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        name = item.get("Name") if isinstance(item, dict) else item
        if name:
            out.append({"label": str(name), "name": str(name)})
    return _dedupe(out, ("name",))


def _generic(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        flat = scalars(item)
        if flat:
            out.append(_labelled(flat, "name", "title", "number"))
    return out


_LIST_HANDLERS: dict[str, Callable[[list[Any]], list[dict[str, Any]]]] = {
    "participants": _participants,
    "staff": _staff,
    "intellectual_property": _intellectual_property,
    "unfair_act": _unfair_acts,
    "hts_number": _hts_numbers,
    "historic_hts": _historic_hts,
    "associated_litigation": _associated_litigation,
    "unfair_import_orders": _unfair_import_orders,
    "investigation_categories": _names,
    "countries": _names,
    "requestor": _names,
}


def _dedupe(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """IDS repeats a party or an unfair act once per order it appears on."""
    seen: set[tuple[Any, ...]] = set()
    out = []
    for item in items:
        identity = tuple(str(item.get(key) or "").strip().lower() for key in keys)
        if identity in seen:
            continue
        seen.add(identity)
        out.append({key: value for key, value in item.items() if value not in (None, "")})
    return out


def flatten_row(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Split one IDS row into (scalar fields, lists), both keyed by slug."""
    fields: dict[str, Any] = {}
    lists: dict[str, list[dict[str, Any]]] = {}

    for label, value in row.items():
        name = slug(label)
        if isinstance(value, list):
            items = _LIST_HANDLERS.get(name, _generic)(value)
            if items:
                lists[name] = items
        elif isinstance(value, dict):
            _flatten_object(name, value, fields)
        else:
            fields[name] = _clean(value, name)

    return {key: value for key, value in fields.items() if value is not None}, lists
