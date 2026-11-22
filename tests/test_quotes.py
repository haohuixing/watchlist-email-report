from __future__ import annotations

import json
import unittest

from watchlist_mailer import quotes


def ds10_callback(node: list) -> str:
    payload = json.dumps([[[node]]])
    return (
        "<script>AF_initDataCallback({key: 'ds:10', hash: '1', data:"
        + payload
        + ", sideChannel: {}});</script>"
    )


def ds8_callback(node: list) -> str:
    payload = json.dumps([[[node]]])
    return (
        "<script>AF_initDataCallback({key: 'ds:8', hash: '1', data:"
        + payload
        + ", sideChannel: {}});</script>"
    )


def quote_node(price: float = 342.56) -> list:
    return [
        [None, ["AAPL", "NASDAQ"]],
        None,
        352.865,
        "/m/07zmbvf",
        340.56,
        354.34,
        price,
        2,
        -6.649994,
        2,
        -1.9042965,
        2,
        "USD",
        "AAPL:NASDAQ",
        "Apple Inc",
        349.21,
        4.978143545321045e12,
        80692545,
        "Information Technology",
        [
            "/m/07zmbvf",
            ["AAPL", "NASDAQ"],
            "Apple Inc",
            0,
            "USD",
            [342.56, -6.649994, -1.9042965, 2, 2, 2],
            None,
            349.21,
            "#666666",
            "US",
            "/m/0k8z",
            [1795224600],
            "America/New_York",
            -18000,
            None,
            None,
            None,
            None,
            None,
            [[1, [2026, 11, 20, 9, 30, None, None, [-18000]], [2026, 11, 20, 16, None, None, None, [-18000]]]],
        ],
    ]


class ParseWatchlistsTest(unittest.TestCase):
    def test_sections_comments_commas_and_dollar_signs(self):
        text = """
        # comment
        [Core]
        AAPL, MSFT
        $NVDA
        [Following]
        TSLA
        """
        watchlists = quotes.parse_watchlists(text)
        self.assertEqual([item.name for item in watchlists], ["Core", "Following"])
        self.assertEqual(watchlists[0].symbols, ("AAPL", "MSFT", "NVDA"))
        self.assertEqual(watchlists[1].symbols, ("TSLA",))

    def test_default_section_and_duplicate_headings_merge(self):
        watchlists = quotes.parse_watchlists("AAPL\n[Core]\nMSFT\n[Core]\nNVDA\n")
        self.assertEqual(len(watchlists), 2)
        self.assertEqual(watchlists[0].symbols, ("AAPL",))
        self.assertEqual(watchlists[1].symbols, ("MSFT", "NVDA"))

    def test_explicit_exchange_is_preserved(self):
        watchlists = quotes.parse_watchlists("[Core]\nbrk.b:nyse\n")
        self.assertEqual(watchlists[0].symbols, ("BRK.B:NYSE",))

    def test_index_alias_is_mapped(self):
        self.assertEqual(quotes.candidate_ids("^GSPC"), [".INX:INDEXSP"])

    def test_invalid_ticker_is_rejected(self):
        with self.assertRaises(quotes.QuoteError):
            quotes.parse_watchlists("[Core]\nApple Inc\n")


class FormattingTest(unittest.TestCase):
    def test_money_and_percent(self):
        self.assertEqual(quotes.money(342.56, "USD"), "$342.56")
        self.assertEqual(quotes.money(None, "USD"), "\u2014")
        self.assertEqual(quotes.signed(-6.649994), "-6.65")
        self.assertEqual(quotes.signed(2.3099976), "+2.31")
        self.assertEqual(quotes.percent(-1.9042965), "-1.90%")
        self.assertEqual(quotes.percent(0), "0.00%")

    def test_compact_numbers(self):
        self.assertEqual(quotes.compact(80692545), "80.69M")
        self.assertEqual(quotes.compact(4.978143545321045e12), "4.98T")
        self.assertEqual(quotes.compact(1200), "1.2K")
        self.assertEqual(quotes.compact(None), "\u2014")

    def test_derived_range_and_money_values(self):
        self.assertEqual(quotes.signed_money(3.66, "USD"), "+$3.66")
        self.assertEqual(quotes.signed_money(-10.30, "USD"), "-$10.30")
        self.assertEqual(
            quotes.day_range(340.56, 354.34, "USD"),
            "$340.56 \u2013 $354.34",
        )
        self.assertEqual(quotes.range_percent(340.56, 354.34, 342.56), "4.02%")
        self.assertEqual(quotes.range_position(340.56, 354.34, 342.56), "15%")
        self.assertEqual(
            quotes.week52_range("$243.42", "$360.83"),
            "$243.42 \u2013 $360.83",
        )
        self.assertEqual(
            quotes.week52_position(342.56, "$243.42", "$360.83"), "84%"
        )
        self.assertEqual(quotes.compact_money(27642038215, "USD"), "$27.64B")


class ParseQuoteTest(unittest.TestCase):
    def test_ds10_extracts_display_values(self):
        html = ds10_callback(quote_node())
        quote = quotes.parse_quote_html(html, "AAPL:NASDAQ")
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.symbol, "AAPL")
        self.assertEqual(quote.name, "Apple Inc")
        self.assertAlmostEqual(quote.price, 342.56)
        self.assertAlmostEqual(quote.change, -6.649994)
        self.assertAlmostEqual(quote.pct, -1.9042965)
        self.assertAlmostEqual(quote.previous_close, 349.21)
        self.assertAlmostEqual(quote.open, 352.865)
        self.assertAlmostEqual(quote.high, 354.34)
        self.assertAlmostEqual(quote.low, 340.56)
        self.assertEqual(quote.volume, 80692545)
        self.assertEqual(quote.as_of_label(), "As of Nov 20, 4:00 PM ET")
        self.assertEqual(quote.row()["Price"], "$342.56")
        self.assertEqual(quote.row()["% Change"], "-1.90%")

    def test_key_stats_grid_enriches_quote(self):
        stats = "".join(
            f'<div class="SwQK7">{label}</div><div class="dO6ijd">{value}</div>'
            for label, value in (
                ("Mkt. cap", "4.98T"),
                ("Avg. vol.", "45.32M"),
                ("Dividend", "0.32%"),
                ("P/E ratio", "38.15"),
                ("52-wk high", "$360.83"),
                ("52-wk low", "$243.42"),
                ("EPS", "$8.98"),
                ("Beta", "1.07"),
            )
        )
        quote = quotes.parse_quote_html(
            ds10_callback(quote_node()) + stats, "AAPL:NASDAQ"
        )
        self.assertIsNotNone(quote)
        assert quote is not None
        row = quote.row()
        self.assertEqual(row["Mkt Cap"], "4.98T")
        self.assertEqual(row["Avg Vol"], "45.32M")
        self.assertEqual(row["P/E"], "38.15")
        self.assertEqual(row["Dividend"], "0.32%")
        self.assertEqual(row["EPS"], "$8.98")
        self.assertEqual(row["Beta"], "1.07")
        self.assertEqual(row["52W Range"], "$243.42 \u2013 $360.83")
        self.assertEqual(row["52W Position"], "84%")
        self.assertEqual(row["Range Position"], "15%")
        self.assertEqual(row["Range %"], "4.02%")
        self.assertEqual(row["Dollar Vol"], "$27.64B")

    def test_ds8_index_payload(self):
        node = [
            [None, [".INX", "INDEXSP"]],
            None,
            7150.39,
            "/m/016yss",
            6882.66,
            7198.67,
            6910.39,
            2,
            -265.43994,
            2,
            -3.6990833,
            2,
            None,
            ".INX:INDEXSP",
            "S&P 500",
            7175.83,
            0,
            4727416323,
            None,
            [
                "/m/016yss",
                [".INX", "INDEXSP"],
                "S&P 500",
                1,
                None,
                [6910.39, -265.43994, -3.6990833, 2, 2, 2],
                None,
                7175.83,
                None,
                None,
                None,
                [1795224600],
                "America/New_York",
                -18000,
                "/m/016yss",
                None,
                None,
                [1795224600],
                None,
                [[1, [2026, 11, 20, 9, 30, None, None, [-18000]], [2026, 11, 20, 16, None, None, None, [-18000]]]],
                None,
                ".INX:INDEXSP",
            ],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "",
        ]
        quote = quotes.parse_quote_html(ds8_callback(node), ".INX:INDEXSP")
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.symbol, ".INX")
        self.assertEqual(quote.name, "S&P 500")
        self.assertEqual(quote.row()["Symbol"], "^GSPC")
        self.assertEqual(quote.row()["Price"], "6,910.39")
        self.assertEqual(quote.row()["% Change"], "-3.70%")
        self.assertEqual(quote.row()["Open"], "7,150.39")
        self.assertEqual(quote.row()["Mkt Cap"], "\u2014")
        self.assertEqual(quote.as_of_label(), "As of Nov 20, 4:00 PM ET")

    def test_mismatched_exchange_is_rejected_unless_allowed(self):
        html = ds10_callback(quote_node())
        self.assertIsNone(
            quotes.parse_quote_html(html, "AAPL:NYSE", allow_other_exchange=False)
        )
        self.assertIsNotNone(
            quotes.parse_quote_html(html, "AAPL:NYSE", allow_other_exchange=True)
        )

    def test_ds2_fallback(self):
        node = [
            "/m/0",
            ["MSFT", "NASDAQ"],
            "Microsoft Corporation",
            0,
            "USD",
            [420.0, 1.5, 0.36, 2, 2, 2],
            None,
            418.5,
            "#666",
            "US",
            "/m/0",
            [1795224600],
            "America/New_York",
            -18000,
        ]
        payload = json.dumps([[[node]]])
        html = (
            "<script>AF_initDataCallback({key: 'ds:15', hash: '1', data:"
            + payload
            + ", sideChannel: {}});</script>"
        )
        quote = quotes.parse_quote_html(html, "MSFT:NASDAQ")
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.name, "Microsoft Corporation")
        self.assertEqual(quote.row()["Price"], "$420.00")


if __name__ == "__main__":
    unittest.main()
