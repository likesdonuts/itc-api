# ITC Section 337 Tracker: Product Overview

*Last updated: 2026-09-25*

## What it is

A desktop tool that tracks every U.S. International Trade Commission Section
337 investigation (patent and unfair-import cases) in one place. For each case
it shows the status, the parties, the law firms and attorneys on each side,
and the filed documents. For selected cases it also shows how the asserted
patent claims narrowed over the life of the case. It runs on one PC and opens
in a browser; one button refreshes it each day.

## How the data is retrieved

| Source | What it provides | How often |
| --- | --- | --- |
| USITC Investigations Data System (IDS) | Every investigation: number, title, status, dates, parties | One public download a day |
| USITC Electronic Document Information System (EDIS) | Each case's docket: every filing, who filed it, for whom, and the PDFs | On demand for chosen cases, and daily for cases already tracked (needs a free EDIS access token) |
| Federal Register | Commission notices cited by the claims analysis | On demand |
| AI model (Claude Haiku) | Reads rulings in the PDFs to find which patent claims were dropped, found invalid or found infringed | On demand per case, with a $20 spending cap |

Each daily download is kept as a dated copy. So the app can always rebuild
from a known file, and every sync is logged to help spot bad data days.

## The data today

- **1,382 investigations**, dating back to 1987:
  - 125 active
  - 55 pending before a judge
  - 15 pending before the Commission
  - 823 terminated
  - the rest inactive, withdrawn or not instituted
- **185 cases with full dockets:** 39,379 filings listed, and 5,619 PDFs
  (about 20 GB) on disk.
- **Counsel:** 1,058 firm-to-client relationships across 179 cases,
  covering 322 law firms and 1,409 attorneys.
- **Non-parties:** 200 companies and people pulled into 73 cases by subpoena
  or intervention.
- **Claims analysis:** 3 pilot cases analyzed for about $0.15 in total AI
  cost.

## User problems and what we built

| Problem | Solution |
| --- | --- |
| Tracking a case meant checking two USITC websites by hand. | A daily one-click sync merges both into one searchable list with a page per case. |
| Neither source says who represents whom. | The app reads filing records and attorneys' Notice of Appearance PDFs. It links each law firm and attorney to its clients, and marks lead counsel and withdrawals. The list is searchable by firm or attorney. |
| Companies subpoenaed into a case never appear in the official party list. | The app finds them from their own filings. It shows why they are involved, such as "responding to a subpoena served by Respondents", read from their notices, including scanned documents. |
| It was hard to know whether a case's data is current. | Each case shows when its documents were last fetched, plus how many documents it has and how many have PDFs on disk. |
| Following how patent claims narrow means reading hundreds of pages of rulings. | An AI-assisted claims timeline shows, for each respondent, which claims were withdrawn, dismissed or found invalid, and when. Every finding links back to its source sentence and is checked before it is shown. Cost is tracked against a hard budget. |
| The USITC's daily data file sometimes fails to download. | The download retries automatically. If it still fails, the rest of the daily update runs anyway and the failure is logged. |
| Gigabytes of PDFs slowed down version control. | Documents stay on the local machine only, and can be downloaded again from the USITC at any time. |

## Current limits

- The app is single-user and runs on one PC.
- The EDIS access token expires and has to be renewed by hand.
- The claims analysis has only been run on three pilot cases so far.
