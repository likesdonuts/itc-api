# ITC Section 337 Tracker: Product Overview

*Last updated: 2026-09-25*

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
- **Claims analysis:** 3 pilot cases analyzed for about $0.15 in total AI
  cost.

## User problems and what we built

| Problem | Solution |
| --- | --- |
| Tracking a case meant checking two USITC websites by hand. | A daily one-click sync merges both into one searchable list with a page per case. |
| Neither source says who represents whom. | The app reads filing records and attorneys' Notice of Appearance PDFs. It links each law firm and attorney to its clients, and marks lead counsel and withdrawals. The list is searchable by firm or attorney. |
| Companies subpoenaed into a case never appear in the official party list. | The app finds them from their own filings. It shows why they are involved, such as "responding to a subpoena served by Respondents", read from their notices, including scanned documents. |
| For an active case, it was hard to know what happens next and when: hearing dates, decision deadlines and review periods are spread across the case record, orders, notices and the Commission's rules. | A **Next actions** tab on every open case shows its stage and the next event with a countdown. It lists every known date, each labeled by its source: the USITC case record, the docket, or the Commission's rules (with the rule cited, e.g. petitions for review due 12 days after the final decision). When nothing is scheduled it says "Awaiting decision" and names who is deciding. Today 72 cases are actively moving, and 70 of them have an upcoming date. |
| It was hard to know whether a case's data is current. | Each case shows when its documents were last fetched, plus how many documents it has and how many have PDFs on disk. |
| Following how patent claims narrow means reading hundreds of pages of rulings. | An AI-assisted claims timeline shows, for each respondent, which claims were withdrawn, dismissed or found invalid, and when. Every finding links back to its source sentence and is checked before it is shown. Cost is tracked against a hard budget. |
| Firm and attorney information only existed for the ~185 cases someone had chosen to fetch, too few for firm-level trends. | A one-click backfill lists the filings of every case (no PDFs), newest first, and can be stopped and resumed. It has now run: counsel covers 1,143 investigations instead of 179, which is what the firm and attorney analytics are built on. |
| The same firm, lawyer or company appears under many spellings: typos, "LLP" vs "L.L.P.", short forms, former company names, and several firms run together in one field. Any count or ranking would be wrong. | Automatic name matching turns spellings into single firms, attorneys and companies. It uses clear rules first, then AI review for borderline cases, and leaves the rest for a person. The person settles each one with a single command (same, different, or renamed firm), and the answer is kept for every future rebuild. It keeps predecessor firms linked rather than merged, keeps companies representing themselves out of the law-firm lists, and follows attorneys who move between firms. A match-quality report shows every merge and why it was made. |
| Questions like "which firms do the most ITC work for respondents?", "who has Apple faced, and with which lawyers?" or "whom has this attorney opposed?" meant reading dockets case by case. | The ITC Analytics app offers leaderboards for firms and attorneys and a page for every firm, attorney and company. Each page shows clients, opponents, attorneys and cases. Search works across all names, and filters cover side and years. |
| Business-development and competitive questions ("which firms is this firm growing with?", "who gets sued most?", "which rivals keep suing each other?") had no answer short of manual research. | Caseload timelines and co-counsel lists on each firm's page. Frequent-flier rankings of the most-sued and most-suing companies. A list of companies that have sued each other in both directions. All of it can be grouped by corporate family and filtered by years. |
| The USITC's daily data file sometimes fails to download. | The download retries automatically. If it still fails, the rest of the daily update runs anyway and the failure is logged. |
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
- Next actions show the dates in the USITC case record, the docket and the
  rules. The detailed procedural schedules in judges' orders (discovery
  cutoffs, expert reports, briefing) come in the next phase. Only the main
  violation phase of a case is covered, not remand, enforcement or
  modification proceedings.
- About 90 very old investigations are still listed by the USITC as
  "active" (usually because their exclusion orders remain in force). They
  have no dates, so there is nothing to show for them.

## In progress

- **Next actions, phase 2:** read the judges' scheduling orders with AI (on
  its own $20 budget) so every date in a case's procedural schedule appears,
  with amendments applied.

## On hold

- **Claims analysis, phase 4.** Paused by choice. The first three phases work
  on the pilot cases.
