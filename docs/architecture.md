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

- `profiles`: display name, base currency.
- `categories`: goal buckets, each with optional target amount, target date and a risk level.
- `holdings`: one row per purchase (parcel): ticker, exchange, units, buy price, buy date,
  fees, platform label (e.g. CMC Markets), category, notes. Deleting a category keeps its
  holdings as "Uncategorised".
- `profiles.preferences` and `categories.preferences` (Week 2): interview answers as JSON,
  validated by `InvestorPreferences` and `CategoryPreferences` in `core/preferences.py`.
  An empty object means the interview hasn't been done.

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
   10% emerging markets, using the home-market answer. Defensive splits into cash and
   bonds: all cash under 2 years, 40% cash under 5, 20% cash beyond.
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
shares, with no bonds or cash, invested in the themes chosen in the interview (AI and
semiconductors, cybersecurity, clean energy, biotech):

- 30% across up to 2 theme ETFs (all ETFs, up to 3, for someone who chose "ETFs only"), and
  70% across up to 6 companies in equal weights, alternating between themes and between
  markets (ASX first by default). The companies come from `SPECULATIVE_SHARES` in
  `core/universe.py` plus themed shares on the main list; ordinary goals never use them.
- Warnings when the money is needed within 5 years, for beginners, and when the pot would be
  more than 10% of everything the person holds (counting the new money).

Each step records its reasons, which the page shows and Claude can turn into prose. Claude
never changes the numbers.

## Plan for later weeks

- Week 3: dashboard and health check by category and platform; CSV import from broker exports;
  daily price cache table.
- Week 4: notifications (email first) and polish.
