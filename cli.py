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

Examples:
    python cli.py sync                      # download today's IDS file, rebuild cases
    python cli.py sync --render             # ...and rebuild the site too
    python cli.py parse                     # re-parse the stored snapshot, offline
    python cli.py docs 337-1478 337-3936    # fetch those cases' documents
    python cli.py docs 337-1478 --no-attachments
    python cli.py render                    # rebuild the site, offline
    python cli.py serve                     # browse the site with working buttons
    python cli.py fields                    # what ui_schema.json can name
    python cli.py status                    # what's on disk, no network
    python cli.py refresh                   # sync + documents we already have + render
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import schema as ui_schema
from datalayer import docs, ids, ingest, normalize
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
        help=f"snapshots to keep on disk (default {ids.DEFAULT_KEEP}, 0 keeps all)",
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
    p_docs.add_argument(
        "--no-attachments",
        action="store_true",
        help="record document metadata only; skip downloading PDFs",
    )
    p_docs.add_argument(
        "--known-only",
        action="store_true",
        help="refuse numbers that aren't on disk instead of asking EDIS about them",
    )
    _add_render_flag(p_docs)

    sub.add_parser("render", help="UI layer only: rebuild site/ from data/ (offline)")
    sub.add_parser("fields", help="list the field names ui_schema.json can use (offline)")
    sub.add_parser("status", help="list what's on disk (offline)")
    sub.add_parser(
        "normalize", help="rewrite stored dates to ISO 8601 in place (offline, no API calls)"
    )

    p_serve = sub.add_parser(
        "serve", help="serve the site locally so its Update / Fetch docs buttons work"
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
    p_refresh.add_argument(
        "--no-attachments", action="store_true", help="skip downloading PDFs"
    )
    p_refresh.add_argument(
        "--no-documents", action="store_true", help="IDS only; make no EDIS calls"
    )

    return parser


def _render(args: argparse.Namespace, store: Store) -> None:
    render_site(store, site_dir=args.site_dir, schema_path=args.schema)


def cmd_sync(args: argparse.Namespace, store: Store) -> int:
    report = ingest.run(
        store,
        ids_dir=args.ids_dir,
        force=args.force,
        keep=args.keep,
        allow_removals=args.allow_removals,
        log=print,
    )
    print(
        f"\nSync done. {report.cases} investigation(s) from IDS snapshot "
        f"{report.snapshot_day}, {len(report.added)} new, "
        f"{len(report.withdrawn)} kept after leaving the feed."
    )
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
        known_only=args.known_only,
    )
    print(
        f"\nDocuments done. {len(report.fetched)} case(s) fetched, "
        f"{report.downloaded} attachment(s) downloaded, {len(report.failed)} skipped."
    )
    if args.render:
        _render(args, store)
    return 0 if report.fetched or not report.failed else 1


def cmd_render(args: argparse.Namespace, store: Store) -> int:
    report = render_site(store, site_dir=args.site_dir, schema_path=args.schema)
    print(f"Open: {report.index_path}")
    print(
        "Opened from disk the page is read-only; 'python cli.py serve' enables "
        "its Update / Fetch docs buttons."
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
        load_token(),
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        site_dir=args.site_dir,
        schema_path=args.schema,
        open_browser=not args.no_browser,
    )
    return 0


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    rows = store.summary_rows()
    if not rows:
        print("Nothing on disk yet. Run 'python cli.py sync'.")
        return 0

    stored = ids.snapshots(args.ids_dir)
    if stored:
        print(f"IDS snapshots: {len(stored)}, newest {stored[-1].day} ({stored[-1].path.name})")
    else:
        print("IDS snapshots: none stored yet")

    with_docs = [row for row in rows if row["documents"]]
    print(
        f"{len(rows)} investigation(s) tracked in {store.data_dir}; "
        f"{len(with_docs)} have documents fetched."
    )
    if not with_docs:
        print("\nNo documents fetched yet. Try 'python cli.py docs <number>'.")
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
    return 0


def cmd_refresh(args: argparse.Namespace, store: Store) -> int:
    report = ingest.run(store, ids_dir=args.ids_dir)

    if not args.no_documents:
        targets = store.numbers_with_documents()
        if targets:
            print(f"\nRefreshing documents for {len(targets)} case(s) already fetched...")
            docs.run(
                store, load_token(), targets, download=not args.no_attachments
            )

    _render(args, store)
    print(
        f"\nDone. {report.cases} investigation(s) tracked from IDS snapshot "
        f"{report.snapshot_day}."
    )
    return 0


COMMANDS = {
    "sync": cmd_sync,
    "parse": cmd_parse,
    "docs": cmd_docs,
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
