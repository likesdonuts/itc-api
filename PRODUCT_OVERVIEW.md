# ITC Section 337 Tracker: Product Overview

*Last updated: 2026-09-26*

## What it is

A desktop tool that tracks every U.S. International Trade Commission Section
337 investigation (patent and unfair-import cases) in one place. For each case
it shows the status, the parties, the law firms and attorneys on each side,
and the filed documents. For selected cases it also shows how the asserted
patent claims narrowed over the life of the case. It runs on one PC and opens
in a browser; one button refreshes it each day.

A companion **ITC Analytics** app answers questions about the Section 337 bar
as a whole:
- which law firms and attorneys appear in the most investigations, and on
  which side
- whom each firm and attorney has represented and opposed
- every company's full record as complainant and respondent, including under
  former names
- the firms and lawyers each company has used
- each firm's caseload over time (still-open vs. closed cases) and the firms
  it most often works alongside (co-counsel)
- the "frequent fliers": the companies most often sued and most often suing
- pairs of companies that have sued each other in both directions
- all of the above for a whole corporate family ("Samsung") as well as for
  single companies

It refreshes itself once a day when opened, or on demand.

## How the data is retrieved

| Source | What it provides | How often |
| --- | --- | --- |
| USITC Investigations Data System (IDS) | Every investigation: number, title, status, dates, parties | One public download a day |
| USITC Electronic Document Information System (EDIS) | Each case's docket: every filing, who filed it, for whom, and the PDFs | Listed once for every case (the backfill), then daily for open cases and cases being followed; PDFs on demand (needs a free EDIS access token) |
| Federal Register | Commission notices cited by the claims analysis | On demand |
| AI model (Claude Haiku) | Reads rulings in the PDFs to find which patent claims were dropped, found invalid or found infringed | On demand per case, with a $20 spending cap |
| AI model (Claude Haiku) | Reads judges' scheduling orders for the procedural schedule of each active case | Daily, only new orders, with its own $20 spending cap |
| AI models (Claude Haiku, Claude Sonnet) | Plain-English case summaries: Haiku takes notes on the complaint and the answers, and Sonnet writes the summary from those notes | On demand per case (about $0.10 to $0.40), with its own $20 spending cap; runs stop at the cap until it is raised |

Each daily download is kept as a dated copy. So the app can always rebuild
from a known file, and every sync is logged to help spot bad data days.

## The data today

- **1,383 investigations**, dating back to 1987:
  - 125 active
  - 59 pending before a judge
  - 14 pending before the Commission
  - 824 terminated
  - the rest inactive, withdrawn or not instituted
- **Dockets for every case with filings on record:** 1,370 cases,
  419,900 filings listed. 12 older cases have nothing in the USITC's
  electronic filing system. 6,187 PDFs (about 20 GB) have been downloaded
  for the cases followed most closely.
- **Counsel:** 8,184 firm-to-client relationships across 1,143 cases,
  covering 1,282 law firms and 10,130 attorneys. The oldest cases' filings
  don't name firms, so they have no counsel. The most active firms are
  Adduci, Mastriani & Schaumberg (236 investigations), Fish & Richardson
  (235) and Finnegan (177).
- **Non-parties:** 1,730 companies and people pulled into 425 cases by
  subpoena or intervention.
- **Name matching (for analytics):**
  - 2,851 firm spellings resolve to 1,434 filers, 1,282 of them law firms.
    The rest are companies or people representing themselves, trade groups
    and government bodies.
  - 11,816 attorney name forms resolve to 10,130 people, 1,391 of whom
    appear at more than one firm over time.
  - 7,322 company names resolve to 7,225 companies. 25 companies are
    linked to their former names (for example Philips Lighting → Signify).
  - Borderline cases were checked by AI for about $0.21 in total, and 64
    are left for a person to decide.
- **Next actions:** schedules for 5 pilot cases read for about $0.32; the
  other active cases' orders (about 175) are read by the next daily update,
  an estimated $1.50.
- **Claims analysis:** 3 pilot cases analyzed for about $0.15 in total AI
  cost.

## User problems and what we built

| Problem | Solution |
| --- | --- |
| Tracking a case meant checking two USITC websites by hand. | A daily one-click sync merges both into one searchable list with a page per case. |
| Neither source says who represents whom. | The app reads filing records and attorneys' Notice of Appearance PDFs. It links each law firm and attorney to its clients, and marks lead counsel and withdrawals. The list is searchable by firm or attorney. |
| Companies subpoenaed into a case never appear in the official party list. | The app finds them from their own filings. It shows why they are involved, such as "responding to a subpoena served by Respondents", read from their notices, including scanned documents. |
| For an active case, it was hard to know what happens next and when: hearing dates, decision deadlines and review periods are spread across the case record, orders, notices and the Commission's rules. | A **Next actions** tab on every open case shows a stage bar and the next event with a countdown. It lists every date in the judge's procedural schedule (discovery cutoffs, expert reports, Markman and evidentiary hearings, briefing), with later amendments applied and each linked to its order. It adds the dates in the USITC case record and the deadlines set by the Commission's rules, with the rule cited (e.g. petitions for review due 12 days after the final decision). When nothing is scheduled it says "Awaiting decision" and names who is deciding. AI reads the schedules, which are often scanned tables, and each date is checked against the order's text. Today 72 cases are actively moving, and 70 of them have an upcoming date. |
| Across dozens of active cases it was hard to see what is coming up this week, which cases are paused, and who has settled out. | The case list has a **Next deadline** column and a **Due in the next 7 days** panel covering every active case. Stayed cases are flagged, with their scheduled dates marked on hold (6 cases today). Each case lists the respondents who have settled out, been terminated or defaulted, with whether the Commission has finalized it. |
| It was hard to know whether a case's data is current. | Each case shows when its documents were last fetched, plus how many documents it has and how many have PDFs on disk. |
| Following how patent claims narrow means reading hundreds of pages of rulings. | An AI-assisted claims timeline shows, for each respondent, which claims were withdrawn, dismissed or found invalid, and when. Every finding links back to its source sentence and is checked before it is shown. Cost is tracked against a hard budget. |
| Firm and attorney information only existed for the ~185 cases someone had chosen to fetch, too few for firm-level trends. | A one-click backfill lists the filings of every case (no PDFs), newest first, and can be stopped and resumed. It has now run: counsel covers 1,143 investigations instead of 179, which is what the firm and attorney analytics are built on. |
| The same firm, lawyer or company appears under many spellings: typos, "LLP" vs "L.L.P.", short forms, former company names, and several firms run together in one field. Any count or ranking would be wrong. | Automatic name matching turns spellings into single firms, attorneys and companies. It uses clear rules first, then AI review for borderline cases, and leaves the rest for a person. The person settles each one with a single command (same, different, or renamed firm), and the answer is kept for every future rebuild. It keeps predecessor firms linked rather than merged, keeps companies representing themselves out of the law-firm lists, and follows attorneys who move between firms. A match-quality report shows every merge and why it was made. |
| Questions like "which firms do the most ITC work for respondents?", "who has Apple faced, and with which lawyers?" or "whom has this attorney opposed?" meant reading dockets case by case. | The ITC Analytics app offers leaderboards for firms and attorneys and a page for every firm, attorney and company. Each page shows clients, opponents, attorneys and cases. Search works across all names, and filters cover side and years. |
| Business-development and competitive questions ("which firms is this firm growing with?", "who gets sued most?", "which rivals keep suing each other?") had no answer short of manual research. | Caseload timelines and co-counsel lists on each firm's page. Frequent-flier rankings of the most-sued and most-suing companies. A list of companies that have sued each other in both directions. All of it can be grouped by corporate family and filtered by years. |
| The daily update took about an hour. Every day it re-read every page of about 190 cases' dockets and re-checked about 1,400 files it already had, roughly 4,000 requests to the USITC. | It now reads only each docket's new filings and skips files already downloaded. Closed and inactive cases are refreshed weekly or monthly instead of daily. Each case still gets a full weekly re-check to catch corrections. That's expected to cut a typical day to about 80 cases and a few hundred requests. Every run's duration, step by step, is now logged and shown on the dashboard. |
| The USITC's daily data file sometimes fails to download. | The download retries automatically. If it still fails, the rest of the daily update runs anyway and the failure is logged. |
| Lawyers new to IP and Section 337 can't easily tell what a case is about or where it stands. A complaint filing runs to over 1,000 pages (one reached 35,000), mostly exhibits, and the rest of the docket runs to hundreds of filings. | A **Summary** tab on every case page. One click writes a plain-English summary: a one-line headline, what the case is about (the parties, the technology, the patents and the products), how the complainant says the law is broken, and each defense team's main defenses. Terms of art are explained in passing. Every paragraph links to the exact pages it came from; hovering shows the quoted words and clicking opens the page. The AI's notes are checked word for word against the filings before they are used. The app picks the few documents worth reading and only the relevant pages of each: the complaint itself, not its exhibits, and one answer per defense team, up to five. A summary costs about $0.10 to $0.40, shown before anything is spent, and updating one pays only for new filings. Next to it is a plain-English guide to how Section 337 cases work, drafted and awaiting review by a practitioner. **Still to come:** the rulings before the hearing and the judge's and Commission's decisions. |
| Gigabytes of PDFs slowed down version control. | Documents stay on the local machine only, and can be downloaded again from the USITC at any time. |

## Current limits

- The app is single-user and runs on one PC.
- The EDIS access token expires and has to be renewed by hand.
- The claims analysis has only been run on three pilot cases so far.
- Firm and attorney rankings cover the 1,143 investigations whose filings
  name counsel. The oldest cases predate electronic filing or don't record
  firms, so they are missing. Company histories cover every investigation.
- 64 name pairs are waiting for a person to decide. Until then each stays
  as two entries, which can undercount a firm, attorney or company but
  never merges two different ones.
- Next actions cover only the main violation phase of a case, not remand,
  enforcement or modification proceedings. Procedural schedules come from
  the orders a judge has actually issued, so a brand-new case shows only its
  rule-based dates until its schedule order is filed.
- About 90 very old investigations are still listed by the USITC as
  "active" (usually because their exclusion orders remain in force). They
  have no dates, so there is nothing to show for them. The daily update
  checks them monthly instead of daily.
- A document edited after it was filed (for example, made public later) is
  picked up by the weekly full re-check, so it can take up to a week to
  appear. New filings appear the next day.
- Case summaries so far cover the complaint and the answers only; the
  rulings and the judge's and Commission's decisions come next. They read
  only public versions of filings, so anything redacted is invisible to them,
  and only the opening and closing pages of a long answer. They have been
  written for 3 pilot cases. The Section 337 guide needs review by a
  Section 337 practitioner before users rely on it.
- About 60 of the oldest investigations (three-digit numbers such as
  337-TA-112) have document lists mixed with other cases' filings. The USITC's
  document search appears to match on the start of the number, so 337-112
  picks up 337-1120 to 337-1129. Their counsel and document counts are
  unreliable until this is fixed.

## On hold

- **Claims analysis, phase 4.** Paused by choice. The first three phases work
  on the pilot cases.
