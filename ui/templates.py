"""Static HTML for the investigation list and the per-investigation pages.

What appears on a page is not decided here: `ui_schema.json` lists the
sections, labels and IDS field names, and this module renders whatever it
says (see schema.py). The only things hard-coded are the page furniture --
the hero, the stage table, the EDIS documents table and the control panel
that drives the local server (server.py).
"""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from typing import Any

import dates
import schema as ui_schema
from datalayer.claims import matrix as claims_matrix

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
.next-deadline { white-space: nowrap; font-variant-numeric: tabular-nums; }
.next-deadline small { display: block; white-space: normal; color: var(--muted); font-size: 0.78rem; max-width: 16rem;
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.due-panel { margin-bottom: 1rem; }
.due-head { margin-bottom: 0.5rem; }
table.due-list td { padding: 0.35rem 0.5rem; vertical-align: top; }
table.due-list td.due-date { width: 7.5rem; white-space: nowrap; font-weight: 600; font-variant-numeric: tabular-nums; min-width: 0; }
table.due-list td:first-child:not(.pick) { min-width: 0; }
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
table.list td:first-child:not(.pick) { min-width: 230px; }
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
.role-block { padding: 1.1rem 1.4rem; border-top: 1px solid var(--border); }
.role-block:first-child { border-top: none; }
.role-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin-bottom: 0.6rem;
}
.party-group {
  display: grid;
  grid-template-columns: minmax(200px, 1fr) minmax(260px, 2fr);
  gap: 0.5rem 1.5rem;
  padding: 0.75rem 0;
  border-top: 1px dashed var(--border);
}
.party-group:first-of-type { border-top: none; padding-top: 0; }
@media (max-width: 640px) { .party-group { grid-template-columns: 1fr; } }
.party-names { margin: 0; padding: 0; list-style: none; }
.party-names li { font-size: 0.95rem; font-weight: 600; margin-bottom: 0.2rem; }
.party-names .case-sub { font-weight: 400; }
.firm { margin-bottom: 0.7rem; }
.firm:last-child { margin-bottom: 0; }
.firm-name { font-size: 0.92rem; font-weight: 600; }
.firm-meta { font-size: 0.78rem; color: var(--muted); }
.attorneys { font-size: 0.88rem; margin-top: 0.15rem; overflow-wrap: anywhere; }
.attorneys .withdrawn { color: var(--muted); text-decoration: line-through; }
.attorneys details { display: inline; }
.attorneys summary { display: inline; cursor: pointer; color: var(--accent); }
.pill-lead { font-size: 0.66rem; padding: 0.08rem 0.45rem; margin-left: 0.2rem; vertical-align: 0.08em; }
.no-counsel { font-size: 0.85rem; color: var(--muted); font-style: italic; }
.non-party-reason { font-size: 0.84rem; font-weight: 400; color: var(--muted); margin-top: 0.15rem; }
.non-party-reason details.why { margin-top: 0.2rem; }
.non-party-reason details.why summary { cursor: pointer; color: var(--accent); }
.non-party-reason details.why p, .non-party-reason details.why ul { margin: 0.35rem 0 0; }
.non-party-reason details.why ul { padding-left: 1.1rem; }
.panel { padding: 0.9rem 1.1rem; margin-bottom: 1.1rem; }
.panel-status { display: flex; flex-wrap: wrap; gap: 0.35rem 1.1rem; font-size: 0.82rem; color: var(--muted); }
.panel-status .ok { color: var(--green-fg); font-weight: 600; }
.panel-status .warn { color: var(--amber-fg); font-weight: 600; }
.panel-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-top: 0.75rem; }
.panel-actions .btn { margin-right: 0; font-size: 0.84rem; padding: 0.45rem 0.85rem; }
.panel-actions .divider { width: 1px; align-self: stretch; background: var(--border); margin: 0 0.35rem; }
.picked-count { font-size: 0.82rem; color: var(--muted); }
.job { margin-top: 0.85rem; border-top: 1px solid var(--border); padding-top: 0.75rem; }
.job-head { font-size: 0.86rem; font-weight: 600; display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.job-message { font-size: 0.85rem; margin-top: 0.3rem; }
.job-message.warn { color: var(--amber-fg); }
.job-message.error { color: var(--amber-fg); font-weight: 600; }
.job-log {
  margin: 0.5rem 0 0;
  padding: 0.6rem 0.75rem;
  max-height: 14rem;
  overflow: auto;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 0.76rem;
  line-height: 1.45;
  white-space: pre-wrap;
  color: var(--muted);
}
th.pick, td.pick { width: 2.2rem; padding-right: 0; }
table.list td.pick + td { min-width: 230px; }
input.pick-case, #pick-all { width: 1rem; height: 1rem; cursor: pointer; }
time.stamp.today { color: var(--green-fg); font-weight: 600; }
.claims-actions { border-top: 1px solid var(--border); padding-top: 0.75rem; }
.claims-label { font-size: 0.78rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.claims-help { font-size: 0.82rem; color: var(--muted); }
.claims-help.failed { color: var(--amber-fg); font-weight: 600; }
.tabs { display: flex; gap: 0.25rem; margin-top: 1.5rem; border-bottom: 1px solid var(--border); }
.tabs a {
  padding: 0.55rem 1rem;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tabs a:hover { color: var(--ink); text-decoration: none; }
.tabs a.active { color: var(--accent); border-bottom-color: var(--accent); }
.na-steps { display: flex; list-style: none; margin: 1rem 0 0.9rem; padding: 0; gap: 0.25rem; flex-wrap: wrap; }
.na-steps li { flex: 1 1 7rem; font-size: 0.78rem; color: var(--muted); padding-top: 0.45rem; border-top: 3px solid var(--border); }
.na-steps li.done { border-top-color: var(--green-fg); color: var(--ink); }
.na-steps li.current { border-top-color: var(--accent); color: var(--accent); font-weight: 700; }
.na-hero { display: flex; gap: 2rem; flex-wrap: wrap; align-items: flex-start; }
.na-hero > div { flex: 1 1 16rem; min-width: 0; }
.na-label { font-size: 0.7rem; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.2rem; }
.na-next { font-size: 1.2rem; font-weight: 650; line-height: 1.3; }
.na-when { margin-top: 0.25rem; color: var(--muted); }
.na-count { display: inline-block; margin-left: 0.4rem; padding: 0.05rem 0.55rem; border-radius: 999px; font-size: 0.8rem;
  font-weight: 600; background: var(--blue-bg); color: var(--blue-fg); }
.na-count.soon { background: var(--amber-bg); color: var(--amber-fg); }
.na-card h3 { margin: 0 0 0.5rem; font-size: 0.95rem; }
.na-card h3 small, .na-past summary small { color: var(--muted); font-weight: 400; }
table.na-list { width: 100%; border-collapse: collapse; }
table.na-list td { padding: 0.55rem 0.5rem; border-top: 1px solid var(--border); vertical-align: top; }
table.na-list tr:first-child td { border-top: none; }
table.na-list td.na-date { width: 8.5rem; white-space: nowrap; font-weight: 600; font-variant-numeric: tabular-nums; }
table.na-list td.na-date small { display: block; font-weight: 400; color: var(--muted); }
table.na-list td.when { width: 7rem; text-align: right; white-space: nowrap; color: var(--muted); font-size: 0.84rem; }
table.na-list tr.is-next td { background: var(--accent-weak, var(--blue-bg)); }
table.na-list tr.is-next td.na-date { box-shadow: inset 3px 0 0 var(--accent); }
.na-title { font-weight: 500; }
.na-meta { margin-top: 0.15rem; font-size: 0.8rem; color: var(--muted); }
.na-meta > * + *::before { content: "·"; margin: 0 0.4rem; color: var(--border); }
.na-basis { font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.68rem; }
.na-basis.b-order { color: var(--green-fg); }
.na-basis.b-case-data { color: var(--blue-fg); }
.na-basis.b-by-rule { color: var(--amber-fg); }
.na-basis.b-docket { color: var(--gray-fg); }
.na-past { padding: 0.8rem 1.1rem; }
.na-past summary { cursor: pointer; font-weight: 600; font-size: 0.95rem; }
.na-past[open] summary { margin-bottom: 0.5rem; }
.na-past td { color: var(--muted); }
.na-notes { margin: 0; padding-left: 1.1rem; }
.na-notes li { margin-bottom: 0.3rem; }
.na-legend { font-size: 0.8rem; color: var(--muted); line-height: 1.55; }
.na-empty { color: var(--muted); padding: 0.3rem 0; }
.na-stay { border-left: 4px solid var(--amber-fg); background: var(--amber-bg); }
.na-stay strong { color: var(--amber-fg); }
.na-hold { color: var(--amber-fg); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.68rem; }
table.na-list tr.on-hold .na-title { color: var(--muted); }
table.na-list td.na-who { width: auto; }
table.na-list td.na-how { width: 11rem; white-space: nowrap; color: var(--muted); font-size: 0.85rem; }
@media (max-width: 640px) { table.na-list td.na-date { width: 6.5rem; } table.na-list td.when { display: none; } }
.sum-hero { display: flex; gap: 2rem; flex-wrap: wrap; align-items: flex-start; }
.sum-hero > div { flex: 1 1 18rem; min-width: 0; }
.sum-cost { font-size: 1.35rem; font-weight: 650; }
.sum-cost small { font-size: 0.85rem; font-weight: 400; color: var(--muted); }
.sum-hero p { margin: 0.35rem 0 0; color: var(--muted); font-size: 0.88rem; line-height: 1.5; }
.sum-hero .btn { margin-top: 0.7rem; }
table.na-list tr.sum-group td { padding-top: 0.9rem; font-size: 0.7rem; font-weight: 700; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em; border-top: none; }
.sum-pages { font-weight: 600; color: var(--ink); }
.sum-tag { font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.68rem; color: var(--green-fg); }
.sum-tag.assumed { color: var(--amber-fg); }
.primer { line-height: 1.6; }
.primer h3 { font-size: 0.98rem; margin: 1.2rem 0 0.35rem; }
.primer ul, .primer ol { padding-left: 1.3rem; margin: 0.3rem 0; }
.primer li { margin-bottom: 0.3rem; }
.primer-draft { display: inline-block; margin-left: 0.5rem; padding: 0.05rem 0.55rem; border-radius: 999px; font-size: 0.72rem;
  font-weight: 700; background: var(--amber-bg); color: var(--amber-fg); vertical-align: 0.1em; }
.primer-note { font-size: 0.8rem; color: var(--muted); }
.case-summary > .card { padding: 1rem 1.2rem; margin-bottom: 0.9rem; }
.case-summary > details.card { padding: 0.8rem 1.2rem; }
.case-summary h4 { font-size: 0.85rem; margin: 0.9rem 0 0.3rem; }
.case-summary h4 small { color: var(--muted); font-weight: 400; }
.primer p, .primer li { color: var(--ink); }
.claims-matrix th .stage-count { font-weight: 400; text-transform: none; letter-spacing: 0; margin-top: 0.15rem; }
.claims-matrix tr.patent-row th {
  background: var(--surface-2);
  font-size: 0.85rem;
  text-transform: none;
  letter-spacing: 0;
  color: var(--ink);
}
.claims-matrix td.claim-no { white-space: nowrap; font-weight: 600; min-width: 0; }
.chip {
  display: inline-block;
  font-size: 0.74rem;
  font-weight: 600;
  padding: 0.18rem 0.55rem;
  border-radius: 999px;
  border: 1px solid transparent;
  white-space: nowrap;
}
.chip-neutral { background: var(--gray-bg); color: var(--gray-fg); }
/* The spec's palette: navy, pale blue and orange also differ in lightness,
   and the outlines are distinguishable without color. */
.chip-withdrawn { background: transparent; color: var(--muted); border: 1px dashed var(--muted); }
.chip-settled { background: transparent; color: var(--ink); border: 1px solid var(--ink); }
.chip-infringed { background: #1F4E8C; color: #ffffff; }
.chip-not-infringed { background: #DCE8F7; color: #1F4E8C; }
.chip-invalid { background: #B8541A; color: #ffffff; }
.chip-appeal { background: transparent; color: #1F4E8C; border: 1px solid #1F4E8C; }
@media (prefers-color-scheme: dark) {
  .chip-appeal { color: #9db0f5; border-color: #9db0f5; }
}
.chip-default { background: var(--amber-bg); color: var(--amber-fg); border: 1px solid var(--amber-fg); }
.chip-varies { background: transparent; color: var(--muted); border: 1px dotted var(--muted); font-weight: 500; }
.claims-controls { display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; align-items: center; margin-bottom: 0.75rem; font-size: 0.88rem; }
.claims-controls select {
  font: inherit;
  margin-left: 0.4rem;
  padding: 0.35rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  max-width: 28rem;
}
.claims-view.hide-withdrawn tr[data-withdrawn] { display: none; }
details.other-events { margin-top: 0.75rem; }
details.other-events > summary { cursor: pointer; color: var(--muted); font-size: 0.85rem; margin-bottom: 0.5rem; }
.review-dot {
  display: inline-block;
  width: 0.5rem;
  height: 0.5rem;
  margin-left: 0.35rem;
  border-radius: 50%;
  background: #B8541A;
  vertical-align: 0.1em;
}
.picked-count.fresh { color: var(--amber-fg); }
.heading-note { font-weight: 400; text-transform: none; letter-spacing: 0; }
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


def _stamp(value: Any) -> str:
    """A moment the app recorded, in local time. The page script marks the
    ones from today, which it has to do itself: the page may be opened days
    after it was rendered.
    """
    return (
        f'<time class="mono stamp" datetime="{_e(value)}">'
        f"{_e(dates.format_ui_time(value))}</time>"
    )


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
    if spec.type == "datetime":
        return _stamp(value)
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
    if spec.type == "next_deadline":
        return _next_deadline_cell(value)
    return _e(value)


def _upcoming(record: dict[str, Any] | None, since: str) -> list[list[str]]:
    """A case's dated, not-on-hold events from `since` on: [[date, label], ...]."""
    return [
        [e["date"], e["label"]]
        for e in (record or {}).get("events") or []
        if e.get("date") and e["date"] >= since and not e.get("on_hold")
    ]


def _next_deadline_cell(value: dict[str, Any]) -> str:
    """The list page's Next deadline: the first of the case's coming dates
    (the script moves on to the next one as days pass), or "Stayed"."""
    if value.get("stayed"):
        return '<span class="pill pill-amber">Stayed</span>'
    items = value.get("items") or []
    if not items:
        return ""
    first = next((i for i in items if i[0] >= value.get("today", "")), None)
    text = f"{_date(first[0])}<small>{_e(first[1])}</small>" if first else ""
    return (
        f'<span class="next-deadline" data-next="{_e(json.dumps(items))}">{text}</span>'
    )


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
  // Next deadline and "due in the next 7 days", against the day the page is
  // viewed rather than the day it was rendered.
  const pad = function (n) { return String(n).padStart(2, '0'); };
  const now = new Date();
  const today = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
  const weekOut = new Date(now.getTime() + 7 * 86400000);
  const horizon = weekOut.getFullYear() + '-' + pad(weekOut.getMonth() + 1) + '-' + pad(weekOut.getDate());
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const show = function (iso) { const p = iso.split('-'); return pad(+p[2]) + ' ' + MONTHS[+p[1] - 1] + ' ' + p[0]; };
  const esc = function (s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };

  document.querySelectorAll('.next-deadline').forEach(function (cell) {
    const items = JSON.parse(cell.dataset.next || '[]');
    const first = items.find(function (i) { return i[0] >= today; });
    cell.innerHTML = first ? show(first[0]) + '<small>' + esc(first[1]) + '</small>' : '';
  });

  const data = document.getElementById('due-data');
  if (!data) return;
  const due = JSON.parse(data.textContent).filter(function (i) { return i[0] >= today && i[0] <= horizon; });
  if (!due.length) return;
  const body = document.getElementById('due-rows');
  const row = function (i) {
    return '<tr><td class="due-date">' + show(i[0]) + '</td><td><a href="' + i[4] + '">' + esc(i[1]) + '</a> '
      + '<span class="muted">' + esc(i[2]) + '</span></td><td>' + esc(i[3]) + '</td></tr>';
  };
  body.innerHTML = due.slice(0, 8).map(row).join('');
  document.getElementById('due-count').textContent = due.length + (due.length === 1 ? ' date' : ' dates');
  const more = document.getElementById('due-more');
  if (due.length > 8) {
    more.hidden = false;
    more.textContent = 'Show all ' + due.length;
    more.addEventListener('click', function () { body.innerHTML = due.map(row).join(''); more.hidden = true; });
  }
  document.getElementById('due-panel').hidden = false;
})();

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
    // A filtered-out row stays checked but is not acted on; tell the panel.
    document.dispatchEvent(new Event('rows-filtered'));
  }

  if (search) search.addEventListener('input', apply);
  if (statusFilter) statusFilter.addEventListener('change', apply);
  apply();
})();
"""

# The control panel on both pages. It talks to server.py, so opened straight
# from disk (file://) there is nothing to call: the buttons are disabled and
# the page says how to start the app instead.
_CONTROL_SCRIPT = """
(function () {
  const panel = document.getElementById('control-panel');
  if (!panel) return;
  // The panel's buttons, and any a tab adds (the Summary tab's "Estimate cost").
  const buttons = Array.from(document.querySelectorAll('button[data-job]'));
  const picks = Array.from(document.querySelectorAll('input.pick-case'));
  const pickAll = document.getElementById('pick-all');
  const count = document.getElementById('picked-count');
  const statusEl = document.getElementById('panel-status');
  const jobBox = document.getElementById('job');
  const offline = location.protocol === 'file:';
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  let running = false;

  function when(iso, withTime) {
    if (!iso) return 'never';
    // A bare day ("2026-09-23") is a calendar date, not midnight UTC -- which
    // would show as the day before anywhere west of Greenwich.
    const day = /^(\\d{4})-(\\d{2})-(\\d{2})$/.exec(iso);
    const d = day ? new Date(+day[1], +day[2] - 1, +day[3]) : new Date(iso);
    if (isNaN(d)) return iso;
    let text = d.getDate() + ' ' + MONTHS[d.getMonth()] + ' ' + d.getFullYear();
    if (withTime !== false) {
      text += ', ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    }
    return text;
  }
  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function isToday(iso) {
    const d = iso ? new Date(iso) : null;
    return !!d && !isNaN(d) && sameDay(d, new Date());
  }
  // "Today" depends on when the page is looked at, not when it was rendered.
  document.querySelectorAll('time.stamp').forEach(function (t) {
    if (isToday(t.getAttribute('datetime'))) t.classList.add('today');
  });
  function ticked() {
    return picks.filter(function (p) { return p.checked && !p.closest('tr').hidden; });
  }
  function selected() {
    if (panel.dataset.number) return [panel.dataset.number];
    return ticked().map(function (p) { return p.value; });
  }
  function refresh() {
    const n = selected().length;
    buttons.forEach(function (b) {
      b.disabled = offline || running || (b.dataset.job === 'documents' && n === 0)
        || b.dataset.current === '1';  // a claims analysis already up to date
    });
    if (count) {
      const fresh = ticked().filter(function (p) { return isToday(p.dataset.fetched); }).length;
      count.textContent = !n ? 'Tick cases below to fetch their documents'
        : n + (n === 1 ? ' case selected' : ' cases selected')
          + (fresh ? ' \\u00b7 ' + (fresh === n ? (n === 1 ? 'already' : 'all') : fresh) + ' fetched today' : '');
      count.className = 'picked-count' + (fresh ? ' fresh' : '');
    }
    if (pickAll) {
      const shown = picks.filter(function (p) { return !p.closest('tr').hidden; });
      pickAll.checked = shown.length > 0 && shown.every(function (p) { return p.checked; });
    }
  }
  picks.forEach(function (p) { p.addEventListener('change', refresh); });
  if (pickAll) {
    pickAll.addEventListener('change', function () {
      picks.forEach(function (p) { if (!p.closest('tr').hidden) p.checked = pickAll.checked; });
      refresh();
    });
  }
  document.addEventListener('rows-filtered', refresh);

  function item(text, cls) {
    const span = document.createElement('span');
    span.textContent = text;
    if (cls) span.className = cls;
    return span;
  }

  if (offline) {
    const notice = document.getElementById('offline-notice');
    if (notice) notice.hidden = false;
    statusEl.replaceChildren(item('Opened from disk: start the app to use these buttons', 'warn'));
    refresh();
    return;
  }

  function showStatus(s) {
    const parts = [];
    const sync = s.sync || {};
    if (sync.finished_at && sameDay(new Date(sync.finished_at), new Date())) {
      parts.push(item('\\u2713 Synced today at ' + when(sync.finished_at).split(', ')[1], 'ok'));
    } else {
      parts.push(item(sync.finished_at ? "Today's sync has not run yet (last " + when(sync.finished_at) + ')' : 'Never synced', 'warn'));
    }
    if (sync.snapshot) parts.push(item('Case data from ' + when(sync.snapshot, false)));
    if ('fetchedAt' in panel.dataset) {
      // A case page: when this case's documents were fetched, not the app's.
      const mine = panel.dataset.fetchedAt;
      parts.push(!mine
        ? item("This case's documents have not been fetched yet", 'warn')
        : mine === 'unknown'
          ? item("This case's documents were fetched before fetch times were recorded")
          : item("This case's documents fetched " + when(mine), isToday(mine) ? 'ok' : ''));
    } else {
      parts.push(item('Documents last fetched ' + when((s.documents || {}).finished_at)));
      const daily = s.daily || {};
      if (daily.seconds != null) {
        const mins = Math.round(daily.seconds / 60);
        const took = item('Last daily sync took ' + (mins >= 1 ? mins + ' min' : Math.round(daily.seconds) + ' s'));
        if (daily.summary) took.title = daily.summary;
        parts.push(took);
      }
      const filled = s.backfill || {};
      if (filled.remaining != null) {
        parts.push(filled.remaining
          ? item('Backfill: ' + filled.remaining + ' cases still without a document list', 'warn')
          : item('\\u2713 Every case has its document list', 'ok'));
      }
    }
    const token = s.token || {};
    if (token.state === 'missing') {
      parts.push(item('No EDIS token in .env', 'warn'));
    } else if (token.state === 'expired') {
      parts.push(item('EDIS token expired ' + when(token.expires_at), 'warn'));
    } else if (token.expires_at) {
      const hours = (new Date(token.expires_at) - new Date()) / 36e5;
      parts.push(item('EDIS token valid until ' + when(token.expires_at), hours < 48 ? 'warn' : ''));
    }
    statusEl.replaceChildren.apply(statusEl, parts);
  }

  function showJob(job) {
    if (!job) return;
    jobBox.hidden = false;
    document.getElementById('job-title').textContent = job.label;
    const state = document.getElementById('job-state');
    const message = document.getElementById('job-message');
    if (job.state === 'running') {
      state.className = 'pill pill-blue';
      state.textContent = 'Running\\u2026';
      message.textContent = '';
      message.className = 'job-message';
    } else {
      state.className = 'pill ' + (job.level === 'ok' ? 'pill-green' : 'pill-amber');
      state.textContent = job.level === 'ok' ? 'Done' : (job.level === 'warn' ? 'Done, with warnings' : 'Failed');
      message.textContent = job.message;
      message.className = 'job-message ' + job.level;
    }
    const stop = document.getElementById('job-stop');
    stop.hidden = !job.stoppable;
    stop.disabled = !!job.stopping;
    stop.textContent = job.stopping ? 'Stopping\\u2026' : 'Stop';
    const log = document.getElementById('job-log');
    log.textContent = (job.lines || []).join('\\n');
    log.scrollTop = log.scrollHeight;
  }
  document.getElementById('job-stop').addEventListener('click', function () {
    const stop = this;
    stop.disabled = true;
    stop.textContent = 'Stopping\\u2026';
    fetch('/api/jobs/stop', { method: 'POST' }).catch(function () { stop.disabled = false; });
  });

  function poll() {
    setTimeout(function () {
      fetch('/api/jobs/current').then(function (r) { return r.json(); }).then(function (body) {
        showJob(body.job);
        if (body.job && body.job.state === 'running') return poll();
        running = false;
        if (body.job && body.job.level === 'ok') {
          // Reload onto the freshly rendered pages; the result shows again after.
          setTimeout(function () { location.reload(); }, 1500);
        } else {
          refresh();
          fetch('/api/status').then(function (r) { return r.json(); }).then(showStatus);
        }
      }).catch(function () { poll(); });
    }, 1000);
  }

  function start(body) {
    running = true;
    refresh();
    fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.json().then(function (result) {
        if (!response.ok || !result.ok) throw new Error(result.message || ('HTTP ' + response.status));
        showJob(result.job);
        poll();
      });
    }).catch(function (error) {
      running = false;
      showJob({ label: 'Could not start', state: 'done', level: 'error', message: error.message, lines: [] });
      refresh();
    });
  }

  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.dataset.job === 'daily') start({ kind: 'daily' });
      else if (b.dataset.job === 'backfill') start({ kind: 'backfill' });
      else if (b.dataset.job === 'claims') start({ kind: 'claims', number: panel.dataset.number });
      else if (b.dataset.job === 'summary_estimate') {
        start({ kind: 'summary_estimate', number: panel.dataset.number });
        panel.scrollIntoView({ behavior: 'smooth' });  // the job's progress shows up there
      }
      else start({ kind: 'documents', numbers: selected(), download: b.dataset.download === '1' });
    });
  });

  fetch('/api/status').then(function (r) { return r.json(); }).then(function (s) {
    showStatus(s);
    const job = s.job;
    if (job && job.state === 'running') {
      running = true;
      showJob(job);
      poll();
    } else if (job && job.finished_at && (new Date() - new Date(job.finished_at)) < 120000) {
      showJob(job);  // the outcome of the job that just reloaded this page
    }
    refresh();
  }).catch(function () {
    statusEl.replaceChildren(item('Cannot reach the app. Is its window still open?', 'warn'));
    buttons.forEach(function (b) { b.disabled = true; });
  });
  refresh();
})();
"""


def _claims_actions(claims_state: dict[str, Any] | None) -> str:
    """The claims-analysis button, per the build's state (see
    datalayer/claims/status.py): create, update, up to date, or retry.
    """
    state = (claims_state or {}).get("state") or "create"
    built = (claims_state or {}).get("built_at")
    current = ""
    help_class = "claims-help"
    if state == "create":
        label, helper = "Create claims analysis", "Tracks how the asserted claims narrow, stage by stage"
    elif state == "up_to_date":
        label, helper = "Update claims analysis", f"Up to date as of {dates.format_ui_time(built)}"
        current = ' data-current="1"'
    elif state == "new_activity":
        reasons = "; ".join((claims_state or {}).get("reasons") or [])
        label, helper = "Update claims analysis", f"New activity since {dates.format_ui_time(built)}: {reasons}"
    else:
        label = "Retry claims analysis"
        helper = f"The last attempt failed: {(claims_state or {}).get('error') or 'unknown error'}"
        help_class += " failed"
    return f"""
  <div class="panel-actions claims-actions">
    <span class="claims-label">Claims analysis</span>
    <button class="btn" data-job="claims"{current}>{_e(label)}</button>
    <span class="{help_class}">{_e(helper)}</span>
  </div>"""


def _control_panel(
    number: str | None = None,
    fetched_at: str | None = None,
    claims_state: dict[str, Any] | None = None,
) -> str:
    """The panel of buttons and status that drives the data layer.

    On the list page it runs the daily sync and fetches documents for the
    ticked rows; on a case page (`number`) it fetches for that case alone and
    builds its claims analysis.
    """
    if number:
        actions = f"""
    <button class="btn" data-job="documents" data-download="1" title="List this case's documents and download any PDFs not on disk yet">Fetch documents</button>
    <button class="btn btn-quiet" data-job="documents" data-download="0" title="Refresh the document list without downloading PDFs">Update list</button>"""
    else:
        actions = """
    <button class="btn" data-job="daily" title="Download today's case data, refresh the documents and attorneys of cases already collected, and rebuild the pages">Run daily sync</button>
    <button class="btn btn-quiet" data-job="backfill" title="List the documents (no PDFs) of every case that has no document list yet, newest first. Long: it saves as it goes, can be stopped, and continues where it left off">Backfill all cases</button>
    <span class="divider"></span>
    <button class="btn" data-job="documents" data-download="1" title="List the ticked cases' documents and download any PDFs not on disk yet">Fetch documents</button>
    <button class="btn btn-quiet" data-job="documents" data-download="0" title="Refresh the ticked cases' document lists without downloading PDFs">Update lists</button>
    <span class="picked-count" id="picked-count"></span>"""
    # On a case page the status line reports that case's last fetch, not the
    # app's; the attribute is there (empty) even when it was never fetched.
    data_number = (
        f' data-number="{_e(number)}" data-fetched-at="{_e(fetched_at or "")}"' if number else ""
    )
    return f"""<div class="notice" id="offline-notice" hidden>
  <strong>These buttons are switched off</strong> because this page was opened
  straight from disk. Double-click <code>ITC Tracker.bat</code> (or run
  <code>python cli.py serve</code>) and use the page it opens.
</div>
<div class="card panel" id="control-panel"{data_number}>
  <div class="panel-status" id="panel-status"><span>Checking status&hellip;</span></div>
  <div class="panel-actions">{actions}
  </div>{_claims_actions(claims_state) if number else ""}
  <div class="job" id="job" hidden>
    <div class="job-head"><span id="job-title"></span><span id="job-state"></span><button class="btn btn-quiet" id="job-stop" hidden title="Stop after the current case; what is done so far is kept">Stop</button></div>
    <div class="job-message" id="job-message"></div>
    <pre class="job-log" id="job-log"></pre>
  </div>
</div>"""

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

(function () {
  // Overview, Next actions and Claims tabs, whichever the case has. The tab
  // is in the address (#next, #claims), so a reload or a shared link keeps it.
  const tabs = Array.from(document.querySelectorAll('.tabs a[data-tab]'));
  if (!tabs.length) return;
  const names = tabs.map(function (t) { return t.dataset.tab; });
  function show() {
    const asked = location.hash.replace('#', '');
    const wanted = names.indexOf(asked) >= 0 ? asked : 'overview';
    tabs.forEach(function (t) { t.classList.toggle('active', t.dataset.tab === wanted); });
    document.querySelectorAll('.tab-panel').forEach(function (p) {
      p.hidden = p.id !== 'tab-' + wanted;
    });
  }
  window.addEventListener('hashchange', show);
  show();
})();

(function () {
  // Next actions: past, next and how far away, against the day the page is
  // viewed rather than the day it was rendered.
  const rows = Array.from(document.querySelectorAll('#na-upcoming tr.next-event'));
  if (!rows.length) return;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const DAY = 86400000;
  const pastBody = document.getElementById('na-past-rows');
  let next = null, upcoming = 0;
  const past = [];
  rows.forEach(function (row) {
    const parts = row.dataset.date.split('-').map(Number);
    const days = Math.round((new Date(parts[0], parts[1] - 1, parts[2]) - today) / DAY);
    const cell = row.querySelector('td.when');
    if (days < 0) {
      cell.textContent = -days === 1 ? 'yesterday' : (-days < 60 ? -days + ' days ago' : '');
      past.push(row);
    } else if (row.dataset.hold) {
      // Suspended by a stay: shown, but not what comes next.
      upcoming += 1;
      cell.textContent = 'on hold';
    } else {
      upcoming += 1;
      cell.textContent = days === 0 ? 'today' : (days === 1 ? 'tomorrow' : 'in ' + days + ' days');
      if (!next) next = {row: row, days: days};
    }
  });
  // The most recent past dates first.
  past.reverse().forEach(function (row) { pastBody.appendChild(row); });
  if (past.length) {
    document.getElementById('na-past').hidden = false;
    document.getElementById('na-past-count').textContent = '(' + past.length + ')';
  }
  document.getElementById('na-up-count').textContent = upcoming ? '(' + upcoming + ' ahead)' : '';
  document.getElementById('na-up-empty').hidden = upcoming > 0;
  const what = document.getElementById('next-what');
  const whenEl = document.getElementById('next-when');
  if (next) {
    next.row.classList.add('is-next');
    what.textContent = next.row.dataset.label;
    const date = next.row.querySelector('td.na-date').firstChild.textContent;
    const badge = document.createElement('span');
    badge.className = 'na-count' + (next.days <= 7 ? ' soon' : '');
    badge.textContent = next.row.querySelector('td.when').textContent;
    whenEl.textContent = date;
    whenEl.appendChild(badge);
  } else if (document.querySelector('.na-stay')) {
    what.textContent = 'Stayed';
    whenEl.textContent = 'Scheduled dates are on hold until the stay ends.';
  } else {
    what.textContent = 'Awaiting decision';
    whenEl.textContent = 'No date on record is still ahead.';
  }
})();

(function () {
  // The Claims tab's respondent filter and "Hide withdrawn claims".
  const pick = document.getElementById('claims-respondent');
  const hide = document.getElementById('claims-hide-withdrawn');
  const views = Array.from(document.querySelectorAll('.claims-view'));
  function update() {
    views.forEach(function (v) {
      v.hidden = pick ? v.dataset.view !== pick.value : v.dataset.view !== '0';
      v.classList.toggle('hide-withdrawn', !!(hide && hide.checked));
    });
  }
  if (pick) pick.addEventListener('change', update);
  if (hide) hide.addEventListener('change', update);
  update();
})();
"""


WITHDRAWN_LABEL = "No longer in the IDS feed"
WITHDRAWN_PILL = f'<span class="pill pill-amber">{WITHDRAWN_LABEL}</span>'


def _filter_status(case: dict[str, Any]) -> str:
    """What the status filter and the headline counts group a case under.

    A withdrawn case keeps its last known status in the Status column, but
    groups under the withdrawal, which is the more useful thing to filter on
    and the only way to find them all at once.
    """
    if case.get("withdrawn"):
        return WITHDRAWN_LABEL
    return str(case.get("status") or "Unknown")


def _search_blob(case: dict[str, Any], counsel: dict[str, Any] | None = None) -> str:
    parts = [
        case.get("title"),
        case.get("investigation_number"),
        case.get("docket_number"),
        case.get("official_number"),
        case.get("status"),
        WITHDRAWN_LABEL if case.get("withdrawn") else None,
        *(case.get("phases") or []),
    ]
    stage = ui_schema.stage_for(case)
    if stage:
        for item in (stage.get("lists") or {}).get("participants", []):
            parts.append(item.get("name"))
    for rep in (counsel or {}).get("representations") or []:
        parts.append(rep.get("firm"))
        parts.extend(attorney.get("name") for attorney in rep.get("attorneys") or [])
    parts.extend(party.get("name") for party in (counsel or {}).get("non_parties") or [])
    return _e(" ".join(str(part) for part in parts if part).lower())


def render_index(
    cases: list[dict[str, Any]],
    schema: ui_schema.Schema,
    *,
    document_counts: dict[str, int] | None = None,
    pdf_counts: dict[str, int] | None = None,
    fetched_at: dict[str, str] | None = None,
    counsel: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    next_actions: dict[str, Any] | None = None,
    today: str | None = None,
) -> str:
    """`pdf_counts` is how many of each case's documents have a PDF on disk;
    `fetched_at` is when each case's documents were last fetched;
    `next_actions` each open case's entry in data/next_actions.json, for the
    Next deadline column and the "due in the next 7 days" panel.
    """
    document_counts = document_counts or {}
    pdf_counts = pdf_counts or {}
    fetched_at = fetched_at or {}
    counsel = counsel or {}
    meta = meta or {}
    next_actions = next_actions or {}
    today = today or _today_iso()
    # Coming dates from a week back, so a page opened days after it was
    # rendered still finds what is next.
    since = _days_before(today, 7)
    columns = schema.index_columns

    rows = sorted(
        cases,
        key=lambda case: dates.sort_key(case.get("date_initiated")),
        reverse=True,
    )

    status_counts: Counter[str] = Counter(_filter_status(case) for case in rows)
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
        record = next_actions.get(number)
        extra = {
            "document_count": document_counts.get(number, 0),
            "pdf_document_count": pdf_counts.get(number, 0),
            "documents_fetched_at": fetched_at.get(number),
            "next_deadline": (
                {"items": _upcoming(record, since), "stayed": bool(record.get("stay")), "today": today}
                if record else None
            ),
        }
        href = f"investigations/{slug_for(number)}.html"
        cells = []
        for column in columns:
            value = ui_schema.resolve(column, case, extra=extra)
            rendered = _rendered(column, value, href=href)
            cells.append(rendered or DASH)
        if case.get("withdrawn"):
            cells[0] += f" {WITHDRAWN_PILL}"
        cells = [f"<td>{cell}</td>" for cell in cells]
        pick = (
            f'<td class="pick"><input type="checkbox" class="pick-case" value="{_e(number)}" '
            f'data-fetched="{_e(fetched_at.get(number) or "")}" aria-label="Select {_e(number)}"></td>'
        )
        row_html.append(
            f"""<tr data-search="{_search_blob(case, counsel.get(number))}" data-status="{_e(_filter_status(case))}">
  {pick}{''.join(cells)}
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
{_control_panel()}
{_due_panel(rows, next_actions, since)}
<div class="toolbar">
  <input type="search" id="search" placeholder="Search by case name, number, party, firm, attorney&hellip;">
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
          <th class="pick"><input type="checkbox" id="pick-all" aria-label="Select every case shown"></th>
          {header_html}
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
    return _page("ITC 337 Investigations", body, script=_INDEX_SCRIPT + _CONTROL_SCRIPT)


def _today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def _days_before(day: str, days: int) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(day) - timedelta(days=days)).isoformat()


def _due_panel(cases: list[dict[str, Any]], next_actions: dict[str, Any], since: str) -> str:
    """"Due in the next 7 days": every open case's dates from `since` to five
    weeks on, which the script narrows to the coming week on the day the page
    is viewed (and hides when there are none)."""
    until = _days_before(since, -42)
    titles = {str(c.get("investigation_number")): c.get("title") or "" for c in cases}
    items = []
    for number, record in sorted(next_actions.items()):
        if record.get("stay"):
            continue
        for day, label in _upcoming(record, since):
            if day <= until:
                items.append([day, number, titles.get(number, ""), label, f"investigations/{slug_for(number)}.html#next"])
    items.sort()
    if not items:
        return ""
    return f"""<div class="card due-panel" id="due-panel" hidden>
  <div class="due-head"><strong>Due in the next 7 days</strong> <span class="muted" id="due-count"></span></div>
  <table class="list due-list"><tbody id="due-rows"></tbody></table>
  <button class="btn btn-quiet" id="due-more" hidden>Show all</button>
  <script type="application/json" id="due-data">{json.dumps(items).replace("</", "<\\/")}</script>
</div>"""


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


SHOWN_ATTORNEYS = 6


def _attorneys_html(attorneys: list[dict[str, Any]]) -> str:
    """Lead first, then the rest; a long team folds after the first few, and
    whoever has withdrawn is struck through at the end.
    """
    def one(attorney: dict[str, Any]) -> str:
        name = _e(attorney.get("name"))
        if attorney.get("withdrawn_on"):
            return (
                f'<span class="withdrawn" title="Withdrew {_date(attorney["withdrawn_on"])}">'
                f"{name}</span>"
            )
        if attorney.get("lead"):
            return f'{name}<span class="pill pill-blue pill-lead">Lead</span>'
        return name

    if not attorneys:
        return '<div class="attorneys no-counsel">No attorneys named in the filings yet</div>'
    shown = [one(a) for a in attorneys[:SHOWN_ATTORNEYS]]
    rest = [one(a) for a in attorneys[SHOWN_ATTORNEYS:]]
    folded = (
        f", <details><summary>+{len(rest)} more</summary>{', '.join(rest)}</details>"
        if rest
        else ""
    )
    return f'<div class="attorneys">{", ".join(shown)}{folded}</div>'


def _firm_html(rep: dict[str, Any], number: str) -> str:
    meta = []
    appearance = next(iter(rep.get("appearances") or []), None)
    if appearance:
        label = f"Appeared {_date(appearance.get('date'))}"
        files = appearance.get("files") or []
        if files:
            href = f"../../data/documents/{slug_for(number)}/{files[0]}"
            label = f'<a href="{_e(href)}" target="_blank" rel="noopener">{label}</a>'
        meta.append(label)
    elif rep.get("first_filed"):
        meta.append(f"First filed {_date(rep['first_filed'])}")
    if rep.get("filings"):
        meta.append(f"{rep['filings']} filing{'s' if rep['filings'] != 1 else ''}")
    emails = rep.get("emails") or []
    if emails:
        meta.append(f"service: {_e(emails[0])}")
    for_text = ""
    if not rep.get("parties") and rep.get("on_behalf_of"):
        for_text = f'<div class="firm-meta">for {_e("; ".join(rep["on_behalf_of"]))}</div>'
    return f"""<div class="firm">
  <div class="firm-name">{_e(rep.get('firm'))}</div>
  <div class="firm-meta">{' &middot; '.join(meta)}</div>
  {for_text}
  {_attorneys_html(rep.get('attorneys') or [])}
</div>"""


def _non_party_reason(party: dict[str, Any], number: str) -> str:
    """Why a non-party is in the case: a line, and the evidence under it --
    the purpose its notice states, and the papers it filed itself.
    """
    summary = party.get("summary")
    if not summary:
        return ""
    if party.get("served_on"):
        summary = f"{summary} on {dates.format_ui(party['served_on'])}"

    evidence = []
    notice = party.get("notice") or {}
    if party.get("purpose"):
        files = notice.get("files") or []
        source = "Notice of limited appearance"
        if files:
            href = f"../../data/documents/{slug_for(number)}/{files[0]}"
            source = f'<a href="{_e(href)}" target="_blank" rel="noopener">{source}</a>'
        if notice.get("date"):
            source += f", {_date(notice['date'])}"
        purpose = str(party["purpose"]).rstrip(" .")
        evidence.append(f"<p>{source}: &ldquo;&hellip;for the limited purpose of {_e(purpose)}.&rdquo;</p>")
    filings = party.get("filings") or []
    if filings:
        items = "".join(
            f'<li><span class="mono">{_date(f.get("date"))}</span> {_e(f.get("title"))}</li>'
            for f in filings
        )
        evidence.append(f"<p>Its own filings:</p><ul>{items}</ul>")

    details = (
        f'<details class="why"><summary>Details</summary>{"".join(evidence)}</details>'
        if evidence
        else ""
    )
    return f'<div class="non-party-reason">{_e(summary)}{details}</div>'


def _party_key(role: Any, name: Any) -> tuple[str, str]:
    return (str(role or "").strip().lower(), str(name or "").strip().lower())


def _parties_section(
    section: ui_schema.Section, case: dict[str, Any], counsel: dict[str, Any] | None
) -> str:
    """Each role's parties, grouped by who represents them.

    Parties sharing exactly the same firms are one group, so two
    complainants with one legal team read as one block, and a respondent with
    its own counsel stands apart. The groupings come from counsel.json; IDS
    alone still lists the parties when no filings have been read.
    """
    number = str(case.get("investigation_number") or "")
    stage = ui_schema.stage_for(case) or {}
    # IDS's parties, plus the non-parties only the filings name (see counsel.py).
    participants = [
        *((stage.get("lists") or {}).get("participants") or []),
        *((counsel or {}).get("non_parties") or []),
    ]
    reps = (counsel or {}).get("representations") or []

    represented: dict[tuple[str, str], list[int]] = {}
    for index, rep in enumerate(reps):
        for party in rep.get("parties") or []:
            represented.setdefault(_party_key(party.get("role"), party.get("name")), []).append(index)

    blocks = []
    for role in section.roles:
        parties = [
            p for p in participants if str(p.get("role") or "").strip().lower() == role.role.lower()
        ]
        if not parties:
            continue
        groups: dict[tuple[int, ...], list[dict[str, Any]]] = {}
        for party in parties:
            key = tuple(represented.get(_party_key(party.get("role"), party.get("name")), []))
            groups.setdefault(key, []).append(party)

        group_html = []
        for rep_indexes, members in groups.items():
            names = "".join(
                f"<li>{_e(p.get('name'))}"
                + (f' <span class="case-sub">{_e(p["disposition"])}</span>' if p.get("disposition") else "")
                + _non_party_reason(p, number)
                + "</li>"
                for p in members
            )
            if rep_indexes:
                firms = "".join(_firm_html(reps[i], number) for i in rep_indexes)
            elif reps:
                firms = '<div class="no-counsel">Counsel not identified in the filings</div>'
            else:
                firms = ""
            group_html.append(
                f'<div class="party-group"><ul class="party-names">{names}</ul><div>{firms}</div></div>'
            )
        blocks.append(
            f'<div class="role-block"><div class="role-label">{_e(role.label)}</div>{"".join(group_html)}</div>'
        )

    others = [rep for rep in reps if not rep.get("parties")]
    if others:
        firms = "".join(_firm_html(rep, number) for rep in others)
        blocks.append(
            '<div class="role-block"><div class="role-label">Other counsel of record</div>'
            f"{firms}</div>"
        )

    if not blocks:
        return ""
    note = ""
    if not reps:
        note = (
            '<p class="case-sub">Counsel is read from this case\'s EDIS filings; fetch its '
            "documents to see who represents each party.</p>"
        )
    return f"""<div class="section-block">
  <h2>{_e(section.title)}</h2>
  <div class="card">{''.join(blocks)}</div>
  {note}
</div>"""


def _documents_section(
    section: ui_schema.Section, documents: list[dict[str, Any]], fetched_at: str | None = None
) -> str:
    def sort_key(doc: dict[str, Any]) -> str:
        return dates.sort_key(doc.get("document_date") or doc.get("official_received_date"))

    docs_sorted = sorted(documents, key=sort_key, reverse=True)

    if not docs_sorted:
        rows = ""
        empty = (
            '<div class="empty-state">No documents fetched yet. Use '
            "<strong>Fetch documents</strong> at the top of this page.</div>"
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

    fetched = (
        f' <span class="heading-note">&middot; last fetched {_stamp(fetched_at)}</span>'
        if fetched_at
        else ""
    )
    return f"""<div class="section-block">
  <h2>{_e(section.title)} ({len(docs_sorted)}){fetched}</h2>
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


# Chip text and style per claim status, in the spec's palette: statuses
# differ in lightness, not only hue.
_CHIPS = {
    "asserted": ("Asserted", "chip-neutral"),
    "instituted": ("Instituted", "chip-neutral"),
    "in_case": ("In case", "chip-neutral"),
    "withdrawn": ("Withdrawn", "chip-withdrawn"),
    "settled": ("Settled", "chip-settled"),
    "infringed": ("Infringed", "chip-infringed"),
    "violation": ("Violation", "chip-infringed"),
    "not_infringed": ("Not infringed", "chip-not-infringed"),
    "no_violation": ("No violation", "chip-not-infringed"),
    "invalid": ("Invalid", "chip-invalid"),
    "on_appeal": ("On appeal", "chip-appeal"),
    "default": ("Default", "chip-default"),
    "by_respondent": ("Varies by respondent", "chip-varies"),
}
_ACTIONS = {
    "asserted": "Asserted",
    "instituted": "Instituted",
    "added": "Added",
    "withdrawn": "Withdrawn",
    "terminated_settlement": "Terminated on settlement",
    "found_infringed": "Found infringed",
    "found_not_infringed": "Found not infringed",
    "found_invalid": "Found invalid",
    "found_not_invalid": "Found not invalid",
    "technical_prong": "Technical-prong ruling on",
    "not_reviewed": "Commission declined review of",
    "no_violation": "Commission found no violation",
    "violation": "Commission found a violation",
}
_SPEAKERS = {
    "tribunal_ruling": "ruling",
    "tribunal_recital": "recital of an earlier ruling",
    "party_argument": "party's position",
    "other": "other",
}
_METHODS = {"rule": "Parsed by rule", "haiku": "Extracted by Haiku", "derived": "Derived from event history",
            "second_pass": "Re-read by Sonnet"}


def _short_patent(patent: str) -> str:
    digits = re.sub(r"\D", "", str(patent or ""))
    return f"&rsquo;{digits[-3:]}" if len(digits) >= 3 else _e(patent)


def _event_row(event: dict[str, Any], number: str, stage_titles: dict[str, str]) -> str:
    source = event.get("source") or {}
    files = source.get("files") or []
    if source.get("url"):
        link = f'<a href="{_e(source["url"])}" target="_blank" rel="noopener">{_e(source.get("title"))}</a>'
    elif files:
        href = f"../../data/documents/{slug_for(number)}/{files[0]}"
        link = f'<a href="{_e(href)}" target="_blank" rel="noopener">{_e(source.get("title"))}</a>'
    else:
        link = _e(source.get("title"))
    source_id = str(source.get("id") or "")
    source_label = source_id if source_id.startswith("FR:") else f"EDIS {source_id}"
    method = _METHODS.get(event.get("method"), _e(event.get("method")))
    if event.get("status") == "needs_review":
        reasons = [*(event.get("case_notes") or []), *(event.get("notes") or [])]
        how = f'<span class="pill pill-amber">Needs review</span> {_e("; ".join(reasons))}<div class="case-sub">{method}</div>'
    else:
        how = method
        extra = [n for n in event.get("notes") or [] if n]
        if extra:
            how += f'<div class="case-sub">{_e("; ".join(extra))}</div>'
    if event.get("corroborated_by"):
        how += (
            f' <span class="pill pill-green" title="Agrees with {len(event["corroborated_by"])} statement(s) '
            f'in other documents">Corroborated</span>'
        )
    who = event.get("respondents") or ["ALL"]
    scope = "" if who == ["ALL"] else f' <span class="case-sub">({_e(", ".join(who))} only)</span>'
    action = _ACTIONS.get(event.get("action"), str(event.get("action") or "").replace("_", " ").capitalize())
    speaker = event.get("speaker")
    said = ""
    if speaker not in ("tribunal_ruling", None) and event.get("action") != "asserted":
        said = f' <span class="case-sub">&middot; {_e(_SPEAKERS.get(speaker, speaker))}</span>'
    if event.get("case_wide"):
        what = f"{_e(action)}, for every claim still in the case"
    else:
        what = f"{_e(action)} claims {_e(event.get('claims_verbatim'))} of the {_short_patent(event.get('patent'))} patent"
    when = ""
    if event.get("effective_note"):
        when = f'<div class="case-sub" title="{_e(event["effective_note"])}">filed {_date(event.get("date"))}</div>'
    return f"""<tr>
  <td class="mono">{_date(event.get("effective_date") or event.get("date"))}{when}</td>
  <td>{_e(stage_titles.get(event.get("stage"), event.get("stage")))}</td>
  <td>{what}{scope}{said}
      <div class="case-sub">&ldquo;{_e(event.get("quote"))}&rdquo;</div></td>
  <td>{link}<div class="case-sub">{_e(source_label)}</div></td>
  <td>{how}</td>
</tr>"""


def _claims_section(claims: dict[str, Any]) -> str:
    """The Claims tab: one row per claim, one column per stage, and how each
    status was determined."""
    if claims.get("outcome") == "no_claims":
        return (
            '<div class="card empty-state">No claim information found in the available '
            "documents.</div>"
        )

    number = str(claims.get("key") or "")
    events = {event["id"]: event for event in claims.get("events") or []}
    stages = claims_matrix.STAGES

    def table(built: dict[str, Any]) -> str:
        header = "".join(
            f'<th>{_e(title)}<div class="stage-count">{built["counts"][key]} {_e(meaning)}</div></th>'
            for key, title, meaning in stages
        )
        body = []
        for group in built["patents"]:
            rows = group["rows"]
            counts = group["counts"]
            if rows:
                summary = (
                    f'{counts["instituted"]} instituted &middot; {counts["hearing"]} to hearing '
                    f'&middot; {counts["final_id"]} infringed at Final ID'
                )
            else:
                summary = "no claims recorded"
            body.append(
                f'<tr class="patent-row"><th colspan="{len(stages) + 1}">'
                f'{_short_patent(group["patent"])} patent <span class="case-sub">U.S. Patent No. '
                f'{_e(group["patent"])} &middot; {summary}</span></th></tr>'
            )
            for row in rows:
                cells = []
                for key, _, _ in stages:
                    cell = row["cells"].get(key)
                    chip = ""
                    if cell:
                        text, css = _CHIPS.get(cell["status"], (cell["status"], "chip-neutral"))
                        event = events.get(cell["event"]) or {}
                        tip = f'{event.get("quote", "")} ({(event.get("source") or {}).get("id", "")})'
                        chip = f'<span class="chip {css}" title="{_e(tip)}">{_e(text)}</span>'
                    cells.append(f"<td>{chip}</td>")
                review = (
                    '<span class="review-dot" title="An event for this claim needs review"></span>'
                    if row["needs_review"]
                    else ""
                )
                withdrawn = any(c.get("status") == "withdrawn" for c in row["cells"].values())
                mark = ' data-withdrawn="1"' if withdrawn else ""
                body.append(f'<tr{mark}><td class="claim-no">Claim {row["claim"]}{review}</td>{"".join(cells)}</tr>')
        return f"""<table class="list claims-matrix">
      <thead><tr><th>Claim</th>{header}</tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table>"""

    # One matrix per view -- every respondent, then each one -- switched on
    # the page, since terminations and defaults are often respondent-specific.
    respondents = list(claims.get("respondents") or [])
    views = [("All respondents", claims_matrix.build(claims))] + [
        (name, claims_matrix.build(claims, respondent=name)) for name in respondents
    ]
    matrices = "".join(
        f'<div class="claims-view" data-view="{i}"{"" if i == 0 else " hidden"}>{table(built)}</div>'
        for i, (_, built) in enumerate(views)
    )
    options = "".join(f'<option value="{i}">{_e(label)}</option>' for i, (label, _) in enumerate(views))
    controls = f"""<div class="claims-controls">
    <label>Respondent <select id="claims-respondent">{options}</select></label>
    <label><input type="checkbox" id="claims-hide-withdrawn"> Hide withdrawn claims</label>
  </div>"""

    stage_titles = {key: title for key, title, _ in stages}
    ordered = sorted(events.values(), key=lambda e: (str(e.get("effective_date") or e.get("date") or ""), e.get("patent") or ""))
    deciding = [e for e in ordered if claims_matrix.changes_status(e) or e.get("status") == "needs_review"]
    other = [e for e in ordered if e not in deciding]
    deciding_rows = "".join(_event_row(e, number, stage_titles) for e in deciding)
    other_rows = "".join(_event_row(e, number, stage_titles) for e in other)
    head = "<thead><tr><th>Effective</th><th>Stage</th><th>Event</th><th>Source</th><th>Method</th></tr></thead>"
    others = ""
    if other:
        others = f"""<details class="other-events"><summary>Recitals and party positions ({len(other)}) &mdash;
  shown for context; they do not change a claim&rsquo;s status</summary>
  <div class="card"><div class="table-wrap"><table class="list">{head}<tbody>{other_rows}</tbody></table></div></div>
</details>"""

    pending = claims.get("pending_sources") or []
    notes = [_e(warning) for warning in claims.get("warnings") or []]
    if pending:
        notes.append(
            f"{len(pending)} source document{'s' if len(pending) != 1 else ''} not read yet "
            "(no public PDF on disk, or the build stopped early); Update claims analysis reads them."
        )
    notice = f'<div class="notice">{"<br>".join(notes)}</div>' if notes else ""
    cost = float(claims.get("cost_usd") or 0)
    calls = int(claims.get("model_calls") or 0)
    spent = f" This build made {calls} model call{'s' if calls != 1 else ''} costing ${cost:.4f}." if calls else ""

    return f"""{notice}<div class="section-block">
  <h2>Claims by stage</h2>
  {controls if len(views) > 1 else ""}
  <div class="card"><div class="table-wrap">{matrices}</div></div>
  <p class="case-sub">Instituted claims are parsed by rule from the notice of institution; the
  complaint and the ALJ and Commission decisions are read by Claude Haiku, and every event is
  checked in code. Only rulings change a claim&rsquo;s status. An orange dot marks a claim with an
  event that needs review. The Federal Circuit column is filled in by a later phase.{spent}</p>
</div>
<div class="section-block">
  <h2>How each status was determined ({len(deciding)})</h2>
  <div class="card"><div class="table-wrap">
    <table class="list">{head}<tbody>{deciding_rows}</tbody></table>
  </div></div>
  {others}
</div>"""


_SUM_GROUPS = {
    "complaint": "What the case is about",
    "answers": "The respondents' answers",
    "rulings": "Rulings before the hearing",
    "decisions": "The ALJ's and the Commission's decisions",
}
_SUM_NOTED = {
    "termination": "Terminations",
    "default": "Defaults",
    "not_reviewed": "The Commission let a ruling stand",
    "remedy": "Remedial orders",
}


def _file_links(documents: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Each document's PDFs on disk, by document id."""
    return {
        str(d.get("id")): [a["href"] for a in d.get("attachments") or [] if a.get("href")]
        for d in documents or []
    }


def _summary_line(line: Any, files: dict[str, list[str]]) -> str:
    from datalayer.summary.preview import KIND_LABEL, pages

    item = line.item
    hrefs = files.get(item.id) or []
    href = next((h for h in hrefs if line.main_id and f"_{line.main_id}_" in h), hrefs[0] if hrefs else "")
    title = _e(item.title if len(item.title) <= 110 else item.title[:107] + "…")
    title = f'<a href="{_e(href)}">{title}</a>' if href else title
    if not line.counted:
        size = f"up to {line.read_pages} pages"
    elif item.kind == "complaint":
        size = f"{line.read_pages} of the complaint's {pages(line.main_pages)}"
        if line.total_pages and line.total_pages > (line.main_pages or 0):
            size += f" ({line.total_pages:,} in the whole filing)"
    elif line.read_pages == line.main_pages:
        size = "all " + pages(line.main_pages) if line.main_pages != 1 else "1 page"
    else:
        size = f"{line.read_pages} of {pages(line.main_pages)}"
    meta = [f'<span class="na-basis">{_e(KIND_LABEL.get(item.kind, item.kind))}</span>',
            f'<span class="sum-pages">{_e(size)}</span>']
    if item.kind == "complaint" and line.body:
        meta.append('<span class="sum-tag">Complaint body confirmed</span>' if line.body == "confirmed"
                    else '<span class="sum-tag assumed" title="Found by its length; confirmed when the text is read">'
                         "Complaint body assumed</span>")
    if line.text_on_file:
        meta.append('<span class="sum-tag" title="Already read for the claims analysis: no download or OCR needed">'
                    "Text on file</span>")
    who = f"<div>{_e(item.who)}</div>" if item.who else ""
    return (
        f'<tr><td class="na-date">{_date(item.day)}</td><td><div class="na-title">{title}</div>{who}'
        f'<div class="na-meta">{"".join(meta)}<span>{_e(item.why)}</span></div></td></tr>'
    )


def _summary_section(estimate: Any, primer: Any, documents: list[dict[str, Any]] | None = None,
                     stage_label: str | None = None) -> str:
    """The Summary tab (datalayer/summary): what a case summary would read
    and cost, and the hand-written Section 337 primer. Phase 1: nothing is
    written yet, so the tab previews the reading and its cost."""
    from datalayer.summary.preview import money

    files = _file_links(documents)
    blocks = []
    if estimate is not None and estimate.lines:
        bound = "About" if estimate.complete else "Up to"
        left = max(estimate.budget_usd - estimate.spent_usd, 0)
        button = (
            '<button class="btn btn-quiet" data-job="summary_estimate" title="Ask EDIS how long each document is '
            '(one request per document; nothing is downloaded and no model is called)">Estimate cost</button>'
            if not estimate.complete else ""
        )
        counted = (
            "" if estimate.complete else
            f" {len(estimate.uncounted)} document(s) are not counted yet, so each is costed at its page limit."
        )
        stage = f'<div class="na-label" style="margin-top:0.8rem">Stage</div><div>{_e(stage_label)}</div>' if stage_label else ""
        blocks.append(f"""<div class="card sum-hero">
    <div>
      <div class="na-label">Case summary</div>
      <div class="sum-cost">{bound} {money(estimate.total_usd)} <small>to write</small></div>
      <p>Summaries are not written yet. This is what one would read, and what it would cost:
      notes on each document by {_e(estimate.notes_model)} ({money(estimate.notes_usd)}, {estimate.pages_read:,} pages),
      then the summary written by {_e(estimate.writer_model)} ({money(estimate.writer_usd)}).{counted}</p>
      {button}
    </div>
    <div>
      <div class="na-label">Budget</div>
      <div>{money(estimate.spent_usd)} spent of {money(estimate.budget_usd)}; {money(left)} left</div>
      {stage}
    </div>
  </div>""")

        rows = []
        for section, heading in _SUM_GROUPS.items():
            lines = [line for line in estimate.lines if line.item.section == section]
            if lines:
                rows.append(f'<tr class="sum-group"><td colspan="2">{heading}</td></tr>')
                rows.extend(_summary_line(line, files) for line in lines)
        blocks.append(f'<div class="card na-card"><h3>What would be read <small>({len(estimate.lines)} documents)</small></h3>'
                      f'<table class="na-list"><tbody>{"".join(rows)}</tbody></table></div>')

        noted = estimate.selection.noted
        if noted:
            parts = []
            for kind, heading in _SUM_NOTED.items():
                items = [i for i in noted if i.kind == kind]
                if items:
                    lis = "".join(f"<li>{_date(i.day)}: {_e(i.title)}</li>" for i in items)
                    parts.append(f"<h4>{heading} <small>({len(items)})</small></h4><ul class=\"na-notes\">{lis}</ul>")
            blocks.append(
                f'<details class="card na-past"><summary>Noted from their titles, not opened <small>({len(noted)})</small>'
                f'</summary><p class="primer-note">The title already says what happened, so these cost nothing.</p>'
                f'{"".join(parts)}</details>'
            )
        left_out = estimate.selection.not_read
        if left_out or estimate.selection.skipped_filings:
            lis = "".join(
                f"<li>{_date(i.day)}: {_e(i.title)}{' (' + _e(i.who) + ')' if i.who else ''}. "
                f'<span class="primer-note">{_e(i.why)}.</span></li>'
                for i in left_out
            )
            n = estimate.selection.skipped_filings
            if n:
                lis += f"<li>{n} appendix or exhibit filing{'' if n == 1 else 's'} to the complaint.</li>"
            count = len(left_out) + (1 if n else 0)
            blocks.append(
                f'<details class="card na-past"><summary>Not read <small>({count})</small></summary>'
                f'<ul class="na-notes">{lis}</ul></details>'
            )
    else:
        blocks.append('<div class="card"><p>No public complaint, answer or decision is on file for this case yet. '
                      "Fetch its documents to see what a summary would read.</p></div>")

    if primer is not None:
        badge = "" if primer.reviewed else '<span class="primer-draft">Draft: awaiting review</span>'
        review = (
            f"Reviewed by {_e(primer.reviewed_by)}{', ' + _e(primer.reviewed_on) if primer.reviewed_on else ''}."
            if primer.reviewed else
            "Written for orientation and not yet reviewed by a Section 337 practitioner."
        )
        blocks.append(
            f'<details class="card na-past primer"><summary>How Section 337 investigations work'
            f"{badge}</summary>{primer.html}"
            f'<p class="primer-note">{review} The same for every case, and not legal advice; '
            "the rules and the Commission's orders govern.</p></details>"
        )
    return f'<div class="section-block case-summary">{"".join(blocks)}</div>'


def _with_tabs(overview: str, claims: dict[str, Any] | None, next_actions: dict[str, Any] | None,
               next_built_at: str | None = None, documents: list[dict[str, Any]] | None = None,
               summary: str | None = None) -> str:
    """The page body, split into tabs when the case has more than its
    overview: Summary once it has documents, Next actions for an open
    investigation, Claims once it has a claims analysis. Unchanged
    otherwise."""
    tabs = [("overview", "Overview", overview)]
    if summary:
        tabs.append(("summary", "Summary", summary))
    if next_actions:
        tabs.append(("next", "Next actions", _next_actions_section(next_actions, next_built_at, documents)))
    if claims and claims.get("built_at"):
        tabs.append(("claims", "Claims", _claims_section(claims)))
    if len(tabs) == 1:
        return overview
    active, hidden = ' class="active"', " hidden"
    nav = "".join(
        f'<a href="#{key}" data-tab="{key}"{active if i == 0 else ""}>{label}</a>'
        for i, (key, label, _) in enumerate(tabs)
    )
    panels = "".join(
        f'<div class="tab-panel" id="tab-{key}"{hidden if i else ""}>{body}</div>'
        for i, (key, _, body) in enumerate(tabs)
    )
    return f'<nav class="tabs">{nav}</nav>\n{panels}'


_NA_STEPS = ("Complaint filed", "Instituted", "Before the ALJ", "Final ID", "Commission", "Presidential review")
# Which step each stage is on; "concluded" has passed them all.
_NA_STEP_OF = {"pre_institution": 0, "alj": 2, "commission": 4, "presidential": 5, "concluded": 6}
_NA_BASIS_LABEL = {"order": "Order", "case data": "Case record", "by rule": "By rule", "docket": "Docket"}


def _na_steps(stage: str) -> str:
    current = _NA_STEP_OF.get(stage)
    if current is None:
        return ""
    items = []
    for i, name in enumerate(_NA_STEPS):
        cls = "done" if i < current else "current" if i == current else ""
        items.append(f'<li class="{cls}">{name}</li>')
    return f'<ol class="na-steps" aria-label="Stage">{"".join(items)}</ol>'


def _na_row(event: dict[str, Any], sources: dict[str, str]) -> str:
    day, end = event.get("date"), event.get("end")
    date_cell = _date(day) if day else "Not set"
    if end and end != day:
        date_cell += f"<small>to {_date(end)}</small>"
    basis = event.get("basis") or ""
    meta = [f'<span class="na-basis b-{basis.replace(" ", "-")}">{_NA_BASIS_LABEL.get(basis, _e(basis))}</span>']
    if event.get("on_hold"):
        meta.insert(0, '<span class="na-hold">On hold</span>')
    if event.get("cite"):
        meta.append(f"<span>{_e(event['cite'])}</span>")
    if event.get("note"):
        meta.append(f"<span>{_e(event['note'])}</span>")
    source = event.get("source") or {}
    if source.get("title"):
        title = _e(source["title"] if len(source["title"]) <= 80 else source["title"][:77] + "…")
        href = sources.get(str(source.get("id") or ""))
        text = f"{title}, {_date(source.get('date'))}"
        meta.append(f'<a href="{_e(href)}">{text}</a>' if href else f"<span>{text}</span>")
    hold = ' data-hold="1"' if event.get("on_hold") else ""
    return (
        f'<tr class="next-event{" on-hold" if hold else ""}" data-date="{_e(end or day or "")}"{hold} '
        f'data-label="{_e(event.get("label"))}">'
        f'<td class="na-date">{date_cell}</td>'
        f'<td><div class="na-title">{_e(event.get("label"))}</div><div class="na-meta">{"".join(meta)}</div></td>'
        f'<td class="when"></td></tr>'
    )


def _next_actions_section(record: dict[str, Any], built_at: str | None,
                          documents: list[dict[str, Any]] | None = None) -> str:
    """What happens next in an open investigation (datalayer/nextactions).

    Which event is next, how far away each is, and which are past are all
    worked out in the page against the day it is viewed (the script moves
    past rows into the folded "Past" list), so the tab stays right between
    rebuilds. Without the script, everything simply shows in date order.
    """
    # Each source document's PDF, where it is on disk.
    sources = {
        str(d.get("id")): (d.get("attachments") or [{}])[0].get("href")
        for d in documents or [] if (d.get("attachments") or [{}])[0].get("href")
    }
    dated = [e for e in record.get("events") or [] if e.get("date")]
    undated = [e for e in record.get("events") or [] if not e.get("date")]
    waiting = record.get("waiting_on")
    notes = "".join(f"<li>{_e(n)}</li>" for n in record.get("notes") or [])

    def doc_link(source: dict[str, Any], text: str) -> str:
        href = sources.get(str((source or {}).get("id") or ""))
        return f'<a href="{_e(href)}">{text}</a>' if href else text

    stay = record.get("stay")
    stay_card = ""
    if stay:
        until = f" It is set to run until {_date(stay['until'])}." if stay.get("until") else ""
        extended = " (since extended)" if stay.get("extended") else ""
        stay_card = (
            f'<div class="card na-stay"><strong>Stayed since {_date(stay["since"])}</strong>{extended}: '
            f'{doc_link(stay.get("source"), _e((stay.get("source") or {}).get("title") or "stay order"))}.{until} '
            "Scheduled dates from then on are on hold, and are likely to be reset when it ends.</div>"
        )
    partial = "".join(
        f'<li>Stayed as to <strong>{_e(p["who"])}</strong> since {_date(p["since"])}'
        + (f" (until {_date(p['until'])})" if p.get("until") else "")
        + f': {doc_link(p.get("source"), _e((p.get("source") or {}).get("title") or "order"))}. '
          "The schedule continues for the other respondents.</li>"
        for p in record.get("partial_stays") or []
    )
    if partial:
        notes = partial + notes
    out = record.get("out_of_case") or []
    out_card = ""
    if out:
        rows = "".join(
            f'<tr><td class="na-date">{_date(o["date"])}</td><td class="na-who">'
            f'{doc_link(o.get("source"), _e(o["who"]))}</td>'
            f'<td class="na-how">{_e(o["how"].capitalize())}'
            f'{"" if o.get("final") else ", pending Commission review"}</td></tr>'
            for o in out
        )
        out_card = (
            f'<details class="card na-past"><summary>Respondents no longer in the case <small>({len(out)})</small>'
            f'</summary><table class="na-list"><tbody>{rows}</tbody></table></details>'
        )

    upcoming = (
        f'<div class="card na-card"><h3>Upcoming <small id="na-up-count"></small></h3>'
        f'<table class="na-list" id="na-upcoming"><tbody>{"".join(_na_row(e, sources) for e in dated)}</tbody></table>'
        f'<div class="na-empty" id="na-up-empty" hidden>Nothing dated is still ahead.</div></div>'
        if dated else ""
    )
    to_be_set = (
        f'<div class="card na-card"><h3>Not yet dated <small>({len(undated)})</small></h3>'
        f'<table class="na-list"><tbody>{"".join(_na_row(e, sources) for e in undated)}</tbody></table></div>'
        if undated else ""
    )
    past = (
        '<details class="card na-past" id="na-past" hidden><summary>Past <small id="na-past-count"></small></summary>'
        '<table class="na-list"><tbody id="na-past-rows"></tbody></table></details>'
        if dated else ""
    )
    return f"""<div class="section-block next-actions">
  {_na_steps(str(record.get("stage") or ""))}
  <div class="card na-hero">
    <div>
      <div class="na-label">Next</div>
      <div class="na-next" id="next-what">{"&mdash;" if dated else "Nothing scheduled"}</div>
      <div class="na-when" id="next-when"></div>
    </div>
    <div>
      <div class="na-label">Stage</div>
      <div>{_e(record.get("stage_label"))}</div>
      {f'<div class="na-label" style="margin-top:0.6rem">Waiting on</div><div>{_e(waiting)}</div>' if waiting else ""}
    </div>
  </div>
  {stay_card}
  {f'<div class="card"><ul class="na-notes">{notes}</ul></div>' if notes else ""}
  {upcoming}
  {to_be_set}
  {past}
  {out_card}
  <p class="na-legend">
    <strong>Order</strong>: the ALJ's or the Commission's procedural schedule, with later amendments applied.
    <strong>Case record</strong>: dates in the USITC's investigation record. <strong>Docket</strong>: when a
    document was issued. <strong>By rule</strong>: calculated under 19 CFR Part 210, counting days as 19 CFR
    201.14 does (from the first business day after the event; a deadline on a weekend or federal holiday moves
    to the next business day). Orders and notices can change any of these; this tab is for orientation, not a
    substitute for them. Worked out {_date(built_at) if built_at else "at the last sync"}.
  </p>
</div>"""


def render_detail(
    case: dict[str, Any],
    documents: list[dict[str, Any]],
    schema: ui_schema.Schema,
    *,
    counsel: dict[str, Any] | None = None,
    fetched_at: str | None = None,
    claims: dict[str, Any] | None = None,
    claims_state: dict[str, Any] | None = None,
    next_actions: dict[str, Any] | None = None,
    next_built_at: str | None = None,
    summary: Any = None,
    primer: Any = None,
) -> str:
    """`fetched_at` is when this case's documents were last fetched;
    `claims` is its stored claims analysis and `claims_state` whether that
    needs building (datalayer/claims/status.py); `next_actions` its entry in
    data/next_actions.json, for an open investigation; `summary` its case
    summary estimate (datalayer/summary/estimate.py) and `primer` the
    Section 337 explainer shown with it.
    """
    number = case.get("investigation_number")
    # What a stage block compares against the primary stage: the field
    # sections, and the parties section as its bare name lists.
    field_sections = [
        s if s.kind == "fields" else s.party_fields()
        for s in schema.sections
        if s.kind in ("fields", "parties")
    ]
    extra = {"document_count": len(documents), "documents_fetched_at": fetched_at}

    blocks = []
    for section in schema.sections:
        if section.kind == "fields":
            blocks.append(_fields_section(section, case, extra=extra))
        elif section.kind == "parties":
            blocks.append(_parties_section(section, case, counsel))
        elif section.kind == "stages":
            blocks.append(_stages_section(section, case, field_sections))
        elif section.kind == "documents":
            blocks.append(_documents_section(section, documents, fetched_at))

    stage_badge = (
        f'<span class="phase-chip">{case["stage_count"]} stages</span>'
        if (case.get("stage_count") or 0) > 1
        else ""
    )

    withdrawn_notice = ""
    if case.get("withdrawn"):
        last_listed = _date(case.get("last_listed_snapshot") or case.get("ids_snapshot"))
        withdrawn_notice = f"""<div class="notice">
  <strong>{WITHDRAWN_LABEL}.</strong> The Commission's investigations file last listed
  this case on {last_listed}, so everything below is as it stood that day and will not
  change until it is listed again. Its documents are unaffected and can still be fetched.
</div>"""

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
{withdrawn_notice}
{_control_panel(str(number or ""), fetched_at, claims_state)}
{_with_tabs(''.join(block for block in blocks if block), claims, next_actions, next_built_at, documents,
            _summary_section(summary, primer, documents, (next_actions or {}).get("stage_label"))
            if documents and (summary is not None or primer is not None) else None)}
<p class="footer-note">
  Investigation {_e(number)} &middot; case information from the IDS investigations
  feed &middot; IDS snapshot {_date(case.get('ids_snapshot'))}
</p>
"""
    return _page(
        f"{case.get('title') or number} - Investigation",
        body,
        script=_DETAIL_SCRIPT + _CONTROL_SCRIPT,
    )
