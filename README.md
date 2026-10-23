# share-recommender

Ian's private share and ETF recommender and portfolio dashboard.

**Week 1 status:** sign-in per person (Supabase Auth), goal categories, and manual holdings
entry with add / edit / delete, valued at current prices from Yahoo Finance.
Coming next: guided preferences interview and recommendations (Week 2), dashboard, health
check and CSV import (Week 3), notifications (Week 4).

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
  core/       models, exchanges, valuation (pure Python; recommender goes here)
  data/       market data providers (Yahoo Finance first, pluggable)
  db/         Supabase client and repository (row-level security does the per-user scoping)
  api/        FastAPI app for a future web / mobile front end
  ui/         Streamlit app
supabase/migrations/   database schema
docs/                  architecture, data provider evaluation, Supabase setup
tests/
```

See [docs/architecture.md](docs/architecture.md) and
[docs/data-providers.md](docs/data-providers.md).
