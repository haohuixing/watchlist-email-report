"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from . import report, schedule, storage
from .config import (
    LOGS_DIR,
    OUT_DIR,
    ConfigError,
    ensure_dirs,
    load_settings,
)
from .quotes import QuoteError


def setup_logging() -> None:
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    logfile = LOGS_DIR / "mailer.log"
    try:
        if logfile.exists() and logfile.stat().st_size > 5_000_000:
            backup = LOGS_DIR / "mailer.log.1"
            if backup.exists():
                backup.unlink()
            logfile.replace(backup)
    except OSError:
        pass
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def print_snapshot(snapshot) -> None:
    print(f"Captured {snapshot.row_count()} rows across {len(snapshot.sections)} list(s).")
    for section in snapshot.sections:
        print(f"  {section.name}: {section.count()} rows")
        if section.note:
            print(f"    note: {section.note}")
    if snapshot.notes:
        for note in snapshot.notes:
            print(f"  note: {note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchlist_mailer",
        description="Email a fixed Google Finance watchlist at 11:00 AM and 4:00 PM ET.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Fetch quotes for watchlists.txt now.")
    sync.add_argument("--debug", action="store_true", help="Accepted for compatibility.")

    preview = sub.add_parser("preview", help="Render out/preview.html without sending.")
    preview.add_argument("--slot", choices=("open", "close"), default=None)
    preview.add_argument("--no-sync", action="store_true", help="Use the last snapshot.")

    send = sub.add_parser("send", help="Fetch and send one report now.")
    send.add_argument("--slot", choices=("open", "close"), default=None)
    send.add_argument(
        "--mark-sent",
        action="store_true",
        help="Record this slot so the supervisor does not resend it.",
    )

    sub.add_parser("serve", help="Run the long-lived supervisor.")

    worker = sub.add_parser("run-slot", help="Worker: fetch and send one slot.")
    worker.add_argument("--slot", choices=("open", "close"), required=True)
    worker.add_argument("--date", default=None, help="ET date in YYYY-MM-DD form.")
    worker.add_argument("--force", action="store_true")

    sub.add_parser("check", help="Show the current ET slot and next scheduled run.")
    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "sync":
        from . import quotes

        snapshot = quotes.capture()
        storage.save_snapshot(snapshot)
        print_snapshot(snapshot)
        return 0

    if args.command == "preview":
        if args.no_sync:
            snapshot = storage.load_snapshot()
            if snapshot is None:
                print("No snapshot exists yet. Run: python -m watchlist_mailer sync")
                return 2
        else:
            from . import quotes

            snapshot = quotes.capture()
            storage.save_snapshot(snapshot)
            print_snapshot(snapshot)

        slot = args.slot or schedule.slot_for_now()
        rendered = report.render_report(snapshot, slot)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "preview.html"
        path.write_text(rendered.html, encoding="utf-8")
        print(f"Preview written to {path}")
        print()
        print(rendered.text)
        return 0

    if args.command == "send":
        slot = args.slot or schedule.slot_for_now()
        return schedule.run_slot(slot, force=True, mark=args.mark_sent)

    if args.command == "serve":
        settings = load_settings(require_mail=True)
        return schedule.serve(settings)

    if args.command == "run-slot":
        day = date.fromisoformat(args.date) if args.date else None
        return schedule.run_slot(args.slot, day=day, force=args.force, mark=True)

    if args.command == "check":
        now = schedule.now_et()
        state = schedule.load_state()
        due = schedule.due_slot(now, state)
        slot, target = schedule.next_slot(now, state)
        print(f"Now: {now.isoformat(timespec='seconds')}")
        print(f"Due now: {due or 'none'}")
        print(f"Next: {schedule.SLOT_LABELS[slot]} at {schedule._fmt(target)}")
        return 0

    return 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging()
    try:
        return dispatch(args)
    except ConfigError as error:
        logging.getLogger(__name__).error("%s", error)
        return 2
    except QuoteError as error:
        logging.getLogger(__name__).error("%s", error)
        return 2
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped by user.")
        return 130
    except Exception:
        logging.getLogger(__name__).exception("Unhandled error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
