# Watchlist email report

A free, local Python daemon that emails a polished HTML + plain-text report of
a fixed stock watchlist at **11:00 AM and 4:00 PM ET, Monday-Friday**.

This version does **not** sign in to Google, use Playwright, or store a Google
session. The watchlist composition lives in `watchlists.txt`; prices and other
fields are read from each symbol's public Google Finance quote page.

## Edit the list

Open `watchlists.txt`. It starts as a template with the examples commented
out. Add one `[Section]` heading per watchlist, then paste one ticker per line:

```text
[Core Holdings]
AAPL
MSFT
NVDA

[Following]
TSLA
```

- Commas also work: `AAPL, MSFT, NVDA`.
- Lines starting with `#` are comments.
- Bare US tickers are resolved automatically.
- For ambiguous or non-US symbols use `TICKER:EXCHANGE`, for example
  `BRK.B:NYSE`, `SPY:NYSEARCA`, or `.INX:INDEXSP`.
- Toronto listings use `:TSE`, TSX Venture listings use `:CVE`, London uses
  `:LON`, Frankfurt uses `:FRA`, and OTC listings use `:OTCMKTS` (for example
  `ATD:TSE`, `LMN:CVE`, `HLMA:LON`, `J2BA:FRA`, `HLMAF:OTCMKTS`).
- Cboe sector indexes use `:INDEXCBOE` (for example `SIXB:INDEXCBOE`).
- The order in the file is the order in the report. Duplicate rows are kept;
  the summary counts unique symbols.
- Edit the file whenever the client's lists change. No re-login is needed.

## Setup

1. Create the virtual environment and install the one runtime dependency:

   ```bat
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in:

   - `SMTP_USERNAME` — the Gmail address that sends the report.
   - `SMTP_PASSWORD` — a 16-character Google app password (Google Account →
     Security → App passwords). Do not use the normal Gmail password.
   - `MAIL_TO` — one or more recipients, comma-separated.
   - `MAIL_FROM` — optional; defaults to `SMTP_USERNAME`.
   - `REPORT_BATCH_SIZE` — optional; rows per email (default `50`). Large
     watchlists are sent as numbered batches.
   - `QUOTE_DELAY_SECONDS` — optional; pause between Google Finance requests
     (default `0.8`). Increase it if Google starts returning HTTP 429.

3. Fetch quotes and check the snapshot:

   ```bat
   .venv\Scripts\python.exe -m watchlist_mailer sync
   ```

4. Render a browser preview without sending:

   ```bat
   .venv\Scripts\python.exe -m watchlist_mailer preview
   ```

   Open `out/preview.html`. Re-run with `--no-sync` to preview the last saved
   snapshot without fetching again.

5. Send one test email:

   ```bat
   .venv\Scripts\python.exe -m watchlist_mailer send --slot open
   ```

6. Double-click `run.bat` and leave the CMD window open (or minimized). The
   supervisor prints the next run and each result. It does not need Task
   Scheduler or a GitHub account.

## Commands

```text
python -m watchlist_mailer sync                 Fetch quotes, save snapshot, no email
python -m watchlist_mailer preview [--no-sync]  Write out/preview.html
python -m watchlist_mailer send [--slot open|close] [--mark-sent]
python -m watchlist_mailer serve                Run the 11:00 / 16:00 ET supervisor
python -m watchlist_mailer run-slot --slot open --date 2026-11-24
python -m watchlist_mailer check                Show the next scheduled run
```

`send` does not record the slot by default; use `--mark-sent` if you want the
supervisor to skip it. The scheduled worker records the slot automatically so
a restart cannot double-send.

## Data and output

- `data/snapshot.json` — last successful capture (atomic), rendered from the
  current `watchlists.txt`.
- `data/watchlist.txt` — derived list of symbols for reference.
- `data/state.json` — sent/attempted slot keys.
- `out/preview.html` — last preview.
- `logs/mailer.log` — rotating console/file log.

If Google cannot be reached, the daemon emails the last snapshot marked
`[STALE]`. If no snapshot exists, it sends a short action-needed alert instead.
If only some symbols fail, the report still goes out and lists the failed
symbols in a notes box.

## Large lists, batches and pacing

Large watchlists are split into numbered emails (`Part 1 of 9`) using
`REPORT_BATCH_SIZE`. Each batch has its own summary, breadth, movers and the
applicable stock cards. Gmail may still collapse an individual batch behind
"View entire message" when it is over roughly 100 KB; lower
`REPORT_BATCH_SIZE` if you want smaller emails.

Quotes are fetched sequentially with `QUOTE_DELAY_SECONDS` between requests.
Google does not publish a quota for Finance pages, but aggressive fetches can
return HTTP 429, a consent interstitial, or a CAPTCHA. The client retries 429
and 5xx responses with backoff; a blocked capture falls back to the last
snapshot. Fetching 400+ symbols takes several minutes, so the worker timeout
is 30 minutes.

## Schedule and cost

The supervisor computes the next slot in `America/New_York` using `zoneinfo`
and `tzdata`, handles DST and sleep/resume, and checks every 30 seconds. On
Windows it runs inside 11:00-11:29 and 16:00-16:29 ET on weekdays; a late wake
inside the window catches up, and a slot outside the window is skipped. One
supervisor lock prevents two copies.

Python, Google Finance and Gmail are free. The only costs are the PC and
internet connection. Gmail's app-password sending limit is far above two
runs per weekday, but 400+ symbols at the default batch size means several
emails per run.

## Notes and limitations

- Values are the public Google Finance quote page display values, not the
  signed-in watchlist table. Google may show slightly different layouts on a
  watchlist, and public pages can be delayed.
- No portfolio holdings, per-ticker news, CSV attachments or web UI.
- The daemon only runs while `run.bat`'s CMD window stays open and the PC is
  awake at the slot. If the PC was off, restart `run.bat`; a missed slot is not
  replayed outside its 30-minute window.
- Ticker exchange codes can change; if a symbol starts failing, set it
  explicitly as `TICKER:EXCHANGE`.

## Check

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
