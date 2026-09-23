"""The JSON-to-UI mapping, loaded from ui_schema.json.

The IDS feed carries far more about an investigation than a page should show,
and which fields matter is a matter of taste that changes. So the site does
not name IDS fields in code: `ui_schema.json` lists the sections, the labels
and the field names, and the templates render whatever it says.

A field spec is a small object:

    {"label": "Target Date", "source": "target_date", "type": "date"}
    {"label": "Complainant", "source": "participants",
     "where": {"role": "Complainant"}, "item": "name", "type": "list"}

`source` is looked up in this order, first match winning:

    1. the case record        investigation_number, title, status, stage_count
    2. the stage's fields     every scalar IDS publishes, flattened (see
                              datalayer/flatten.py) -- start_date, target_date,
                              fr_citation_for_notice_of_institution, ...
    3. the stage's lists      participants, staff, intellectual_property, ...

"the stage" is the primary (Violation) stage of the case unless the spec says
`"stage": "current"`. `python cli.py fields` prints everything available.

This module is shared by both layers, like dates.py: the data layer uses it to
check the schema, the UI layer to render from it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from datalayer.config import SCHEMA_PATH

FIELD_TYPES = {
    "text", "long_text", "date", "datetime", "bool", "list", "mono", "number", "status", "case_link",
}

# Values the UI layer supplies itself rather than reading from a case record.
RENDER_EXTRAS = {"document_count", "documents_fetched_at"}

SECTION_KINDS = {"fields", "stages", "documents", "parties"}
STAGE_CHOICES = {"primary", "current"}


class SchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    label: str
    source: str
    type: str = "text"
    stage: str = "primary"
    where: dict[str, Any] = field(default_factory=dict)
    item: str = "label"
    join: str = "; "
    limit: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FieldSpec":
        if not isinstance(raw, dict):
            raise SchemaError(f"a field must be an object, got {raw!r}")
        try:
            label, source = raw["label"], raw["source"]
        except KeyError as exc:
            raise SchemaError(f"field {raw!r} is missing {exc.args[0]!r}") from exc
        spec = cls(
            label=str(label),
            source=str(source),
            type=str(raw.get("type", "text")),
            stage=str(raw.get("stage", "primary")),
            where=dict(raw.get("where") or {}),
            item=str(raw.get("item", "label")),
            join=str(raw.get("join", "; ")),
            limit=raw.get("limit"),
        )
        if spec.type not in FIELD_TYPES:
            raise SchemaError(
                f"field {spec.label!r} has unknown type {spec.type!r}; "
                f"known types are {', '.join(sorted(FIELD_TYPES))}"
            )
        if spec.stage not in STAGE_CHOICES:
            raise SchemaError(
                f"field {spec.label!r} has stage {spec.stage!r}; expected primary or current"
            )
        return spec


@dataclass(frozen=True)
class RoleSpec:
    """One side of a "parties" section: {"label": "Respondents", "role": "Respondent"}."""

    label: str
    role: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RoleSpec":
        if not isinstance(raw, dict) or not raw.get("role"):
            raise SchemaError(f"a party role must be an object with a 'role', got {raw!r}")
        return cls(label=str(raw.get("label") or raw["role"]), role=str(raw["role"]))


@dataclass(frozen=True)
class Section:
    title: str
    kind: str = "fields"
    fields: tuple[FieldSpec, ...] = ()
    columns: tuple[FieldSpec, ...] = ()
    roles: tuple[RoleSpec, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Section":
        kind = str(raw.get("kind", "fields"))
        if kind not in SECTION_KINDS:
            raise SchemaError(
                f"section {raw.get('title')!r} has unknown kind {kind!r}; "
                f"known kinds are {', '.join(sorted(SECTION_KINDS))}"
            )
        return cls(
            title=str(raw.get("title") or ""),
            kind=kind,
            fields=tuple(FieldSpec.from_dict(f) for f in raw.get("fields") or ()),
            columns=tuple(FieldSpec.from_dict(c) for c in raw.get("columns") or ()),
            roles=tuple(RoleSpec.from_dict(r) for r in raw.get("roles") or ()),
        )

    def party_fields(self) -> "Section":
        """A "parties" section as plain name lists, one per role -- what a
        stage block shows, since counsel is recorded per case, not per stage.
        """
        return Section(
            title=self.title,
            fields=tuple(
                FieldSpec(
                    label=role.label,
                    source="participants",
                    where={"role": role.role},
                    item="name",
                    type="list",
                )
                for role in self.roles
            ),
        )


@dataclass(frozen=True)
class Schema:
    index_columns: tuple[FieldSpec, ...]
    sections: tuple[Section, ...]
    path: Path | None = None

    def section_titles(self) -> list[str]:
        return [section.title for section in self.sections]


def load(path: Path = SCHEMA_PATH) -> Schema:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaError(f"no UI schema at {path}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"{path.name} is not valid JSON: {exc}") from exc
    return from_dict(raw, path=path)


def from_dict(raw: dict[str, Any], *, path: Path | None = None) -> Schema:
    if not isinstance(raw, dict):
        raise SchemaError("the schema must be a JSON object")
    return Schema(
        index_columns=tuple(FieldSpec.from_dict(c) for c in raw.get("index_columns") or ()),
        sections=tuple(Section.from_dict(s) for s in raw.get("sections") or ()),
        path=path,
    )


def stage_for(case: dict[str, Any], which: str = "primary") -> dict[str, Any] | None:
    stages = case.get("stages") or []
    if not stages:
        return None
    flag = "is_current" if which == "current" else "is_primary"
    return next((s for s in stages if s.get(flag)), stages[-1] if which == "current" else stages[0])


def sources(case: dict[str, Any]) -> dict[str, str]:
    """Every `source` this case could answer, and where it comes from."""
    found = {key: "case" for key in case if key != "stages"}
    for stage in case.get("stages") or []:
        for key in stage.get("fields") or {}:
            found.setdefault(key, "stage field")
        for key in stage.get("lists") or {}:
            found.setdefault(key, "stage list")
    return found


def _matches(item: dict[str, Any], where: dict[str, Any]) -> bool:
    for key, wanted in where.items():
        value = item.get(key)
        options = wanted if isinstance(wanted, list) else [wanted]
        if isinstance(value, bool) or isinstance(wanted, bool):
            if value not in options:
                return False
            continue
        text = str(value or "").strip().lower()
        if text not in {str(option or "").strip().lower() for option in options}:
            return False
    return True


def _from_items(spec: FieldSpec, items: Iterable[Any]) -> list[str]:
    values: list[str] = []
    for item in items:
        if isinstance(item, dict):
            if spec.where and not _matches(item, spec.where):
                continue
            value = item.get(spec.item, item.get("label"))
        else:
            value = item
        if value in (None, "", []):
            continue
        text = str(value)
        if text not in values:
            values.append(text)
    if spec.limit:
        return values[: spec.limit]
    return values


def resolve(
    spec: FieldSpec,
    case: dict[str, Any],
    *,
    stage: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    stage_only: bool = False,
) -> Any:
    """The value a field spec names, or None when the case does not have it.

    `stage_only` drops the case scope, which is how a per-stage block shows
    what that stage says rather than repeating the case summary.
    """
    target = stage if stage is not None else stage_for(case, spec.stage)
    scopes: list[dict[str, Any]] = [
        (target or {}).get("fields") or {},
        (target or {}).get("lists") or {},
    ]
    if not stage_only:
        scopes = [extra or {}, case, *scopes]

    for scope in scopes:
        if spec.source not in scope:
            continue
        value = scope[spec.source]
        if isinstance(value, (list, tuple)):
            values = _from_items(spec, value)
            if spec.type == "list":
                return values
            return spec.join.join(values) or None
        if spec.type == "list":
            return [str(value)] if value not in (None, "") else []
        return value

    return [] if spec.type == "list" else None


def unused_sources(schema: Schema, cases: Iterable[dict[str, Any]]) -> list[str]:
    """Schema sources that no case can answer -- almost always a typo."""
    wanted = {
        spec.source
        for spec in [
            *schema.index_columns,
            *(spec for section in schema.sections for spec in (*section.fields, *section.columns)),
        ]
    }
    available: set[str] = set(RENDER_EXTRAS)
    for case in cases:
        available |= set(sources(case))
    return sorted(wanted - available)
