# NewsPulse 📈

**A zero-cost pre-market stock screener and prediction verifier for Indian equities (NSE, Nifty 200).**

Every trading morning it reads overnight news + global cues, asks Gemini for 4–6 stock ideas with
LONG/SHORT *bias*, then — and this is the point — **checks its own homework**: at 09:35 it records the
actual opening gap, and at 16:00 it verifies every prediction against real intraday prices, simulates
one rule-based paper trade, and updates a lifetime scoreboard published on GitHub Pages.

> **Educational research tool. Not investment advice.**
> NewsPulse never connects to a broker, never places orders, and speaks only in
> "long bias / short bias" — hypotheses to be verified, not recommendations.

---

## How it works

| Time (IST) | Workflow | What it does |
|---|---|---|
| 08:00 Mon–Fri | `premarket` | Global cues + overnight news → **one** Gemini call → filtered picks → `data/predictions/YYYY-MM-DD.json` (+ optional Telegram) |
| 09:10 Mon–Fri | `monitor` *(optional)* | One long job until 15:30: every 3 min re-checks prices/news for today's picks, Telegram alerts on sudden moves. Only runs if Telegram secrets exist. |
| 09:35 Mon–Fri | `gapcheck` | Records actual gap %, 09:15–09:30 opening range, whether the gap agreed with the prediction |
| 16:00 Mon–Fri | `verify` | Scores m1/m2/m3, simulates the paper trade with realistic fills, recomputes `stats.json`, refreshes the dashboard data |

Everything runs on **free** infrastructure: GitHub Actions cron (public repo), JSON files committed
back to the repo as storage, GitHub Pages for the dashboard, and the Gemini free tier
(exactly one batched LLM call per morning, ≤3/day with retries).

**Success metrics** per prediction (LONG shown; mirrored for SHORT):

- `m1_gap` — open > previous close (did the gap agree?)
- `m2_day_direction` — close > previous close
- **`m3_tradeable`** — price at 15:10 > price at 09:30 ← *primary metric*
- `m4_simulated_trade` — opening-range-breakout paper trade: entry on first 1-min close above the
  09:15–09:30 range high (09:31–14:30), stop = max(range low, entry −1%), target = entry +1.5%,
  forced exit 15:10, minus 0.10% costs. Fills are realistic: candles are walked in time order; if
  price gaps *through* a level the fill is that candle's **open** (slippage recorded); |gap| > 4%
  skips the trade; circuit-locked candles fill only at the next normally traded candle.

---

## Setup (10 minutes, no expertise needed)

1. **Fork this repo** (top-right on GitHub) and keep it **Public**
   (public repos get free unlimited Actions minutes).
2. **Get a free Gemini API key**: https://aistudio.google.com → "Get API key".
3. In your fork: **Settings → Secrets and variables → Actions → New repository secret**
   - `GEMINI_API_KEY` = your key *(required)*
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` *(optional — enables the morning Telegram
     summary and the intraday monitor)*
4. *(Usually not needed)* Same page, **Variables** tab: add `GEMINI_MODEL` if the default
   `gemini-flash-latest` isn't available to your key — set it to whichever **Flash** model your
   free AI Studio key supports (e.g. `gemini-3.5-flash`).
5. **Enable workflows**: Actions tab → "I understand my workflows, go ahead and enable them".
6. **Enable the dashboard**: Settings → Pages → Source: *Deploy from a branch* →
   Branch `main`, folder `/docs` → Save. Your dashboard appears at
   `https://<your-username>.github.io/<repo-name>/` within a few minutes,
   pre-loaded with two days of demo data (replaced by real data after the first `verify` run).
7. **Test it**: Actions → `premarket` → *Run workflow* → set `force` to `true`
   (bypasses the holiday/late-start guards so you can test at any hour) → Run.
   Watch the log; a `data/predictions/<today>.json` file should be committed.
   Then run `verify` the same way to see the dashboard update.

### Telegram (optional)

1. Message **@BotFather** → `/newbot` → copy the token → secret `TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` → secret `TELEGRAM_CHAT_ID`.

---

## Running locally

```bash
# Python 3.11
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # then edit .env and paste your GEMINI_API_KEY

FORCE_RUN=1 python -m src.jobs.premarket   # Windows PowerShell: $env:FORCE_RUN="1"; python -m src.jobs.premarket
python -m src.jobs.gapcheck
python -m src.jobs.verify
```

Local runs write all JSON/log files but **skip the git commit+push** (that only happens inside
GitHub Actions). `FORCE_RUN=1` bypasses the weekend/holiday check and the premarket 09:10 IST
late-start guard.

**Previewing the dashboard locally:** don't double-click `docs/index.html` — browsers block
`fetch()` of the JSON data on `file://`, so the page stays empty. Serve it over HTTP instead:

```bash
python -m http.server 8000 --directory docs   # then open http://localhost:8000
```

---

## Customising

| What | Where |
|---|---|
| **Universe** | [data/nifty200.csv](data/nifty200.csv) — best-effort Nifty 200 snapshot (symbol, name, pipe-separated aliases, sector, yahoo ticker). Refresh it occasionally from NSE's official constituents CSV (niftyindices.com → Nifty 200) — keep the same columns, add `.NS` for the yahoo ticker. |
| **Filters** (confidence ≥60, turnover ≥₹25 cr, ≤2/sector, top 6) | `src/common/config.py` — `MIN_CONFIDENCE`, `MIN_TURNOVER_CRORE`, `MAX_PER_SECTOR`, `MAX_PICKS` |
| **Trade rules** (1.5% target, 1% stop, 0.10% costs, ±4% gap skip, ₹10,000 notional) | `src/common/config.py` — `TARGET_PCT`, `STOP_PCT`, `ROUND_TRIP_COST_PCT`, `MAX_GAP_FOR_ENTRY_PCT`, `NOTIONAL_INR`, entry/exit windows |
| **News feeds** | `src/common/config.py` — `RSS_FEEDS` (each has an `enabled` flag) + `GOOGLE_NEWS_QUERIES`. Note: Moneycontrol / Business Standard RSS returned HTTP 403 to non-browser clients at build time, so they ship disabled; their stories still arrive via Google News. |
| **Macro event → sector map** | [data/event_sector_map.csv](data/event_sector_map.csv) — keywords are pipe-separated, sector names must match `nifty200.csv` |
| **Holiday calendar** | [data/nse_holidays_2026.csv](data/nse_holidays_2026.csv) — verify against NSE's official circular each year and add `nse_holidays_2027.csv` etc. (all `nse_holidays_*.csv` files are loaded) |

## Data files

- `data/predictions/YYYY-MM-DD.json` — one file per day: market context, picks, and per-pick
  `gap_check` (09:35) and `verification` (16:00) blocks. Jobs are idempotent — each overwrites
  only its own fields, so any workflow can be safely re-run.
- `data/stats.json` — lifetime scoreboard, recomputed from scratch by every `verify` run: success %
  per metric (overall / by direction / by catalyst / by confidence bucket 60–70 / 70–85 / 85+),
  rolling last-20-session m3, cumulative simulated P&L (₹10,000 notional per trade), streak.
- `data/logs/` — one log file per job per day, committed with each run.
- `docs/data/` — copy of the above for GitHub Pages, rebuilt by `verify` (this wipes the demo data
  on the first real run).

## Design notes & limitations

- **GitHub cron is late by 5–20 min sometimes.** The premarket job *aborts with a warning* if it
  starts after 09:10 IST (predictions that close to the open are useless); gapcheck/verify only
  need data from before their run time, so lateness is harmless for them.
- **One Gemini call per morning** (schema-forced JSON, up to 3 attempts) keeps you far inside the
  free tier.
- **yfinance 1-minute data** is delayed and only kept ~30 days by Yahoo; the verify job falls back
  to 5-minute candles when needed. GIFT Nifty has no reliable free feed, so overnight bias is
  derived from US futures + Asia.
- **NSE endpoints** (announcements, F&O ban, ASM/GSM) block naive clients; the client warms up
  cookies with browser-like headers and retries 3× — on persistent failure it logs and continues
  without that source, and the affected filter is skipped with a warning.
- Headlines + summaries only are stored — never full article text.
- All timestamps are IST ISO strings. No database, no servers, no broker APIs, nothing paid.

## Disclaimer

This project exists to measure whether news-based pre-market hypotheses have any predictive value.
It is an **educational research tool, not investment advice**. Past simulated performance means
nothing for the future. Do your own research; consult a SEBI-registered advisor before investing.
