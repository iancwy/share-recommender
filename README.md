# share-recommender

Ian's private share and ETF recommender and portfolio dashboard.

**Status:**
- Week 1: sign-in per person (Supabase Auth), goal categories, and manual holdings entry with
  add / edit / delete, valued at current prices from Yahoo Finance.
- Week 2: a preferences questionnaire (about you, then each goal), which you can fill in as
  a form or by chatting with Claude, who fills it in for you to confirm and keeps any extra
  nuance as notes. Then rules-based recommendations per goal: a target mix, suggested ETFs
  and shares across ASX, US and other markets, gaps against what you hold, and what to buy
  with new money. A goal can be marked speculative: a small pot in high-growth themes such as
  AI, with guardrails on its size. Claude can explain a recommendation in plain English.
- Week 3: a dashboard (value against money invested over time, breakdowns by goal,
  platform, asset class, currency and holding, progress towards each goal), a health check
  by goal, platform and portfolio (fixes are always top-ups, never sells), CSV import from
  CommSec and CMC Markets exports with a preview, and a shared daily price cache in Supabase.
- Week 4: shares, ETFs and cash only; cash in high-interest savings accounts,
  which counts in totals, fills each goal's cash slice and grows at its own rate; any
  platform name with a changeable default; dividends with franking credits; notifications
  about new or changed recommendations, in the app and by email, on each person's chosen
  schedule (a GitHub Actions job); a phone-friendly layout; and deployment to Streamlit
  Community Cloud ([docs/deploy.md](docs/deploy.md)).
- Week 5: a **watchlist** (shares and ETFs you track without owning, with price alerts and
  a one-step "convert to holding"), and an **AI stock picker**: a rules screen of the largest
  shares on your exchanges plus the curated ETFs, your watchlist and what you hold, scored on
  momentum, quality, value, income, growth and stability, then ranked by Claude against your
  answers and goals, checked, and saved. It runs on demand from the Picker page (5 a day) and
  weekly on Mondays from the notifications job, which also sends new buy ideas and watchlist
  alerts.
- Week 6: **projections** of the whole portfolio, a goal or one share or ETF over 1 to 40
  years, as a worst, base and best case (1 in 10, the middle, 1 in 10) from 2,000 simulated
  paths, with the chance of reaching a goal's target; **regular contributions** (weekly to
  yearly, into the portfolio, a goal or a savings account) included in every scenario; and
  savings accounts growing at their own interest rate.

## Run it locally (VS Code)

Needs Python 3.11 or newer.

1. **Set up Supabase** once: follow [docs/supabase-setup.md](docs/supabase-setup.md), including
   every migration in `supabase/migrations/`.
2. **Create a virtual environment and install** (VS Code terminal, in the repo folder):

   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements-dev.txt
   pip install -e .
   ```

   VS Code should pick up `.venv` automatically (or *Python: Select Interpreter*).
3. **Configure:** copy `.env.example` to `.env` and fill in:

   | Setting | What it's for |
   |---|---|
   | `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Required: your Supabase project (the anon / publishable key, never the secret one) |
   | `MARKET_DATA_PROVIDER` | `yahoo` (the only one so far) |
   | `DEFAULT_EXCHANGE` | Preselected exchange for new holdings, e.g. `ASX` |
   | `ANTHROPIC_API_KEY` | Needed for the Picker page (Claude ranks the shortlist), the chat interview and plain-English explanations. Without it the rest of the app works and the Picker shows the screen's results unranked |

4. **Run the app:**

   ```bash
   streamlit run share_recommender/ui/app.py      # or: streamlit run streamlit_app.py
   ```

   It opens at <http://localhost:8501>. Or use the **Run and Debug** panel: *Streamlit app*.
   To try it on your phone over Wi-Fi: add `--server.address 0.0.0.0` and open
   `http://<your computer's IP>:8501`.
5. **Optional, the API:** `uvicorn share_recommender.api.main:app --reload`, then open
   <http://127.0.0.1:8000/docs>. Calls need `Authorization: Bearer <Supabase access token>`.

## Notifications job

Runs on GitHub Actions; setup in [docs/notifications-setup.md](docs/notifications-setup.md).
To try it locally, put `SUPABASE_SERVICE_ROLE_KEY` (and, to send email, the `SMTP_*` settings
and `EMAIL_FROM`) in your shell or `.env`, never in the repo or Streamlit secrets, then:

```bash
python -m share_recommender.jobs.notify --dry-run                 # prints, saves and sends nothing
python -m share_recommender.jobs.notify --dry-run --user you@example.com --force
```

## Trying the AI picker without the app

`scripts/picker_dry_run.py` runs the whole picker for a sample profile against live market
data and Claude, with no database, and prints each goal's shortlist and the picks:

```bash
python scripts/picker_dry_run.py --profile tests/fixtures/picker/balanced.json
python scripts/picker_dry_run.py --profile tests/fixtures/picker/income.json --screen-only
```

`--screen-only` stops before Claude (no API key needed). A run takes one to two minutes.

## Tests

```bash
pytest          # uses in-memory fakes; no Supabase or internet needed
ruff check .
```

GitHub Actions runs both on every push and pull request (`.github/workflows/ci.yml`).

## Project layout

```
share_recommender/
  core/       models, exchanges, valuation, interview answers, curated investment list,
              projections (assumptions and the Monte Carlo engine),
              recommender, health check, notifications, watchlist, the picker's rules
              screen and checks (pure Python, no network)
  data/       market data providers (Yahoo Finance first, pluggable), discovery and seed
              lists, and the price and fundamentals caches
  ai/         chat interview, plain-English explanations and the picker's ranking (Claude)
  recommendations.py   fetches prices, FX and dividend data, then runs the recommender
  picker_service.py    runs the AI picker: pool, screen, Claude, checks
  projection_service.py gathers one scope and runs a projection
  watchlist_service.py looks up a ticker before it goes on the watchlist
  portfolio_service.py gathers data for the dashboard and health check
  notifier.py          works out what's new for a person's notifications
  jobs/       the scheduled notification job (python -m share_recommender.jobs.notify)
  mailer.py   SMTP email for the job
  db/         Supabase repository (row-level security scopes it to the signed-in user);
              db/admin.py is the job's service-key access, filtered by user explicitly
  api/        FastAPI app for a future web / mobile front end
  ui/         Streamlit app
streamlit_app.py       entry point for Streamlit Community Cloud
scripts/               picker dry run, seed-list refresh
supabase/migrations/   database schema, run oldest first
docs/                  architecture, setup, notifications, deploy, data providers, design
tests/
```

See [docs/architecture.md](docs/architecture.md) and
[docs/data-providers.md](docs/data-providers.md).
