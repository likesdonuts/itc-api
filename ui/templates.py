"""Static HTML for the investigation list and the per-investigation pages.

What appears on a page is not decided here: `ui_schema.json` lists the
sections, labels and IDS field names, and this module renders whatever it
says (see schema.py). The only things hard-coded are the page furniture --
the hero, the stage table, the EDIS documents table and the row buttons.
"""

from __future__ import annotations

import html
from collections import Counter
from typing import Any

import dates
import schema as ui_schema

APP_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f5f6f8;
  --surface: #ffffff;
  --surface-2: #fafbfc;
  --border: #e3e6ea;
  --ink: #14181f;
  --muted: #64707d;
  --accent: #3457d5;
  --accent-weak: #eef1fd;
  --shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 8px rgba(16,24,40,0.04);
  --radius: 12px;
  --green-bg: #e6f6ec; --green-fg: #1b7a3d;
  --amber-bg: #fef3e0; --amber-fg: #a15c00;
  --gray-bg: #eef0f3; --gray-fg: #4a5361;
  --blue-bg: #e8eefd; --blue-fg: #2646b8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216;
    --surface: #171b21;
    --surface-2: #1c2129;
    --border: #2a2f38;
    --ink: #e7eaee;
    --muted: #8a94a3;
    --accent: #7c93f7;
    --accent-weak: #232a45;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 1px 8px rgba(0,0,0,0.25);
    --green-bg: #10301d; --green-fg: #5fd38a;
    --amber-bg: #35270a; --amber-fg: #f0b545;
    --gray-bg: #232830; --gray-fg: #aab2bd;
    --blue-bg: #1c2440; --blue-fg: #9db0f5;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2.5rem 1.25rem 4rem;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.shell { max-width: 1240px; margin: 0 auto; }
.hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 1.75rem;
}
.hero .eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--muted);
  margin: 0 0 0.35rem;
}
.hero h1 { margin: 0; font-size: 1.65rem; font-weight: 700; letter-spacing: -0.01em; }
.stats { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 0.6rem 1rem;
  min-width: 92px;
  text-align: center;
  box-shadow: var(--shadow);
}
.stat .n { font-size: 1.35rem; font-weight: 700; line-height: 1.1; }
.stat .l { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-top: 0.15rem; }
.toolbar {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
  margin-bottom: 1.1rem;
}
.toolbar input[type="search"], .toolbar select {
  font: inherit;
  font-size: 0.9rem;
  padding: 0.55rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--surface);
  color: var(--ink);
}
.toolbar input[type="search"] { flex: 1 1 240px; }
.toolbar select { flex: 0 0 auto; }
.toolbar input:focus, .toolbar select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-weak);
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}
table.list { width: 100%; border-collapse: collapse; }
table.list th, table.list td {
  text-align: left;
  padding: 0.8rem 1rem;
  font-size: 0.88rem;
  vertical-align: middle;
}
table.list thead th {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  font-weight: 600;
  white-space: nowrap;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}
table.list td:first-child { min-width: 230px; }
/* Column count comes from the schema, so let a wide table scroll inside its
   card rather than squeezing the case name into one word per line. */
.table-wrap { overflow-x: auto; }
table.list tbody tr { border-bottom: 1px solid var(--border); transition: background 0.1s; }
table.list tbody tr:last-child { border-bottom: none; }
table.list tbody tr:hover { background: var(--surface-2); }
table.list tbody tr[hidden] { display: none; }
.case-link { display: block; font-weight: 600; color: var(--ink); }
.case-link:hover { color: var(--accent); text-decoration: none; }
.case-sub { color: var(--muted); font-size: 0.8rem; margin-top: 0.1rem; }
.mono { font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--muted); }
.pill {
  display: inline-flex;
  align-items: center;
  font-size: 0.74rem;
  font-weight: 600;
  padding: 0.22rem 0.6rem;
  border-radius: 999px;
  white-space: nowrap;
}
.pill-green { background: var(--green-bg); color: var(--green-fg); }
.pill-amber { background: var(--amber-bg); color: var(--amber-fg); }
.pill-gray { background: var(--gray-bg); color: var(--gray-fg); }
.pill-blue { background: var(--blue-bg); color: var(--blue-fg); }
.phase-chip { display: inline-block; font-size: 0.78rem; color: var(--muted); }
.notice {
  background: var(--amber-bg);
  color: var(--amber-fg);
  border: 1px solid transparent;
  border-radius: var(--radius);
  padding: 0.75rem 1rem;
  margin-bottom: 1.1rem;
  font-size: 0.85rem;
}
.notice code {
  background: rgba(0,0,0,0.06);
  border-radius: 5px;
  padding: 0.05rem 0.3rem;
  font-size: 0.82rem;
}
td.actions { white-space: nowrap; }
.btn {
  font: inherit;
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0.3rem 0.65rem;
  margin-right: 0.35rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  cursor: pointer;
}
.btn.btn-quiet { background: var(--surface); border-color: var(--border); color: var(--ink); }
.btn:hover:not(:disabled) { filter: brightness(1.06); }
.btn.btn-quiet:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.action-status {
  display: block;
  margin-top: 0.3rem;
  font-size: 0.74rem;
  color: var(--muted);
  white-space: normal;
}
.action-status.done { color: var(--green-fg); font-weight: 600; }
.action-status.failed { color: var(--amber-fg); }
.empty-state, .no-results {
  padding: 3rem 1rem;
  text-align: center;
  color: var(--muted);
  font-size: 0.9rem;
}
.footer-note {
  margin-top: 1.5rem;
  font-size: 0.78rem;
  color: var(--muted);
  text-align: center;
}
.back-link {
  display: inline-block;
  margin-bottom: 1.1rem;
  font-size: 0.85rem;
  color: var(--muted);
}
.back-link:hover { color: var(--accent); }
.detail-hero { margin-bottom: 1.75rem; }
.detail-hero h1 { margin: 0.3rem 0 0.6rem; font-size: 1.55rem; font-weight: 700; letter-spacing: -0.01em; }
.detail-hero .badges { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.section-block { margin-top: 1.75rem; }
.section-block h2 {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  font-weight: 700;
  margin: 0 0 0.75rem;
}
.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1.1rem 1.5rem;
  padding: 1.25rem 1.4rem;
}
.field-grid .field-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin-bottom: 0.25rem;
}
.field-grid .field-value { font-size: 0.95rem; overflow-wrap: anywhere; }
.field-grid ul { margin: 0; padding-left: 1.1rem; }
.field-grid li { font-size: 0.92rem; }
details.stage {
  border-top: 1px solid var(--border);
  padding: 0.85rem 1.4rem;
}
details.stage:first-child { border-top: none; }
details.stage > summary {
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 600;
  display: flex;
  gap: 0.6rem;
  align-items: center;
  flex-wrap: wrap;
}
details.stage > summary .case-sub { margin: 0; font-weight: 400; }
details.stage .field-grid { padding: 1rem 0 0.25rem; }
table.list .attachments a { display: block; font-size: 0.85rem; margin-bottom: 0.15rem; }
table.list .attachments a:last-child { margin-bottom: 0; }
"""


DASH = '<span class="case-sub">&mdash;</span>'


def _e(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _val(value: Any, fallback: str = "Unknown") -> str:
    return _e(value) if value not in (None, "", []) else fallback


def slug_for(investigation_number: str) -> str:
    return str(investigation_number).replace("/", "-")


def _date(value: Any) -> str:
    """Every date on the site goes through here. See dates.py for why the
    day/month order is decided by the source format rather than per value.
    """
    return _e(dates.format_ui(value))


def _page(title: str, body: str, *, style: str = APP_STYLE, script: str = "") -> str:
    script_tag = f"<script>{script}</script>" if script else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{style}</style>
</head>
<body>
<div class="shell">
{body}
</div>
{script_tag}
</body>
</html>
"""


_STATUS_PILL_CLASS = {
    "active": "pill-green",
    "pending before the alj": "pill-blue",
    "pending before the commission": "pill-blue",
    "before the alj - internal remand": "pill-blue",
    "pre-institution": "pill-amber",
    "not instituted": "pill-gray",
    "complaint/request withdrawn": "pill-gray",
    "terminated": "pill-gray",
    "inactive": "pill-gray",
}


def _status_pill(status: Any) -> str:
    if not status:
        return '<span class="pill pill-gray">Unknown</span>'
    css_class = _STATUS_PILL_CLASS.get(str(status).strip().lower(), "pill-amber")
    return f'<span class="pill {css_class}">{_e(status)}</span>'


def _rendered(spec: ui_schema.FieldSpec, value: Any, *, href: str | None = None) -> str:
    """One resolved value as HTML, according to the field's type."""
    if value in (None, "", []):
        return ""
    if spec.type == "status":
        return _status_pill(value)
    if spec.type == "date":
        return f'<span class="mono">{_date(value)}</span>'
    if spec.type in ("number", "mono"):
        return f'<span class="mono">{_e(value)}</span>'
    if spec.type == "bool":
        return "Yes" if value else "No"
    if spec.type == "case_link" and href:
        return f'<a class="case-link" href="{_e(href)}">{_e(value)}</a>'
    if spec.type == "list":
        values = value if isinstance(value, list) else [value]
        if len(values) == 1:
            return _e(values[0])
        items = "".join(f"<li>{_e(item)}</li>" for item in values)
        return f"<ul>{items}</ul>"
    return _e(value)


def _resolved_cells(
    section: ui_schema.Section,
    case: dict[str, Any],
    *,
    stage: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    stage_only: bool = False,
) -> list[tuple[str, str]]:
    """The section's (label, HTML) pairs, dropping the fields with nothing in them."""
    cells = []
    for spec in section.fields:
        value = ui_schema.resolve(
            spec, case, stage=stage, extra=extra, stage_only=stage_only
        )
        rendered = _rendered(spec, value)
        if rendered:
            cells.append((spec.label, rendered))
    return cells


def _field_grid(cells: list[tuple[str, str]], *, card: bool = True) -> str:
    if not cells:
        return ""
    body = "".join(
        f'<div><div class="field-label">{_e(label)}</div>'
        f'<div class="field-value">{rendered}</div></div>'
        for label, rendered in cells
    )
    return f'<div class="{"card field-grid" if card else "field-grid"}">{body}</div>'


def _fields_section(
    section: ui_schema.Section,
    case: dict[str, Any],
    *,
    stage: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    heading: bool = True,
    stage_only: bool = False,
) -> str:
    """A field grid, with empty fields left out. Returns "" if nothing is set."""
    cells = _resolved_cells(
        section, case, stage=stage, extra=extra, stage_only=stage_only
    )
    if not cells:
        return ""
    if not heading:
        return _field_grid(cells, card=False)
    grid = _field_grid(cells)
    return f'<div class="section-block"><h2>{_e(section.title)}</h2>{grid}</div>'


_INDEX_SCRIPT = """
(function () {
  const rows = Array.from(document.querySelectorAll('table.list tbody tr[data-search]'));
  const search = document.getElementById('search');
  const statusFilter = document.getElementById('status-filter');
  const noResults = document.getElementById('no-results');
  const shown = document.getElementById('shown-count');

  function apply() {
    const q = (search && search.value || '').trim().toLowerCase();
    const status = statusFilter ? statusFilter.value : '';
    let visible = 0;
    rows.forEach((row) => {
      const matchesQuery = !q || row.dataset.search.includes(q);
      const matchesStatus = !status || row.dataset.status === status;
      const show = matchesQuery && matchesStatus;
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (noResults) noResults.hidden = rows.length === 0 || visible > 0;
    if (shown) shown.textContent = visible;
  }

  if (search) search.addEventListener('input', apply);
  if (statusFilter) statusFilter.addEventListener('change', apply);
  apply();
})();

(function () {
  // The row buttons call the local server from cli.py serve. Opened straight
  // off disk there is nothing listening, so say so rather than failing later.
  const buttons = Array.from(document.querySelectorAll('button[data-action]'));
  if (location.protocol === 'file:') {
    const notice = document.getElementById('offline-notice');
    if (notice) notice.hidden = false;
    buttons.forEach(function (button) {
      button.disabled = true;
      button.title = 'Start the local server first: python cli.py serve';
    });
    return;
  }

  function setBusy(busy) {
    buttons.forEach(function (button) { button.disabled = busy; });
  }

  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      const row = button.closest('tr');
      const status = row.querySelector('.action-status');
      const withDocuments = button.dataset.action === 'fetch-docs';
      setBusy(true);
      status.hidden = false;
      status.className = 'action-status';
      status.textContent = withDocuments ? 'Fetching documents...' : 'Updating...';

      fetch('api/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ number: button.dataset.number, documents: withDocuments })
      }).then(function (response) {
        return response.json().then(function (result) {
          if (!response.ok || !result.ok) {
            throw new Error(result.message || ('HTTP ' + response.status));
          }
          return result;
        });
      }).then(function (result) {
        // Hold the outcome on screen long enough to read before the page
        // reloads onto the freshly rendered data.
        status.className = 'action-status done';
        status.textContent = result.message;
        setTimeout(function () { location.reload(); }, 1500);
      }).catch(function (error) {
        status.className = 'action-status failed';
        status.textContent = 'Failed: ' + error.message;
        setBusy(false);
      });
    });
  });
})();
"""

_DETAIL_SCRIPT = """
(function () {
  // Stage rows in the table link to the stage's own block further down; open
  // it when it is the link target, since it is collapsed by default.
  function openTarget() {
    if (!location.hash) return;
    const target = document.querySelector(location.hash);
    if (target && target.tagName === 'DETAILS') target.open = true;
  }
  window.addEventListener('hashchange', openTarget);
  openTarget();
})();
"""


def _search_blob(case: dict[str, Any]) -> str:
    parts = [
        case.get("title"),
        case.get("investigation_number"),
        case.get("docket_number"),
        case.get("official_number"),
        case.get("status"),
        *(case.get("phases") or []),
    ]
    stage = ui_schema.stage_for(case)
    if stage:
        for item in (stage.get("lists") or {}).get("participants", []):
            parts.append(item.get("name"))
    return _e(" ".join(str(part) for part in parts if part).lower())


def render_index(
    cases: list[dict[str, Any]],
    schema: ui_schema.Schema,
    *,
    document_counts: dict[str, int] | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    document_counts = document_counts or {}
    meta = meta or {}
    columns = schema.index_columns

    rows = sorted(
        cases,
        key=lambda case: dates.sort_key(case.get("date_initiated")),
        reverse=True,
    )

    status_counts: Counter[str] = Counter(str(case.get("status") or "Unknown") for case in rows)
    headline = [("Total", len(rows)), *status_counts.most_common(4)]
    stats_html = "".join(
        f'<div class="stat"><div class="n">{count}</div><div class="l">{_e(label)}</div></div>'
        for label, count in headline
    )
    status_options = "".join(
        f'<option value="{_e(status)}">{_e(status)}</option>'
        for status in sorted(status_counts)
    )

    header_html = "".join(f"<th>{_e(column.label)}</th>" for column in columns)

    row_html = []
    for case in rows:
        number = str(case.get("investigation_number") or "")
        extra = {"document_count": document_counts.get(number, 0)}
        href = f"investigations/{slug_for(number)}.html"
        cells = []
        for column in columns:
            value = ui_schema.resolve(column, case, extra=extra)
            rendered = _rendered(column, value, href=href)
            cells.append(f"<td>{rendered or DASH}</td>")
        status = str(case.get("status") or "Unknown")
        row_html.append(
            f"""<tr data-search="{_search_blob(case)}" data-status="{_e(status)}">
  {''.join(cells)}
  <td class="actions">
    <button class="btn" data-action="update" data-number="{_e(number)}" title="Refresh this case's document list from EDIS, without downloading PDFs">Update</button>
    <button class="btn btn-quiet" data-action="fetch-docs" data-number="{_e(number)}" title="Refresh the document list and download any missing PDFs">Fetch docs</button>
    <span class="action-status" hidden></span>
  </td>
</tr>"""
        )

    empty_state = (
        ""
        if rows
        else '<div class="empty-state">No investigations yet. Run <code>python cli.py sync</code>.</div>'
    )

    snapshot = meta.get("snapshot_day")
    provenance = (
        f"IDS snapshot {_date(snapshot)}" if snapshot else "no IDS snapshot stored yet"
    )

    body = f"""
<div class="hero">
  <div>
    <p class="eyebrow">U.S. International Trade Commission &middot; Section 337</p>
    <h1>ITC 337 Investigations</h1>
  </div>
  <div class="stats">{stats_html}</div>
</div>
<div class="notice" id="offline-notice" hidden>
  <strong>Update and Fetch docs are switched off</strong> because this page was opened
  straight from disk, where it has no way to reach EDIS. Run
  <code>python cli.py serve</code> (or double-click <code>serve.bat</code>) and use the
  page it opens to enable them.
</div>
<div class="toolbar">
  <input type="search" id="search" placeholder="Search by case name, number, party&hellip;">
  <select id="status-filter">
    <option value="">All statuses</option>
    {status_options}
  </select>
</div>
<div class="card">
  <div class="table-wrap">
    <table class="list">
      <thead>
        <tr>
          {header_html}
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {"".join(row_html)}
      </tbody>
    </table>
  </div>
  {empty_state}
  <div id="no-results" class="no-results" hidden>No investigations match your filters.</div>
</div>
<p class="footer-note">
  Showing <span id="shown-count">{len(rows)}</span> of {len(rows)} investigation(s) &middot; {provenance}
</p>
"""
    return _page("ITC 337 Investigations", body, script=_INDEX_SCRIPT)


def _stage_label(stage: dict[str, Any]) -> str:
    phase = stage.get("phase") or "Stage"
    started = stage.get("start_date")
    return f"{phase} ({dates.format_ui(started, 'date unknown')})" if started else str(phase)


def _stages_section(
    section: ui_schema.Section, case: dict[str, Any], field_sections: list[ui_schema.Section]
) -> str:
    """The stage table plus one collapsed block per stage.

    A case with a single stage is fully described by the sections above, so
    this only appears once a case has picked up a second proceeding.
    """
    stages = case.get("stages") or []
    if len(stages) < 2:
        return ""

    header = "".join(f"<th>{_e(column.label)}</th>" for column in section.columns)
    rows = []
    for stage in stages:
        anchor = f"stage-{stage.get('stage_id')}"
        cells = []
        for index, column in enumerate(section.columns):
            rendered = _rendered(column, ui_schema.resolve(column, case, stage=stage))
            if index == 0:
                rendered = f'<a href="#{_e(anchor)}">{rendered or _e(_stage_label(stage))}</a>'
            cells.append(f"<td>{rendered or DASH}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")

    # A stage block is there to say what that stage says, so a field holding
    # the same value as the primary stage -- the investigation number, its
    # type, often the parties -- is left out rather than repeated per stage.
    primary = ui_schema.stage_for(case, "primary") or stages[0]
    baselines = [
        dict(_resolved_cells(field_section, case, stage=primary, stage_only=True))
        for field_section in field_sections
    ]

    blocks = []
    for stage in stages:
        anchor = f"stage-{stage.get('stage_id')}"
        grids = "".join(
            _field_grid(
                [
                    (label, rendered)
                    for label, rendered in _resolved_cells(
                        field_section, case, stage=stage, stage_only=True
                    )
                    if baseline.get(label) != rendered
                ],
                card=False,
            )
            for field_section, baseline in zip(field_sections, baselines)
        )
        if stage.get("is_primary"):
            note = "the sections above describe this stage"
        elif not grids:
            note = "nothing recorded for this stage differs from the sections above"
        else:
            note = ""
        note = f'<span class="case-sub">{note}</span>' if note else ""
        blocks.append(
            f"""<details class="stage" id="{_e(anchor)}">
  <summary>{_e(_stage_label(stage))} {_status_pill(stage.get('status'))} {note}</summary>
  {grids}
</details>"""
        )

    return f"""<div class="section-block">
  <h2>{_e(section.title)} ({len(stages)})</h2>
  <div class="card">
    <table class="list">
      <thead><tr>{header}</tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <p class="case-sub">IDS files each stage of an investigation as its own record. The
  sections above describe the {_e(ui_schema.stage_for(case, 'primary').get('phase') or 'first')}
  stage; every stage is listed here, with its own details.</p>
  <div class="card" style="margin-top:0.75rem">{''.join(blocks)}</div>
</div>"""


def _documents_section(section: ui_schema.Section, documents: list[dict[str, Any]]) -> str:
    def sort_key(doc: dict[str, Any]) -> str:
        return dates.sort_key(doc.get("document_date") or doc.get("official_received_date"))

    docs_sorted = sorted(documents, key=sort_key, reverse=True)

    if not docs_sorted:
        rows = ""
        empty = (
            '<div class="empty-state">No documents fetched yet. Use the '
            "<strong>Fetch docs</strong> button on the list page, or run "
            "<code>python cli.py docs &lt;number&gt;</code>.</div>"
        )
    else:
        empty = ""
        parts = []
        for doc in docs_sorted:
            is_complaint = "complaint" in (doc.get("document_type") or "").lower()
            badge = ' <span class="pill pill-blue">Complaint</span>' if is_complaint else ""
            attachments = "".join(
                f'<a href="{_e(att["href"])}" target="_blank" rel="noopener">'
                f'{_val(att.get("label"), "Download")}</a>'
                for att in doc.get("attachments") or []
            ) or '<span class="case-sub">&mdash;</span>'
            on_behalf_of = doc.get("on_behalf_of")
            filed_for = f' <span class="case-sub">for {_e(on_behalf_of)}</span>' if on_behalf_of else ""
            parts.append(
                f"""<tr>
  <td class="mono">{_date(doc.get('document_date') or doc.get('official_received_date'))}</td>
  <td>{_val(doc.get('document_type'))}{badge}</td>
  <td>{_val(doc.get('title'))}</td>
  <td>{_val(doc.get('filed_by'))}{filed_for}</td>
  <td class="attachments">{attachments}</td>
</tr>"""
            )
        rows = "".join(parts)

    return f"""<div class="section-block">
  <h2>{_e(section.title)} ({len(docs_sorted)})</h2>
  <div class="card">
    <table class="list">
      <thead>
        <tr>
          <th>Date</th>
          <th>Type</th>
          <th>Title</th>
          <th>Filed By</th>
          <th>Attachments</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    {empty}
  </div>
</div>"""


def render_detail(
    case: dict[str, Any], documents: list[dict[str, Any]], schema: ui_schema.Schema
) -> str:
    number = case.get("investigation_number")
    field_sections = [s for s in schema.sections if s.kind == "fields"]
    extra = {"document_count": len(documents)}

    blocks = []
    for section in schema.sections:
        if section.kind == "fields":
            blocks.append(_fields_section(section, case, extra=extra))
        elif section.kind == "stages":
            blocks.append(_stages_section(section, case, field_sections))
        elif section.kind == "documents":
            blocks.append(_documents_section(section, documents))

    stage_badge = (
        f'<span class="phase-chip">{case["stage_count"]} stages</span>'
        if (case.get("stage_count") or 0) > 1
        else ""
    )
    source_note = (
        "from the IDS investigations feed"
        if case.get("source") == "ids"
        else "from the EDIS complaint feed; not in IDS yet"
    )

    body = f"""
<a class="back-link" href="../index.html">&larr; All investigations</a>
<div class="detail-hero">
  <p class="eyebrow">U.S. International Trade Commission &middot; Section 337</p>
  <h1>{_val(case.get('title'), 'Untitled Investigation')}</h1>
  <div class="badges">
    {_status_pill(case.get('status'))}
    <span class="phase-chip">{_val(case.get('phase'), 'Phase unknown')}</span>
    {stage_badge}
  </div>
</div>
{''.join(block for block in blocks if block)}
<p class="footer-note">
  Investigation {_e(number)} &middot; case information {_e(source_note)}
  &middot; IDS snapshot {_date(case.get('ids_snapshot'))}
</p>
"""
    return _page(f"{case.get('title') or number} - Investigation", body, script=_DETAIL_SCRIPT)
