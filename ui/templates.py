"""Static HTML rendering for the investigation list and docket-style detail pages."""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Any

import dates

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
.shell { max-width: 1080px; margin: 0 auto; }
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
  padding: 0.85rem 1.1rem;
  font-size: 0.88rem;
  vertical-align: middle;
}
table.list thead th {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  font-weight: 600;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}
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
.phase-chip {
  display: inline-block;
  font-size: 0.78rem;
  color: var(--muted);
}
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
.field-grid .field-value { font-size: 0.95rem; }
table.list .attachments a { display: block; font-size: 0.85rem; margin-bottom: 0.15rem; }
table.list .attachments a:last-child { margin-bottom: 0; }
"""


def _e(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _val(value: str | None, fallback: str = "Unknown") -> str:
    return _e(value) if value else fallback


def slug_for(investigation_number: str) -> str:
    return investigation_number.replace("/", "-")


def _page(title: str, body: str, *, style: str = APP_STYLE, wrapper_class: str = "shell", script: str = "") -> str:
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
<div class="{wrapper_class}">
{body}
</div>
{script_tag}
</body>
</html>
"""


_STATUS_PILL_CLASS = {
    "active": "pill-green",
    "pending institution": "pill-amber",
    "terminated": "pill-gray",
    "suspended": "pill-amber",
}


def _status_pill(status: str | None) -> str:
    if not status:
        return f'<span class="pill pill-gray">Unknown</span>'
    key = status.strip().lower()
    css_class = _STATUS_PILL_CLASS.get(key, "pill-blue")
    return f'<span class="pill {css_class}">{_e(status)}</span>'


def _date(value: str | None) -> str:
    """Every date on the site goes through here. See dates.py for why the
    day/month order is decided by the source format rather than per value.
    """
    return _e(dates.format_ui(value))


_INDEX_SCRIPT = """
(function () {
  const rows = Array.from(document.querySelectorAll('table.list tbody tr[data-search]'));
  const search = document.getElementById('search');
  const statusFilter = document.getElementById('status-filter');
  const noResults = document.getElementById('no-results');

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
  }

  if (search) search.addEventListener('input', apply);
  if (statusFilter) statusFilter.addEventListener('change', apply);
  apply();
})();
"""


def render_index(investigations: list[dict[str, Any]]) -> str:
    def sort_key(inv: dict[str, Any]) -> str:
        return dates.sort_key(inv.get("date_initiated") or inv.get("last_refreshed"))

    rows = sorted(investigations, key=sort_key, reverse=True)
    status_counts: Counter[str] = Counter((inv.get("investigation_status") or "Unknown") for inv in rows)

    stats_html = "".join(
        f'<div class="stat"><div class="n">{count}</div><div class="l">{_e(status)}</div></div>'
        for status, count in [("Total", len(rows)), *sorted(status_counts.items())]
    )

    statuses_present = sorted({inv.get("investigation_status") or "Unknown" for inv in rows})
    status_options = "".join(
        f'<option value="{_e(s)}">{_e(s)}</option>' for s in statuses_present
    )

    if not rows:
        body_rows = ""
        empty_state = '<div class="empty-state">No investigations tracked yet. Run <code>python cli.py discover</code>.</div>'
    else:
        empty_state = ""
        row_html = []
        for inv in rows:
            number = inv.get("investigation_number") or ""
            title = inv.get("title") or number or "Untitled Investigation"
            status = inv.get("investigation_status") or "Unknown"
            search_blob = _e(
                " ".join(
                    str(v)
                    for v in [title, number, inv.get("docket_number"), inv.get("investigation_type"), status]
                    if v
                ).lower()
            )
            row_html.append(
                f"""<tr data-search="{search_blob}" data-status="{_e(status)}">
  <td><a class="case-link" href="investigations/{_e(slug_for(number))}.html">{_val(title)}</a></td>
  <td class="mono">{_val(number)}</td>
  <td class="mono">{_date(inv.get('date_initiated'))}</td>
  <td><span class="phase-chip">{_val(inv.get('investigation_phase'))}</span></td>
  <td>{_status_pill(status)}</td>
</tr>"""
            )
        body_rows = "\n".join(row_html)

    body = f"""
<div class="hero">
  <div>
    <p class="eyebrow">U.S. International Trade Commission &middot; Section 337</p>
    <h1>ITC 337 Investigations</h1>
  </div>
  <div class="stats">{stats_html}</div>
</div>
<div class="toolbar">
  <input type="search" id="search" placeholder="Search by case name, docket, or number&hellip;">
  <select id="status-filter">
    <option value="">All statuses</option>
    {status_options}
  </select>
</div>
<div class="card">
  <table class="list">
    <thead>
      <tr>
        <th>Case</th>
        <th>Investigation Number</th>
        <th>Date Initiated</th>
        <th>Phase</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
  {empty_state}
  <div id="no-results" class="no-results" hidden>No investigations match your filters.</div>
</div>
<p class="footer-note">Generated from EDIS data &middot; {len(rows)} investigation(s) tracked.</p>
"""
    return _page("ITC 337 Investigations", body, script=_INDEX_SCRIPT)


_PARTY_NAME_STOPWORDS = {
    "inc", "llc", "ltd", "co", "corp", "corporation", "company", "and", "the",
    "of", "electronics", "electronic", "technologies", "technology", "group",
    "holdings", "holding", "america", "americas", "usa", "international",
}


def _significant_tokens(name: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z]+", name.lower())
        if len(t) >= 3 and t not in _PARTY_NAME_STOPWORDS
    }


def _same_party(name_a: str, name_b: str) -> bool:
    """EDIS restates the same party's name inconsistently across documents
    (word order swapped, "Electronics" vs. "Electronic" typos, etc.), so an
    exact string match misses real matches. Compare on distinctive word
    overlap instead, ignoring generic corporate-suffix words.
    """
    if not name_a or not name_b:
        return False
    if name_a.strip().lower() == name_b.strip().lower():
        return True
    tokens_a = _significant_tokens(name_a)
    tokens_b = _significant_tokens(name_b)
    if not tokens_a or not tokens_b:
        return False
    overlap = tokens_a & tokens_b
    return len(overlap) / min(len(tokens_a), len(tokens_b)) >= 0.5


def _extract_parties(documents: list[dict[str, Any]]) -> dict[str, str | None]:
    """EDIS doesn't tag which party is Complainant vs. Respondent -- it only
    gives filedBy/onBehalfOf/firmOrganization per document. Infer roles from
    context: the earliest "Complaint" document tells us the Complainant side
    directly (a complaint can only be filed by/for the Complainant); the ALJ
    is whoever files "on behalf of" the Administrative Law Judge; Respondents
    are inferred from documents whose title explicitly says "Respondent(s)"
    and whose filer isn't the Complainant or the ALJ.
    """

    def sort_key(doc: dict[str, Any]) -> str:
        return dates.sort_key(doc.get("document_date") or doc.get("official_received_date"))

    docs_asc = sorted(documents, key=sort_key)

    alj: str | None = None
    for doc in docs_asc:
        on_behalf_of = (doc.get("on_behalf_of") or "").strip()
        if on_behalf_of.lower() == "administrative law judge":
            alj = doc.get("filed_by")
            break

    complainant_name: str | None = None
    complainant_attorney: str | None = None
    complainant_firm: str | None = None
    for doc in docs_asc:
        if (doc.get("document_type") or "").strip().lower() == "complaint":
            complainant_name = doc.get("on_behalf_of")
            complainant_attorney = doc.get("filed_by")
            complainant_firm = doc.get("firm_organization")
            break

    respondent_names: list[str] = []
    respondent_firms: list[str] = []
    seen_names: set[str] = set()
    seen_firms: set[str] = set()
    for doc in docs_asc:
        title = (doc.get("title") or "").lower()
        on_behalf_of = (doc.get("on_behalf_of") or "").strip()
        if not on_behalf_of or "respondent" not in title:
            continue
        key = on_behalf_of.lower()
        if key == "administrative law judge" or _same_party(on_behalf_of, complainant_name or ""):
            continue
        if key not in seen_names and not any(_same_party(on_behalf_of, existing) for existing in respondent_names):
            seen_names.add(key)
            respondent_names.append(on_behalf_of)
        firm = (doc.get("firm_organization") or "").strip()
        if firm and firm.lower() not in seen_firms:
            seen_firms.add(firm.lower())
            respondent_firms.append(firm)

    return {
        "alj": alj,
        "complainant": complainant_name,
        "complainant_attorney": complainant_attorney,
        "complainant_firm": complainant_firm,
        "respondent": "; ".join(respondent_names) or None,
        "respondent_firm": "; ".join(respondent_firms) or None,
    }


def render_detail(investigation: dict[str, Any], documents: list[dict[str, Any]]) -> str:
    def doc_sort_key(doc: dict[str, Any]) -> str:
        return dates.sort_key(doc.get("document_date") or doc.get("official_received_date"))

    docs_sorted = sorted(documents, key=doc_sort_key, reverse=True)

    if not docs_sorted:
        doc_rows = ""
        docs_empty_state = '<div class="empty-state">No documents on file.</div>'
    else:
        docs_empty_state = ""
        doc_rows_list = []
        for doc in docs_sorted:
            is_complaint = "complaint" in (doc.get("document_type") or "").lower()
            badge = ' <span class="pill pill-blue">Complaint</span>' if is_complaint else ""
            attachments_html = "".join(
                f'<a href="{_e(att["href"])}" target="_blank" rel="noopener">{_val(att.get("label"), "Download")}</a>'
                for att in doc.get("attachments", [])
            ) or '<span class="case-sub">&mdash;</span>'
            doc_rows_list.append(
                f"""<tr>
  <td class="mono">{_date(doc.get('document_date') or doc.get('official_received_date'))}</td>
  <td>{_val(doc.get('document_type'))}{badge}</td>
  <td>{_val(doc.get('title'))}</td>
  <td>{_val(doc.get('filed_by'))}{f' <span class="case-sub">for {_e(doc.get("on_behalf_of"))}</span>' if doc.get('on_behalf_of') else ''}</td>
  <td class="attachments">{attachments_html}</td>
</tr>"""
            )
        doc_rows = "\n".join(doc_rows_list)

    parties = _extract_parties(documents)
    number = investigation.get("investigation_number")

    body = f"""
<a class="back-link" href="../index.html">&larr; All investigations</a>
<div class="detail-hero">
  <p class="eyebrow">U.S. International Trade Commission &middot; Section 337</p>
  <h1>{_val(investigation.get('title'), 'Untitled Investigation')}</h1>
  <div class="badges">
    {_status_pill(investigation.get('investigation_status'))}
    <span class="phase-chip">{_val(investigation.get('investigation_phase'))}</span>
  </div>
</div>

<div class="section-block">
  <h2>Investigation Information</h2>
  <div class="card field-grid">
    <div><div class="field-label">Investigation Number</div><div class="field-value">{_val(number)}</div></div>
    <div><div class="field-label">Date Initiated</div><div class="field-value">{_date(investigation.get('date_initiated'))}</div></div>
    <div><div class="field-label">Investigation Type</div><div class="field-value">{_val(investigation.get('investigation_type'))}</div></div>
    <div><div class="field-label">ITC Docket Number</div><div class="field-value">{_val(investigation.get('docket_number'))}</div></div>
    <div><div class="field-label">Investigation Phase</div><div class="field-value">{_val(investigation.get('investigation_phase'))}</div></div>
    <div><div class="field-label">Investigation Status</div><div class="field-value">{_val(investigation.get('investigation_status'))}</div></div>
  </div>
</div>

<div class="section-block">
  <h2>Parties</h2>
  <div class="card field-grid">
    <div><div class="field-label">Administrative Law Judge</div><div class="field-value">{_val(parties['alj'])}</div></div>
    <div><div class="field-label">Complainant</div><div class="field-value">{_val(parties['complainant'])}</div></div>
    <div><div class="field-label">Complainant Attorney</div><div class="field-value">{_val(parties['complainant_attorney'])}</div></div>
    <div><div class="field-label">Complainant Law Firm</div><div class="field-value">{_val(parties['complainant_firm'])}</div></div>
    <div><div class="field-label">Respondent</div><div class="field-value">{_val(parties['respondent'])}</div></div>
    <div><div class="field-label">Respondent Law Firm</div><div class="field-value">{_val(parties['respondent_firm'])}</div></div>
  </div>
</div>

<div class="section-block">
  <h2>Documents ({len(docs_sorted)})</h2>
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
      <tbody>
        {doc_rows}
      </tbody>
    </table>
    {docs_empty_state}
  </div>
</div>

<p class="footer-note">Investigation {_e(number)} &middot; last refreshed {_date(investigation.get('last_refreshed'))}.</p>
"""
    return _page(f"{investigation.get('title') or number} - Docket", body)
