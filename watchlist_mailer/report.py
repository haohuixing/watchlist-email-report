"""Render the Google Finance snapshot as a structured HTML and text email."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from statistics import mean

from .config import SLOT_LABELS
from .snapshot import Section, Snapshot, change_direction, clean_text, symbol_of


@dataclass(frozen=True)
class Report:
    subject: str
    html: str
    text: str


@dataclass(frozen=True)
class Mover:
    symbol: str
    name: str
    price: str
    pct: float
    pct_text: str


GREEN = "#0b8a4b"
RED = "#c5221f"
FLAT = "#5f6368"
BLUE = "#2563eb"
PURPLE = "#7c3aed"
INK = "#0f172a"
MUTED = "#64748b"
FAINT = "#94a3b8"
BORDER = "#e6eaf0"
SURFACE = "#f8fafc"
EMPTY_VALUES = {"", "-", "--", "\u2014", "\u2013", "N/A", "n/a"}

STYLE = """
<style>
  body { -webkit-text-size-adjust: 100%; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
  @media (max-width: 520px) {
    .pads { padding-left: 16px !important; padding-right: 16px !important; }
    .title { font-size: 24px !important; }
    .stat-value { font-size: 19px !important; }
    .stock-price { font-size: 20px !important; }
    .mover-col { display: block !important; width: 100% !important; padding: 0 0 8px !important; }
  }
</style>
"""


def _esc(value: str) -> str:
    return html_lib.escape(value or "", quote=True)


def _date_label(dt) -> str:
    return f"{dt:%A, %B} {dt.day}, {dt.year}"


def _short_date(dt) -> str:
    return f"{dt:%a, %b} {dt.day}"


def _time_label(dt) -> str:
    return f"{dt:%I:%M:%S %p} ET".lstrip("0")


def _find(headers: list[str], *needles: str, exclude: tuple[str, ...] = ()) -> str | None:
    for header in headers:
        lowered = header.casefold()
        if any(needle in lowered for needle in needles) and not any(
            blocked in lowered for blocked in exclude
        ):
            return header
    return None


def _value(row: dict[str, str], key: str | None) -> str:
    if not key:
        return ""
    return clean_text(row.get(key, ""))


def _clean_value(value: str | None) -> str:
    text = clean_text(value or "")
    return "" if text in EMPTY_VALUES else text


def _row_value(row: dict[str, str], key: str) -> str:
    return _clean_value(row.get(key))


def _number(value: str | None) -> float | None:
    text = clean_text(value or "").replace(",", "")
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _quote_keys(headers: list[str]) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    symbol = _find(headers, "symbol", "ticker")
    name = _find(headers, "name", "company")
    price = _find(headers, "price", "last", exclude=("prev", "open", "day", "52"))
    pct = _find(headers, "% change", "percent", "pct")
    if pct and not any(term in pct.casefold() for term in ("change", "percent", "pct")):
        pct = None
    change = _find(headers, "change", exclude=("%", "percent", "pct"))
    if symbol is None and headers:
        symbol = headers[0]
    return symbol, name, price, change, pct


def _row_pct(row: dict[str, str], headers: list[str]) -> float | None:
    _, _, _, _, pct = _quote_keys(headers)
    return _number(_value(row, pct))


def _row_price(row: dict[str, str], headers: list[str]) -> str:
    _, _, price, _, _ = _quote_keys(headers)
    return _value(row, price)


def _row_name(row: dict[str, str], headers: list[str]) -> str:
    _, name, _, _, _ = _quote_keys(headers)
    return _value(row, name)


def _summary(snapshot: Snapshot) -> tuple[int, int, int, int]:
    unique = len(snapshot.unique_symbols()) or snapshot.row_count()
    up, down, flat = snapshot.directions()
    return unique, up, down, flat


def _unique_entries(snapshot: Snapshot) -> list[tuple[list[str], dict[str, str]]]:
    entries: list[tuple[list[str], dict[str, str]]] = []
    seen: set[str] = set()
    for section in snapshot.sections:
        for index, row in enumerate(section.rows):
            symbol = symbol_of(row)
            key = symbol.casefold() or f"{section.name.casefold()}:{index}"
            if key in seen:
                continue
            seen.add(key)
            entries.append((section.headers, row))
    return entries


def _average_pct(entries: list[tuple[list[str], dict[str, str]]]) -> float | None:
    values = [
        pct
        for headers, row in entries
        if (pct := _row_pct(row, headers)) is not None
    ]
    return mean(values) if values else None


def _pct_text(value: float | None) -> str:
    return "\u2014" if value is None else f"{value:+.2f}%"


def _direction_color(direction: int) -> str:
    if direction > 0:
        return GREEN
    if direction < 0:
        return RED
    return FLAT


def _tint(color: str) -> str:
    return {
        GREEN: "#e8f5ee",
        RED: "#fdeceb",
        FLAT: "#f1f5f9",
        BLUE: "#eff6ff",
        PURPLE: "#f3e8ff",
    }.get(color, "#f1f5f9")


def _signed_color(value: str) -> str:
    number = _number(value)
    if number is None or number == 0:
        return FLAT
    return GREEN if number > 0 else RED


def _movers(snapshot: Snapshot, limit: int = 3) -> tuple[list[Mover], list[Mover]]:
    entries = _unique_entries(snapshot)
    movers: list[Mover] = []
    for headers, row in entries:
        pct = _row_pct(row, headers)
        if pct is None or pct == 0:
            continue
        symbol = symbol_of(row) or "?"
        movers.append(
            Mover(
                symbol=symbol,
                name=_row_name(row, headers),
                price=_row_price(row, headers),
                pct=pct,
                pct_text=_pct_text(pct),
            )
        )
    movers.sort(key=lambda item: item.pct, reverse=True)
    gainers = [item for item in movers if item.pct > 0][:limit]
    losers = sorted((item for item in movers if item.pct < 0), key=lambda item: item.pct)[:limit]
    return gainers, losers


def _stat(value: int, label: str, color: str) -> str:
    return (
        '<td width="25%" style="padding:0 4px;vertical-align:top;">'
        f'<div style="background:{SURFACE};border:1px solid {BORDER};border-top:3px solid {color};'
        'border-radius:9px;padding:10px 4px;text-align:center;">'
        f'<div class="stat-value" style="font-size:22px;font-weight:700;color:{color};line-height:1.15;">{value}</div>'
        f'<div style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:{MUTED};margin-top:3px;">{_esc(label)}</div>'
        "</div></td>"
    )


def _summary_html(snapshot: Snapshot) -> str:
    unique, up, down, flat = _summary(snapshot)
    return (
        '<tr><td class="pads" style="padding:16px 24px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        + _stat(unique, "Symbols", BLUE)
        + _stat(up, "Up", GREEN)
        + _stat(down, "Down", RED)
        + _stat(flat, "Flat", FLAT)
        + "</tr></table></td></tr>"
    )


def _bar_segments(up: int, down: int, flat: int) -> str:
    total = max(1, up + down + flat)
    segments: list[str] = []

    def add(count: int, color: str) -> None:
        if count <= 0:
            return
        width = max(1, round(count / total * 100))
        segments.append(
            f'<td width="{width}%" style="height:8px;background:{color};'
            'font-size:0;line-height:0;">&nbsp;</td>'
        )

    add(up, GREEN)
    add(down, RED)
    add(flat, "#cbd5e1")
    if not segments:
        return (
            '<td width="100%" style="height:8px;background:#cbd5e1;'
            'font-size:0;line-height:0;">&nbsp;</td>'
        )
    return "".join(segments)


def _breadth_html(snapshot: Snapshot) -> str:
    unique, up, down, flat = _summary(snapshot)
    total = max(1, up + down + flat)
    average = _average_pct(_unique_entries(snapshot))
    avg_color = FLAT if average is None else _direction_color(1 if average > 0 else -1 if average < 0 else 0)
    return (
        '<tr><td class="pads" style="padding:12px 24px 0;">'
        f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:10px;padding:11px 14px 10px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:#475569;font-weight:700;">Market breadth</td>'
        f'<td align="right" style="font-size:10px;color:{MUTED};">Avg move '
        f'<strong style="color:{avg_color};font-size:12px;">{_pct_text(average)}</strong></td></tr></table>'
        f'<div style="background:#e8edf3;border-radius:5px;overflow:hidden;margin-top:7px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="height:8px;"><tr>{_bar_segments(up, down, flat)}</tr></table></div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px;"><tr>'
        f'<td style="font-size:9.5px;color:{MUTED};">'
        f'<span style="color:{GREEN};font-weight:700;">&#9679;</span> {up} up'
        f' &nbsp; <span style="color:{RED};font-weight:700;">&#9679;</span> {down} down'
        f' &nbsp; <span style="color:#94a3b8;font-weight:700;">&#9679;</span> {flat} flat'
        "</td>"
        f'<td align="right" style="font-size:9.5px;color:{FAINT};">{round(up / total * 100)}% advancing</td>'
        "</tr></table></div></td></tr>"
    )


def _mover_rows(movers: list[Mover], color: str) -> str:
    if not movers:
        return (
            f'<div style="font-size:10px;color:{FAINT};padding:8px 0 2px;">'
            "No qualifying moves in this capture.</div>"
        )
    rows: list[str] = []
    for index, mover in enumerate(movers, 1):
        name = (
            f'<div style="font-size:9.5px;color:{MUTED};line-height:1.35;">{_esc(mover.name)}</div>'
            if mover.name and mover.name.casefold() != mover.symbol.casefold()
            else ""
        )
        rows.append(
            '<tr><td style="padding:7px 0 6px;border-top:1px solid #eef2f7;vertical-align:top;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td width="18" style="vertical-align:top;font-size:9px;color:{FAINT};padding-top:2px;">{index}</td>'
            '<td style="vertical-align:top;">'
            f'<div style="font-size:12px;font-weight:700;color:{INK};">{_esc(mover.symbol)}</div>{name}</td>'
            f'<td align="right" style="vertical-align:top;font-size:11.5px;font-weight:700;color:{color};white-space:nowrap;">'
            f"{_esc(mover.pct_text)}"
            f'<div style="font-size:9.5px;font-weight:400;color:{MUTED};margin-top:1px;">{_esc(mover.price)}</div>'
            "</td></tr></table></td></tr>"
        )
    return "".join(rows)


def _movers_html(snapshot: Snapshot) -> str:
    gainers, losers = _movers(snapshot)
    if not gainers and not losers:
        return ""
    columns: list[str] = []
    for title, movers, color in (
        ("Gainers", gainers, GREEN),
        ("Decliners", losers, RED),
    ):
        if not movers:
            continue
        columns.append(
            f'<td class="mover-col" width="50%" valign="top" style="padding-right:5px;">'
            f'<div style="border:1px solid {BORDER};border-radius:10px;padding:10px 12px 4px;background:#ffffff;">'
            f'<div style="font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:{color};font-weight:700;">'
            f"{_esc(title)}</div>"
            f"{_mover_rows(movers, color)}</div></td>"
        )
    if not columns:
        return ""
    if len(columns) == 1:
        columns = [columns[0].replace('width="50%"', 'width="100%"').replace("padding-right:5px;", "")]
    else:
        columns[1] = columns[1].replace("padding-right:5px;", "padding-left:5px;")
    return (
        '<tr><td class="pads" style="padding:14px 24px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="padding-bottom:7px;font-size:11px;font-weight:700;letter-spacing:.09em;'
        f'text-transform:uppercase;color:#334155;">Top movers</td>'
        f'<td align="right" style="padding-bottom:7px;font-size:9.5px;color:{FAINT};">'
        "Largest moves this session</td></tr></table>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        + "".join(columns)
        + "</tr></table></td></tr>"
    )


def _metric_cell(label: str, value: str) -> str:
    return (
        '<td width="33.33%" style="padding:7px 8px 7px 0;vertical-align:top;">'
        f'<div style="font-size:8.5px;letter-spacing:.08em;text-transform:uppercase;color:{FAINT};line-height:1.35;">'
        f"{_esc(label)}</div>"
        '<div style="font-size:12px;font-weight:600;color:#1e293b;line-height:1.4;'
        f'font-variant-numeric:tabular-nums;">{_esc(value)}</div></td>'
    )


def _metric_grid(row: dict[str, str]) -> str:
    candidates = (
        ("Open", "Open"),
        ("Prev Close", "Prev Close"),
        ("Day Range", "Day Range"),
        ("Volume", "Volume"),
        ("Avg Vol", "Avg Vol"),
        ("Traded Value", "Dollar Vol"),
        ("Mkt Cap", "Mkt Cap"),
        ("P/E", "P/E"),
        ("EPS", "EPS"),
        ("Dividend", "Dividend"),
        ("Beta", "Beta"),
        ("52W Range", "52W Range"),
    )
    items = [
        (label, value)
        for label, key in candidates
        if (value := _row_value(row, key))
    ]
    if not items:
        return ""
    rows: list[str] = []
    for start in range(0, len(items), 3):
        chunk = items[start : start + 3]
        cells = "".join(_metric_cell(label, value) for label, value in chunk)
        cells += '<td width="33.33%"></td>' * (3 - len(chunk))
        border = "border-bottom:1px solid #f1f5f9;" if start + 3 < len(items) else ""
        rows.append(f'<tr style="{border}">{cells}</tr>')
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-top:1px solid #eef2f7;margin-top:2px;">{"".join(rows)}</table>'
    )


def _split_range(value: str) -> tuple[str, str]:
    for separator in ("\u2013", "\u2014", " - "):
        if separator in value:
            low, _, high = value.partition(separator)
            return low.strip(), high.strip()
    return "", ""


def _range_bar(
    label: str,
    low: str,
    high: str,
    position_text: str,
    color: str,
    note: str = "",
) -> str:
    position = _number(position_text)
    if position is None or not low or not high:
        return ""
    position = max(0.0, min(100.0, position))
    width = f"{position:.0f}%"
    fill_width = "100%" if position >= 100 else ("1%" if position <= 0 else width)
    remainder = "" if position >= 100 else '<td style="height:7px;font-size:0;line-height:0;">&nbsp;</td>'
    note_text = f"{position:.0f}% of range"
    if note:
        note_text += f" &middot; {note}"
    return (
        '<div style="margin-top:9px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-size:8.5px;letter-spacing:.08em;text-transform:uppercase;color:{FAINT};font-weight:700;">'
        f"{_esc(label)}</td>"
        f'<td align="right" style="font-size:8.5px;color:{FAINT};">{note_text}</td>'
        "</tr></table>"
        '<div style="background:#e8edf3;border-radius:5px;overflow:hidden;margin-top:4px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="height:7px;"><tr>'
        f'<td width="{fill_width}" style="height:7px;background:{color};font-size:0;line-height:0;">&nbsp;</td>'
        f"{remainder}</tr></table></div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:3px;"><tr>'
        f'<td style="font-size:9px;color:{MUTED};">{_esc(low)}</td>'
        f'<td align="right" style="font-size:9px;color:{MUTED};">{_esc(high)}</td>'
        "</tr></table></div>"
    )


def _day_range(row: dict[str, str]) -> str:
    low = _row_value(row, "Low")
    high = _row_value(row, "High")
    range_text = _row_value(row, "Day Range")
    if range_text:
        range_low, range_high = _split_range(range_text)
        low = low or range_low
        high = high or range_high
    if not (low and high):
        return ""
    position = _row_value(row, "Range Position")
    if not position:
        price = _number(_row_value(row, "Price"))
        low_number = _number(low)
        high_number = _number(high)
        if price is not None and low_number is not None and high_number is not None and high_number > low_number:
            position = f"{(price - low_number) / (high_number - low_number) * 100:.0f}%"
    range_width = _row_value(row, "Range %")
    note = f"width {range_width}" if range_width else ""
    return _range_bar("Day range", low, high, position, BLUE, note)


def _week_range(row: dict[str, str]) -> str:
    range_text = _row_value(row, "52W Range")
    if not range_text:
        return ""
    low, high = _split_range(range_text)
    if not (low and high):
        return ""
    return _range_bar(
        "52-week range",
        low,
        high,
        _row_value(row, "52W Position"),
        PURPLE,
    )


def _session_strip(row: dict[str, str]) -> str:
    items = [
        ("Gap vs prev close", _row_value(row, "Gap")),
        ("From open", _row_value(row, "From Open")),
    ]
    items = [(label, value) for label, value in items if value]
    if not items:
        return ""
    cells = "".join(
        '<td width="50%" style="padding:0 5px 0 0;vertical-align:top;">'
        f'<div style="font-size:8.5px;letter-spacing:.08em;text-transform:uppercase;color:{FAINT};font-weight:700;">'
        f"{_esc(label)}</div>"
        f'<div style="font-size:11.5px;font-weight:600;color:{_signed_color(value)};margin-top:2px;">'
        f"{_esc(value)}</div></td>"
        for label, value in items
    )
    if len(items) == 1:
        cells += '<td width="50%"></td>'
    return (
        f'<div style="background:{SURFACE};border-radius:8px;padding:8px 10px;margin-top:8px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f"{cells}</tr></table></div>"
    )


def _change_pill(row: dict[str, str], headers: list[str]) -> str:
    _, _, _, change_key, pct_key = _quote_keys(headers)
    change = _value(row, change_key)
    pct = _value(row, pct_key)
    direction = change_direction(row)
    color = _direction_color(direction)
    if not (change or pct):
        return f'<div style="font-size:12px;color:{FAINT};margin-top:3px;">No move data</div>'
    if change and pct:
        text = f"{change} ({pct})"
    else:
        text = change or pct
    arrow = ""
    if direction and "\u25b2" not in text and "\u25bc" not in text:
        arrow = "\u25b2 " if direction > 0 else "\u25bc "
    return (
        f'<div style="margin-top:5px;"><span style="display:inline-block;background:{_tint(color)};'
        f'color:{color};border-radius:12px;padding:3px 8px;font-size:10.5px;font-weight:700;'
        f'white-space:nowrap;">{arrow}{_esc(text)}</span></div>'
    )


def _card_html(section: Section, row: dict[str, str]) -> str:
    headers = section.headers
    symbol = symbol_of(row) or "\u2014"
    name = _row_name(row, headers)
    price = _row_price(row, headers)
    direction = change_direction(row)
    accent = _direction_color(direction) if direction else "#cbd5e1"
    exchange = _row_value(row, "Exchange")
    sector = _row_value(row, "Sector")
    badges = ""
    if exchange:
        badges += (
            f'<span style="display:inline-block;background:#eef2f7;color:{MUTED};font-size:8.5px;'
            'font-weight:700;letter-spacing:.05em;padding:2px 6px;border-radius:4px;margin-left:6px;'
            f'vertical-align:middle;">{_esc(exchange)}</span>'
        )
    name_html = (
        f'<div style="font-size:11px;color:{MUTED};line-height:1.4;margin-top:1px;">{_esc(name)}</div>'
        if name and name.casefold() != symbol.casefold()
        else ""
    )
    sector_html = (
        f'<div style="font-size:8.5px;letter-spacing:.07em;text-transform:uppercase;color:{FAINT};'
        f'margin-top:4px;">{_esc(sector)}</div>'
        if sector
        else ""
    )
    metrics = _metric_grid(row)
    ranges = _day_range(row) + _week_range(row)
    session = _session_strip(row)
    return (
        '<div style="padding:0 0 10px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border:1px solid #e3e8ef;border-left:3px solid '
        f'{accent};border-radius:10px;">'
        '<tr><td style="padding:12px 14px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td valign="top" style="padding-right:10px;">'
        f'<div style="font-size:15px;font-weight:700;color:{INK};line-height:1.25;">'
        f"{_esc(symbol)}{badges}</div>"
        f"{name_html}{sector_html}</td>"
        '<td valign="top" align="right" style="white-space:nowrap;">'
        f'<div class="stock-price" style="font-size:23px;font-weight:700;color:{INK};line-height:1.15;'
        f'font-variant-numeric:tabular-nums;">{_esc(price or "\u2014")}</div>'
        f"{_change_pill(row, headers)}</td>"
        "</tr></table>"
        f"{metrics}{session}{ranges}"
        "</td></tr></table></div>"
    )


def _section_stats(rows: list[dict[str, str]], headers: list[str]) -> tuple[int, int, int, float | None]:
    up = down = flat = 0
    values: list[float] = []
    for row in rows:
        direction = change_direction(row)
        if direction > 0:
            up += 1
        elif direction < 0:
            down += 1
        else:
            flat += 1
        pct = _row_pct(row, headers)
        if pct is not None:
            values.append(pct)
    return up, down, flat, mean(values) if values else None


def _section_header_html(section: Section) -> str:
    up, down, flat, average = _section_stats(section.rows, section.headers)
    avg_color = FLAT if average is None else _direction_color(1 if average > 0 else -1 if average < 0 else 0)
    note = (
        f'<div style="font-size:10px;color:{MUTED};line-height:1.45;margin-top:5px;">{_esc(section.note)}</div>'
        if section.note
        else ""
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td style="padding:0 0 7px;border-bottom:2px solid #dfe5ec;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-size:12.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#334155;">'
        f"{_esc(section.name)}</td>"
        f'<td align="right" style="font-size:9.5px;color:{MUTED};white-space:nowrap;">'
        f'<span style="display:inline-block;background:#eef2f7;border-radius:10px;padding:2px 7px;font-weight:700;color:#475569;">'
        f'{section.count()} {"symbol" if section.count() == 1 else "symbols"}</span></td>'
        "</tr></table></td></tr>"
        '<tr><td style="padding:7px 0 9px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-size:10px;color:{MUTED};">'
        f'<span style="color:{GREEN};font-weight:700;">{up} up</span>'
        f' &nbsp;&middot;&nbsp; <span style="color:{RED};font-weight:700;">{down} down</span>'
        f' &nbsp;&middot;&nbsp; <span style="color:{FLAT};font-weight:700;">{flat} flat</span></td>'
        f'<td align="right" style="font-size:10px;color:{MUTED};">Avg move '
        f'<strong style="color:{avg_color};">{_pct_text(average)}</strong></td>'
        "</tr></table>"
        f"{note}</td></tr></table>"
    )


def _generic_table(section: Section) -> str:
    headers = list(section.headers)
    if not headers and section.rows:
        headers = list(section.rows[0].keys())
    if not headers:
        headers = ["Value"]
    header_cells = "".join(
        '<td style="padding:0 8px 6px 0;font-size:9px;letter-spacing:.06em;'
        f'text-transform:uppercase;color:{FAINT};">{_esc(header)}</td>'
        for header in headers
    )
    rows: list[str] = []
    for row in section.rows:
        cells = "".join(
            '<td style="padding:8px 8px 8px 0;border-bottom:1px solid #eef2f7;'
            f'font-size:11px;color:#334155;vertical-align:top;">{_esc(_row_value(row, header) or "\u2014")}</td>'
            for header in headers
        )
        rows.append(f"<tr>{cells}</tr>")
    if not rows:
        rows.append(
            f'<tr><td colspan="{len(headers)}" style="padding:14px 0;font-size:11px;color:{FAINT};">'
            "No rows captured.</td></tr>"
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border:1px solid #e3e8ef;border-radius:10px;padding:0;">'
        f"<tr>{header_cells}</tr>{''.join(rows)}</table>"
    )


def _section_html(section: Section) -> str:
    _, _, price, change, pct = _quote_keys(section.headers)
    if not (price or change or pct):
        return (
            '<tr><td class="pads" style="padding:22px 24px 0;">'
            + _section_header_html(section)
            + _generic_table(section)
            + "</td></tr>"
        )
    cards = "".join(_card_html(section, row) for row in section.rows)
    if not cards:
        cards = (
            f'<div style="padding:14px 0;font-size:11px;color:{FAINT};">No rows captured.</div>'
        )
    return (
        '<tr><td class="pads" style="padding:22px 24px 0;">'
        + _section_header_html(section)
        + cards
        + "</td></tr>"
    )


def _notes_html(snapshot: Snapshot) -> str:
    notes = [note for note in snapshot.notes if note]
    if not notes:
        return ""
    items = "".join(
        f'<li style="margin:3px 0;">{_esc(note)}</li>' for note in notes
    )
    return (
        '<tr><td class="pads" style="padding:14px 24px 0;">'
        f'<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;'
        f'padding:9px 12px;font-size:10px;color:#78716c;line-height:1.5;">'
        f'<strong style="color:#92400e;">Notes</strong><ul style="margin:5px 0 0 16px;padding:0;">{items}</ul>'
        "</div></td></tr>"
    )


def _html_report(
    snapshot: Snapshot,
    slot: str,
    subject: str,
    stale: bool,
    stale_reason: str | None,
    batch_index: int = 1,
    batch_count: int = 1,
) -> str:
    dt = snapshot.captured_dt
    label = SLOT_LABELS.get(slot, slot)
    unique, up, down, flat = _summary(snapshot)
    average = _average_pct(_unique_entries(snapshot))
    batch_text = f"Part {batch_index} of {batch_count} - {snapshot.row_count()} symbols" if batch_count > 1 else ""
    preheader = (
        f"{batch_text + '. ' if batch_text else ''}{unique} symbols: {up} up, {down} down, {flat} flat. "
        f"Average move {_pct_text(average)}. {label}, {_short_date(dt)}."
    )
    batch_html = (
        f'<div style="font-size:10px;color:#8fa1ba;margin-top:5px;line-height:1.5;">'
        f"Part {batch_index} of {batch_count} &middot; {snapshot.row_count()} symbols</div>"
        if batch_count > 1
        else ""
    )
    source_line = (
        f"Google Finance &middot; {_esc(snapshot.as_of)}"
        if snapshot.as_of
        else f"Captured from Google Finance at {_time_label(dt)}"
    )

    header = (
        '<tr><td style="padding:0;background:#0f172a;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td style="height:4px;background:#2563eb;font-size:0;line-height:0;">&nbsp;</td></tr></table>'
        '<div style="padding:22px 26px 21px;color:#ffffff;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:#93a4bd;font-weight:700;">'
        "Watchlist Mailer</td>"
        '<td align="right"><span style="display:inline-block;background:#1e293b;border:1px solid #334155;'
        'border-radius:12px;padding:3px 9px;font-size:9px;letter-spacing:.08em;text-transform:uppercase;'
        f'color:#cbd5e1;font-weight:700;">{_esc(label)}</span></td></tr></table>'
        '<div class="title" style="font-size:29px;font-weight:700;letter-spacing:-.025em;line-height:1.12;'
        'margin:13px 0 4px;">Watchlist report</div>'
        f'<div style="font-size:12.5px;color:#c2ccdb;">{_date_label(dt)}</div>'
        f"{batch_html}"
        f'<div style="font-size:10px;color:#8fa1ba;margin-top:8px;line-height:1.5;">{source_line}</div>'
        "</div></td></tr>"
    )

    stale_html = ""
    if stale:
        reason = f" {_esc(stale_reason)}" if stale_reason else ""
        stale_html = (
            '<tr><td class="pads" style="padding:12px 24px 0;">'
            f'<div style="background:#fff1f0;border:1px solid #f3c2bd;border-left:4px solid {RED};'
            'border-radius:8px;padding:10px 12px;font-size:11px;color:#7a1c17;line-height:1.5;">'
            "<strong>Stale data.</strong> Google Finance could not be captured, so this is the "
            f"last successful snapshot.{reason}</div></td></tr>"
        )

    sections_html = "".join(_section_html(section) for section in snapshot.sections)
    if not sections_html:
        sections_html = (
            '<tr><td style="padding:20px 24px;font-size:12px;color:#64748b;">'
            "No watchlists were captured.</td></tr>"
        )

    footer = (
        '<tr><td class="pads" style="padding:16px 24px 20px;background:#f8fafc;'
        'border-top:1px solid #e6eaf0;font-size:9.5px;color:#7a8796;line-height:1.6;">'
        f"Captured from Google Finance at <strong>{_time_label(dt)}</strong> on {_date_label(dt)}."
        + (f" {_esc(snapshot.as_of)}" if snapshot.as_of else "")
        + "<br>Values are as displayed by Google Finance and may be delayed. "
        "Not investment advice."
        f'<br><span style="color:{FAINT};">watchlist-mailer &middot; {unique} unique symbols</span>'
        "</td></tr>"
    )

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(subject)}</title>"
        + STYLE
        + '</head><body style="margin:0;padding:0;background:#eef1f5;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        '">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">'
        f"{_esc(preheader)}</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#eef1f5;padding:22px 10px;"><tr><td align="center">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:640px;background:#ffffff;border-radius:14px;overflow:hidden;'
        'box-shadow:0 6px 24px rgba(15,23,42,.08);">'
        + header
        + stale_html
        + _summary_html(snapshot)
        + _breadth_html(snapshot)
        + _movers_html(snapshot)
        + sections_html
        + _notes_html(snapshot)
        + footer
        + "</table></td></tr></table></body></html>"
    )


def _text_breadth(up: int, down: int, flat: int) -> str:
    total = max(1, up + down + flat)
    up_count = round(up / total * 24)
    down_count = round(down / total * 24)
    flat_count = max(0, 24 - up_count - down_count)
    bar = "#" * up_count + "x" * down_count + "." * flat_count
    return (
        f"BREADTH  [{bar[:24]:.<24}]  "
        f"{round(up / total * 100)}% up | {round(down / total * 100)}% down | "
        f"{round(flat / total * 100)}% flat"
    )


def _text_movers(snapshot: Snapshot) -> list[str]:
    gainers, losers = _movers(snapshot)
    if not gainers and not losers:
        return []
    lines = ["", "TOP MOVERS"]
    for title, movers in (("Gainers", gainers), ("Decliners", losers)):
        if not movers:
            continue
        lines.append(f"  {title}")
        for index, mover in enumerate(movers, 1):
            name = f"  {mover.name}" if mover.name else ""
            lines.append(
                f"    {index}. {mover.symbol:<7} {mover.pct_text:>8}  "
                f"{mover.price:>11}{name}"
            )
    return lines


def _text_section(section: Section) -> list[str]:
    headers = section.headers
    up, down, flat, average = _section_stats(section.rows, headers)
    lines = [
        "",
        f"{section.name.upper()} ({section.count()})",
        "-" * 54,
        f"  {up} up | {down} down | {flat} flat | avg move {_pct_text(average)}",
    ]
    if section.note:
        lines.append(f"  Note: {section.note}")

    symbol_key, name_key, price_key, change_key, pct_key = _quote_keys(headers)
    for row in section.rows:
        symbol = _value(row, symbol_key)
        name = _value(row, name_key)
        price = _value(row, price_key)
        change = _value(row, change_key)
        pct = _value(row, pct_key)
        if any((symbol, name, price, change, pct)):
            parts = [part for part in (symbol, name, price, change, pct) if part]
            lines.append("  " + "  ".join(parts))

            session = []
            for label, key in (
                ("Open", "Open"),
                ("Prev close", "Prev Close"),
                ("Gap", "Gap"),
                ("From open", "From Open"),
            ):
                value = _row_value(row, key)
                if value:
                    session.append(f"{label} {value}")
            if session:
                lines.append("    " + " | ".join(session))

            range_parts = []
            day_range = _row_value(row, "Day Range")
            if day_range:
                range_parts.append(f"Day {day_range}")
            range_position = _row_value(row, "Range Position")
            if range_position:
                range_parts.append(f"at {range_position} of day range")
            range_width = _row_value(row, "Range %")
            if range_width:
                range_parts.append(f"width {range_width}")
            week_range = _row_value(row, "52W Range")
            if week_range:
                range_parts.append(f"52W {week_range}")
            week_position = _row_value(row, "52W Position")
            if week_position:
                range_parts.append(f"at {week_position} of 52W range")
            if range_parts:
                lines.append("    " + " | ".join(range_parts))

            liquidity = []
            for label, key in (
                ("Vol", "Volume"),
                ("Avg vol", "Avg Vol"),
                ("Traded value", "Dollar Vol"),
                ("Mkt cap", "Mkt Cap"),
            ):
                value = _row_value(row, key)
                if value:
                    liquidity.append(f"{label} {value}")
            if liquidity:
                lines.append("    " + " | ".join(liquidity))

            fundamentals = []
            for label, key in (
                ("P/E", "P/E"),
                ("EPS", "EPS"),
                ("Dividend", "Dividend"),
                ("Beta", "Beta"),
            ):
                value = _row_value(row, key)
                if value:
                    fundamentals.append(f"{label} {value}")
            if fundamentals:
                lines.append("    " + " | ".join(fundamentals))

            listing = [
                value
                for key in ("Exchange", "Sector")
                if (value := _row_value(row, key))
            ]
            if listing:
                lines.append("    " + " | ".join(listing))
        else:
            parts = list(row.values())
            lines.append("  " + "  ".join(part for part in parts if part))
    if not section.rows:
        lines.append("  (no rows captured)")
    return lines


def _text_report(
    snapshot: Snapshot,
    slot: str,
    stale: bool,
    stale_reason: str | None,
    batch_index: int = 1,
    batch_count: int = 1,
) -> str:
    dt = snapshot.captured_dt
    label = SLOT_LABELS.get(slot, slot)
    unique, up, down, flat = _summary(snapshot)
    average = _average_pct(_unique_entries(snapshot))
    lines = [
        f"WATCHLIST REPORT - {label.upper()}",
        f"{_date_label(dt)} - captured {_time_label(dt)}",
    ]
    if batch_count > 1:
        lines.append(f"PART {batch_index} OF {batch_count} - {snapshot.row_count()} symbols")
    lines.append("")
    if stale:
        lines.append("STALE DATA: " + (stale_reason or "using the last successful capture"))
        lines.append("")
    lines.append(f"{unique} unique symbols | {up} up | {down} down | {flat} flat")
    lines.append(_text_breadth(up, down, flat))
    lines.append(f"Average move {_pct_text(average)}")
    lines.extend(_text_movers(snapshot))

    for section in snapshot.sections:
        lines.extend(_text_section(section))

    if snapshot.notes:
        lines.extend(["", "NOTES"])
        lines.extend(f"  - {note}" for note in snapshot.notes)
    lines.extend(
        [
            "",
            "Source: Google Finance. Values as displayed; may be delayed.",
            "Not investment advice.",
        ]
    )
    return "\n".join(lines)


def batch_snapshots(snapshot: Snapshot, batch_size: int) -> list[Snapshot]:
    """Split a snapshot into row-count batches while preserving section order."""
    batch_size = max(1, batch_size)
    if snapshot.row_count() <= batch_size:
        return [snapshot]

    batches: list[Snapshot] = []
    current_sections: list[Section] = []
    current_count = 0

    def flush() -> None:
        nonlocal current_sections, current_count
        if current_sections:
            batches.append(
                Snapshot(
                    captured_at=snapshot.captured_at,
                    sections=current_sections,
                    as_of=snapshot.as_of,
                    notes=list(snapshot.notes),
                )
            )
        current_sections = []
        current_count = 0

    for section in snapshot.sections:
        rows = section.rows
        if not rows:
            continue
        offset = 0
        while offset < len(rows):
            room = batch_size - current_count
            take = min(room, len(rows) - offset)
            current_sections.append(
                Section(
                    section.name,
                    list(section.headers),
                    rows[offset : offset + take],
                    note=section.note,
                )
            )
            current_count += take
            offset += take
            if current_count >= batch_size:
                flush()
    flush()
    return batches or [snapshot]


def render_report(
    snapshot: Snapshot,
    slot: str,
    stale: bool = False,
    stale_reason: str | None = None,
    batch_index: int = 1,
    batch_count: int = 1,
) -> Report:
    dt = snapshot.captured_dt
    label = SLOT_LABELS.get(slot, slot)
    suffix = f" ({batch_index}/{batch_count})" if batch_count > 1 else ""
    subject = f"{'[STALE] ' if stale else ''}Watchlist - {label} - {_short_date(dt)}{suffix}"
    return Report(
        subject=subject,
        html=_html_report(
            snapshot,
            slot,
            subject,
            stale,
            stale_reason,
            batch_index=batch_index,
            batch_count=batch_count,
        ),
        text=_text_report(
            snapshot,
            slot,
            stale,
            stale_reason,
            batch_index=batch_index,
            batch_count=batch_count,
        ),
    )
