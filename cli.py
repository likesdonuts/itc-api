"""Entry point for the ITC 337 investigation tracker.

The work is split into two independent layers so that changing how the site
looks never means re-fetching anything:

  Data layer (datalayer/)  -- talks to IDS and EDIS, writes data/ + PDFs
  UI layer   (ui/)         -- reads data/*.json + ui_schema.json, writes site/

and the data layer has two separate processes over two separate sources:

  sync   the daily IDS snapshot. Every investigation, its stages, its
         parties. Owns data/investigations.json. No token needed.
  docs   the EDIS API, for the numbers you name. Owns document lists and
         downloaded PDFs, and cannot touch a case record.

plus one that reads what those two wrote, offline:

  counsel  which firms and attorneys represent which parties, read from the
           filings. Owns data/counsel.json. Runs after sync and docs.

Examples:
    python cli.py sync                      # download today's IDS file, rebuild cases
    python cli.py sync --render             # ...and rebuild the site too
    python cli.py parse                     # re-parse the stored snapshot, offline
    python cli.py docs 337-1478 337-3936    # fetch those cases' documents
    python cli.py docs 337-1478 --no-attachments
    python cli.py docs --existing --appearances   # just the Notice of Appearance PDFs
    python cli.py backfill                  # document lists (no PDFs) for every case; resumable
    python cli.py counsel                   # rebuild who-represents-whom, offline
    python cli.py next-actions --render     # what happens next in each open case, offline
    python cli.py analytics                 # firms, attorneys, companies as entities
    python cli.py decide                    # the name pairs waiting for you; decide 3 same
    python cli.py analytics-serve           # open the analytics app (what ITC Analytics.bat runs)
    python cli.py claims 337-1366 --render  # build a claims analysis
    python cli.py render                    # rebuild the site, offline
    python cli.py serve                     # open the app (what ITC Tracker.bat runs)
    python cli.py fields                    # what ui_schema.json can name
    python cli.py status                    # what's on disk, no network
    python cli.py refresh                   # sync + documents we already have + render
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import schema as ui_schema
from datalayer import backfill, counsel, dailylog, docs, ids, ingest, normalize, runlog
from datalayer.nextactions import build as nextactions_build
from datalayer.config import DATA_DIR, IDS_DIR, MissingTokenError, SCHEMA_PATH, SITE_DIR, load_token
from datalayer.runner import ProcessAborted
from datalayer.store import Store
from ui.render import render_site


def _add_render_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--render",
        action="store_true",
        help="rebuild the site afterwards (same as running 'render')",
    )


def _add_removals_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-removals",
        action="store_true",
        help="accept a snapshot that drops an implausible number of cases",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--site-dir", type=Path, default=SITE_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--ids-dir", type=Path, default=IDS_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser(
        "sync",
        help="daily IDS snapshot: download it if today's is missing, rebuild every case",
    )
    p_sync.add_argument(
        "--force", action="store_true", help="download again even if today's snapshot is stored"
    )
    p_sync.add_argument(
        "--keep",
        type=int,
        default=ids.DEFAULT_KEEP,
        help=f"days of snapshots to keep on disk (default {ids.DEFAULT_KEEP}, 0 keeps all)",
    )
    _add_removals_flag(p_sync)
    _add_render_flag(p_sync)

    p_parse = sub.add_parser(
        "parse", help="rebuild cases from the newest stored snapshot (offline, no download)"
    )
    _add_removals_flag(p_parse)
    _add_render_flag(p_parse)

    p_docs = sub.add_parser(
        "docs", help="EDIS documents for the case numbers you name (writes nothing else)"
    )
    p_docs.add_argument(
        "numbers",
        nargs="*",
        help="investigation or docket numbers, e.g. 337-1478 337-TA-1478 337-3936",
    )
    p_docs.add_argument(
        "--existing",
        action="store_true",
        help="every case documents have already been fetched for",
    )
    p_docs.add_argument(
        "--all", action="store_true", help="every case on disk (over a thousand EDIS calls)"
    )
    p_docs.add_argument("--limit", type=int, help="stop after this many cases")
    attachments = p_docs.add_mutually_exclusive_group()
    attachments.add_argument(
        "--no-attachments",
        action="store_true",
        help="record document metadata only; skip downloading PDFs",
    )
    attachments.add_argument(
        "--appearances",
        action="store_true",
        help="download only Notice of Appearance PDFs (they name each party's attorneys)",
    )
    p_docs.add_argument(
        "--known-only",
        action="store_true",
        help="refuse numbers that aren't on disk instead of asking EDIS about them",
    )
    _add_render_flag(p_docs)

    p_backfill = sub.add_parser(
        "backfill",
        help="list the documents (no PDFs) of every case that has no list yet; resumable",
    )
    p_backfill.add_argument("--limit", type=int, help="stop after this many cases")
    p_backfill.add_argument(
        "--retry-empty", action="store_true", help="ask EDIS again about cases it had nothing for"
    )
    p_backfill.add_argument(
        "--pause", type=float, default=None, help="seconds between cases (default 0.5)"
    )
    p_backfill.add_argument(
        "--force", action="store_true", help=argparse.SUPPRESS  # run even with the app open
    )
    _add_render_flag(p_backfill)

    p_next = sub.add_parser(
        "next-actions",
        help="rebuild each open investigation's next actions: stage, dates and deadlines (offline)",
    )
    p_next.add_argument(
        "--orders", action="store_true",
        help="first read new scheduling orders (downloads their PDFs from EDIS; Claude Haiku, own budget)",
    )
    p_next.add_argument("numbers", nargs="*", help="with --orders: only these investigations (default: every live one)")
    _add_render_flag(p_next)

    p_analytics = sub.add_parser(
        "analytics",
        help="rebuild the representation analytics: firms, attorneys and companies as entities (offline, "
        "plus a few model calls for pairs the rules cannot settle)",
    )
    p_analytics.add_argument(
        "--no-review", action="store_true", help="make no model calls; borderline pairs stay unmerged"
    )
    p_analytics.add_argument(
        "--max-review", type=int, default=None, help="ask the model about at most this many pairs (default 300)"
    )

    p_analytics_serve = sub.add_parser(
        "analytics-serve",
        help="open the analytics app (what ITC Analytics.bat runs): leaderboards, firms, attorneys, companies",
    )
    p_analytics_serve.add_argument("--port", type=int, default=8766, help="port to listen on (default 8766)")
    p_analytics_serve.add_argument("--no-browser", action="store_true", help="don't open a browser window on startup")

    p_decide = sub.add_parser(
        "decide",
        help="settle the analytics name pairs that need a person: list them, show one, or record an answer",
    )
    p_decide.add_argument("number", nargs="?", type=int, help="the pair's number in the list")
    p_decide.add_argument(
        "answer", nargs="?", help="same | different | a-became-b | b-became-a (firms) | split | one (firm fields)"
    )
    p_decide.add_argument("parts", nargs="*", help="for split: the firms the field names")

    p_claims = sub.add_parser(
        "claims",
        help="build or update the claims analysis for the investigations you name",
    )
    p_claims.add_argument("numbers", nargs="+", help="investigation numbers, e.g. 337-1366 337-TA-1384")
    p_claims.add_argument(
        "--reread",
        action="store_true",
        help="read every source document again (paid again), e.g. after the reading rules change",
    )
    _add_render_flag(p_claims)

    p_counsel = sub.add_parser(
        "counsel",
        help="rebuild which firms and attorneys represent which parties, from the filings (offline)",
    )
    p_counsel.add_argument(
        "--verbose", action="store_true", help="list each non-party and where its reason stands"
    )
    _add_render_flag(p_counsel)

    sub.add_parser("render", help="UI layer only: rebuild site/ from data/ (offline)")
    sub.add_parser("fields", help="list the field names ui_schema.json can use (offline)")
    sub.add_parser("status", help="list what's on disk (offline)")
    sub.add_parser(
        "normalize", help="rewrite stored dates to ISO 8601 in place (offline, no API calls)"
    )

    p_serve = sub.add_parser(
        "serve",
        help="open the app: the site plus its buttons for the daily sync and document fetches",
    )
    p_serve.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    p_serve.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    p_serve.add_argument(
        "--no-browser", action="store_true", help="don't open a browser window on startup"
    )

    p_refresh = sub.add_parser(
        "refresh",
        help="the daily job: sync, refresh documents already fetched, render",
    )
    refresh_attachments = p_refresh.add_mutually_exclusive_group()
    refresh_attachments.add_argument(
        "--no-attachments", action="store_true", help="skip downloading PDFs"
    )
    refresh_attachments.add_argument(
        "--appearances", action="store_true", help="download only Notice of Appearance PDFs"
    )
    p_refresh.add_argument(
        "--no-documents", action="store_true", help="IDS only; make no EDIS calls"
    )

    return parser


def _render(args: argparse.Namespace, store: Store) -> None:
    render_site(store, site_dir=args.site_dir, schema_path=args.schema)


def _counsel(store: Store, *, verbose: bool = False) -> None:
    """Counsel is matched against the IDS parties and read from the EDIS
    filings, so it is rebuilt whenever either of those changes -- and so are
    the next actions, which read the same two.
    """
    counsel.run(store, verbose=verbose, log=print)
    nextactions_build.run(store, log=print)


def _read_schedules(store: Store) -> None:
    """The daily job's last document step: the live cases' new scheduling
    orders, for Next actions. Skipped, with a note, without an API key."""
    from datalayer.claims.extract import MissingApiKeyError
    from datalayer.nextactions import orders

    try:
        orders.run(store, token=load_token(), log=print)
    except MissingApiKeyError as exc:
        print(f"  ! Scheduling orders not read: {exc}")


def cmd_next_actions(args: argparse.Namespace, store: Store) -> int:
    code = 0
    if args.orders:
        from datalayer.nextactions import orders

        numbers = [store.find_key(n) or n for n in args.numbers] or None
        report = orders.run(store, token=load_token(), keys=numbers, log=print)
        code = 1 if report.stopped else 0
    nextactions_build.run(store, log=print)
    if args.render:
        _render(args, store)
    return code


def _only_types(args: argparse.Namespace) -> frozenset[str] | None:
    return docs.APPEARANCE_TYPES if getattr(args, "appearances", False) else None


def cmd_sync(args: argparse.Namespace, store: Store) -> int:
    report = ingest.run(
        store,
        ids_dir=args.ids_dir,
        force=args.force,
        keep=args.keep,
        allow_removals=args.allow_removals,
        log=print,
    )
    _counsel(store)
    print(
        f"\nSync done. {report.cases} investigation(s) from IDS snapshot "
        f"{report.snapshot_day}: {len(report.added)} new, {len(report.changed)} changed, "
        f"{len(report.withdrawn)} kept after leaving the feed."
    )
    print(f"Logged this run to {runlog.path_for(args.data_dir)}.")
    if args.render:
        _render(args, store)
    else:
        print("Run 'python cli.py render' to rebuild the site.")
    return 0


def cmd_parse(args: argparse.Namespace, store: Store) -> int:
    report = ingest.run(
        store,
        ids_dir=args.ids_dir,
        offline=True,
        allow_removals=args.allow_removals,
    )
    _counsel(store)
    print(f"\nParsed {report.cases} investigation(s) from snapshot {report.snapshot_day}.")
    if args.render:
        _render(args, store)
    return 0


def cmd_docs(args: argparse.Namespace, store: Store) -> int:
    numbers = list(args.numbers)
    if args.all:
        numbers = store.tracked_numbers()
    elif args.existing:
        numbers = store.numbers_with_documents()
    if args.limit:
        numbers = numbers[: args.limit]

    if not numbers:
        print(
            "Give one or more investigation numbers, or use --existing "
            "(cases already fetched) or --all."
        )
        return 1

    report = docs.run(
        store,
        load_token(),
        numbers,
        download=not args.no_attachments,
        only_types=_only_types(args),
        known_only=args.known_only,
        # Naming cases makes them yours; --existing/--all is a refresh.
        by_hand=not (args.all or args.existing),
    )
    print(
        f"\nDocuments done. {len(report.fetched)} case(s) fetched, "
        f"{report.downloaded} attachment(s) downloaded, {len(report.failed)} skipped."
    )
    if report.fetched:
        _counsel(store)
    if args.render:
        _render(args, store)
    return 0 if report.fetched or not report.failed else 1


def _app_is_running(port: int = 8765) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def cmd_backfill(args: argparse.Namespace, store: Store) -> int:
    if _app_is_running() and not args.force:
        # Both would rewrite the document lists (data/documents_index/),
        # each without the other's changes.
        print(
            "The app is open. Use its 'Backfill all cases' button instead, or close it "
            "first: two processes writing the document lists would overwrite each other."
        )
        return 1
    options = {"pause": args.pause} if args.pause is not None else {}
    report = backfill.run(
        store, load_token(), limit=args.limit, retry_empty=args.retry_empty, **options
    )
    if report.listed:
        _counsel(store)
    if args.render:
        _render(args, store)
    return 1 if report.stopped else 0


def cmd_analytics(args: argparse.Namespace, store: Store) -> int:
    from datalayer.analytics import build as analytics_build

    options = {"max_review_items": args.max_review} if args.max_review is not None else {}
    report = analytics_build.run(store, review=not args.no_review, **options)
    _refresh_analytics_app(args)
    return 1 if report.review.stopped else 0


def _refresh_analytics_app(args: argparse.Namespace, log=print) -> None:
    """Keep the analytics app's page in step with a rebuild from here."""
    from analytics_ui import render as analytics_render

    analytics_render.render(args.data_dir, _analytics_site(args), log=log)


def _analytics_site(args: argparse.Namespace) -> Path:
    # Beside the tracker's site/: site_analytics/ in the project folder.
    return Path(args.site_dir).parent / "site_analytics"


def cmd_analytics_serve(args: argparse.Namespace, store: Store) -> int:
    from analytics_server import serve

    serve(port=args.port, data_dir=args.data_dir, site_dir=_analytics_site(args), open_browser=not args.no_browser)
    return 0


def cmd_decide(args: argparse.Namespace, store: Store) -> int:
    from datalayer.analytics import build as analytics_build
    from datalayer.analytics import decide

    try:
        items = decide.pending(args.data_dir)
    except decide.DecideError as exc:
        print(exc)
        return 1
    if not items:
        print("Nothing waiting: every analytics pair is settled.")
        return 0

    if args.number is None:
        print(f"{len(items)} pair(s) need a person. Nothing is merged until you decide.\n")
        for number, item in enumerate(items, 1):
            print(decide.summary_line(number, item))
        print("\nSee one:   python cli.py decide <number>")
        print("Answer:    python cli.py decide <number> same | different  (firms also: a-became-b | b-became-a)")
        return 0

    if not 1 <= args.number <= len(items):
        print(f"There is no pair {args.number}; the list has 1 to {len(items)}.")
        return 1
    item = items[args.number - 1]
    if args.answer is None:
        print("\n".join(decide.details(args.number, item)))
        return 0

    try:
        recorded = decide.record(item, args.answer, args.parts)
    except decide.DecideError as exc:
        print(exc)
        return 1
    print(f"Recorded in analytics_reference.json ({recorded.section}): {recorded.entry}")
    analytics_build.run(store, review=False, log=lambda line: None)
    _refresh_analytics_app(args, log=lambda line: None)
    left = decide.pending(args.data_dir)
    print(f"Rebuilt. {len(left)} pair(s) still waiting" + (" (the numbers have moved: run 'python cli.py decide')." if left else "."))
    return 0


def cmd_claims(args: argparse.Namespace, store: Store) -> int:
    from datalayer.claims import build as claims_build

    failed = 0
    for number in args.numbers:
        key = store.find_key(number)
        if key is None:
            print(f"{number} is not on disk; run 'python cli.py sync' first.")
            failed += 1
            continue
        try:
            claims_build.run(
                store, key, fetch_pdfs=claims_build.edis_pdf_fetcher(load_token), reread=args.reread
            )
        except Exception as exc:  # recorded on the analysis; keep going with the rest
            print(f"  ! {number}: {type(exc).__name__}: {exc}")
            failed += 1
    if args.render:
        _render(args, store)
    return 1 if failed else 0


def cmd_counsel(args: argparse.Namespace, store: Store) -> int:
    _counsel(store, verbose=args.verbose)
    if args.render:
        _render(args, store)
    return 0


def cmd_render(args: argparse.Namespace, store: Store) -> int:
    report = render_site(store, site_dir=args.site_dir, schema_path=args.schema)
    print(f"Open: {report.index_path}")
    print(
        "Opened from disk the page is read-only; start the app (ITC Tracker.bat, or "
        "'python cli.py serve') to use its buttons."
    )
    return 0


def cmd_fields(args: argparse.Namespace, store: Store) -> int:
    """Print what ui_schema.json can name, since that is the whole point of
    having the mapping in a file the user edits.
    """
    from collections import Counter

    cases = [c for c in store.investigations.values() if isinstance(c, dict)]
    if not cases:
        print("Nothing on disk yet. Run 'python cli.py sync' first.")
        return 1

    counts: Counter[str] = Counter()
    kinds: dict[str, str] = {}
    samples: dict[str, str] = {}
    for case in cases:
        for name, kind in ui_schema.sources(case).items():
            counts[name] += 1
            kinds[name] = kind
            if name in samples:
                continue
            spec = ui_schema.FieldSpec(label=name, source=name, type="text")
            for stage in case.get("stages") or [None]:
                value = ui_schema.resolve(spec, case, stage=stage)
                if value not in (None, "", []):
                    samples[name] = str(value)[:44]
                    break

    for kind in ("case", "stage field", "stage list"):
        group = sorted(name for name, k in kinds.items() if k == kind)
        print(f"\n{kind.upper()}S ({len(group)}) -- usable as \"source\" in {args.schema.name}")
        for name in group:
            share = f"{counts[name]}/{len(cases)}"
            print(f"  {name:<46} {share:>12}  {samples.get(name, '')}")

    print(
        '\nList sources also take "where" (e.g. {"role": "Complainant"}), "item" '
        'and "limit".'
    )
    return 0


def cmd_normalize(args: argparse.Namespace, store: Store) -> int:
    normalize.run(store)
    _render(args, store)
    return 0


def cmd_serve(args: argparse.Namespace, store: Store) -> int:
    from server import serve

    _render(args, store)
    serve(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        site_dir=args.site_dir,
        schema_path=args.schema,
        open_browser=not args.no_browser,
    )
    return 0


def _print_recent_syncs(data_dir: Path, limit: int = 5) -> None:
    recent = runlog.read(data_dir, last=limit)
    if not recent:
        return
    print(f"\nLast {len(recent)} sync(s) -- full history in {runlog.path_for(data_dir)}")
    print(
        f"{'RUN AT':<20} {'MODE':<9} {'OUTCOME':<8} {'337 ROWS':>9} {'CASES':>6} "
        f"{'NEW':>4} {'CHANGED':>8} {'GONE':>5}"
    )
    for row in recent:
        print(
            f"{row['run_at'][:19]:<20} {row['mode']:<9} {row['outcome']:<8} "
            f"{row['rows_337']:>9} {row['cases_in_file']:>6} {row['cases_added']:>4} "
            f"{row['cases_changed']:>8} {row['cases_left_feed']:>5}"
        )


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    rows = store.summary_rows()
    if not rows:
        print("Nothing on disk yet. Run 'python cli.py sync'.")
        return 0

    stored = ids.snapshots(args.ids_dir)
    if stored:
        newest = stored[-1]
        taken = newest.taken_at or newest.day
        print(f"IDS snapshots: {len(stored)}, newest taken {taken} ({newest.path.name})")
    else:
        print("IDS snapshots: none stored yet")

    with_docs = [row for row in rows if row["documents"]]
    print(
        f"{len(rows)} investigation(s) tracked in {store.data_dir}; "
        f"{len(with_docs)} have documents fetched."
    )
    if not with_docs:
        print("\nNo documents fetched yet. Try 'python cli.py docs <number>'.")
        _print_recent_syncs(args.data_dir)
        return 0

    width = max(len(row["investigation_number"]) for row in with_docs)
    print(f"\n{'NUMBER'.ljust(width)}  {'STATUS':<22} {'STAGES':>6} {'DOCS':>5} {'FILES':>6}  FETCHED")
    for row in with_docs:
        # Documents fetched before documents_state.json existed have no time.
        fetched = (row["documents_fetched_at"] or "unknown")[:19]
        print(
            f"{row['investigation_number'].ljust(width)}  {row['status'][:22]:<22} "
            f"{row['stages']:>6} {row['documents']:>5} {row['attachments']:>6}  {fetched}"
        )
    _print_recent_syncs(args.data_dir)
    return 0


def cmd_refresh(args: argparse.Namespace, store: Store) -> int:
    timer = dailylog.DailyTimer()
    outcome = "error"
    try:
        with timer.step("ingest"):
            try:
                report = ingest.run(store, ids_dir=args.ids_dir)
            except ids.IdsError as exc:
                # Carry on with the documents: they don't depend on today's feed.
                report = None
                print(f"IDS ERROR: {exc}\nCase data not updated; refreshing documents with the case data on disk.")

        if not args.no_documents:
            # The cases due today, as in the app (backfill.refresh_every).
            targets = backfill.daily_targets(store)
            if targets:
                print(f"\nRefreshing documents for {len(targets)} case(s) (new filings)...")
                with timer.step("documents"):
                    fetched = docs.run(
                        store,
                        load_token(),
                        targets,
                        download=not args.no_attachments,
                        only_types=_only_types(args),
                        by_hand=False,
                        new_only=True,
                    )
                timer.count("cases", len(fetched.fetched))
                timer.count("full_listings", sum(1 for r in fetched.fetched if r.full))
                timer.count("pdfs_downloaded", fetched.downloaded)
            if not args.no_attachments:
                with timer.step("schedules"):
                    _read_schedules(store)

        with timer.step("counsel"):
            counsel.run(store, log=print)
        with timer.step("next_actions"):
            nextactions_build.run(store, log=print)
        with timer.step("render"):
            _render(args, store)
        outcome = "ok" if report is not None else "warn"
    finally:
        row = timer.row(outcome)
        dailylog.append(args.data_dir, row)
        store.record_run("daily", seconds=row["seconds"], summary=dailylog.summary(row), outcome=outcome)
        store.save_state()
        print("\n" + dailylog.summary(row))
    if report is None:
        print("Done, but the case data was not updated (see IDS ERROR above).")
        return 1
    print(f"Done. {report.cases} investigation(s) tracked from IDS snapshot {report.snapshot_day}.")
    return 0


COMMANDS = {
    "sync": cmd_sync,
    "parse": cmd_parse,
    "docs": cmd_docs,
    "backfill": cmd_backfill,
    "counsel": cmd_counsel,
    "next-actions": cmd_next_actions,
    "analytics": cmd_analytics,
    "decide": cmd_decide,
    "analytics-serve": cmd_analytics_serve,
    "claims": cmd_claims,
    "render": cmd_render,
    "fields": cmd_fields,
    "serve": cmd_serve,
    "status": cmd_status,
    "normalize": cmd_normalize,
    "refresh": cmd_refresh,
}

# The old names, from when EDIS was the only source.
RENAMED = {"discover": "sync", "update": "docs"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for index, token in enumerate(argv):
        if token in COMMANDS:
            break
        if token in RENAMED:
            print(f"'{token}' is now '{RENAMED[token]}'; running that instead.\n")
            argv[index] = RENAMED[token]
            break

    args = build_parser().parse_args(argv)
    store = Store.load(args.data_dir)
    try:
        return COMMANDS[args.command](args, store)
    except MissingTokenError as exc:
        print(exc)
        return 1
    except ids.IdsError as exc:
        print(f"IDS ERROR: {exc}")
        return 1
    except ui_schema.SchemaError as exc:
        print(f"SCHEMA ERROR: {exc}")
        return 1
    except ProcessAborted as exc:
        print(f"AUTH ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; data collected so far has been saved.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
