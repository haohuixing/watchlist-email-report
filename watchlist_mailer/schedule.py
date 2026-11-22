"""ET slot scheduling, supervisor/worker split, state and OS locks."""

from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import (
    ROOT,
    SERVE_LOCK,
    SLOT_LABELS,
    SLOT_TIMES,
    SLOT_WINDOW_MINUTES,
    STATE_PATH,
    Settings,
    ensure_dirs,
    load_settings,
    report_batch_size,
)
from .storage import load_snapshot, save_snapshot

ET = ZoneInfo("America/New_York")
log = logging.getLogger(__name__)


def now_et() -> datetime:
    return datetime.now(ET)


def slot_key(day: date, slot: str) -> str:
    return f"{day.isoformat()}:{slot}"


def in_window(dt: datetime, slot: str) -> bool:
    if dt.weekday() > 4:
        return False
    start = datetime.combine(dt.date(), SLOT_TIMES[slot], tzinfo=ET)
    return start <= dt < start + timedelta(minutes=SLOT_WINDOW_MINUTES)


def handled(state: dict, day: date, slot: str) -> bool:
    key = slot_key(day, slot)
    return key in state.get("sent", {}) or key in state.get("attempted", {})


def due_slot(dt: datetime, state: dict) -> str | None:
    for slot in ("open", "close"):
        if in_window(dt, slot) and not handled(state, dt.date(), slot):
            return slot
    return None


def next_slot(dt: datetime, state: dict) -> tuple[str, datetime]:
    for offset in range(10):
        day = (dt + timedelta(days=offset)).date()
        if day.weekday() > 4:
            continue
        for slot in ("open", "close"):
            candidate = datetime.combine(day, SLOT_TIMES[slot], tzinfo=ET)
            if candidate <= dt or handled(state, day, slot):
                continue
            return slot, candidate
    # Defensive fallback: next Monday at the open.
    day = dt.date() + timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return "open", datetime.combine(day, SLOT_TIMES["open"], tzinfo=ET)


def _fmt(dt: datetime) -> str:
    text = dt.strftime("%a %b %d, %I:%M %p ET")
    return re.sub(r" 0(\d)", r" \1", text)


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"sent": {}, "attempted": {}}
    if not isinstance(data, dict):
        return {"sent": {}, "attempted": {}}
    data.setdefault("sent", {})
    data.setdefault("attempted", {})
    return data


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    cutoff = (now_et().date() - timedelta(days=60)).isoformat()
    for bucket in ("sent", "attempted"):
        items = state.get(bucket, {})
        state[bucket] = {
            key: value for key, value in items.items() if str(key)[:10] >= cutoff
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def mark_sent(day: date, slot: str, detail: str = "") -> None:
    state = load_state()
    state.setdefault("sent", {})[slot_key(day, slot)] = {
        "at": now_et().isoformat(timespec="seconds"),
        "detail": detail,
    }
    save_state(state)


def mark_attempted(day: date, slot: str, detail: str = "") -> None:
    state = load_state()
    state.setdefault("attempted", {})[slot_key(day, slot)] = {
        "at": now_et().isoformat(timespec="seconds"),
        "detail": detail,
    }
    save_state(state)


def acquire_lock(path: Path):
    """Return an open file holding an OS lock, or None if another process owns it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")  # noqa: SIM115 - kept open to hold the lock
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write(" ")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _run_child(slot: str, day: date) -> int:
    command = [
        sys.executable,
        "-m",
        "watchlist_mailer",
        "run-slot",
        "--slot",
        slot,
        "--date",
        day.isoformat(),
    ]
    log.info("Starting worker: %s", " ".join(command))
    try:
        result = subprocess.run(command, check=False, cwd=str(ROOT), timeout=1800)
        return result.returncode
    except subprocess.TimeoutExpired:
        log.error("Worker timed out after 30 minutes.")
        return 124
    except OSError as error:
        log.error("Could not start worker: %s", error)
        return 1


def serve(settings: Settings) -> int:
    ensure_dirs()
    lock = acquire_lock(SERVE_LOCK)
    if lock is None:
        log.error("Another watchlist-mailer supervisor is already running.")
        return 1

    state = load_state()
    log.info("Supervisor started. Recipients: %s", ", ".join(settings.mail_to))
    last_target: datetime | None = None
    try:
        while True:
            now = now_et()
            slot = due_slot(now, state)
            if slot is not None:
                day = now.date()
                log.info("Due now: %s for %s", SLOT_LABELS[slot], day)
                code = _run_child(slot, day)
                state = load_state()
                if code == 0:
                    log.info("Completed %s for %s", slot, day)
                else:
                    if not handled(state, day, slot):
                        mark_attempted(day, slot, f"worker exit {code}")
                        state = load_state()
                    log.warning("Worker exited %s for %s; not retrying this window", code, slot)
                last_target = None
                continue

            slot, target = next_slot(now, state)
            if last_target != target:
                log.info(
                    "Next: %s at %s (in %s)",
                    SLOT_LABELS[slot],
                    _fmt(target),
                    _duration((target - now).total_seconds()),
                )
                last_target = target
            time.sleep(max(1, min(30, int((target - now).total_seconds()))))
    except KeyboardInterrupt:
        log.info("Supervisor stopped by user.")
        return 0
    finally:
        lock.close()


def slot_for_now(now: datetime | None = None) -> str:
    now = now or now_et()
    return "open" if now.hour < 13 else "close"


def _send_alert(settings: Settings, error: Exception) -> None:
    from . import mailer

    subject = "[Action needed] Google Finance quote fetch failed"
    text = (
        "The scheduled watchlist report could not be fetched from Google Finance.\n\n"
        f"Reason: {error}\n\n"
        "Check watchlists.txt and your internet connection, then run:\n"
        "  .venv\\Scripts\\python.exe -m watchlist_mailer sync\n"
    )
    html = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;'
        'max-width:560px;margin:0 auto;padding:20px;color:#17202a;">'
        '<h2 style="margin:0 0 8px;">Google Finance quote fetch failed</h2>'
        f'<p style="line-height:1.5;">{html_lib.escape(str(error))}</p>'
        "<p style=\"line-height:1.5;\">Check <code>watchlists.txt</code> and your "
        "internet connection, then run "
        "<code>.venv\\Scripts\\python.exe -m watchlist_mailer sync</code>.</p>"
        "</div>"
    )
    mailer.send(settings, subject, text, html)


def run_slot(
    slot: str,
    day: date | None = None,
    force: bool = False,
    mark: bool = True,
) -> int:
    from . import mailer, quotes, report

    settings = load_settings(require_mail=True)
    day = day or now_et().date()
    state = load_state()
    if not force and handled(state, day, slot):
        log.info("Already handled: %s", slot_key(day, slot))
        return 0

    snapshot = None
    stale = False
    reason: str | None = None
    try:
        snapshot = quotes.capture()
        save_snapshot(snapshot)
        log.info(
            "Captured %d rows across %d lists",
            snapshot.row_count(),
            len(snapshot.sections),
        )
    except quotes.QuoteError as error:
        stale = True
        reason = str(error)
        log.error("Capture failed: %s", error)
        snapshot = load_snapshot()
        if snapshot is None:
            try:
                _send_alert(settings, error)
            except Exception:
                log.exception("Alert email failed")
                if mark:
                    mark_attempted(day, slot, f"capture failed: {error}; alert failed")
                return 1
            if mark:
                mark_sent(day, slot, "capture failed; alert sent")
            log.info("Capture failed and no snapshot exists; alert sent.")
            return 0

    try:
        batches = report.batch_snapshots(snapshot, report_batch_size())
        total = len(batches)
        for index, batch in enumerate(batches, 1):
            rendered = report.render_report(
                batch,
                slot,
                stale=stale,
                stale_reason=reason,
                batch_index=index,
                batch_count=total,
            )
            mailer.send(settings, rendered.subject, rendered.text, rendered.html)
            log.info("Emailed %d/%d: %s", index, total, rendered.subject)
            if index < total:
                time.sleep(1)
    except Exception as error:
        log.exception("Send failed")
        if mark:
            mark_attempted(day, slot, f"send failed: {error}")
        return 1

    if mark:
        detail = f"sent {total} batch{'es' if total != 1 else ''}"
        mark_sent(day, slot, ("stale; " if stale else "") + detail)
    return 0
