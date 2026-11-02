# Architecture

```
            ┌──────────────────────┐      ┌──────────────────────┐
            │  Streamlit UI (MVP)  │      │  FastAPI (for future │
            │  share_recommender/ui│      │  web / mobile apps)  │
            └──────────┬───────────┘      └──────────┬───────────┘
                       │  both use the same layers below          │
       ┌───────────────┴──────────────┬───────────────┴───────────┐
       ▼                              ▼                           ▼
 core/ (pure Python)          db/ repository              data/ providers
 models, exchanges,           Supabase client using       MarketDataProvider
 portfolio valuation,         the signed-in user's        interface; Yahoo
 recommender                  token                       Finance first
                                      │
                                      ▼
                       Supabase: Postgres + Auth, row-level security
```

## Layers

- **`core/`**: domain models (`HoldingIn`, `CategoryIn`, …), validation rules, exchange
  list and pure calculations, including the recommender. No network or database code, so it
  is quick to test; the Week 3 health check will live here too.
- **`data/`**: market data behind the `MarketDataProvider` interface. Pick the vendor with
  `MARKET_DATA_PROVIDER`. See [data-providers.md](data-providers.md).
- **`db/`**: `PortfolioRepository` reads and writes Supabase tables. It never filters by
  user itself; Postgres row-level security does that, using the user's own access token.
- **`recommendations.py`**: fetches what the recommender needs (prices, exchange rates and,
  for income-focused investors, dividend yields) and runs it. Shared by the UI and the API.
- **`ai/`**: optional, needs `ANTHROPIC_API_KEY`; uses Claude (`claude-opus-5-5`).
  `interview.py` runs the chat version of the questionnaire and `explain.py` rewrites a
  finished recommendation in plain English. Neither saves anything or changes the rules.
- **`ui/`**: the Streamlit app. Talks to `core`, `data` and `db` directly (one process,
  simplest to run locally).
- **`api/`**: FastAPI exposing the same operations over HTTP with the Supabase access token
  as a Bearer token. Not needed by Streamlit; it is there so a web or mobile front end can
  replace Streamlit later without rewriting logic.

## Multiple people

Each person (Ian, his wife) signs up with their own email in Supabase Auth. A trigger
creates their `profiles` row and a starter "General investing" category. Every table has
a `user_id` and RLS policies limiting all reads and writes to `auth.uid() = user_id`.
A holding can only point at a category owned by the same user (composite foreign key).

## Data model

- `profiles`: display name, base currency, default platform, and notification settings
  (`notify_email`, `notify_frequency`).
- `categories`: goal buckets, each with optional target amount, target date and a risk level.
- `holdings`: one row per purchase (parcel): ticker, exchange, units, buy price, buy date,
  fees, platform label (e.g. CMC Markets), category, notes. Deleting a category keeps its
  holdings as "Uncategorised".
- `price_history` and `price_sync` (Week 3): a shared cache of daily closes and exchange
  rates (exchange `FX`, ticker like `AUDUSD`), and which date range has been fetched for
  each. Any signed-in user can read and add to it; nobody can delete. It holds market data
  only, never anyone's holdings.
- `profiles.preferences` and `categories.preferences` (Week 2): interview answers as JSON,
  validated by `InvestorPreferences` and `CategoryPreferences` in `core/preferences.py`.
  An empty object means the interview hasn't been done.
- `cash_accounts` (Week 4): cash in savings accounts, entered by hand: bank or platform,
  account name, balance, interest rate, currency, the date the balance was checked, and a
  goal (same composite foreign key as holdings, so deleting a goal keeps the account).
- `dividends` (Week 4): one row per payment, with franking credits, foreign tax withheld
  and whether it was reinvested.
- `recommendation_snapshots` and `notifications` (Week 4): the job's last snapshot per
  person and scope, and the in-app inbox. Only the job (service key) writes them; people can
  read their own, mark notifications read (a column grant allows only `read_at`) and delete.

Schema: [`supabase/migrations/`](../supabase/migrations/), run oldest first.

## The preferences questionnaire (Week 2)

`core/questionnaire.py` is the single definition of what is asked and saved: each question's
wording, the field it fills on `InvestorPreferences` or `CategoryPreferences`, and its
allowed answers. Two front ends fill in the same fields:

- **The form** on the Preferences page renders those questions directly.
- **The chat** (`ai/interview.py`) gives Claude the same question list, the person's goals,
  their saved answers and the draft so far. Each turn returns, as structured output, a reply
  plus a complete draft of the answers. The app merges the draft onto the saved answers,
  keeping only values that pass the same validation as the form, and shows it for review,
  marking what's new or changed. Nothing is saved until the person clicks Save.

Nuance the questions don't cover goes into free-text `notes` (for the person and for each
goal). Where a nuance also maps to a field, the chat fills both: "I get lots of CBA shares
through work" becomes `avoid_tickers: [CBA]` plus a note. The rules use `avoid_tickers` but
can't read notes, so notes are shown with the recommendations and passed to the plain-English
explanation, which is told to point out picks that conflict with them.

## How recommendations work (Week 2)

All rules live in `core/recommender.py`, with their numbers as named constants at the top.
For one goal category:

1. **Shares versus defensive.** Start from the category's risk level (low 30%, medium 70%,
   high 90% in shares). Adjust for the answer about a 20% market fall (-20 to +10 points)
   and how much the goal can flex (-10 to +5). Cap by time horizon: no shares inside a
   year, then at most 10% under 2 years, 30% under 3, 50% under 5, 70% under 7, 85% under 10.
   The horizon comes from the target date if set, otherwise the interview answer.
2. **Asset classes.** Shares split into Australian, international and (from 60% shares)
   10% emerging markets, using the home-market answer. The defensive part is all cash
   (high-interest savings or a cash ETF such as AAA or BILL): the app tracks shares, ETFs
   and cash only.
3. **Picks.** One ETF per slice from the curated list in `core/universe.py`: one already held
   in the category, else a sustainability-screened one if the person has exclusions, else an
   income one for income seekers, else the broadest on their chosen markets (ASX first). If
   they want individual shares, a quarter or half of the Australian and international slices
   (a quarter at most for beginners) goes to up to 5 shares each, one per sector first,
   alternating between markets, ranked by dividend yield for income seekers, otherwise
   largest first. Shares in excluded industries are left out.
4. **Gaps.** Current holdings in the category, valued in the base currency, against the mix.
   Holdings not on the curated list show as their own row.
5. **Buy plan.** New money goes to the most underweight slices first (never selling), in whole
   units. Trades under 500 (or 50 times the brokerage) are dropped and their money shared
   out, and leftover cash buys extra units of the most underweight picks.

**Speculative goals** work differently. A goal is speculative when its interview answer says
so, or, if it hasn't been answered, when the category is named "Speculative". It is all in
shares, with no cash, invested in the themes chosen in the interview (AI and
semiconductors, cybersecurity, clean energy, biotech):

- 30% across up to 2 theme ETFs (all ETFs, up to 3, for someone who chose "ETFs only"), and
  70% across up to 6 companies in equal weights, alternating between themes and between
  markets (ASX first by default). The companies come from `SPECULATIVE_SHARES` in
  `core/universe.py` plus themed shares on the main list; ordinary goals never use them.
- Warnings when the money is needed within 5 years, for beginners, and when the pot would be
  more than 10% of everything the person holds (counting the new money).

Each step records its reasons, which the page shows and Claude can turn into prose. Claude
never changes the numbers.

## Dashboard, health check and import (Week 3)

- **Price history** (`data/history.py`) wraps the market data provider with the Supabase
  cache. Each instrument is fetched once; later requests only fetch days before the cached
  range, or after it if it wasn't refreshed today (re-fetching the last cached day, whose
  close may have been mid-session). If the cache tables are missing, history comes straight
  from Yahoo.
- **Dashboard** (`core/performance.py`): value over time sums units held on each day times
  that day's close (daily up to about 13 months, weekly beyond), converted at that day's
  exchange rate; money invested is cost including fees at the rate on the buy date.
  Breakdowns fold anything past the top 10 into "Other". Goal progress projects the current
  value to the target date at an assumed 7% a year for shares and 3.5% for cash,
  and works out the monthly top-up needed. These are illustrations, not forecasts.
- **Health check** (`core/health.py`): per goal, drift from the target mix (warning at 5
  points, alert at 10), too much in shares within 2 years of a deadline, and on or behind
  target; per platform, brokerage as a share of money invested (warning at 1%); across the
  portfolio, any one company over 10% (alert at 20%), unpriced or uncategorised holdings,
  and currency exposure. Fixes are always about where new money goes, never selling, so
  the check never triggers capital gains tax.
- **CSV import** (`core/importer.py`): detects CommSec's layout (a Details column like
  `B 100 VAS @ 95.50`, with brokerage worked out from the debit) or one column per field
  (CMC Markets and most others), matching headers against common names. The person can fix
  the column mapping, sees every row's status, and duplicates of existing holdings are
  unticked. Only purchases are imported; sales are listed for the person to adjust their
  parcels. A third layout, the **holdings snapshot** (CMC's profit and loss export: Code,
  Units Held, Net Avg Price AUD, FX Rate), has no dates. It is imported after the
  transaction history: per stock it adds only the units existing holdings don't cover, as
  one parcel dated at an "as at" date (by default the earliest purchase already imported
  for that platform), priced so the total cost matches the broker's. AUD prices of overseas
  shares are converted with the file's FX rate, `:US` codes resolve to NASDAQ or NYSE, and
  rows with no cost (such as demerger shares) need a price entered before import.
- **Classification**: holdings not on the curated list count as one company's shares
  (Australian on the ASX, international elsewhere). Diversified funds such as DHHF and VDHG
  are split across asset classes by their approximate target allocation, in the gaps, the
  dashboard breakdown and the goal projections; VDHG's 10% fixed interest counts as cash.
  The dashboard can also break holdings down by **type**: shares, ETFs and cash.

## Cash, notifications and deploy (Week 4)

- **Shares, ETFs and cash only.** That's all the app tracks. A goal's defensive part is
  all cash: savings accounts, or a cash ETF such as AAA or BILL. Dashboard breakdowns include
  "Type" (shares, ETFs, cash) alongside asset class.
- **Platforms** are free text. Each person has a default for new holdings and imports
  (CMC Markets if unset), and "Rename a platform" moves every matching holding and cash
  account.
- **Cash accounts** are valued at their balance and passed alongside holdings as
  `CashValue`s (`core/performance.py`), never as fake tickers. They count in totals and in
  money invested (so they don't change the gain), in every breakdown, and in value over time
  as a flat line from the date the balance was last checked. A goal's accounts fill its cash
  slice in the gaps; the buy plan tops up the best-paying account instead of buying a cash
  ETF (`BuyPlan.cash_topups`). Projections grow each account at its own interest rate and
  holdings at the assumptions. The health check counts cash in the portfolio total and in
  platform shares, but never as a company and never in brokerage percentages.
- **Notifications** (`core/notifications.py`, `notifier.py`, `jobs/notify.py`). For each goal,
  and once for the portfolio, a snapshot records the recommended picks and weights and the
  Warning and Alert findings. Compared with the last snapshot, a notification is due when a
  pick is added or removed, a weight moves 5 points or more, or a new Warning or Alert
  appears (numbers are stripped from finding titles, so a changing figure isn't a new issue).
  Cleared findings are mentioned as good news but never trigger one. Extras: a price move of
  5% in a day (10% in a week for weekly people) and goal deadline reminders at 24, 12, 6, 3
  and 1 month(s), remembered in the portfolio snapshot so each is sent once. The first run
  for a person stores a baseline only. The job runs at 07:00 UTC on weekdays from GitHub
  Actions, processes people whose frequency is due (daily: weekdays; weekly: Mondays, Sydney
  time), adds one in-app notification per goal with changes, sends one email combining them
  (SMTP, `mailer.py`) and saves the new snapshots. It uses the service key through
  `db/admin.py`, where every query filters by `user_id`, with settings in `JobSettings` so
  the app can never read the key. Setup: [notifications-setup.md](notifications-setup.md).
- **Deploy**: Streamlit Community Cloud runs `streamlit_app.py`, which runs the real app on
  every rerun. Settings come from Cloud secrets as environment variables. See
  [deploy.md](deploy.md).

## Possible later work

- Selling: reduce parcels and record capital gains, including sales from broker exports.
- Dividend import from broker statements.
- Remembering the sign-in on the phone between visits.
