# share-recommender

Ian's private share and ETF recommender and portfolio dashboard.

**Status:**
- Week 1: sign-in per person (Supabase Auth), goal categories, and manual holdings entry with
  add / edit / delete, valued at current prices from Yahoo Finance.
- Week 2: a preferences questionnaire (about you, then each goal), which you can fill in as
  a form or by chatting with Claude, who fills it in for you to confirm and keeps any extra
  nuance as notes. Then rules-based
  recommendations per goal: a target mix, suggested ETFs and shares across ASX, US and other
  markets, gaps against what you hold, and what to buy with new money. A goal can be marked
  speculative: a small pot in high-growth themes such as AI, with guardrails on its size.
  Claude can explain a recommendation in plain English.

- Week 3: a dashboard (value against money invested over time, breakdowns by goal,
  platform, asset class, currency and holding, progress towards each goal), a health check
  by goal, platform and portfolio (fixes are always top-ups, never sells), CSV import from
  CommSec and CMC Markets exports with a preview, and a shared daily price cache in Supabase.

Coming next: notifications (Week 4).

## Run it locally (VS Code)

Needs Python 3.11 or newer.

1. **Set up Supabase** once: follow [docs/supabase-setup.md](docs/supabase-setup.md).
2. **Create a virtual environment and install** (VS Code terminal, in the repo folder):

   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements-dev.txt
   pip install -e .
   ```

   VS Code should pick up `.venv` automatically (or *Python: Select Interpreter*).
3. **Configure:** copy `.env.example` to `.env` and paste in your Supabase URL and anon key.
   Optionally add `ANTHROPIC_API_KEY` (from <https://console.anthropic.com>) to turn on
   plain-English explanations of recommendations; everything else works without it.
4. **Run the app:**

   ```bash
   streamlit run share_recommender/ui/app.py
   ```

   It opens at <http://localhost:8501>. Or use the **Run and Debug** panel: *Streamlit app*.
5. **Optional, the API:** `uvicorn share_recommender.api.main:app --reload`, then open
   <http://127.0.0.1:8000/docs>. Calls need `Authorization: Bearer <Supabase access token>`.

## Tests

```bash
pytest          # uses in-memory fakes; no Supabase or internet needed
ruff check .
```

## Project layout

```
share_recommender/
  core/       models, exchanges, valuation, interview answers, curated investment list
              and the rules-based recommender (pure Python, no network)
  data/       market data providers (Yahoo Finance first, pluggable)
  ai/         optional plain-English explanations from Claude
  recommendations.py   fetches prices, FX and dividend data, then runs the recommender
  portfolio_service.py gathers data for the dashboard and health check
  db/         Supabase client and repository (row-level security does the per-user scoping)
  api/        FastAPI app for a future web / mobile front end
  ui/         Streamlit app
supabase/migrations/   database schema
docs/                  architecture, data provider evaluation, Supabase setup
tests/
```

See [docs/architecture.md](docs/architecture.md) and
[docs/data-providers.md](docs/data-providers.md).
