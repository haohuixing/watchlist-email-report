"""Read a fixed watchlist file and fetch Google Finance quote pages over HTTP.

No Google account, cookies, browser or Playwright are involved. The watchlist
composition lives in ``watchlists.txt``; prices and other fields come from the
public Google Finance quote page for each symbol.
"""

from __future__ import annotations

import gzip
import html as html_lib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DATA_DIR, WATCHLISTS_PATH, load_env
from .snapshot import Section, Snapshot, clean_text

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CACHE_PATH = DATA_DIR / "symbol_cache.json"
QUOTE_URL = "https://www.google.com/finance/quote/{quote_id}?hl=en&gl=US"
NA = "\u2014"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

HEADERS = [
    "Symbol",
    "Name",
    "Price",
    "Change",
    "% Change",
    "Prev Close",
    "Open",
    "High",
    "Low",
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
]

TOKEN_RE = re.compile(r"^[\^\.]?[A-Za-z0-9][A-Za-z0-9.^_\-]*(?::[A-Za-z0-9]+)?$")
CALLBACK_RE = re.compile(
    r"AF_initDataCallback\(\{key: '(ds:\d+)', hash: '[^']*', "
    r"data:(\[.*?\]), sideChannel: \{\}\}\)",
    re.DOTALL,
)
STAT_RE = re.compile(
    r'<div class="SwQK7">([^<]+)</div><div class="dO6ijd">([^<]*)</div>'
)
US_EXCHANGES = ("NASDAQ", "NYSE", "NYSEARCA", "BATS", "OTC")
INDEX_ALIASES = {
    "^GSPC": ".INX:INDEXSP",
    "^DJI": ".DJI:INDEXDJX",
    "^IXIC": ".IXIC:INDEXNASDAQ",
    "^RUT": "RUT:INDEXRUSSELL",
}
INDEX_DISPLAY = {
    ".INX": "^GSPC",
    ".DJI": "^DJI",
    ".IXIC": "^IXIC",
    "RUT": "^RUT",
}
CURRENCY_SIGNS = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "JPY": "\u00a5",
    "CNY": "\u00a5",
    "CAD": "C$",
    "AUD": "A$",
    "HKD": "HK$",
    "INR": "\u20b9",
    "KRW": "\u20a9",
    "CHF": "CHF ",
    "BRL": "R$",
    "MXN": "MX$",
    "SGD": "S$",
    "NZD": "NZ$",
    "SEK": "kr ",
    "NOK": "kr ",
    "DKK": "kr ",
    "ZAR": "R",
    "TWD": "NT$",
    "ILS": "\u20aa",
    "RUB": "\u20bd",
    "TRY": "\u20ba",
}
TZ_LABELS = {
    "America/New_York": "ET",
    "America/Chicago": "CT",
    "America/Denver": "MT",
    "America/Los_Angeles": "PT",
}


class QuoteError(RuntimeError):
    """Raised when the watchlist file or quote fetching fails."""


@dataclass(frozen=True)
class Watchlist:
    name: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class Quote:
    symbol: str
    exchange: str
    name: str
    quote_id: str
    currency: str
    price: float
    change: float | None
    pct: float | None
    previous_close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    market_cap: float | None
    as_of: datetime | None
    timezone: str | None
    session_close: datetime | None = None
    sector: str | None = None
    avg_volume: str | None = None
    week52_high: str | None = None
    week52_low: str | None = None
    pe_ratio: str | None = None
    dividend_yield: str | None = None
    eps: str | None = None
    beta: str | None = None
    market_cap_text: str | None = None

    def row(self) -> dict[str, str]:
        dollar_volume = (
            self.price * self.volume if self.volume is not None else None
        )
        return {
            "Symbol": INDEX_DISPLAY.get(self.symbol, self.symbol),
            "Name": self.name,
            "Exchange": self.exchange,
            "Sector": self.sector or "",
            "Price": money(self.price, self.currency),
            "Change": signed(self.change),
            "% Change": percent(self.pct),
            "Prev Close": money(self.previous_close, self.currency),
            "Open": money(self.open, self.currency),
            "High": money(self.high, self.currency),
            "Low": money(self.low, self.currency),
            "Day Range": day_range(self.low, self.high, self.currency),
            "Range %": range_percent(self.low, self.high, self.price),
            "Range Position": range_position(self.low, self.high, self.price),
            "Gap": change_detail(
                self.open, self.previous_close, self.currency
            ),
            "From Open": change_detail(self.price, self.open, self.currency),
            "Volume": compact(self.volume),
            "Avg Vol": self.avg_volume or NA,
            "Mkt Cap": self.market_cap_text or compact(self.market_cap),
            "Dollar Vol": compact_money(dollar_volume, self.currency),
            "52W Range": week52_range(self.week52_low, self.week52_high),
            "52W Position": week52_position(
                self.price, self.week52_low, self.week52_high
            ),
            "P/E": self.pe_ratio or NA,
            "Dividend": self.dividend_yield or NA,
            "EPS": self.eps or NA,
            "Beta": self.beta or NA,
        }

    def as_of_label(self) -> str | None:
        if self.as_of is None:
            return None
        display = self.as_of
        # After-hours timestamps still describe the regular session's close.
        # The price shown in the table is the regular-session price, so label
        # the close rather than a later after-hours clock time.
        if self.session_close is not None and display > self.session_close:
            display = self.session_close
        zone = TZ_LABELS.get(self.timezone or "", display.tzname() or "local")
        date_text = f"{display:%b} {display.day}"
        time_text = display.strftime("%I:%M %p").lstrip("0")
        return f"As of {date_text}, {time_text} {zone}"


def parse_watchlists(text: str) -> list[Watchlist]:
    """Parse ``[Section]`` headings and one-or-more tickers per line."""
    sections: dict[str, list] = {}
    order: list[str] = []
    current = "Watchlist"
    for line_number, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()
        if not line:
            continue

        heading = re.fullmatch(r"\[(.+?)\]", line)
        if heading:
            current = heading.group(1).strip() or "Watchlist"
            key = current.casefold()
            if key not in sections:
                sections[key] = [current, []]
                order.append(key)
            continue

        key = current.casefold()
        if key not in sections:
            sections[key] = [current, []]
            order.append(key)

        for part in re.split(r"[,;]+", line):
            token = part.strip().strip("\"'").lstrip("$").strip()
            if not token:
                continue
            if not TOKEN_RE.fullmatch(token):
                raise QuoteError(
                    f"Invalid ticker on line {line_number}: {token!r}. "
                    "Use one ticker per line, or separate tickers with commas."
                )
            sections[key][1].append(normalize_spec(token))

    return [
        Watchlist(sections[key][0], tuple(sections[key][1]))
        for key in order
        if sections[key][1]
    ]


def load_watchlists(path: Path = WATCHLISTS_PATH) -> list[Watchlist]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise QuoteError(
            f"Could not read {path}. Create it with one ticker per line, "
            "grouped by [Watchlist] headings."
        ) from error
    watchlists = parse_watchlists(text)
    if not watchlists:
        raise QuoteError(f"No tickers found in {path}. Add at least one symbol.")
    return watchlists


def normalize_spec(spec: str) -> str:
    value = spec.strip().strip("\"'").lstrip("$").replace(" ", "")
    if not value:
        raise QuoteError("Empty ticker.")
    if ":" in value:
        symbol, exchange = value.split(":", 1)
        return f"{symbol.upper()}:{exchange.upper()}"
    return value.upper()


def candidate_ids(spec: str) -> list[str]:
    symbol = normalize_spec(spec)
    if ":" in symbol:
        return [symbol]
    if symbol in INDEX_ALIASES:
        return [INDEX_ALIASES[symbol]]
    return [f"{symbol}:{exchange}" for exchange in US_EXCHANGES]


def money(value: float | None, currency: str = "") -> str:
    if value is None:
        return NA
    prefix = CURRENCY_SIGNS.get(currency.upper(), f"{currency} " if currency else "")
    return f"{prefix}{_number_text(value)}"


def _number_text(value: float) -> str:
    places = 2 if abs(value) >= 1 else 4
    return f"{value:,.{places}f}"


def signed(value: float | None) -> str:
    if value is None:
        return NA
    places = 2 if abs(value) >= 1 else 4
    if value == 0:
        return f"{0:,.{places}f}"
    return f"{value:+,.{places}f}"


def percent(value: float | None) -> str:
    if value is None:
        return NA
    if value == 0:
        return "0.00%"
    return f"{value:+.2f}%"


def compact(value: float | None) -> str:
    if value is None:
        return NA
    magnitude = abs(value)
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= divisor:
            text = f"{value / divisor:,.2f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return f"{value:,.0f}"


def _currency_prefix(currency: str) -> str:
    return CURRENCY_SIGNS.get(currency.upper(), f"{currency} " if currency else "")


def signed_money(value: float | None, currency: str = "") -> str:
    if value is None:
        return NA
    places = 2 if abs(value) >= 1 else 4
    prefix = _currency_prefix(currency)
    if value == 0:
        return f"{prefix}{0:,.{places}f}"
    sign = "-" if value < 0 else "+"
    return f"{sign}{prefix}{abs(value):,.{places}f}"


def compact_money(value: float | None, currency: str = "") -> str:
    if value is None or value == 0:
        return NA
    return f"{_currency_prefix(currency)}{compact(abs(value))}"


def day_range(low: float | None, high: float | None, currency: str = "") -> str:
    if low is None or high is None:
        return NA
    return f"{money(low, currency)} \u2013 {money(high, currency)}"


def range_percent(low: float | None, high: float | None, price: float | None) -> str:
    if low is None or high is None or not price:
        return NA
    return f"{(high - low) / price * 100:.2f}%"


def range_position(low: float | None, high: float | None, price: float | None) -> str:
    if low is None or high is None or price is None or high <= low:
        return NA
    position = (price - low) / (high - low) * 100
    return f"{max(0.0, min(100.0, position)):.0f}%"


def change_detail(value: float | None, base: float | None, currency: str = "") -> str:
    if value is None or base is None or base == 0:
        return NA
    delta = value - base
    return f"{signed_money(delta, currency)} ({percent(delta / base * 100)})"


def _display_number(value: str | None) -> float | None:
    if not value or value in {NA, "-", "--", "N/A", "n/a"}:
        return None
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def week52_range(low: str | None, high: str | None) -> str:
    if not low or not high or low in {NA, "-"} or high in {NA, "-"}:
        return NA
    return f"{low} \u2013 {high}"


def week52_position(
    price: float | None, low: str | None, high: str | None
) -> str:
    low_value = _display_number(low)
    high_value = _display_number(high)
    if price is None or low_value is None or high_value is None:
        return NA
    if high_value <= low_value:
        return NA
    position = (price - low_value) / (high_value - low_value) * 100
    return f"{max(0.0, min(100.0, position)):.0f}%"


def _capture_delay() -> float:
    load_env()
    raw = os.environ.get("QUOTE_DELAY_SECONDS", "0.8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.8
    return max(0.0, min(5.0, value))


def capture() -> Snapshot:
    """Fetch every symbol in ``watchlists.txt`` and build a snapshot."""
    watchlists = load_watchlists(WATCHLISTS_PATH)
    cache = _load_cache()
    quotes: dict[str, Quote] = {}
    failures: dict[str, str] = {}
    sections: list[Section] = []
    success_count = 0
    delay = _capture_delay()
    fetched_any = False

    for watchlist in watchlists:
        rows: list[dict[str, str]] = []
        section_failures = 0
        for spec in watchlist.symbols:
            if spec not in quotes and spec not in failures:
                if fetched_any and delay:
                    time.sleep(delay)
                fetched_any = True
                try:
                    quotes[spec] = fetch_quote(spec, cache)
                    log.info("Fetched %s (%s)", quotes[spec].symbol, quotes[spec].quote_id)
                except QuoteError as error:
                    failures[spec] = str(error)
                    log.warning("%s", error)
            if spec in quotes:
                rows.append(quotes[spec].row())
                success_count += 1
            else:
                rows.append(_missing_row(spec))
                section_failures += 1

        note = None
        if section_failures:
            note = (
                f"{section_failures} symbol(s) could not be fetched; "
                "see the notes below."
            )
        sections.append(Section(watchlist.name, list(HEADERS), rows, note=note))

    if success_count == 0:
        raise QuoteError(
            "No Google Finance quotes could be fetched. Check the tickers in "
            "watchlists.txt and your internet connection. The last snapshot was kept."
        )

    _save_cache({spec: quote.quote_id for spec, quote in quotes.items()})

    latest = None
    for quote in quotes.values():
        if quote.as_of is not None and (latest is None or quote.as_of > latest.as_of):
            latest = quote

    notes: list[str] = []
    if failures:
        details = []
        for spec, error in failures.items():
            short = clean_text(error)
            if len(short) > 140:
                short = short[:137].rstrip() + "..."
            details.append(f"{spec} ({short})")
        notes.append("Some symbols could not be fetched: " + "; ".join(details[:8]))
        if len(details) > 8:
            notes.append(f"Plus {len(details) - 8} more failed symbol(s); see logs/mailer.log.")

    return Snapshot(
        captured_at=datetime.now(ET).isoformat(timespec="seconds"),
        sections=sections,
        as_of=latest.as_of_label() if latest else None,
        notes=notes,
    )


def fetch_quote(spec: str, cache: dict[str, str] | None = None) -> Quote:
    """Resolve a ticker (or TICKER:EXCHANGE) and return its public Google quote."""
    cache = cache if cache is not None else {}
    normalized = normalize_spec(spec)
    candidates: list[str] = []
    cached = cache.get(normalized)
    if cached:
        candidates.append(cached)
    for candidate in candidate_ids(normalized):
        if candidate not in candidates:
            candidates.append(candidate)

    allow_other_exchange = ":" not in normalized
    last_error: Exception | None = None
    for index, quote_id in enumerate(candidates):
        try:
            html = _fetch_html(quote_id)
        except QuoteError as error:
            last_error = error
        else:
            quote = parse_quote_html(html, quote_id, allow_other_exchange)
            if quote is not None:
                cache[normalized] = quote.quote_id
                return quote
            last_error = QuoteError(f"no quote data at {quote_id}")
        if index < len(candidates) - 1:
            time.sleep(0.35)

    raise QuoteError(
        f"Could not find {normalized} on Google Finance. "
        "Use TICKER:EXCHANGE in watchlists.txt for non-US or ambiguous symbols. "
        f"Last error: {last_error or 'no quote data'}"
    )


def _fetch_html(quote_id: str) -> str:
    url = QUOTE_URL.format(quote_id=urllib.parse.quote(quote_id, safe=":._-"))
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                wait = 8 * (attempt + 1)
                retry_after = error.headers.get("Retry-After", "")
                try:
                    wait = max(wait, int(float(retry_after)))
                except (TypeError, ValueError):
                    pass
                time.sleep(min(60, wait))
                continue
            raise QuoteError(f"HTTP {error.code} for {quote_id}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2 + attempt * 2)
                continue
            raise QuoteError(f"Network error for {quote_id}: {error}") from error
    raise QuoteError(f"Network error for {quote_id}: {last_error}")


def parse_quote_html(
    html: str,
    expected_quote_id: str,
    allow_other_exchange: bool = False,
) -> Quote | None:
    scripts = {match.group(1): match.group(2) for match in CALLBACK_RE.finditer(html)}
    stats = _key_stats(html)
    expected = normalize_spec(expected_quote_id)
    expected_symbol = expected.split(":", 1)[0].casefold()
    daily = _daily_from_scripts(scripts)

    for key, builder in (
        ("ds:10", _quote_from_ds10),
        ("ds:8", _quote_from_ds10),
        ("ds:15", _quote_from_ds2),
        ("ds:2", _quote_from_ds2),
    ):
        raw = scripts.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            log.debug("Could not parse %s payload for %s", key, expected)
            continue
        quote = builder(data)
        if quote is None:
            continue
        if quote.quote_id.casefold() != expected.casefold() and not (
            allow_other_exchange and quote.symbol.casefold() == expected_symbol
        ):
            continue
        if stats:
            quote = replace(
                quote,
                avg_volume=stats.get("Avg. vol."),
                week52_high=stats.get("52-wk high"),
                week52_low=stats.get("52-wk low"),
                pe_ratio=stats.get("P/E ratio"),
                dividend_yield=stats.get("Dividend"),
                eps=stats.get("EPS"),
                beta=stats.get("Beta"),
                market_cap_text=stats.get("Mkt. cap"),
            )
        if daily and daily.get("symbol", "").casefold() == quote.symbol.casefold():
            quote = replace(
                quote,
                open=quote.open if quote.open is not None else daily.get("open"),
                high=quote.high if quote.high is not None else daily.get("high"),
                low=quote.low if quote.low is not None else daily.get("low"),
                volume=quote.volume if quote.volume is not None else daily.get("volume"),
            )
        return quote
    return None


def _key_stats(html: str) -> dict[str, str]:
    """Read Google's key-stats grid (P/E, 52-week range, average volume, etc.)."""
    stats: dict[str, str] = {}
    missing = {NA, "-", "--", "N/A", "n/a"}
    for label, value in STAT_RE.findall(html):
        label = clean_text(html_lib.unescape(label))
        value = clean_text(html_lib.unescape(value))
        if label and value and value not in missing and label not in stats:
            stats[label] = value
    return stats


def _quote_from_ds10(data: list) -> Quote | None:
    node = _find_node(data, _is_ds10_node)
    if node is None:
        return None

    symbol_pair = node[0][1]
    symbol = clean_text(str(symbol_pair[0] or ""))
    exchange = clean_text(str(symbol_pair[1] or ""))
    quote_id = clean_text(str(node[13] or f"{symbol}:{exchange}"))
    name = clean_text(str(node[14] or symbol))
    currency = clean_text(str(node[12] or ""))
    price = _number(node[6])
    if price is None:
        return None

    timestamp = None
    tz_name = None
    nested = node[19] if len(node) > 19 and isinstance(node[19], list) else []
    if len(nested) > 11 and isinstance(nested[11], list) and nested[11]:
        timestamp = _number(nested[11][0])
    if len(nested) > 12 and isinstance(nested[12], str):
        tz_name = nested[12]
    market_hours = nested[19] if len(nested) > 19 and isinstance(nested[19], list) else []

    return Quote(
        symbol=symbol,
        exchange=exchange,
        name=name,
        quote_id=quote_id,
        currency=currency,
        price=price,
        change=_number(node[8]),
        pct=_number(node[10]),
        previous_close=_number(node[15]),
        open=_number(node[2]),
        high=_number(node[5]),
        low=_number(node[4]),
        volume=_number(node[17]),
        market_cap=_number(node[16]) or None,
        as_of=_timestamp(timestamp, tz_name),
        timezone=tz_name,
        session_close=_session_close(market_hours),
        sector=clean_text(str(node[18])) if node[18] else None,
    )


def _quote_from_ds2(data: list) -> Quote | None:
    node = _find_node(data, _is_ds2_node)
    if node is None:
        return None

    symbol_pair = node[1]
    symbol = clean_text(str(symbol_pair[0] or ""))
    exchange = clean_text(str(symbol_pair[1] or ""))
    quote_values = node[5]
    price = _number(quote_values[0])
    if price is None:
        return None

    timestamp = None
    timezone = None
    if len(node) > 11 and isinstance(node[11], list) and node[11]:
        timestamp = _number(node[11][0])
    if len(node) > 12 and isinstance(node[12], str):
        timezone = node[12]
    market_hours = node[19] if len(node) > 19 and isinstance(node[19], list) else []

    return Quote(
        symbol=symbol,
        exchange=exchange,
        name=clean_text(str(node[2] or symbol)),
        quote_id=f"{symbol}:{exchange}",
        currency=clean_text(str(node[4] or "")),
        price=price,
        change=_number(quote_values[1]) if len(quote_values) > 1 else None,
        pct=_number(quote_values[2]) if len(quote_values) > 2 else None,
        previous_close=_number(node[7]),
        open=None,
        high=None,
        low=None,
        volume=None,
        market_cap=None,
        as_of=_timestamp(timestamp, timezone),
        timezone=timezone,
        session_close=_session_close(market_hours),
    )


def _is_ds10_node(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 20
        and isinstance(value[0], list)
        and len(value[0]) == 2
        and isinstance(value[0][1], list)
        and len(value[0][1]) == 2
        and isinstance(value[13], str)
        and ":" in value[13]
        and _number(value[6]) is not None
    )


def _daily_from_scripts(scripts: dict[str, str]) -> dict | None:
    """Find the latest daily OHLC bar when the quote payload omits those fields."""
    for key in ("ds:14", "ds:12", "ds:13", "ds:15", "ds:2"):
        raw = scripts.get(key)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        node = _find_node(data, _is_daily_node)
        if node is None:
            continue
        bars = node[3][0][2]
        latest = bars[-1]
        return {
            "symbol": clean_text(str(node[0][0] or "")),
            "open": _number(latest[0]),
            "close": _number(latest[1]),
            "high": _number(latest[2]),
            "low": _number(latest[3]),
            "volume": _number(latest[5]),
        }
    return None


def _is_daily_node(value) -> bool:
    if not (
        isinstance(value, list)
        and len(value) >= 4
        and isinstance(value[0], list)
        and len(value[0]) == 2
        and all(isinstance(item, str) for item in value[0])
        and isinstance(value[2], str)
        and isinstance(value[3], list)
        and value[3]
    ):
        return False
    days = value[3][0]
    if not isinstance(days, list) or len(days) < 3 or not isinstance(days[2], list):
        return False
    bars = days[2]
    if not bars or not isinstance(bars[-1], list) or len(bars[-1]) < 6:
        return False
    latest = bars[-1]
    return _number(latest[0]) is not None and _number(latest[5]) is not None


def _is_ds2_node(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 13
        and isinstance(value[1], list)
        and len(value[1]) == 2
        and isinstance(value[1][0], str)
        and isinstance(value[2], str)
        and isinstance(value[4], str)
        and isinstance(value[5], list)
        and len(value[5]) >= 1
        and _number(value[5][0]) is not None
    )


def _find_node(value, predicate, depth: int = 0):
    if depth > 24:
        return None
    if predicate(value):
        return value
    if isinstance(value, list):
        for child in value:
            found = _find_node(child, predicate, depth + 1)
            if found is not None:
                return found
    return None


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("%", "").strip()
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _timestamp(value: float | None, timezone: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        zone = ZoneInfo(timezone) if timezone else ET
    except (KeyError, ValueError):
        zone = ET
    try:
        result = datetime.fromtimestamp(value, zone)
    except (OverflowError, OSError, ValueError):
        return None
    if not 2000 <= result.year <= 2100:
        return None
    return result


def _session_close(market_hours) -> datetime | None:
    """Extract the regular session close from Google's market-hours payload."""
    if not isinstance(market_hours, list) or not market_hours:
        return None
    session = market_hours[0]
    if not isinstance(session, list) or len(session) < 3:
        return None
    close = session[2]
    if not isinstance(close, list) or len(close) < 3:
        return None
    year, month, day = (_number(close[0]), _number(close[1]), _number(close[2]))
    if year is None or month is None or day is None:
        return None
    hour = int(_number(close[3]) or 16) if len(close) > 3 else 16
    minute = int(_number(close[4]) or 0) if len(close) > 4 else 0
    offset = 0
    if len(close) > 7 and isinstance(close[7], list) and close[7]:
        offset = int(_number(close[7][0]) or 0)
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            hour,
            minute,
            tzinfo=timezone(timedelta(seconds=offset)),
        )
    except ValueError:
        return None


def _missing_row(spec: str) -> dict[str, str]:
    row = {header: NA for header in HEADERS}
    row["Symbol"] = spec
    row["Name"] = ""
    return row


def _load_cache() -> dict[str, str]:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cache: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str):
            cache[str(key)] = value
        elif isinstance(value, dict) and isinstance(value.get("quote_id"), str):
            cache[str(key)] = value["quote_id"]
    return cache


def _save_cache(cache: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, CACHE_PATH)
