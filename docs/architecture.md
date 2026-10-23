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
 (Wk2: recommender)           token                       Finance first
                                      │
                                      ▼
                       Supabase: Postgres + Auth, row-level security
```

## Layers

- **`core/`**: domain models (`HoldingIn`, `CategoryIn`, …), validation rules, exchange
  list and pure calculations. No network or database code, so it is quick to test and the
  Week 2 recommender and Week 3 health check will live here too.
- **`data/`**: market data behind the `MarketDataProvider` interface. Pick the vendor with
  `MARKET_DATA_PROVIDER`. See [data-providers.md](data-providers.md).
- **`db/`**: `PortfolioRepository` reads and writes Supabase tables. It never filters by
  user itself; Postgres row-level security does that, using the user's own access token.
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

Schema: [`supabase/migrations/20261024000000_initial_schema.sql`](../supabase/migrations/20261024000000_initial_schema.sql).

## Plan for later weeks

- Week 2: guided interview stored per profile and per category; recommender in `core/`.
- Week 3: dashboard and health check by category and platform; CSV import from broker exports;
  daily price cache table.
- Week 4: notifications (email first) and polish.
