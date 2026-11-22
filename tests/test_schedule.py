from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from watchlist_mailer import schedule
from watchlist_mailer.config import Settings
from watchlist_mailer.snapshot import Section, Snapshot


class SlotTest(unittest.TestCase):
    def test_window_boundaries(self):
        monday = datetime(2026, 11, 23, 11, 5, tzinfo=schedule.ET)
        self.assertTrue(schedule.in_window(monday, "open"))
        self.assertFalse(schedule.in_window(monday.replace(hour=10, minute=59), "open"))
        self.assertFalse(schedule.in_window(monday.replace(hour=11, minute=30), "open"))
        self.assertFalse(schedule.in_window(monday.replace(day=22), "open"))

    def test_due_slot_and_handled_state(self):
        monday = datetime(2026, 11, 23, 11, 5, tzinfo=schedule.ET)
        self.assertEqual(schedule.due_slot(monday, {}), "open")
        state = {"sent": {"2026-11-23:open": {"detail": "sent"}}, "attempted": {}}
        self.assertIsNone(schedule.due_slot(monday, state))

    def test_next_slot_skips_weekend_and_finished_day(self):
        friday_evening = datetime(2026, 11, 27, 17, 0, tzinfo=schedule.ET)
        slot, target = schedule.next_slot(friday_evening, {})
        self.assertEqual(slot, "open")
        self.assertEqual(target, datetime(2026, 11, 30, 11, 0, tzinfo=schedule.ET))

    def test_next_slot_honors_handled_open(self):
        monday = datetime(2026, 11, 23, 10, 0, tzinfo=schedule.ET)
        state = {"sent": {"2026-11-23:open": {}}, "attempted": {}}
        slot, target = schedule.next_slot(monday, state)
        self.assertEqual(slot, "close")
        self.assertEqual(target, datetime(2026, 11, 23, 16, 0, tzinfo=schedule.ET))

    def test_slot_for_now(self):
        self.assertEqual(
            schedule.slot_for_now(datetime(2026, 11, 23, 10, 0, tzinfo=schedule.ET)),
            "open",
        )
        self.assertEqual(
            schedule.slot_for_now(datetime(2026, 11, 23, 15, 0, tzinfo=schedule.ET)),
            "close",
        )

    def test_duration_format(self):
        self.assertEqual(schedule._duration(45), "45s")
        self.assertEqual(schedule._duration(125), "2m")
        self.assertEqual(schedule._duration(3725), "1h 2m")


class BatchSendTest(unittest.TestCase):
    def test_run_slot_sends_each_batch(self):
        snapshot = Snapshot(
            captured_at="2026-11-24T11:00:12-05:00",
            as_of="As of Nov 24, 11:00 AM ET",
            sections=[
                Section(
                    "Core",
                    ["Symbol", "Price", "Change", "% Change"],
                    [
                        {"Symbol": "AAPL", "Price": "$342.56", "Change": "-6.65", "% Change": "-1.90%"},
                        {"Symbol": "MSFT", "Price": "$420.00", "Change": "+2.10", "% Change": "+0.50%"},
                        {"Symbol": "NVDA", "Price": "$125.53", "Change": "+1.20", "% Change": "+0.97%"},
                    ],
                ),
                Section(
                    "Following",
                    ["Symbol", "Price", "Change", "% Change"],
                    [
                        {"Symbol": "TSLA", "Price": "$401.00", "Change": "0.00", "% Change": "0.00%"},
                    ],
                ),
            ],
        )
        settings = Settings("sender@example.com", "secret", ("recipient@example.com",), "sender@example.com")
        with (
            patch.object(schedule, "load_settings", return_value=settings),
            patch.object(schedule, "load_state", return_value={"sent": {}, "attempted": {}}),
            patch.object(schedule, "report_batch_size", return_value=2),
            patch.object(schedule, "save_snapshot"),
            patch.object(schedule, "mark_sent") as mark_sent,
            patch("watchlist_mailer.quotes.capture", return_value=snapshot),
            patch("watchlist_mailer.mailer.send") as send,
            patch("watchlist_mailer.schedule.time.sleep"),
        ):
            code = schedule.run_slot("open", day=date(2026, 11, 24), force=True)

        self.assertEqual(code, 0)
        self.assertEqual(send.call_count, 2)
        subjects = [call.args[1] for call in send.call_args_list]
        self.assertIn("(1/2)", subjects[0])
        self.assertIn("(2/2)", subjects[1])
        bodies = "\n".join(call.args[2] for call in send.call_args_list)
        for symbol in ("AAPL", "MSFT", "NVDA", "TSLA"):
            self.assertIn(symbol, bodies)
        mark_sent.assert_called_once()
        self.assertIn("sent 2 batches", mark_sent.call_args.args[2])


class StateTest(unittest.TestCase):
    def test_state_round_trip_and_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            today = schedule.now_et().date()
            old = today - timedelta(days=100)
            state = {
                "sent": {f"{today.isoformat()}:open": {"detail": "sent"}},
                "attempted": {f"{old.isoformat()}:open": {"detail": "old"}},
            }
            schedule.save_state(state, path)
            loaded = schedule.load_state(path)
            self.assertIn(f"{today.isoformat()}:open", loaded["sent"])
            self.assertEqual(loaded["attempted"], {})

    def test_invalid_state_file_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not json", encoding="utf-8")
            loaded = schedule.load_state(path)
            self.assertEqual(loaded, {"sent": {}, "attempted": {}})


if __name__ == "__main__":
    unittest.main()
