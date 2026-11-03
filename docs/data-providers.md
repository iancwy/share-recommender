# Market data providers: free tier evaluation (Week 1)

What we need for the MVP: end-of-day or delayed prices for shares and ETFs on the
exchanges Ian uses (starting with ASX, plus US and others later), some price history
for performance charts, and ideally basic fundamentals (P/E, dividend yield, sector)
for the recommender in Week 2. A handful of users, so tens to low hundreds of calls a day.

> Free tiers change often. The limits below are from the providers' published plans as
> I know them; check each pricing page before relying on one.

| Provider | API key | Free limit (approx.) | Non-US exchanges on free tier | Fundamentals | Notes |
|---|---|---|---|---|---|
| **Yahoo Finance** (`yfinance`) | No | Unofficial; informal rate limiting | Yes, wide (ASX `.AX`, LSE `.L`, TSX `.TO`, …) | Yes (ratios, dividends, sector) | Not an official API. Breaks occasionally when Yahoo changes things; personal use only per Yahoo's terms. |
| **Alpha Vantage** | Free key | ~25 requests/day | Some, via suffixes | Yes, mostly US | Daily cap is too tight beyond a few tickers. |
| **Finnhub** | Free key | ~60 requests/min | Mostly US on free; international is paid | Basic, US | Generous rate, but coverage is the problem for ASX. |
| **Financial Modeling Prep** | Free key | ~250 requests/day | Mostly US on free | Strong (statements, ratios) | Good fundamentals if we ever go US-heavy; paid for global. |
| **Twelve Data** | Free key | ~800 credits/day, 8/min | Limited on free; most non-US is paid | Paid | Clean API; free tier is US-centric. |
| **Polygon.io** | Free key | ~5 requests/min, EOD | No (US only) | Limited | US-only, so not useful for ASX. |
| **Stooq** | Historically none | CSV downloads | Yes (incl. AU, UK, US) | No | Prices only. Access has become less reliable recently (download restrictions reported), so treat as a fallback. |

## Recommendation

1. **Start with Yahoo Finance via `yfinance`** (implemented in `share_recommender/data/yahoo.py`).
   It is the only free option that covers ASX and other non-US exchanges *and* has the
   fundamentals the recommender will want, with no key to manage.
2. **Design for it to break.** All code goes through the `MarketDataProvider` interface and
   `get_provider()`, chosen by `MARKET_DATA_PROVIDER` in `.env`, so swapping vendors is a new
   class plus one line in `data/registry.py`. A missing price shows as blank rather than crashing.
3. **Cache prices** (Streamlit caches quotes for 15 minutes today; Week 3 adds a daily price
   table in Supabase) so we stay well under any rate limit.
4. **Fallback candidates if Yahoo becomes unreliable:** Stooq for ASX/global end-of-day prices,
   or a paid tier of Twelve Data / FMP / EODHD if Ian wants an official, supported feed
   (typically around US$20–30/month for global end-of-day data).

## Discovery, seed lists and fundamentals (Week 5)

- **Discovery** (`MarketDataProvider.discover(exchange, limit)`): the AI picker's candidate
  pool. Yahoo's screener (`yf.screen` with an `EquityQuery` on the exchange's Yahoo codes, at
  least US$100m, sorted by market cap) returns name, price, size, P/E, yield, 52-week change,
  volume and the 200-day average in one request per 100 shares. Foreign listings are left out
  (Canadian depositary receipts on the TSX, lines quoted in another currency), and share
  classes of the same company are de-duplicated, keeping the more-traded one. It's shares
  only; ETF candidates come from the curated list and the watchlist.
- **Seed lists** (`share_recommender/data/seeds/<EXCHANGE>.csv`): the largest ~150 shares on
  each exchange (tickers and names), used when discovery fails or is rate-limited. Refresh
  them every few months and commit the CSVs:

  ```bash
  python scripts/refresh_seeds.py
  ```

- **Fundamentals** now include industry, forward P/E, revenue and earnings growth, return on
  equity, profit margin, debt to equity (as a ratio), beta, average volume, the 52-week high
  and low, a fund's expense ratio, and the first 300 characters of the description. Yahoo's
  quirks are handled in `data/yahoo.py`: London prices and 52-week figures are in pence but
  market cap in pounds, `debtToEquity` and `netExpenseRatio` are percentages, and the yield
  is a fraction. Known gaps: banks have no debt to equity, and ASX ETFs have no expense ratio
  or category, so those score 50 (neutral) with a note.
- **History in bulk**: `get_many_history` fetches several symbols in parallel, skipping
  Yahoo's NaN gaps. Everything is cached: fundamentals for a week in `fundamentals_cache`,
  daily prices in `price_cache`.
