"""The analytics app's one page.

Everything is drawn in the browser from data.js (see bundle.py), with the
address saying what is shown, so a view can be bookmarked or reloaded:

    #/                      leaderboards: firms and attorneys by cases
    #/firms  #/attorneys  #/companies     every one, searchable
    #/firm/<id>  #/attorney/<id>  #/company/<id>
    #/search/<text>         everything whose name matches
    #/review                the name pairs waiting for a person

The filters (which side, which years, law firms only) apply to every count
on every view and are remembered in this browser.
"""

from __future__ import annotations

TITLE = "ITC Analytics"

CSS = """
:root {
  color-scheme: light;
  --bg: #f5f6f8; --surface: #ffffff; --border: #e3e6ea; --ink: #14181f; --muted: #64707d;
  --accent: #3457d5; --accent-weak: #eef1fd; --radius: 12px;
  --shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 8px rgba(16,24,40,0.04);
  --green-bg: #e6f6ec; --green-fg: #1b7a3d; --amber-bg: #fef3e0; --amber-fg: #a15c00;
  --gray-bg: #eef0f3; --gray-fg: #4a5361; --blue-bg: #e8eefd; --blue-fg: #2646b8;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #0f1216; --surface: #171b21; --border: #2a2f38; --ink: #e7eaee; --muted: #8a94a3;
    --accent: #7c93f7; --accent-weak: #232a45;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 1px 8px rgba(0,0,0,0.25);
    --green-bg: #10301d; --green-fg: #5fd38a; --amber-bg: #35270a; --amber-fg: #f0b545;
    --gray-bg: #232830; --gray-fg: #aab2bd; --blue-bg: #1c2440; --blue-fg: #9db0f5;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header { background: var(--surface); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 5; }
.bar { max-width: 1200px; margin: 0 auto; padding: 0.7rem 1rem; display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
.brand { font-weight: 700; font-size: 1.05rem; color: var(--ink); }
nav { display: flex; gap: 0.2rem; flex-wrap: wrap; }
nav a { padding: 0.3rem 0.65rem; border-radius: 8px; color: var(--muted); font-weight: 500; }
nav a.active, nav a:hover { background: var(--accent-weak); color: var(--accent); text-decoration: none; }
.badge { display: inline-block; min-width: 1.3rem; padding: 0 0.35rem; margin-left: 0.3rem; border-radius: 999px;
  background: var(--amber-bg); color: var(--amber-fg); font-size: 0.75rem; font-weight: 700; text-align: center; }
.search { position: relative; margin-left: auto; flex: 1 1 16rem; max-width: 24rem; }
.search input { width: 100%; padding: 0.45rem 0.7rem; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); color: var(--ink); font: inherit; }
.results { position: absolute; top: 2.4rem; left: 0; right: 0; background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: var(--shadow); max-height: 70vh; overflow: auto; }
.results .group { padding: 0.35rem 0.8rem 0.15rem; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
.results a { display: block; padding: 0.3rem 0.8rem; color: var(--ink); }
.results a:hover, .results a.hot { background: var(--accent-weak); text-decoration: none; }
.results .none { padding: 0.6rem 0.8rem; color: var(--muted); }
main { max-width: 1200px; margin: 0 auto; padding: 1rem; }
.status { display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap; color: var(--muted); font-size: 0.85rem; margin-bottom: 0.8rem; }
.status .warn { color: var(--amber-fg); }
.btn { font: inherit; font-size: 0.85rem; padding: 0.35rem 0.8rem; border-radius: 8px; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink); cursor: pointer; }
.btn:hover { border-color: var(--accent); color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: default; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 1rem 1.1rem; margin-bottom: 1rem; }
.card h2 { margin: 0 0 0.6rem; font-size: 1rem; }
.card h2 small { color: var(--muted); font-weight: 400; margin-left: 0.4rem; }
h1 { font-size: 1.4rem; margin: 0.2rem 0 0.3rem; }
.sub { color: var(--muted); margin-bottom: 0.8rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(28rem, 1fr)); gap: 1rem; }
.grid > .card { margin-bottom: 0; min-width: 0; }
.filters { display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap; font-size: 0.85rem; margin-bottom: 1rem; }
.filters label { display: inline-flex; gap: 0.35rem; align-items: center; color: var(--muted); }
.filters select, .filters input[type=number] { font: inherit; padding: 0.25rem 0.4rem; border: 1px solid var(--border);
  border-radius: 6px; background: var(--surface); color: var(--ink); }
.filters input[type=number] { width: 5.2rem; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.87rem; }
th { text-align: left; color: var(--muted); font-weight: 600; font-size: 0.78rem; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border); white-space: nowrap; }
td { padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); vertical-align: top; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
.muted { color: var(--muted); }
.pill { display: inline-block; padding: 0.05rem 0.45rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
.pill-C { background: var(--blue-bg); color: var(--blue-fg); }
.pill-R { background: var(--gray-bg); color: var(--gray-fg); }
.pill-N, .pill-O { background: var(--amber-bg); color: var(--amber-fg); }
.pill-open { background: var(--green-bg); color: var(--green-fg); }
.stats { display: flex; gap: 1.6rem; flex-wrap: wrap; margin: 0.4rem 0 1rem; }
.stat b { display: block; font-size: 1.35rem; }
.stat span { color: var(--muted); font-size: 0.8rem; }
.more { margin-top: 0.5rem; }
.job { margin: 0 0 1rem; }
.job pre { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 0.5rem; max-height: 12rem;
  overflow: auto; font-size: 0.78rem; white-space: pre-wrap; }
code { background: var(--gray-bg); padding: 0.05rem 0.3rem; border-radius: 4px; font-size: 0.85em; }
.empty { color: var(--muted); padding: 0.4rem 0; }
@media (max-width: 640px) { .grid { grid-template-columns: 1fr; } .search { max-width: none; } }
"""

SCRIPT = r"""
(function () {
  const D = window.ANALYTICS;
  const app = document.getElementById('app');
  const statusEl = document.getElementById('status');
  if (!D) {
    app.innerHTML = '<div class="card">No analytics yet. Run <code>python cli.py analytics</code>, or press Rebuild.</div>';
  }

  // ---- helpers -------------------------------------------------------------
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const SIDE = {C: 'Complainant', R: 'Respondent', N: 'Non-party', O: 'Other'};
  const pill = (code) => `<span class="pill pill-${code}">${SIDE[code] || code}</span>`;
  const link = (kind, entity) => `<a href="#/${kind}/${encodeURIComponent(entity.id)}">${esc(entity.name)}</a>`;
  const caseText = (i) => {
    const c = D.cases[i];
    return `<span title="${esc(c[3])}">${esc(c[0])}</span> <span class="muted">${esc(c[1])}${c[2] ? ' (' + c[2] + ')' : ''}</span>`;
  };
  const byCount = (a, b) => b[1] - a[1] || String(a[2] || '').localeCompare(String(b[2] || ''));
  function table(head, rows, limit) {
    if (!rows.length) return '<div class="empty">None.</div>';
    const shown = limit ? rows.slice(0, limit) : rows;
    const id = 't' + Math.random().toString(36).slice(2, 8);
    const th = head.map((h) => `<th class="${h.num ? 'num' : ''}">${esc(h.label)}</th>`).join('');
    const body = (rs) => rs.map((r) => '<tr>' + r.map((cell, i) => `<td class="${head[i].num ? 'num' : ''}">${cell}</td>`).join('') + '</tr>').join('');
    let html = `<div class="table-wrap"><table id="${id}"><thead><tr>${th}</tr></thead><tbody>${body(shown)}</tbody></table></div>`;
    if (limit && rows.length > limit) {
      html += `<div class="more"><button class="btn" data-more="${id}">Show all ${rows.length}</button></div>`;
      MORE[id] = body(rows);
    }
    return html;
  }
  const MORE = {};
  app.addEventListener('click', (e) => {
    const id = e.target.getAttribute && e.target.getAttribute('data-more');
    if (id && MORE[id]) { document.querySelector('#' + id + ' tbody').innerHTML = MORE[id]; e.target.parentNode.remove(); }
  });

  if (!D) return setupStatus();

  // ---- indexes ---------------------------------------------------------------
  const repsBy = {firm: D.firms.map(() => []), attorney: D.attorneys.map(() => []), company: D.companies.map(() => [])};
  D.reps.forEach((r, i) => {
    r[1].forEach((f) => repsBy.firm[f].push(i));
    r[2].forEach((a) => repsBy.attorney[a].push(i));
    r[3].forEach((p) => repsBy.company[p[0]].push(i));
  });
  const caseParties = D.cases.map(() => ({C: [], R: [], N: [], O: []}));
  D.companies.forEach((c, ci) => c.cases.forEach(([k, roles]) => roles.split('').forEach((r) => caseParties[k][r] && caseParties[k][r].push(ci))));
  const index = {firm: new Map(), attorney: new Map(), company: new Map()};
  D.firms.forEach((e, i) => index.firm.set(e.id, i));
  D.attorneys.forEach((e, i) => index.attorney.set(e.id, i));
  D.companies.forEach((e, i) => index.company.set(e.id, i));
  const OTHER = {C: 'R', R: 'C'};

  // ---- filters -------------------------------------------------------------
  const F = {side: 'all', from: '', to: '', law: true};
  try { Object.assign(F, JSON.parse(localStorage.getItem('analytics-filters') || '{}')); } catch (e) {}
  const saveFilters = () => { try { localStorage.setItem('analytics-filters', JSON.stringify(F)); } catch (e) {} };
  const inYears = (k) => {
    const y = D.cases[k][2];
    if (F.from && (!y || y < +F.from)) return false;
    if (F.to && (!y || y > +F.to)) return false;
    return true;
  };
  const repOk = (i) => {
    const r = D.reps[i];
    return (F.side === 'all' || r[4] === F.side) && inYears(r[0]);
  };
  const lawOk = (f) => !F.law || D.firms[f].kind === 'law_firm';
  function filterBar(withLaw, withSide = true) {
    return `<div class="filters">
      ${withSide ? `<label>Acting for <select id="f-side">
        <option value="all">any side</option><option value="C">complainants</option><option value="R">respondents</option><option value="N">non-parties</option></select></label>` : ''}
      <label>Cases started <input type="number" id="f-from" placeholder="from" value="${esc(F.from)}"> to
        <input type="number" id="f-to" placeholder="to" value="${esc(F.to)}"></label>
      ${withLaw ? '<label><input type="checkbox" id="f-law"> Law firms only</label>' : ''}
    </div>`;
  }
  function wireFilters() {
    const side = document.getElementById('f-side');
    if (!document.getElementById('f-from')) return;
    if (side) side.value = F.side;
    const law = document.getElementById('f-law');
    if (law) law.checked = F.law;
    const apply = () => {
      if (side) F.side = side.value;
      F.from = document.getElementById('f-from').value;
      F.to = document.getElementById('f-to').value;
      if (law) F.law = law.checked;
      saveFilters();
      route();
    };
    app.querySelectorAll('.filters select, .filters input').forEach((el) => el.addEventListener('change', apply));
  }

  // ---- counting ------------------------------------------------------------
  function casesOf(reps) {
    const all = new Set(), bySide = {C: new Set(), R: new Set(), N: new Set()}, open = new Set();
    reps.forEach((i) => {
      if (!repOk(i)) return;
      const r = D.reps[i];
      all.add(r[0]);
      if (bySide[r[4]]) bySide[r[4]].add(r[0]);
      if (D.cases[r[0]][4]) open.add(r[0]);
    });
    return {all, C: bySide.C, R: bySide.R, N: bySide.N, open};
  }
  // Companies a set of representations acted for, and the ones they faced.
  function clientsAndOpponents(reps) {
    const clients = new Map(), opponents = new Map();
    reps.forEach((i) => {
      if (!repOk(i)) return;
      const [k, , , parties, side] = D.reps[i];
      const own = new Set(parties.map((p) => p[0]));
      parties.forEach(([c, role]) => {
        const e = clients.get(c) || {roles: new Set(), cases: new Set()};
        e.roles.add(role); e.cases.add(k); clients.set(c, e);
      });
      (caseParties[k][OTHER[side]] || []).forEach((c) => {
        if (own.has(c)) return;
        const e = opponents.get(c) || new Set();
        e.add(k); opponents.set(c, e);
      });
    });
    return {clients, opponents};
  }
  const casesCell = (set) => [...set].sort((a, b) => b - a).slice(0, 4).map((k) => esc(D.cases[k][0])).join(', ') + (set.size > 4 ? ` +${set.size - 4}` : '');
  function companyRows(map, withRoles) {
    return [...map.entries()].map(([c, v]) => {
      const cases = v.cases || v;
      const row = [link('company', D.companies[c]), cases.size];
      if (withRoles) row.splice(1, 0, [...v.roles].sort().map(pill).join(' '));
      row.push(`<span class="muted">${casesCell(cases)}</span>`);
      return row;
    }).sort((a, b) => b[withRoles ? 2 : 1] - a[withRoles ? 2 : 1]);
  }

  // ---- views ---------------------------------------------------------------
  function firmBoard(limit) {
    const rows = D.firms.map((f, i) => {
      if (!lawOk(i)) return null;
      const c = casesOf(repsBy.firm[i]);
      return c.all.size ? [link('firm', f), c.all.size, c.C.size, c.R.size, c.open.size] : null;
    }).filter(Boolean).sort((a, b) => b[1] - a[1]);
    rows.forEach((r, i) => r.unshift(i + 1));
    return table([{label: '#'}, {label: 'Firm'}, {label: 'Cases', num: 1}, {label: 'For complainants', num: 1},
      {label: 'For respondents', num: 1}, {label: 'Open', num: 1}], rows, limit);
  }
  function currentFirm(a) {
    const stints = a.firms.filter((s) => s[0] >= 0).sort((x, y) => String(y[2] || '').localeCompare(String(x[2] || '')));
    return stints.length ? link('firm', D.firms[stints[0][0]]) : '<span class="muted">unknown</span>';
  }
  function attorneyBoard(limit) {
    const rows = D.attorneys.map((a, i) => {
      const reps = repsBy.attorney[i].filter((r) => !F.law || D.reps[r][1].some(lawOk));
      const c = casesOf(reps);
      return c.all.size ? [link('attorney', a), currentFirm(a), c.all.size, c.C.size, c.R.size] : null;
    }).filter(Boolean).sort((a, b) => b[2] - a[2]);
    rows.forEach((r, i) => r.unshift(i + 1));
    return table([{label: '#'}, {label: 'Attorney'}, {label: 'Latest firm'}, {label: 'Cases', num: 1},
      {label: 'For complainants', num: 1}, {label: 'For respondents', num: 1}], rows, limit);
  }
  function companyBoard(limit) {
    const rows = D.companies.map((c) => {
      const cases = c.cases.filter(([k]) => inYears(k));
      const as = (code) => cases.filter(([, r]) => r.includes(code)).length;
      return cases.length ? [link('company', c), cases.length, as('C'), as('R'), c.family ? esc(c.family) : ''] : null;
    }).filter(Boolean).sort((a, b) => b[1] - a[1]);
    return table([{label: 'Company'}, {label: 'Cases', num: 1}, {label: 'As complainant', num: 1},
      {label: 'As respondent', num: 1}, {label: 'Family'}], rows, limit);
  }

  function viewHome() {
    return `<h1>Who represents whom</h1>
      <div class="sub">Counsel from the filings of ${D.meta.cases_with_counsel} of ${D.meta.cases} investigations.</div>
      ${filterBar(true)}
      <div class="grid">
        <div class="card"><h2>Law firms <small>by cases</small></h2>${firmBoard(25)}</div>
        <div class="card"><h2>Attorneys <small>by cases</small></h2>${attorneyBoard(25)}</div>
      </div>`;
  }
  const viewFirms = () => `<h1>Firms</h1>${filterBar(true)}<div class="card">${firmBoard(200)}</div>`;
  const viewAttorneys = () => `<h1>Attorneys</h1>${filterBar(true)}<div class="card">${attorneyBoard(200)}</div>`;
  const viewCompanies = () => `<h1>Companies</h1>
    <div class="sub">Every party in every investigation, including the ones without counsel on file.</div>
    ${filterBar(false, false)}<div class="card">${companyBoard(200)}</div>`;

  function stats(items) {
    return '<div class="stats">' + items.map(([n, label]) => `<div class="stat"><b>${n}</b><span>${esc(label)}</span></div>`).join('') + '</div>';
  }
  function casesTable(reps, extra) {
    const seen = new Map();
    reps.forEach((i) => {
      if (!repOk(i)) return;
      const r = D.reps[i];
      const e = seen.get(r[0]) || {sides: new Set(), extra: new Set()};
      e.sides.add(r[4]);
      extra(r).forEach((x) => e.extra.add(x));
      seen.set(r[0], e);
    });
    const rows = [...seen.entries()].sort((a, b) => (D.cases[b[0]][2] || 0) - (D.cases[a[0]][2] || 0)).map(([k, e]) =>
      [caseText(k), [...e.sides].map(pill).join(' ') + (D.cases[k][4] ? ' <span class="pill pill-open">open</span>' : ''), [...e.extra].join(', ')]);
    return rows;
  }

  function viewFirm(i) {
    const f = D.firms[i];
    const reps = repsBy.firm[i];
    const c = casesOf(reps);
    const {clients, opponents} = clientsAndOpponents(reps);
    const attorneys = new Map();
    reps.forEach((r) => { if (repOk(r)) D.reps[r][2].forEach((a) => (attorneys.get(a) || attorneys.set(a, new Set()).get(a)).add(D.reps[r][0])); });
    const linked = (list, label) => list.length ? `<div class="sub">${label} ${list.map((x) => link('firm', D.firms[x])).join(', ')}</div>` : '';
    return `<h1>${esc(f.name)}</h1>
      <div class="sub">${f.kind === 'law_firm' ? 'Law firm' : esc(f.kind.replace('_', ' '))}${f.spellings.length ? ' · also filed as ' + f.spellings.map(esc).join('; ') : ''}</div>
      ${linked(f.predecessors, 'Formerly or merged from:')}${linked(f.successors, 'Became or merged into:')}
      ${filterBar(false)}
      ${stats([[c.all.size, 'cases'], [c.C.size, 'for complainants'], [c.R.size, 'for respondents'], ...(c.N.size ? [[c.N.size, 'for non-parties']] : []), [c.open.size, 'open'], [clients.size, 'clients']])}
      <div class="grid">
        <div class="card"><h2>Clients <small>companies it represented</small></h2>${table([{label: 'Company'}, {label: 'Role'}, {label: 'Cases', num: 1}, {label: ''}], companyRows(clients, true), 15)}</div>
        <div class="card"><h2>Opposed <small>companies on the other side</small></h2>${table([{label: 'Company'}, {label: 'Cases', num: 1}, {label: ''}], companyRows(opponents, false), 15)}</div>
      </div>
      <div class="card" style="margin-top:1rem"><h2>Attorneys</h2>${table([{label: 'Attorney'}, {label: 'Cases here', num: 1}],
        [...attorneys.entries()].map(([a, s]) => [link('attorney', D.attorneys[a]), s.size]).sort((x, y) => y[1] - x[1]), 15)}</div>
      <div class="card"><h2>Cases</h2>${table([{label: 'Case'}, {label: 'Acting for'}, {label: 'Clients'}],
        casesTable(reps, (r) => r[3].map((p) => link('company', D.companies[p[0]]))), 25)}</div>`;
  }

  function viewAttorney(i) {
    const a = D.attorneys[i];
    const reps = repsBy.attorney[i];
    const c = casesOf(reps);
    const {clients, opponents} = clientsAndOpponents(reps);
    const stints = a.firms.map(([f, first, last]) => [f >= 0 ? link('firm', D.firms[f]) : '<span class="muted">not known (several firms on one filing)</span>',
      esc((first || '').slice(0, 10)), esc((last || '').slice(0, 10))]);
    return `<h1>${esc(a.name)}</h1>
      <div class="sub">Attorney${a.spellings.length ? ' · also filed as ' + a.spellings.map(esc).join('; ') : ''}</div>
      ${filterBar(false)}
      ${stats([[c.all.size, 'cases'], [c.C.size, 'for complainants'], [c.R.size, 'for respondents'], ...(c.N.size ? [[c.N.size, 'for non-parties']] : []), [c.open.size, 'open']])}
      <div class="card"><h2>Firms</h2>${table([{label: 'Firm'}, {label: 'First filing'}, {label: 'Last filing'}], stints)}</div>
      <div class="grid">
        <div class="card"><h2>Clients</h2>${table([{label: 'Company'}, {label: 'Role'}, {label: 'Cases', num: 1}, {label: ''}], companyRows(clients, true), 15)}</div>
        <div class="card"><h2>Opposed</h2>${table([{label: 'Company'}, {label: 'Cases', num: 1}, {label: ''}], companyRows(opponents, false), 15)}</div>
      </div>
      <div class="card" style="margin-top:1rem"><h2>Cases</h2>${table([{label: 'Case'}, {label: 'Acting for'}, {label: 'Firm'}],
        casesTable(reps, (r) => r[1].map((f) => link('firm', D.firms[f]))), 25)}</div>`;
  }

  function viewCompany(i) {
    const co = D.companies[i];
    const cases = co.cases.filter(([k]) => inYears(k));
    const as = (code) => cases.filter(([, r]) => r.includes(code)).length;
    const reps = repsBy.company[i].filter(repOk);
    const firms = new Map(), attorneys = new Map();
    reps.forEach((r) => {
      D.reps[r][1].forEach((f) => (firms.get(f) || firms.set(f, new Set()).get(f)).add(D.reps[r][0]));
      D.reps[r][2].forEach((a) => (attorneys.get(a) || attorneys.set(a, new Set()).get(a)).add(D.reps[r][0]));
    });
    // Opponents from the case records themselves: every party on the other side.
    const opponents = new Map();
    cases.forEach(([k, roles]) => roles.split('').forEach((role) => (caseParties[k][OTHER[role]] || []).forEach((o) => {
      if (o === i) return;
      const e = opponents.get(o) || {cases: new Set(), roles: new Set()};
      e.cases.add(k); e.roles.add(role === 'C' ? 'sued by it' : 'sued it'); opponents.set(o, e);
    })));
    const oppRows = [...opponents.entries()].map(([o, e]) => [link('company', D.companies[o]), [...e.roles].join(', '), e.cases.size,
      `<span class="muted">${casesCell(e.cases)}</span>`]).sort((a, b) => b[2] - a[2]);
    const family = co.family ? D.companies.filter((x, j) => j !== i && x.family === co.family) : [];
    const names = [co.former.length ? 'formerly ' + co.former.map(esc).join('; ') : '', co.trade.length ? 'trading as ' + co.trade.map(esc).join('; ') : '']
      .filter(Boolean).join(' · ');
    return `<h1>${esc(co.name)}</h1>
      <div class="sub">Company${names ? ' · ' + names : ''}</div>
      ${family.length ? `<div class="sub">Same family (${esc(co.family)}): ${family.slice(0, 12).map((x) => link('company', x)).join(', ')}${family.length > 12 ? ' …' : ''}</div>` : ''}
      ${filterBar(false)}
      ${stats([[cases.length, 'cases'], [as('C'), 'as complainant'], [as('R'), 'as respondent'], [as('N'), 'as non-party']])}
      <div class="grid">
        <div class="card"><h2>Law firms <small>that represented it</small></h2>${table([{label: 'Firm'}, {label: 'Cases', num: 1}],
          [...firms.entries()].map(([f, s]) => [link('firm', D.firms[f]), s.size]).sort((a, b) => b[1] - a[1]), 15)}</div>
        <div class="card"><h2>Attorneys <small>that represented it</small></h2>${table([{label: 'Attorney'}, {label: 'Cases', num: 1}],
          [...attorneys.entries()].map(([a, s]) => [link('attorney', D.attorneys[a]), s.size]).sort((a, b) => b[1] - a[1]), 15)}</div>
      </div>
      <div class="card" style="margin-top:1rem"><h2>Opposed <small>parties on the other side of its cases</small></h2>${table([{label: 'Company'},
        {label: 'Direction'}, {label: 'Cases', num: 1}, {label: ''}], oppRows, 20)}</div>
      <div class="card"><h2>Cases</h2>${table([{label: 'Case'}, {label: 'Role'}],
        cases.slice().sort((a, b) => (D.cases[b[0]][2] || 0) - (D.cases[a[0]][2] || 0)).map(([k, r]) => [caseText(k), r.split('').map(pill).join(' ')]), 25)}</div>`;
  }

  function matches(q) {
    q = q.trim().toLowerCase();
    if (!q) return {firm: [], attorney: [], company: []};
    const hit = (e, extra) => [e.name, ...(extra || [])].some((n) => String(n).toLowerCase().includes(q));
    return {
      firm: D.firms.map((e, i) => i).filter((i) => hit(D.firms[i], D.firms[i].spellings)),
      attorney: D.attorneys.map((e, i) => i).filter((i) => hit(D.attorneys[i], D.attorneys[i].spellings)),
      company: D.companies.map((e, i) => i).filter((i) => hit(D.companies[i], D.companies[i].former.concat(D.companies[i].trade))),
    };
  }
  const LISTS = {firm: 'firms', attorney: 'attorneys', company: 'companies'};
  function viewSearch(q) {
    const m = matches(q);
    const block = (kind, label) => `<div class="card"><h2>${label} <small>${m[kind].length}</small></h2>${table([{label: 'Name'}],
      m[kind].map((i) => [link(kind, D[LISTS[kind]][i])]), 30)}</div>`;
    return `<h1>Results for “${esc(q)}”</h1>${block('firm', 'Firms')}${block('attorney', 'Attorneys')}${block('company', 'Companies')}`;
  }

  function viewReview() {
    const rows = D.review.map((item, i) => [i + 1, esc(item.kind), item.names.map(esc).join(' <span class="muted">vs</span> '),
      item.model ? `${esc(item.model)} <span class="muted">(${Number(item.confidence || 0).toFixed(2)})</span>` : '<span class="muted">not asked</span>']);
    return `<h1>Name pairs that need you</h1>
      <div class="sub">The rules and the model could not tell whether these are the same. Until you decide, each stays two
        separate entries, so a count can come out low but never merges two different firms, companies or people.</div>
      <div class="card">
        <p>Settle them from the project folder in a terminal:</p>
        <p><code>python cli.py decide</code> lists them with the same numbers as below;
           <code>python cli.py decide 3</code> shows one in full;
           <code>python cli.py decide 3 same</code> (or <code>different</code>; for firms also <code>a-became-b</code> / <code>b-became-a</code>) records your answer.
           Then press Rebuild here.</p>
        ${table([{label: '#'}, {label: 'Kind'}, {label: 'Names'}, {label: 'Model said'}], rows)}
      </div>`;
  }

  // ---- routing ---------------------------------------------------------------
  function route() {
    const hash = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
    const [kind, ...rest] = hash.split('/');
    const id = rest.join('/');
    let html;
    if (!kind) html = viewHome();
    else if (kind === 'firms') html = viewFirms();
    else if (kind === 'attorneys') html = viewAttorneys();
    else if (kind === 'companies') html = viewCompanies();
    else if (kind === 'review') html = viewReview();
    else if (kind === 'search') html = viewSearch(id);
    else if (index[kind] && index[kind].has(id)) {
      const i = index[kind].get(id);
      html = kind === 'firm' ? viewFirm(i) : kind === 'attorney' ? viewAttorney(i) : viewCompany(i);
    } else html = '<div class="card">Not found. It may have been merged into another entry by the last rebuild: try the search.</div>';
    app.innerHTML = html;
    wireFilters();
    document.querySelectorAll('nav a').forEach((a) => a.classList.toggle('active', a.getAttribute('href') === '#/' + (kind || '')));
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', route);

  // ---- search box --------------------------------------------------------------
  const box = document.getElementById('q');
  const drop = document.getElementById('results');
  box.addEventListener('input', () => {
    const q = box.value;
    if (!q.trim()) { drop.hidden = true; return; }
    const m = matches(q);
    const group = (kind, label) => m[kind].length ? `<div class="group">${label}</div>` + m[kind].slice(0, 6).map((i) => link(kind, D[LISTS[kind]][i])).join('') : '';
    const html = group('firm', 'Firms') + group('attorney', 'Attorneys') + group('company', 'Companies');
    drop.innerHTML = html || '<div class="none">No matches</div>';
    drop.hidden = false;
  });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && box.value.trim()) { location.hash = '#/search/' + encodeURIComponent(box.value.trim()); drop.hidden = true; }
    if (e.key === 'Escape') drop.hidden = true;
  });
  drop.addEventListener('click', () => { drop.hidden = true; box.value = ''; });
  document.addEventListener('click', (e) => { if (!e.target.closest('.search')) drop.hidden = true; });

  const badge = document.getElementById('review-count');
  if (D.review.length) { badge.textContent = D.review.length; badge.hidden = false; }
  route();
  setupStatus();

  // ---- status and rebuild ------------------------------------------------------
  function setupStatus() {
    const when = (iso) => iso ? new Date(iso).toLocaleString() : 'never';
    const built = D && D.meta.built_at;
    const parts = [`<span>Analytics built ${esc(when(built))}</span>`];
    if (D && D.review.length) parts.push(`<a class="warn" href="#/review">${D.review.length} name pair(s) need your review</a>`);
    if (location.protocol === 'file:') {
      parts.push('<span class="warn">Opened from disk: start ITC Analytics.bat to rebuild.</span>');
      statusEl.innerHTML = parts.join(' · ');
      return;
    }
    statusEl.innerHTML = parts.join(' · ') + ' <span id="stale"></span> <button class="btn" id="rebuild">Rebuild</button>';
    const button = document.getElementById('rebuild');
    const jobBox = document.getElementById('job');
    function show(job) {
      if (!job) return;
      jobBox.hidden = false;
      jobBox.querySelector('b').textContent = job.label + (job.state === 'running' ? ' (running…)' : '');
      jobBox.querySelector('.msg').textContent = job.message || '';
      jobBox.querySelector('pre').textContent = (job.lines || []).join('\n');
    }
    function poll() {
      fetch('api/jobs/current').then((r) => r.json()).then((b) => {
        show(b.job);
        if (b.job && b.job.state === 'running') { button.disabled = true; return setTimeout(poll, 1000); }
        button.disabled = false;
        if (b.job && b.job.level !== 'error' && b.job.finished_at && Date.now() - new Date(b.job.finished_at) < 5000) location.reload();
      }).catch(() => setTimeout(poll, 2000));
    }
    button.addEventListener('click', () => {
      button.disabled = true;
      fetch('api/rebuild', {method: 'POST'}).then((r) => r.json()).then((b) => { if (!b.ok) { alertBox(b.message); button.disabled = false; } else poll(); });
    });
    function alertBox(message) { jobBox.hidden = false; jobBox.querySelector('b').textContent = 'Could not start'; jobBox.querySelector('.msg').textContent = message; }
    fetch('api/status').then((r) => r.json()).then((s) => {
      if (s.stale) document.getElementById('stale').innerHTML = '<span class="warn">· the counsel data has changed since</span>';
      if (s.job && s.job.state === 'running') { show(s.job); button.disabled = true; poll(); }
    }).catch(() => {});
  }
})();
"""


def html() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<style>{CSS}</style>
</head>
<body>
<header><div class="bar">
  <span class="brand">{TITLE}</span>
  <nav>
    <a href="#/">Leaderboards</a><a href="#/firms">Firms</a><a href="#/attorneys">Attorneys</a>
    <a href="#/companies">Companies</a><a href="#/review">Review<span class="badge" id="review-count" hidden></span></a>
  </nav>
  <div class="search"><input id="q" type="search" placeholder="Search firms, attorneys, companies" autocomplete="off">
    <div class="results" id="results" hidden></div></div>
</div></header>
<main>
  <div class="status" id="status"></div>
  <div class="job card" id="job" hidden><b></b><div class="msg muted"></div><pre></pre></div>
  <div id="app"></div>
</main>
<script src="data.js"></script>
<script>{SCRIPT}</script>
</body>
</html>
"""
