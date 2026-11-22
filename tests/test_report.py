from __future__ import annotations

import unittest

from watchlist_mailer.report import batch_snapshots, render_report
from watchlist_mailer.snapshot import Section, Snapshot, change_direction


def sample_snapshot() -> Snapshot:
    return Snapshot(
        captured_at="2026-11-24T11:00:12-05:00",
        as_of="As of Nov 24, 11:00 AM ET",
        sections=[
            Section(
                "Core Holdings",
                [
                    "Symbol",
                    "Name",
                    "Exchange",
                    "Sector",
                    "Price",
                    "Change",
                    "% Change",
                    "Prev Close",
                    "Open",
                    "Day Range",
                    "Range %",
                    "Range Position",
                    "Gap",
                    "From Open",
                    "Volume",
                    "Avg Vol",
                    "Mkt Cap",
                    "Dollar Vol",
                    "52W Range",
                    "52W Position",
                    "P/E",
                    "Dividend",
                    "EPS",
                    "Beta",
                ],
                [
                    {
                        "Symbol": "AAPL",
                        "Name": "Apple Inc",
                        "Exchange": "NASDAQ",
                        "Sector": "Information Technology",
                        "Price": "$342.56",
                        "Change": "-6.65",
                        "% Change": "-1.90%",
                        "Prev Close": "$349.21",
                        "Open": "$352.87",
                        "Day Range": "$340.56 \u2013 $354.34",
                        "Range %": "4.02%",
                        "Range Position": "15%",
                        "Gap": "+$3.66 (+1.05%)",
                        "From Open": "-$10.31 (-2.92%)",
                        "Volume": "80.69M",
                        "Avg Vol": "45.32M",
                        "Mkt Cap": "4.98T",
                        "Dollar Vol": "$27.64B",
                        "52W Range": "$243.42 \u2013 $360.83",
                        "52W Position": "84%",
                        "P/E": "38.15",
                        "Dividend": "0.32%",
                        "EPS": "$8.98",
                        "Beta": "1.07",
                    },
                    {
                        "Symbol": "MSFT",
                        "Name": "Microsoft Corporation",
                        "Exchange": "NASDAQ",
                        "Sector": "Information Technology",
                        "Price": "$420.00",
                        "Change": "+2.10",
                        "% Change": "+0.50%",
                        "Prev Close": "$417.90",
                        "Open": "$418.50",
                        "Day Range": "$416.20 \u2013 $421.80",
                        "Range %": "1.33%",
                        "Range Position": "68%",
                        "Gap": "+$0.60 (+0.14%)",
                        "From Open": "+$1.50 (+0.36%)",
                        "Volume": "22.40M",
                        "Avg Vol": "19.10M",
                        "Mkt Cap": "3.12T",
                        "Dollar Vol": "$9.41B",
                        "52W Range": "$344.77 \u2013 $438.90",
                        "52W Position": "80%",
                        "P/E": "34.20",
                        "Dividend": "0.72%",
                        "EPS": "$12.28",
                        "Beta": "0.91",
                    },
                ],
            ),
            Section(
                "Following",
                ["Symbol", "Name", "Price", "Change", "% Change"],
                [
                    {
                        "Symbol": "AAPL",
                        "Name": "Apple Inc",
                        "Price": "$342.56",
                        "Change": "-6.65",
                        "% Change": "-1.90%",
                    },
                    {
                        "Symbol": "TSLA",
                        "Name": "Tesla Inc",
                        "Price": "$401.00",
                        "Change": "0.00",
                        "% Change": "0.00%",
                    },
                ],
            ),
        ],
        notes=["Quotes may be delayed."],
    )


class DirectionTest(unittest.TestCase):
    def test_signed_zero_is_flat(self):
        self.assertEqual(change_direction({"Change": "+0.00"}), 0)
        self.assertEqual(change_direction({"Change": "-0.00"}), 0)
        self.assertEqual(change_direction({"Change": "0.00%"}), 0)


class ReportTest(unittest.TestCase):
    def test_subject_and_summary(self):
        rendered = render_report(sample_snapshot(), "open")
        self.assertEqual(rendered.subject, "Watchlist - 11:00 AM ET - Tue, Nov 24")
        self.assertIn("3 unique symbols", rendered.text)
        self.assertIn("1 up", rendered.text)
        self.assertIn("CORE HOLDINGS (2)", rendered.text)
        self.assertIn("FOLLOWING (2)", rendered.text)
        self.assertIn("AAPL", rendered.text)
        self.assertIn("$342.56", rendered.text)

    def test_html_contains_core_columns_and_direction_colors(self):
        rendered = render_report(sample_snapshot(), "open")
        self.assertIn("Apple Inc", rendered.html)
        self.assertIn("$342.56", rendered.html)
        self.assertIn("-1.90%", rendered.html)
        self.assertIn("#c5221f", rendered.html)
        self.assertIn("#0b8a4b", rendered.html)
        self.assertIn("Prev Close", rendered.html)
        self.assertIn("Quotes may be delayed.", rendered.html)
        self.assertNotIn("[STALE]", rendered.subject)

    def test_html_adds_breadth_movers_and_stock_detail(self):
        rendered = render_report(sample_snapshot(), "open")
        self.assertIn("Market breadth", rendered.html)
        self.assertIn("Top movers", rendered.html)
        self.assertIn("Gainers", rendered.html)
        self.assertIn("Decliners", rendered.html)
        self.assertIn("Information Technology", rendered.html)
        self.assertIn("P/E", rendered.html)
        self.assertIn("38.15", rendered.html)
        self.assertIn("Avg Vol", rendered.html)
        self.assertIn("45.32M", rendered.html)
        self.assertIn("52W Range", rendered.html)
        self.assertIn("84% of range", rendered.html)
        self.assertIn("$27.64B", rendered.html)

    def test_text_adds_rich_detail(self):
        rendered = render_report(sample_snapshot(), "open")
        self.assertIn("BREADTH", rendered.text)
        self.assertIn("TOP MOVERS", rendered.text)
        self.assertIn("Market breadth", rendered.html)
        self.assertIn("P/E 38.15", rendered.text)
        self.assertIn("Avg vol 45.32M", rendered.text)
        self.assertIn("52W $243.42 \u2013 $360.83", rendered.text)
        self.assertIn("Information Technology", rendered.text)

    def test_batch_split_preserves_all_rows(self):
        snapshot = sample_snapshot()
        batches = batch_snapshots(snapshot, 3)
        self.assertEqual(len(batches), 2)
        symbols = [
            row["Symbol"]
            for batch in batches
            for section in batch.sections
            for row in section.rows
        ]
        self.assertEqual(symbols, ["AAPL", "MSFT", "AAPL", "TSLA"])
        self.assertEqual(batches[0].as_of, snapshot.as_of)
        self.assertEqual(sum(batch.row_count() for batch in batches), 4)

    def test_batch_label_is_rendered(self):
        rendered = render_report(
            sample_snapshot(), "open", batch_index=2, batch_count=3
        )
        self.assertTrue(rendered.subject.endswith("(2/3)"))
        self.assertIn("Part 2 of 3", rendered.html)
        self.assertIn("PART 2 OF 3 - 4 symbols", rendered.text)

    def test_stale_report_is_marked(self):
        rendered = render_report(
            sample_snapshot(),
            "close",
            stale=True,
            stale_reason="Google Finance unavailable.",
        )
        self.assertTrue(rendered.subject.startswith("[STALE] Watchlist - Market close"))
        self.assertIn("Stale data.", rendered.html)
        self.assertIn("Google Finance unavailable.", rendered.html)
        self.assertIn("STALE DATA", rendered.text)


if __name__ == "__main__":
    unittest.main()
