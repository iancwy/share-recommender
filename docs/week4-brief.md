# Week 4 build brief: notifications, cash, redesign and deploy

This brief is for Claude Code, running in VS Code on Ian's machine. Read all of it before you
start. Build the items **in the order given**, one commit per item, and run the tests after
each one. If something in here doesn't match the code you find, trust the code, tell Ian
what differed, and carry on with the closest sensible reading.

---

## 0. Before you start

### Where the code is

Weeks 1 to 3 live on the branch `claude/project-thread-q6cv74` (open PR #1, "Week 1:
architecture, Supabase schema and holdings entry", which also carries the Week 2 and Week 3
commits). **`main` only has the initial README.** Streamlit Community Cloud deploys from a
branch, so get everything onto `main` first:

1. Ask Ian to merge PR #1 into `main` on GitHub (or merge it locally if he says so).
2. `git checkout main && git pull`
3. `git checkout -b week-4`

### Set up and check the baseline

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt && pip install -e .
pytest && ruff check .
```

Both must pass before you change anything. If they don't, tell Ian and fix that first.

Then read `README.md`, `docs/architecture.md` and `docs/supabase-setup.md`.

### Ground rules (these are how the project has been built so far; keep them)

- **`core/` stays pure**: no network, no Supabase, no Streamlit. Fetching is passed in as
  functions (see `recommendations.py` and `portfolio_service.py`). Put new logic in `core/`
  and test it there.
- **Row-level security does the per-user scoping.** `db/repository.py` never filters by
  `user_id`; Postgres RLS does. Every new table gets RLS enabled, owner-only policies, and
  `revoke all ... from anon`.
- **The Supabase `service_role` key never goes near the Streamlit app**, its secrets, `.env`,
  or the repo. It is only for the scheduled notifications job (item 4), stored as a GitHub
  Actions secret.
- **Migrations are new files** in `supabase/migrations/`, named with a timestamp after
  `20261031000000`, e.g. `20261101000000_...sql`. Never edit an existing migration. Add each
  one to step 2 of `docs/supabase-setup.md`, and tell Ian to run it in the Supabase SQL
  Editor.
- **Tests use the in-memory fakes** in `tests/fakes.py` (no Supabase or internet). Extend
  `FakeRepository` for anything new. UI tests drive the app with `streamlit.testing.v1.AppTest`
  (see `tests/test_ui.py`).
- Defaults are **ASX and AUD**. Dates show as DD/MM/YYYY. UI text is plain English, short,
  and never sounds like personal financial advice (keep the existing `DISCLAIMER`).
- Keep it easy to run locally: anything new goes in `README.md` and `.env.example`.
- Don't add dependencies unless this brief says so. Email uses the standard library.

### What's already there (and what Week 4 touches)

| Area | Where | Week 4 change |
|---|---|---|
| Sign-in, profiles, categories, holdings (one row per purchase) | `supabase/migrations/20261024000000_initial_schema.sql`, `core/models.py`, `db/repository.py` | Add cash accounts, default platform, notification settings |
| Interview answers (`preferences` jsonb on `profiles` and `categories`) | `20261025000000_preferences.sql`, `core/preferences.py`, `core/questionnaire.py`, `ai/interview.py` | Remove "bonds" wording |
| Curated investment list and asset classes | `core/universe.py` (`AssetClass` has `BONDS`) | Remove bonds |
| Rules-based recommender (target mix, picks, gaps, buy plan) | `core/recommender.py`, `recommendations.py` | Defensive = cash only; cash accounts fill the cash slice |
| Dashboard, goal progress, health check | `core/performance.py`, `core/health.py`, `portfolio_service.py` | Count cash; reuse for notifications |
| CSV import | `core/importer.py` | Platform default |
| Price cache | `20261031000000_price_cache.sql`, `data/history.py`, `db/price_store.py` | Used by the job too |
| Streamlit UI (one file, sidebar radio of pages) | `ui/app.py` | New pages, redesign, mobile layout |
| FastAPI mirror of the same operations | `api/main.py` | Add cash and notifications endpoints |
| Notifications, cash, deploy, CI | nothing yet | All new |

Recommendations are **computed on the fly and not stored** anywhere today. Notifications
(item 4) therefore need a stored snapshot to tell what is "new".

---

## 1. Remove Bonds (asset classes are shares, ETFs and cash only)

Ian only holds and wants shares, ETFs and cash. In the code, "ETF" and "share" are the
instrument `Kind`, and `AssetClass` is what the money is in. After this item, `AssetClass` is
`AU_SHARES`, `GLOBAL_SHARES`, `EMERGING_SHARES` and `CASH`.

Places that mention bonds today (from `grep -rni bond`):

- `core/universe.py`: `AssetClass.BONDS`, its label, the `B` shorthand, bond ETFs `VAF`,
  `IAF`, `VGB`, `BND`, and the diversified funds `VDHG`, `VDGR`, `VDBA` (which hold bonds).
- `core/recommender.py`: module docstring, and `asset_mix()`, which splits the defensive part
  into cash and bonds by horizon.
- `core/health.py` line ~112: "Put new money into the cash and bond picks".
- `core/preferences.py`: `GoalStyle.CORE` comment; `core/questionnaire.py`: the CORE option
  label "a sensible mix of shares, bonds and cash".
- `ui/app.py`: metric "Bonds and cash (defensive)" on Recommendations, and the dashboard
  caption "3.5% for bonds and cash".
- `docs/architecture.md` (several lines), and tests in `tests/test_recommender.py` and
  `tests/test_health.py`.

What to do:

- Delete `AssetClass.BONDS` and the four bond ETFs.
- `asset_mix()`: the whole defensive part is cash (high-interest savings or a cash ETF such
  as AAA or BILL). Update the reason text to say so.
- Diversified funds: drop `VDGR` and `VDBA` (30% and 50% bonds). Keep `DHHF` (no bonds).
  For `VDHG`, count its 10% defensive slice as cash, with a code comment saying it is
  really fixed interest and is shown as cash because the app only tracks shares and cash.
  If Ian holds VDGR or VDBA he'll want them recognised, so ask him before removing them if
  either is in his holdings (check with him, you can't see his data).
- Keep `SGOV` as a cash ETF (it holds 0 to 3 month Treasury bills); its real fund name
  contains "Bond", which is fine.
- Rename the UI metric to "Cash (defensive)". Projections: "7% a year for shares and 3.5% for
  cash" (item 3 changes cash to use each account's own rate).
- Add a dashboard breakdown dimension **"Type"** with slices **Shares**, **ETFs** and **Cash**
  (from `Kind`, and cash accounts from item 3), since that's how Ian thinks about it.
  The existing "Asset class" breakdown stays.
- Nothing in the database stores bonds, so no migration is needed. Saved interview answers
  are unaffected.

**Acceptance criteria**

- `grep -rni bond share_recommender docs README.md` only finds the SGOV fund name and the
  VDHG comment.
- A long-horizon, medium-risk goal's target mix has no bonds slice and its defensive part is
  all cash; weights still add to 100.
- Tests updated (not deleted) to assert the new behaviour; `pytest` and `ruff check .` pass.

---

## 2. Platform: any text, with a changeable default

Today the platform is a `st.selectbox(..., accept_new_options=True)` in `_holding_form` and on
the Import page, so typing a new name already works if you know to. The default comes from
`COMMON_PLATFORMS = ["CMC Markets"]` plus platforms already used, and is effectively always
CMC Markets. Ian wants it obvious that he can type any platform, on new and existing
holdings, and to be able to replace the CMC Markets default.

What to do:

- Migration `..._default_platform.sql`: `alter table public.profiles add column
  default_platform text check (default_platform is null or length(btrim(default_platform))
  between 1 and 50);`. Add it to `Profile`, `PortfolioRepository.update_profile` and the
  Profile page ("Default platform for new holdings", free text).
- New holdings and imports default to the profile's `default_platform`; if that's empty,
  fall back to "CMC Markets". Existing holdings keep their own value.
- In the holding form (add and edit), label and help text make it clear you can type a new
  platform: e.g. placeholder "Choose or type any platform", help "Type a new name to add it."
  The user can also clear it (saved as no platform, shown as "No platform set").
- On the Holdings page, add **Rename a platform**: pick an existing platform, type a new name,
  and every holding (and cash account, item 3) on the old name moves to the new one, after a
  confirmation. Add `rename_platform(old, new)` to the repository (an update filtered by
  `platform = old`; RLS keeps it to the user's own rows).
- Mirror in the API: `PUT /me` accepts `default_platform`.

**Acceptance criteria**

- Adding a holding with a typed platform such as "Stake" saves "Stake"; editing an existing
  holding to "Pearler" saves "Pearler"; both show up in the platform list next time.
- After setting the default to "CommSec" on the Profile page, the add form and Import page
  preselect CommSec. With no default, they preselect CMC Markets.
- Renaming "CMC Markets" to "CMC Invest" updates every matching holding and nothing else.
- UI tests cover typing a new platform on add and on edit, and the default.

---

## 3. Cash holdings (high-interest savings accounts)

Manual entry of cash in savings accounts. Cash counts in the dashboard totals, in each
goal's allocation (it fills the recommender's cash slice), and in goal projections at its
own interest rate.

### Migration `..._cash_accounts.sql`

Follow the holdings table's pattern (including the composite foreign key so an account can
only use its owner's categories):

```sql
-- Week 4: cash in high-interest savings accounts, entered manually.
create table public.cash_accounts (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
  category_id    uuid,
  institution    text not null check (length(btrim(institution)) between 1 and 50), -- bank or platform
  account_name   text check (account_name is null or length(account_name) <= 100),
  balance        numeric(14, 2) not null check (balance >= 0),
  interest_rate  numeric(6, 3) not null default 0
                   check (interest_rate >= 0 and interest_rate <= 100),  -- % a year, e.g. 5.250
  currency       text not null default 'AUD' check (currency ~ '^[A-Z]{3}$'),
  as_of          date not null default current_date,   -- date the balance was last checked
  notes          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id)
);

create index cash_accounts_user_id_idx on public.cash_accounts (user_id);
create index cash_accounts_category_id_idx on public.cash_accounts (category_id);

create trigger cash_accounts_set_updated_at
  before update on public.cash_accounts
  for each row execute function public.set_updated_at();

alter table public.cash_accounts enable row level security;

create policy "Own cash: read"   on public.cash_accounts for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own cash: insert" on public.cash_accounts for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own cash: update" on public.cash_accounts for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own cash: delete" on public.cash_accounts for delete to authenticated
  using ((select auth.uid()) = user_id);

revoke all on public.cash_accounts from anon;
```

### Code

- `core/models.py`: `CashAccountIn` (institution, account_name, balance, interest_rate,
  currency default from the profile's base currency, as_of not in the future, category_id,
  notes) and `CashAccount`. Same validator style as `HoldingIn`.
- `db/repository.py`: `list_cash_accounts(category_id=None)`, `create_`, `update_`,
  `delete_cash_account`. `FakeRepository` gets the same.
- **Valuation**: cash is valued at its balance (converted to base currency if needed) and
  always counts as `AssetClass.CASH`. Pick one clean way to feed it into the existing code
  and use it everywhere: for example a small `CashValue` alongside `HoldingValue` in
  `core/performance.py`, plus a `cash_values()` helper, with `breakdown`, `goal_progress`,
  `assumed_return` and the health checks accepting both. Avoid faking cash as a ticker on an
  exchange; it leaks into price lookups and "not on the list" notes.
- **Recommender**: in `recommendations.build_recommendation`, the goal's cash accounts add to
  the cash slice in `analyse_gaps` (so the current mix and gaps include them). In
  `plan_purchase`, when the cash slice is underweight and the goal has a cash account,
  suggest "Add $X to <institution> (<rate>% a year)" rather than buying a cash ETF. Add a
  `cash_topups` list to `BuyPlan` for this rather than overloading `BuyOrder`.
- **Dashboard**: totals ("Portfolio value", "Money invested") include cash; gain is unchanged
  by cash. Breakdowns by goal, platform (institution), asset class (Cash) and type (Cash)
  include it. Value over time adds each account's balance as a flat line from its `as_of`
  date (there's no balance history); say so in the chart caption.
- **Goal progress**: cash grows at its own `interest_rate` instead of the 3.5% defensive
  assumption; shares keep 7%.
- **Health check**: cash is excluded from the one-company concentration check and from
  brokerage-fee percentages, but included in platform value shares. Optional, if quick: an
  info finding when a goal's cash earns well below the user's best account rate.
- **UI**: a **Cash** page (or a "Cash" tab on Holdings): add form, table of accounts with
  total, select a row to edit or delete (with the same "Yes, I want to delete" checkbox as
  holdings). Fields: Bank or platform (free text, suggests institutions already used),
  Account name (optional), Balance, Interest rate (% a year), Goal, Balance as at, Notes.
- **API**: `GET/POST /cash-accounts`, `PUT/DELETE /cash-accounts/{id}`.

**Acceptance criteria**

- Ian can add, edit and delete a cash account, and his wife can't see it (RLS; check by
  signing in as each).
- With A$10,000 of shares and a A$5,000 savings account in one goal, the dashboard shows
  A$15,000, the goal's current mix shows about 33% cash, and gaps and the buy plan reflect it.
- A goal's projection uses the account's rate for its cash.
- Deleting a goal keeps its cash accounts as uncategorised.
- Tests for the model validation, valuation, gaps with cash, buy plan top-up, goal progress
  and the UI add/edit/delete flow. `pytest` and `ruff check .` pass.

---

## 4. Notifications: email and in-app, per profile, on a schedule

Each person gets their own notifications about **new or changed recommendations** for their
goals, in the app and by email, on a schedule they choose. Scheduling is a GitHub Actions
cron job, which runs a Python module from this repo.

### What counts as "new"

For each goal (and once for the whole portfolio), build a small **snapshot**:

- the recommended picks: `{ticker, exchange, weight}` from `Recommendation.mix.picks`;
- the health findings at Warning or Alert severity for that goal: `{severity, title}`.

Compare it with the last stored snapshot. A notification is due when:

- a pick is added or removed, or a pick's weight changes by 5 points or more;
- a Warning or Alert finding appears that wasn't there last time (for example "Off its
  target mix by 12 points" or "Behind its target").

Findings that clear can be mentioned as good news in the same message, but never trigger one
on their own. The first run for a person only stores a baseline and sends nothing. Put this
logic in a new pure module `core/notifications.py` (`snapshot()`, `diff()`, `compose()`),
with thorough tests. `portfolio_service.health_report` already builds every goal's
recommendation; extend `HealthReport` to also return them so the job doesn't build them twice.

### Migration `..._notifications.sql`

```sql
-- Week 4: notification settings, last recommendation snapshot, and the in-app inbox.
alter table public.profiles
  add column notify_email     boolean not null default true,
  add column notify_frequency text    not null default 'weekly'
    check (notify_frequency in ('off', 'daily', 'weekly'));

-- Latest snapshot per person and scope ('portfolio' or a category id). Written only by the
-- scheduled job (service role); people can read their own.
create table public.recommendation_snapshots (
  user_id     uuid not null references auth.users (id) on delete cascade,
  scope       text not null,
  payload     jsonb not null check (jsonb_typeof(payload) = 'object'),
  created_at  timestamptz not null default now(),
  primary key (user_id, scope)
);

create table public.notifications (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users (id) on delete cascade,
  category_id  uuid references public.categories (id) on delete set null,
  kind         text not null default 'recommendation'
                 check (kind in ('recommendation', 'health', 'system')),
  title        text not null,
  body         text not null,
  payload      jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  read_at      timestamptz,
  emailed_at   timestamptz
);

create index notifications_user_created_idx on public.notifications (user_id, created_at desc);

alter table public.recommendation_snapshots enable row level security;
alter table public.notifications            enable row level security;

create policy "Own snapshots: read" on public.recommendation_snapshots for select
  to authenticated using ((select auth.uid()) = user_id);

create policy "Own notifications: read"   on public.notifications for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own notifications: update" on public.notifications for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own notifications: delete" on public.notifications for delete to authenticated
  using ((select auth.uid()) = user_id);

-- People can only mark notifications read; only the job (service role) creates them.
revoke all on public.recommendation_snapshots, public.notifications from anon;
revoke insert, update on public.notifications from authenticated;
grant update (read_at) on public.notifications to authenticated;
revoke insert, update, delete on public.recommendation_snapshots from authenticated;
```

### The scheduled job

- New package `share_recommender/jobs/` with `notify.py`, run as
  `python -m share_recommender.jobs.notify [--dry-run] [--user EMAIL] [--force]`.
  - `--dry-run`: print what would be sent and saved; write nothing, send nothing.
  - `--user`: only that person. `--force`: ignore their frequency (weekly people are
    otherwise only processed on Mondays, Sydney time).
- `db/admin.py`: `admin_client()` built from `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`,
  and an `AdminRepository` that reads one person's profile, categories, holdings, cash
  accounts and preferences and writes snapshots and notifications. **The service role
  bypasses RLS, so every query in `AdminRepository` must filter by `user_id` explicitly**,
  and there must be a test that checks it for each method (a fake client that records
  filters is enough). People and their emails come from `auth.admin.list_users()`.
- Settings for the job live in their own settings class (e.g. `JobSettings` in
  `config.py`), not in `Settings`, so the Streamlit app can never pick up the service key.
- For each person whose frequency is due: build the health report and recommendations
  (reuse `portfolio_service.health_report` with the provider functions from
  `recommendations.provider_fns`), snapshot, diff, and if there are changes, insert one
  notification per goal with changes, then send **one** email summarising them all (if
  `notify_email` is on), set `emailed_at`, and save the new snapshots. A failure for one
  person is logged and doesn't stop the others; the job exits non-zero at the end if any
  failed, so GitHub emails Ian about it.
- **Email** (`share_recommender/email.py` or similar): `smtplib` and `email.message`, plain
  text plus a simple HTML version, behind a small `EmailSender` interface with a fake for
  tests. Settings: `SMTP_HOST`, `SMTP_PORT` (587, STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`,
  `EMAIL_FROM`, and `APP_URL` for the "Open the app" link. Gmail works with an
  [app password](https://myaccount.google.com/apppasswords) (needs 2-step verification).
  Every email ends with the disclaimer and a line on how to change notification settings.

### GitHub Actions

`.github/workflows/notify.yml`:

```yaml
name: Notifications
on:
  schedule:
    - cron: "0 7 * * 1-5"   # 07:00 UTC = 6pm Sydney in summer (AEDT), 5pm in winter; after ASX close
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Dry run (send nothing)"
        type: boolean
        default: true
jobs:
  notify:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.txt && pip install -e .
      - run: python -m share_recommender.jobs.notify ${{ inputs.dry_run && '--dry-run' || '' }}
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
          APP_URL: ${{ secrets.APP_URL }}
```

Also add `.github/workflows/ci.yml` running `pytest` and `ruff check .` on pushes and pull
requests.

Tell Ian to add the secrets under **GitHub > repo > Settings > Secrets and variables >
Actions** (the service role key is in Supabase under **Project Settings > API Keys**, the
secret key). Then run the workflow by hand with dry run on, check the log, then with it off.

### In the app

- **Notifications** page, with the unread count in its navigation label (e.g.
  "Notifications (2)"): newest first, each shows the goal, title, body and date; unread
  ones stand out; "Mark as read", "Mark all as read" and "Delete" buttons. Only `read_at` is
  updated (the column grant enforces that).
- Optional but useful: a "Check for changes now" button that runs the same
  snapshot-and-diff for the signed-in user (through the normal RLS client) and shows what
  the next notification would say, without saving or emailing.
- **Profile** page: "Email me about new recommendations" (on/off) and "How often" (Off,
  Daily on weekdays, Weekly on Mondays).
- API: `GET /notifications`, `POST /notifications/{id}/read`, `DELETE /notifications/{id}`,
  and notification settings on `GET/PUT /me`.

**Acceptance criteria**

- Unit tests for `snapshot`/`diff`/`compose` cover: first run (baseline, no message), pick
  added, pick removed, weight change under and over 5 points, new Warning, cleared finding
  only (no message), and two goals changing (one email, two notifications).
- A job test with fake admin repo, fake provider and fake sender processes two people, sends
  each their own email only, respects `off`/`daily`/`weekly` and `--dry-run`, and keeps going
  when one person fails.
- Manually: a dry run in GitHub Actions lists both profiles; a real run sends Ian an email
  only about Ian's goals, and the notification appears on his Notifications page but not on
  his wife's.
- The service role key appears nowhere in the repo, `.env.example` (other than an empty,
  commented `SUPABASE_SERVICE_ROLE_KEY=` line explaining it is for running the job locally
  only), or Streamlit secrets.

---

## 5. Polish

Keep this to the list; no new features.

- Generalise the "database needs the Week 2 update" handler in `main()`: catch missing
  columns (`42703`) and missing tables (`42P01`) and say "The database is missing an update.
  Run any migrations in `supabase/migrations/` you haven't run yet (see
  docs/supabase-setup.md), then reload."
- Spinners (`st.spinner`) around slow price, history and recommendation fetches, and a
  friendly message when Yahoo returns nothing.
- Consistent money formatting through one helper (base currency code, thousands separators,
  no cents on big totals). Empty states on every page say what to do next.
- Show the disclaimer once in a consistent place (footer) on Dashboard, Recommendations,
  Health check and Notifications.
- Update `README.md` (Week 4 status, new env vars, how to run the job locally with
  `--dry-run`), `docs/architecture.md` (Week 4 section: cash, notifications, deploy;
  remove the bonds wording), `docs/supabase-setup.md` (new migrations, deployed Site URL) and
  `.env.example`.

**Acceptance criteria:** with a fresh database that's missing the Week 4 migrations, the app
shows the friendly message instead of a stack trace; all docs match the code.

---

## 6. Redesign from Ian's Claude Design style (placeholder: don't invent one)

Ian is designing the look in Claude Design and will supply it. **Do not make up colours,
fonts or layouts.** If `docs/design/` doesn't contain his design yet, do only the mobile and
structure work below, then stop and ask Ian for the design.

### Placeholder to create now

- `docs/design/README.md`: "Put the Claude Design export here (screenshots, HTML/CSS, or the
  colour and font list). Claude Code reads this folder to implement the redesign."
- `share_recommender/ui/theme.py`: one place for design tokens (colours, fonts, radius,
  spacing, chart palette), filled with **the current values** and a `# TODO: from Ian's
  design` marker. `PALETTE` and `SEVERITY_BADGE` in `app.py` move here.
- `.streamlit/config.toml` with a `[theme]` section, also holding current/default values for
  now. (Make sure `.gitignore` still ignores `.streamlit/secrets.toml` but not
  `config.toml`.)

### When the design arrives

- Map it onto `.streamlit/config.toml` `[theme]` options supported by the installed Streamlit
  version first, then `theme.py` tokens, and only then a small amount of CSS injected from
  `theme.py` for what the theme can't do. Keep CSS minimal: Streamlit's internal class names
  change between versions.
- Charts (Altair in `app.py`) take their colours from `theme.py`, in light and dark.
- Show Ian screenshots of each page (desktop and phone width) and ask before polishing
  further.

### Mobile-friendly layout (do this regardless of the design)

- Switch navigation from the sidebar radio to `st.navigation` with `st.Page` per page (gives
  each page its own URL, and works well on a phone). Use `position="top"` if the installed
  Streamlit supports it (bump `streamlit>=1.46` in `requirements.txt` if needed); otherwise
  keep the sidebar but start it collapsed. Update `tests/test_ui.py` for the new navigation.
- Rows of 4 metrics become 2 x 2. Forms use at most 2 columns per row (Streamlit stacks
  columns on narrow screens, but 4 narrow inputs per row are hard to tap).
- Wide tables (Holdings, Cash, Health "By platform") show the essential columns by default
  (e.g. Ticker, Units, Value, Gain %) with the full table in an expander.
- Charts use the container width and stay readable at 390px wide.
- `st.set_page_config` keeps a short `page_title` ("Shares") and an icon, as that's what the
  home-screen shortcut shows.
- Check at iPhone width (390 x 844) in the browser's device toolbar, and on Ian's iPhone
  over Wi-Fi: `streamlit run share_recommender/ui/app.py --server.address 0.0.0.0`, then open
  `http://<laptop IP>:8501` in Safari.

**Acceptance criteria:** no horizontal scrolling on any page at 390px except inside tables;
every button and input is tappable; the design (once supplied) is applied through
`config.toml` and `theme.py` with no colours hard-coded elsewhere; UI tests pass.

---

## 7. Deploy to Streamlit Community Cloud (for Ian's iPhone)

### Make the app deployable

- Community Cloud runs `streamlit run <file>` from the repo root and only adds the script's
  folder to the import path, so `import share_recommender` can fail. Add a root
  `streamlit_app.py` that runs the real app on every rerun:

  ```python
  """Entry point for Streamlit Community Cloud."""
  import runpy
  runpy.run_module("share_recommender.ui.app", run_name="__main__")
  ```

  (A plain `import` would only run once, because Python caches modules.) Check it works
  locally with `streamlit run streamlit_app.py`. Keep the VS Code launch config and README
  command working too.
- `requirements.txt` is what Cloud installs; make sure it has everything the app needs
  (`requirements-dev.txt` isn't used there).
- Settings are read by `pydantic-settings` from environment variables. Community Cloud
  exposes root-level secrets as environment variables, so no code change should be needed;
  confirm it on the first deploy.

### Steps for Ian (put these in `docs/deploy.md` and link from the README)

1. Merge `week-4` into `main` and run every new migration in the Supabase SQL Editor.
2. Go to <https://share.streamlit.io>, sign in with GitHub, and allow access to private
   repositories when asked.
3. **Create app**, deploying from GitHub: repository `iancwy/share-recommender`,
   branch `main`, main file `streamlit_app.py`, and pick a custom subdomain (e.g.
   `ian-shares`). Under **Advanced settings**, choose Python 3.12 and paste the secrets:

   ```toml
   SUPABASE_URL = "https://<project-ref>.supabase.co"
   SUPABASE_ANON_KEY = "<anon / publishable key>"
   MARKET_DATA_PROVIDER = "yahoo"
   DEFAULT_EXCHANGE = "ASX"
   ANTHROPIC_API_KEY = "<optional>"
   ```

   Never add the service role key here.
4. In Supabase, **Authentication > URL Configuration**: set **Site URL** to the app's URL
   (e.g. `https://ian-shares.streamlit.app`) and add both that and `http://localhost:8501`
   under **Redirect URLs**, so confirmation and reset links work from either.
5. Confirm **Allow new users to sign up** is off (docs/supabase-setup.md step 6), since the
   app URL is reachable from the internet. The sign-in and RLS protect the data. In the
   app's **Share** settings on Community Cloud, check who can view it; if it's private,
   add Ian's and his wife's emails as viewers.
6. Add the app URL as the `APP_URL` GitHub Actions secret (used in emails).
7. On the iPhone: open the URL in **Safari**, sign in, tap **Share > Add to Home Screen**.

### Known limits to tell Ian

- Community Cloud apps go to sleep after a while without visits; the first open after that
  takes up to a minute to wake.
- Yahoo Finance sometimes rate-limits cloud servers. If prices are blank on the deployed app
  but fine locally, say so to Ian; the price cache helps for history.
- The home-screen icon comes from Streamlit, not our `page_icon`.
- Sign-in is kept per browser session, so reopening the app from the home screen may ask to
  sign in again. Remembering the sign-in is a possible later improvement.
- The old `.env` that was committed early in history (commit `ebaf962`) held only the URL and
  the publishable anon key, which are designed to be public behind RLS. No action needed,
  but it's a reminder to never commit the service role key.

**Acceptance criteria:** the app at the `streamlit.app` URL loads on Ian's iPhone from the
home-screen icon; Ian and his wife can each sign in and see only their own data; adding a
holding and a cash account works there; the dashboard shows prices.

---

## Definition of done for Week 4

- Items 1 to 7 done in order, one commit each on `week-4`, `pytest` and `ruff check .`
  passing, CI green.
- All new migrations listed in `docs/supabase-setup.md`, and Ian has run them.
- README, architecture, setup and deploy docs updated; `.env.example` lists every setting.
- A pull request from `week-4` to `main` summarising what changed, what Ian must do (run
  migrations, add GitHub secrets, deploy), and anything left open (e.g. waiting on the
  design).
