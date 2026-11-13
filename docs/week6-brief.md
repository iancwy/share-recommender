# Week 6 build brief: value projections

This brief is for Claude Code, running in VS Code on Ian's machine. Read all of it before you
start. Build the items **in the order given**, one commit per item, and run the tests after
each one. If something in here doesn't match the code you find, trust the code, tell Ian
what differed, and carry on with the closest sensible reading.

Week 6 adds **projections**: what the whole portfolio, one goal, or one share or ETF could be
worth over a horizon the person chooses (1 to 40 years, 5 by default), shown as a **worst,
base and best case** with a shaded band on a chart. It covers:

1. **Return and volatility assumptions** for every holding and savings account, from
   long-run averages (the default) or from past prices.
2. A **Monte Carlo engine** (pure, seeded, monthly steps) that turns those into worst (10th
   percentile), base (median) and best (90th percentile) values over time.
3. **Regular contributions**: an amount and how often (weekly, fortnightly, monthly,
   quarterly or yearly), paid into the whole portfolio, one goal, or a savings account, with
   an optional start and end date. They're saved, included in every scenario, and the result
   shows **money put in against growth**.
4. **Cash in savings accounts grows at its own interest rate**, with no ups and downs.
5. A **Projections page**, links from the Dashboard, API endpoints, and a clear "this is an
   illustration, not a forecast" caveat everywhere a projection appears.

Ian has about 2 to 3 hours this weekend, mostly for reviewing, running one migration and
trying the page. A sensible split is items 1 to 3 on Saturday and 4 to 6 on Sunday. **If time
runs short**, leave out the "Past performance" option (the second half of item 1) and the
API endpoints (end of item 5); the page works on long-run averages alone.

---

## 0. Before you start

### Where the code is

Weeks 1 to 5 are all merged into `main`, including the Week 5 review fixes (latest commit
`227a4d9 Show picker and notification times in Sydney time`). Work on a new branch from
`main`:

```bash
git checkout main && git pull
git checkout -b week-6
```

### Set up and check the baseline

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt && pip install -e .
pytest && ruff check .
```

Both must pass before you change anything. If they don't, tell Ian and fix that first.

Then read `README.md`, `docs/architecture.md`, `docs/design/README.md` and these modules,
which Week 6 builds on: `core/performance.py` (`ASSUMED_RETURN`, `HoldingValue`,
`CashValue`, `assumed_return`, `goal_progress`), `core/universe.py` (`AssetClass`,
`GROWTH_CLASSES`, `classify`, `lookup`, `SPECULATIVE_SHARES`, `EtfStyle.THEMATIC`),
`core/recommender.py` (`BASE_GROWTH`, `growth_percent`, `horizon_years`),
`core/screen.py` (`momentum`, for the volatility maths), `portfolio_service.py`
(`valued_holdings`, `valued_cash`, `HistoryFn`), `data/history.py` (`CachedHistory`),
`data/fundamentals_cache.py`, `ui/app.py` (`PAGES`, `NAV`, `_page`, `FOOTER_PAGES`,
`_value_chart`, `_history`, `_fundamentals`, `_dashboard_page`, `_cash_form`) and
`ui/theme.py`.

### Ground rules (unchanged from Week 5; keep them)

- **`core/` stays pure**: no network, no Supabase, no Streamlit, no Claude. Fetching is
  passed in as functions (see `recommendations.py`, `portfolio_service.py`,
  `picker_service.py`). Put the maths in `core/` and test it there.
- **Row-level security does the per-user scoping.** `db/repository.py` never filters by
  `user_id`; Postgres RLS does. Every new per-person table gets RLS enabled, owner-only
  policies and `revoke all ... from anon`.
- **The service role key never goes near the Streamlit app**, its secrets, `.env` or the
  repo. (Week 6 doesn't touch the scheduled job, so `db/admin.py` doesn't change.)
- **Migrations are new files** in `supabase/migrations/`, named after the last one
  (`20261108000300_ai_picks.sql`), so this week's is `20261115000100_contributions.sql`.
  Never edit an existing migration. Add it to step 2 of `docs/supabase-setup.md`, and tell
  Ian to run it in the Supabase SQL Editor.
- **Tests use the in-memory fakes** in `tests/fakes.py` (no Supabase, no internet, no
  Claude). Extend `FakeRepository` for contributions. UI tests use
  `streamlit.testing.v1.AppTest` (see `tests/test_ui.py`).
- **Asset classes are shares, ETFs and cash only. No bonds**, anywhere: not in the
  assumptions table, not in a goal's default mix.
- Defaults are **ASX and AUD**. Dates show as DD/MM/YYYY (month and year, e.g. "Jan 2031",
  on charts and in sentences about the future). UI text is plain English, short, and never
  sounds like personal financial advice.
- Colours, fonts and badges come from `ui/theme.py` and `.streamlit/config.toml` only (Ian's
  "Shares" design). The new chart reuses `_palette()`; nothing hard-coded.
- Phone layout rule from Week 4: **two inputs per row at most**.
- Keep it easy to run locally: anything new goes in `README.md` and `.env.example`.
- **No new packages to install.** The engine uses `numpy`, which is already installed with
  `pandas`; add `numpy>=1.26` to `requirements.txt` anyway, because `core/` now imports it
  directly.

### What's already there (and what Week 6 touches)

| Area | Where | Week 6 change |
|---|---|---|
| Steady-return goal projection on the Dashboard ("On track: about A$X by Jan 2031 at an assumed 6.2% a year") | `core/performance.py` `ASSUMED_RETURN`, `goal_progress`; `ui/app.py` `_dashboard_page` | Kept as is. Its 7% / 3.5% assumptions become the base case of the new projections, so the two agree. Each goal card gets a "See projection" link |
| Valuing holdings and savings in the base currency | `portfolio_service.py` `valued_holdings`, `valued_cash` | Reused for starting values |
| Shared daily price cache | `data/history.py` `CachedHistory`, `ui/app.py` `_history` | Reused for volatility and past returns (up to 10 years back) |
| Shared fundamentals cache | `data/fundamentals_cache.py`, `ui/app.py` `_fundamentals` | Reused for dividend yields ("Past performance" basis) |
| Goal mix from the interview | `core/recommender.py` `BASE_GROWTH`, `growth_percent` | Gives an empty goal its starting mix |
| Value-over-time chart | `ui/app.py` `_value_chart` | Pattern for the new projection chart |
| Streamlit pages (`PAGES` + `st.navigation`) | `ui/app.py` | New **Projections** page |
| FastAPI mirror | `api/main.py` | Projection and contribution endpoints |
| Contributions, projection engine | nothing yet | All new |

`sydney_today()` lives in `jobs/notify.py` today, and the UI uses `date.today()`, which is
UTC on Streamlit Cloud (so before about 11am in Sydney, "today" is yesterday). Move
`sydney_today()` to a small `core/dates.py` (pure: it takes no arguments and only reads the
clock, which is fine), import it in `jobs/notify.py`, and use it everywhere Week 6 needs
"today". Don't change other pages this week.

---

## 1. Return and volatility assumptions (`core/projection.py`, pure)

Every projection starts from a list of **assets**, each with a starting value in the base
currency, a yearly return and a yearly volatility:

```python
@dataclass(frozen=True)
class Asset:
    key: str                  # "VAS:ASX", or "cash:<account id>"
    label: str                # "VAS", "ING Savings Maximiser"
    value: Decimal            # base currency, today
    expected_return: Decimal  # yearly, fraction (0.07)
    volatility: Decimal       # yearly, fraction (0.15); 0 for savings accounts
    is_cash: bool             # savings accounts grow at their rate, never simulated
    source: str               # plain-English note: "Long-run average for Australian shares"
```

### Basis A: long-run averages (the default)

Put the table at the top of `core/projection.py` as named constants, next to a comment
saying they're assumptions, not forecasts. **Reuse `performance.ASSUMED_RETURN`** for the
returns rather than copying 7% and 3.5%, so the Dashboard and the Projections page always
agree.

| What | Yearly return (base case) | Yearly volatility |
|---|---|---|
| Australian shares ETF (`AU_SHARES`) | 7% (`ASSUMED_RETURN["growth"]`) | 15% |
| International shares ETF (`GLOBAL_SHARES`) | 7% | 15% |
| Emerging markets ETF (`EMERGING_SHARES`) | 7% | 20% |
| Cash ETF (`AssetClass.CASH`, e.g. AAA) | 3.5% (`ASSUMED_RETURN["cash"]`) | 1% |
| Individual share | 7% | Its own volatility over the last 3 years, clamped to 15%–60%; 30% if it has less than a year of prices |
| Speculative share (in `SPECULATIVE_SHARES`) or thematic ETF (`EtfStyle.THEMATIC`) | 7% | Its own 3-year volatility, clamped to 20%–80%; 40% if under a year |
| Savings account | Its interest rate (`CashValue.rate`) | 0 |

Multi-class funds (e.g. VDHG, whose `classify()` returns a split) take the weighted average
of the rows above. A holding not on the curated list counts as an individual share, as
`classify()` already assumes.

Volatility of a share comes from **monthly** closes (month-end, from the price cache):
standard deviation of the monthly log returns times √12. Put this in
`volatility(bars, years=3) -> Decimal | None` (None under 12 monthly returns), next to the
existing daily maths in `core/screen.py`; share a helper if it's simple.

### Basis B: past performance (optional; leave out if time is short)

For each holding with **at least 3 years** of monthly closes (up to the last 10):

- Return = the annualised price growth over that period (geometric, from the first to last
  month-end close) **plus its trailing dividend yield** from the fundamentals cache. Our price
  history is not adjusted for dividends (`auto_adjust=False`), so without the yield an income
  ETF like VHY looks far worse than it was.
- Volatility = as in basis A, over the same period.
- **Clamp the return to between 0% and 12% a year** (`PAST_RETURN_FLOOR`,
  `PAST_RETURN_CAP`), and say so in `source` when it was clamped ("Past 10 years: 34.1% a
  year, capped at 12% for this projection"). A few great years for one share must not turn
  into a 40-year forecast.
- Holdings with less than 3 years of prices, and savings accounts, fall back to basis A, with
  a note ("Less than 3 years of prices, so the long-run average is used").

### Combining assets into one portfolio

The engine (item 2) simulates the invested part as **one** portfolio, so the assets'
returns and volatilities are combined by value:

- Expected return = value-weighted average of the non-cash assets.
- Volatility: `σp² = Σᵢ Σⱼ wᵢ wⱼ ρᵢⱼ σᵢ σⱼ`, with a single assumed correlation
  `CORRELATION = 0.7` between any two share or share-ETF assets, `0` between a cash ETF and
  anything else, and `1` on the diagonal. Put it in
  `combine(assets) -> tuple[Decimal, Decimal]` and test it: two identical assets with
  ρ = 0.7 give a lower volatility than either; one asset gives its own.
- Savings accounts are **not** combined: each grows on its own at its rate (item 2).

### An empty goal

A goal with nothing in it yet (but with contributions, item 3) still needs a mix. Use the
goal's **share of growth assets** from the rules recommender: `growth_percent()` when the
goal has interview answers, else `BASE_GROWTH[category.risk_level]` (30 / 70 / 90). Turn it
into two synthetic assets: growth at 7% and 15%, the rest as a cash ETF at 3.5% and 1%, with
the note "Nothing in this goal yet, so it uses the goal's target mix: 70% shares, 30% cash".

**Acceptance criteria**

- Tests: each row of the table (ETF per class, VDHG split, individual share with and without
  enough history, speculative share, savings account), the volatility clamps, monthly
  volatility on synthetic bars with a known answer, past performance with dividends added,
  the 12% cap and 0% floor with their notes, the fallback for under 3 years, `combine()`, and
  an empty goal at each risk level. `pytest` and `ruff check .` pass.

---

## 2. The projection engine (`core/projection.py`, pure)

### Inputs

```python
class Frequency(StrEnum):
    WEEKLY = "weekly"; FORTNIGHTLY = "fortnightly"; MONTHLY = "monthly"
    QUARTERLY = "quarterly"; YEARLY = "yearly"

@dataclass(frozen=True)
class Contribution:          # the engine's view; item 3 has the saved model
    amount: Decimal          # base currency, each payment
    frequency: Frequency
    start: date
    end: date | None
    into_cash_rate: Decimal | None   # set when paid into a savings account (its rate)

def project(assets: list[Asset], contributions: list[Contribution], *, today: date,
            years: int, target: Decimal | None = None, target_date: date | None = None,
            real: bool = False, paths: int = PATHS, seed: int = SEED) -> Projection
```

### How it works

- **Monthly steps** from `today` for `years * 12` months. `PATHS = 2000`, `SEED = 6`: fixed,
  so the same inputs always give the same chart (and tests are exact). Use
  `numpy.random.default_rng(seed)`.
- The invested part: combine the non-cash assets (item 1) into `μ` and `σ`. Each month, for
  each path, draw a log return from a normal distribution with mean `ln(1 + μ) / 12` and
  standard deviation `σ / √12`. With that mean, **the median path grows at exactly `μ`** a
  year, so the base case matches the Dashboard's steady-return figure when there are no
  contributions. Test that.
- **Savings accounts** grow each month by `(1 + rate) ** (1/12)`, the same on every path.
- **Contributions** are added at the end of each month they're active (from `start`
  to `end`, inclusive, and only in the future):

  | Frequency | Paid in a month |
  |---|---|
  | Weekly | `amount × 52 / 12` every month |
  | Fortnightly | `amount × 26 / 12` every month |
  | Monthly | `amount` every month |
  | Quarterly | `amount` every third month, counting from the start month |
  | Yearly | `amount` in the start date's month each year |

  A contribution into a savings account (`into_cash_rate` set) grows at that rate with no
  ups and downs; anything else is invested in the scope's mix and follows the same random
  path as the rest of the invested part.
- **Worst, base and best** are the 10th, 50th and 90th percentiles **of the total across
  paths, month by month** (`WORST_PCT`, `BASE_PCT`, `BEST_PCT`). Say "1 in 10" in the UI, not
  "percentile".
- **Today's dollars** (`real=True`): divide every value, including money put in, by
  `(1 + INFLATION) ** (months / 12)` with `INFLATION = Decimal("0.025")` (the RBA's target
  midpoint), and say so in the caption.
- If a **target** and **target date** are given and the date falls within the horizon,
  `chance_of_target` = the share of paths at or above the target in the target month,
  rounded to the nearest 5% and kept between 5% and 95% (never "0%" or "100%", which sound
  like certainties).

### Output

```python
@dataclass(frozen=True)
class ProjectionPoint:
    month: date            # first of the month
    worst: Decimal         # whole dollars, base currency
    base: Decimal
    best: Decimal
    put_in: Decimal        # starting value plus contributions so far

@dataclass(frozen=True)
class Projection:
    points: list[ProjectionPoint]   # month 0 (today) to the horizon
    start_value: Decimal
    contributed: Decimal            # total contributions over the horizon
    expected_return: Decimal        # combined μ of the invested part
    volatility: Decimal             # combined σ
    chance_of_target: Decimal | None
    assets: list[Asset]             # with their notes, for "How this works"
    notes: list[str]                # e.g. "Contributions to Kids school fund end in Jan 2031"

    @property
    def final(self) -> ProjectionPoint: ...
    @property
    def growth(self) -> Decimal:    # base case at the end minus everything put in
        ...
```

Keep values as `float` inside numpy and convert to `Decimal` (whole dollars) only for the
output.

**Acceptance criteria**

- Tests: no volatility gives worst = base = best and matches compound growth exactly; the
  median with volatility is within 1% of `V × (1 + μ)^years`; worst < base < best after month
  0; the same inputs give identical results; each frequency adds the right total over a year;
  start and end dates are respected (a contribution that started in the past only counts from
  today); savings-account contributions grow at their rate; a cash-only scope has no band;
  today's dollars; `chance_of_target` with a target well below, near and well above the
  base case (5%, about 50%, 95%); a 40-year horizon runs in under a second. `pytest` and
  `ruff check .` pass.

---

## 3. Regular contributions

### Migration `20261115000100_contributions.sql`

`cash_accounts` doesn't have the `unique (id, user_id)` that the composite foreign key
needs, so add it first:

```sql
-- Week 6: regular contributions, used in projections.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

-- Lets a contribution point at its owner's savings accounts only.
alter table public.cash_accounts
  add constraint cash_accounts_id_user_id_key unique (id, user_id);

create table public.contributions (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
  label            text check (label is null or length(label) <= 100),
  amount           numeric(14, 2) not null check (amount > 0),        -- base currency, each payment
  frequency        text not null
                     check (frequency in ('weekly', 'fortnightly', 'monthly', 'quarterly', 'yearly')),
  category_id      uuid,          -- the goal; null means the whole portfolio
  cash_account_id  uuid,          -- set when it's paid into a savings account
  start_date       date not null default current_date,
  end_date         date check (end_date is null or end_date >= start_date),
  active           boolean not null default true,   -- untick to leave it out of projections
  notes            text check (notes is null or length(notes) <= 1000),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id),
  foreign key (cash_account_id, user_id)
    references public.cash_accounts (id, user_id)
    on delete set null (cash_account_id)
);

create index contributions_user_id_idx on public.contributions (user_id);

create trigger contributions_set_updated_at
  before update on public.contributions
  for each row execute function public.set_updated_at();

alter table public.contributions enable row level security;

create policy "Own contributions: read"   on public.contributions for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own contributions: insert" on public.contributions for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own contributions: update" on public.contributions for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own contributions: delete" on public.contributions for delete to authenticated
  using ((select auth.uid()) = user_id);

revoke all on public.contributions from anon;
```

Before running it, check `cash_accounts` has no constraint with that name already (it
shouldn't). Deleting a goal turns its contributions into whole-portfolio ones; deleting a
savings account turns its contributions into ordinary ones for the same goal. Both are shown
on the page so the person can tidy up.

### Code

- `core/models.py`: `ContributionIn` (label, amount, frequency, category_id,
  cash_account_id, start_date, end_date, active, notes) and `Contribution` (rename the
  engine's dataclass to `ContributionFlow` if the names clash). Validate `end_date >=
  start_date` and `amount > 0`. Amounts are in the profile's **base currency**.
- **Which goal a contribution belongs to**: when `cash_account_id` is set, the account's goal
  (the UI sets `category_id` to match, but the service trusts the account); otherwise
  `category_id`; `None` means the whole portfolio.
- `db/repository.py`: `list_contributions()`, `add_contribution()`,
  `update_contribution(id, c)`, `delete_contribution(id)`. `FakeRepository` gets the same.
- `core/projection.py`: `monthly_equivalent(c) -> Decimal` (for display: "A$500 a fortnight,
  about A$1,083 a month") and `to_flows(contributions, cash_by_id, today)` turning saved rows
  into the engine's flows (inactive ones and ones that have already ended are left out).

### UI: a "Regular contributions" section on the Projections page (item 5)

- A table: Label, Amount and how often ("A$500 a fortnight"), Into (goal name, "Whole
  portfolio", or "ING Savings Maximiser (Kids school fund)"), Starts, Ends, Included (the
  `active` flag). Select a row to **Edit**, **Pause/Include** or **Delete** (with the same
  "Yes, I want to delete it" checkbox as holdings).
- Add form, two inputs per row: Amount (base currency) and How often; Paid into (Whole
  portfolio, each goal, then each savings account labelled with its goal) and Label
  (optional, e.g. "Pay day top-up"); Starts (default today) and Ends (optional, with a
  "No end date" checkbox ticked by default); Notes.
- Caption: "Contributions are added at the end of each month. Weekly and fortnightly amounts
  are spread evenly over the month."
- Empty state: "No regular contributions yet. Add one to see how topping up changes the
  picture."

**Acceptance criteria**

- Ian adds "A$500 a fortnight into Kids school fund" and "A$200 a month into ING Savings
  Maximiser", edits one, pauses one and deletes one; his wife can't see them (RLS; sign in as
  each).
- Deleting a goal keeps its contributions as whole-portfolio ones.
- Tests: model validation, `monthly_equivalent` for each frequency, `to_flows` (paused,
  ended, savings-account rate, goal from the account), repository fake, and UI
  add/edit/pause/delete. `pytest` and `ruff check .` pass.

---

## 4. The projection service (`projection_service.py`, next to `portfolio_service.py`)

Gathers the data for one **scope** and calls `core.projection.project()`. Every fetch is
passed in, as in `portfolio_service.py`, so the UI uses its cached versions and tests use
fakes.

```python
class Scope(StrEnum):
    PORTFOLIO = "portfolio"; GOAL = "goal"; SHARE = "share"

def run_projection(*, scope: Scope, category_id: UUID | None = None,
                   ticker: str | None = None, exchange: str | None = None,
                   amount: Decimal | None = None, extra: ContributionFlow | None = None,
                   holdings, cash_accounts, categories, contributions, goal_prefs, inv,
                   base, quotes_fn, fx_fn, history_fn, fundamentals_fn,
                   years: int, basis: Literal["average", "past"], real: bool,
                   today: date) -> Projection
```

| Scope | Starting assets | Contributions | Target |
|---|---|---|---|
| Whole portfolio | All holdings (grouped by ticker and exchange) and all savings accounts | All active ones | None |
| One goal | That goal's holdings and savings accounts | That goal's, including ones into its savings accounts | The goal's target amount and date |
| One share or ETF | All lots of that ticker the person holds, or, for a watchlist item or any other ticker, the typed `amount` | None saved; an optional unsaved `extra` ("add A$100 a month") | None |

- **Holdings are lots**: group them by ticker and exchange into one asset each, valued with
  `valued_holdings()`; savings accounts with `valued_cash()`. Holdings without a price count
  at cost, as on the Dashboard, with a note.
- **Price history**: fetch month-end closes for the non-cash assets only, 3 years back for
  basis A and 10 for basis B, through `history_fn` (the shared cache). Fundamentals (for
  dividend yields) only for basis B.
- A goal with nothing in it uses the empty-goal mix (item 1). A whole portfolio with nothing
  in it but contributions uses the person's medium-risk mix (70% shares).
- Whole-portfolio contributions in the portfolio scope are invested in the portfolio's mix;
  in a goal scope they're left out (they belong to no goal), and the page says so if there
  are any.
- For a goal, the **default horizon** is the years to its target date, rounded up, at least 1
  (`horizon_years()` already does most of this); otherwise 5.
- Use `core.dates.sydney_today()` for `today`.

**Acceptance criteria**

- Tests with fakes (no internet): each scope; lots grouped; a goal with a target gets a
  chance; an empty goal with contributions; whole-portfolio contributions excluded from a goal
  with a note; a watchlist ticker with an amount; an unpriced holding counted at cost with a
  note; basis B fetches 10 years and fundamentals, basis A doesn't fetch fundamentals.
  `pytest` and `ruff check .` pass.

---

## 5. Projections page, Dashboard links and API

### Projections page (`PAGES`, after "Health check", icon `:material/trending_up:`)

Top, two inputs per row:

- **Project**: a segmented control or radio with "Whole portfolio", "A goal" and "One share or
  ETF". Then the matching picker: Goal (each goal), or Share or ETF (what they hold, then their
  watchlist, labelled "VAS (ASX), held" / "NVDA (NASDAQ), watchlist"). For a watchlist item,
  an **Amount to invest** input (default A$1,000). For a share or ETF, an optional "Add
  regularly" amount and how often (not saved).
- **Years**: a slider from 1 to 40 (5 by default; a goal's default from item 4). **Based on**:
  "Long-run averages" (default) or "Past performance". A **Show in today's dollars** toggle,
  off by default.

Results:

- Metric tiles (`_metric_rows`): "Worst case (1 in 10)",
  "Base case", "Best case (1 in 10)" at the horizon, then "Money put in" (today's value
  plus contributions) and "Growth (base case)". Label the year: "In 5 years (Nov 2031)".
- For a goal with a target: "About 6 in 10 simulations reach your A$80,000 target by Jan
  2031", using `chance_of_target` and its 5% to 95% bounds. If the base case falls short
  and the goal has no contributions yet, also show the Dashboard's existing "Adding about
  A$X a month would close the gap" figure from `goal_progress()`, so the two pages tell the
  same story. (`goal_progress()` doesn't know about contributions, so leave that line out
  when there are any.)
- **Chart** (`_projection_chart(projection, base, target)`, next to `_value_chart`, Altair):
  - a shaded band between worst and best (`mark_area`, the first series colour at about 20%
    opacity), the base case as a solid line in that colour, and "Money put in" as a dashed
    line in the second series colour;
  - for a goal, a horizontal rule at the target and a vertical rule at the target date, in
    `muted`, with small text labels;
  - x axis in month and year ("%b %Y"), y axis in whole dollars; the same hover crosshair and
    tooltip pattern as `_value_chart` (Month, Worst, Base, Best, Money put in);
  - colours only from `_palette()`. If the band needs its own colour, add a `"band"` key to
    both modes of `PALETTE` in `theme.py`.
- "Show as a table" expander: one row a year (Year, Worst, Base, Best, Money put in).
- **How this works** expander: two or three sentences (each month, 2,000 possible paths are
  drawn from the expected return and volatility below; worst and best are the paths that
  1 in 10 do worse or better than; savings accounts grow at their interest rate), then a
  table of the assets used: Holding or account, Value, Return a year, Ups and downs (volatility)
  a year, Based on (the `source` note). Then the combined figures and any notes.
- The **Regular contributions** section from item 3, below the chart.
- **Caveat**: a new `PROJECTION_DISCLAIMER` constant in `core/projection.py`, shown as a
  caption directly under the chart (not only in the footer):

  > Projections are illustrations, not forecasts. They use assumed returns and past prices,
  > and real results can be much better or worse. Figures are before fees and tax, and in
  > future dollars unless you chose today's dollars. This is general information, not
  > personal financial advice.

  Add "Projections" to `FOOTER_PAGES` so the usual `DISCLAIMER` shows too.
- Empty state (no holdings, savings or contributions): "Add holdings, savings or a regular
  contribution to see a projection." For one share or ETF with nothing held or watched,
  offer the Watchlist page (`st.page_link(NAV["Watchlist"])`).
- Cache the projection in the Streamlit app (`st.cache_data`, keyed by the inputs as JSON, a
  few hours) so moving between pages doesn't recompute it.

### Dashboard

- Each goal card in "Progress towards your goals" gets a **See projection** link to the
  Projections page with that goal chosen (set the page's session-state keys before
  `st.switch_page`, the way `nav_page` works in tests, or use `st.page_link` with
  `query_params` if the installed Streamlit supports it).
- Change the caption under the goal cards to end with "See the Projections page for a range
  of outcomes." The steady-return figures themselves stay as they are.

### API (leave out if time is short)

- `GET /projections?scope=portfolio|goal|share&category_id=&ticker=&exchange=&amount=&years=5&basis=average&real=false`
  returning the `Projection` (points, totals, chance, assets, notes, and
  `PROJECTION_DISCLAIMER`). `years` between 1 and 40; 422 otherwise.
- `GET/POST /contributions`, `PUT/DELETE /contributions/{id}`.

**Acceptance criteria**

- Ian opens Projections and sees his whole portfolio over 5 years with a band, three figures
  and money put in. Switching to "Kids private school investment fund" defaults to the years
  to Jan 2031 and shows the target lines and the chance of reaching it.
- Adding "A$500 a fortnight" to that goal raises all three lines and "Money put in", and the
  growth figure changes accordingly. Pausing it puts them back.
- A goal with only a savings account shows a single line (no band) growing at its rate.
- One share (e.g. CBA) shows a wider band than VAS over the same horizon.
- "Past performance" on a share that has risen a lot says its return was capped at 12%.
- Today's dollars lowers every figure and says so.
- The page works at phone width (390px) and in dark mode.
- Tests: UI tests for each scope with fakes, adding a contribution from the page, the
  disclaimer present, and the Dashboard's "See projection" link opening the right goal.
  `pytest` and `ruff check .` pass.

---

## 6. Docs and wrap-up

- `README.md`: a Week 6 status line (projections with worst, base and best cases, regular
  contributions, savings at their own rate).
- `docs/architecture.md`: a "Projections (Week 6)" section: the assumptions table and where
  it lives, how past performance is capped, the correlation shortcut, the Monte Carlo steps
  (monthly, 2,000 seeded paths, 10th/50th/90th percentiles, median grows at the expected
  return so it matches the Dashboard), contributions and the new table.
- `docs/supabase-setup.md`: the new migration in step 2.
- `requirements.txt`: `numpy>=1.26`.
- Show Ian screenshots of the Projections page (whole portfolio, and his school-fund goal
  with a contribution) at desktop and phone width (390px), light and dark, and compare with
  screen 14 ("Projections") of his "Shares" design (linked in `docs/design/README.md`; ask
  him for a screenshot of it if it isn't in `docs/design/` and you can't open the link). Ask
  before polishing further.

---

## Definition of done for Week 6

- Items 1 to 6 done in order, one commit each on `week-6`, `pytest` and `ruff check .`
  passing, CI green. (If time ran out: items 1 to 5 without "Past performance" and the API,
  with those noted as moved to later.)
- One new migration listed in `docs/supabase-setup.md`, and Ian has run it.
- No Yahoo, Supabase or Claude calls in tests.
- A pull request from `week-6` to `main` summarising what changed, what Ian must do (run the
  migration, redeploy), and anything left open.

## Choices made in this brief (Ian can change them)

- **Base case = 7% a year for shares, 3.5% for cash ETFs, the interest rate for savings**,
  the same as the Dashboard's goal figures, so the two pages agree. Change them in one place:
  `ASSUMED_RETURN` in `core/performance.py`.
- **Worst and best are 1-in-10 outcomes** (10th and 90th percentiles). A 1-in-20 band
  (5th/95th) is a constant change, but looks scarier and wider.
- **Long-run averages are the default**; "Past performance" is opt-in and capped at 12% a
  year, because a few good years shouldn't drive a 20-year projection.
- **One correlation (0.7) between all shares and share ETFs** rather than a full correlation
  matrix. It's simple and slightly cautious; a matrix from price history is a possible later
  step.
- **Contributions are in the base currency** and are added at the end of each month; weekly
  and fortnightly ones are spread evenly over the month.
- **No fees, tax or rebalancing costs** are modelled, and dividends are assumed reinvested
  (they're part of the 7%). The caveat says so.
- **Projections aren't stored and don't trigger notifications.** A "your goal's chance
  dropped below 50%" alert is a possible later step that would reuse Week 4's snapshot and
  diff.
