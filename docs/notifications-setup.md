# Notifications setup (about 15 minutes, once)

A GitHub Actions job (`.github/workflows/notify.yml`) runs at 07:00 UTC on weekdays, which is
after the ASX closes (6pm Sydney in summer, 5pm in winter). For each person whose frequency
is due it compares their recommendations with the last check and, if anything changed,
adds notifications to their **Notifications** page and sends one email summarising them:

- a goal's suggested investments changed (added, removed, or a weight moved 5 points or more);
- a new Warning or Alert from the health check (cleared ones are mentioned as good news but
  never trigger a message on their own);
- a big price move in a holding (5% in a day for daily people, 10% in a week for weekly);
- a goal deadline reminder 2 years, 1 year, 6, 3 and 1 month(s) before its target date.

The first run for each person only records where things stand and sends nothing.

Each person chooses on their **Profile** page: **How often** (Off, Daily on weekdays, Weekly on
Mondays) and **Email me about new recommendations** (otherwise in-app only).

## 1. Run the migration

In Supabase, **SQL Editor > New query**, paste
`supabase/migrations/20261101000400_notifications.sql` and click **Run** (after the earlier
Week 4 migrations).

## 2. Create a Gmail app password

1. Your Google account needs **2-Step Verification** on
   (<https://myaccount.google.com/security>).
2. At <https://myaccount.google.com/apppasswords>, create one called "Share Recommender" and
   copy the 16 characters. Revoke it there any time.

## 3. Add the GitHub Actions secrets

**GitHub > iancwy/share-recommender > Settings > Secrets and variables > Actions > New
repository secret**:

| Name | Value |
|---|---|
| `SUPABASE_URL` | Same as in your `.env` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase **Project Settings > API Keys**, the secret (service_role) key |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASSWORD` | The app password from step 2 |
| `EMAIL_FROM` | e.g. `Share Recommender <you@gmail.com>` |
| `APP_URL` | The app's address, e.g. `https://ian-shares.streamlit.app` (see docs/deploy.md) |

**The service key bypasses row-level security.** Only the job uses it: it reads through
`db/admin.py`, where every query filters by person (tests check each one), and its settings
live in their own `JobSettings` class so the Streamlit app can never pick the key up. Keep it
only in GitHub secrets: never in Streamlit secrets or the repo.

## 4. Try it

**Actions > Notifications > Run workflow** with **Dry run** ticked: the log lists each person
and what would be sent, without sending or saving. Then run it with Dry run unticked. The
first real run stores baselines; you'll get emails from the next change on.

Locally (the service key in your shell or `.env`, never committed):

```bash
python -m share_recommender.jobs.notify --dry-run
python -m share_recommender.jobs.notify --dry-run --user you@example.com --force
```

`--force` runs weekly people on any day; anyone set to Off is always skipped.

In the app, **Notifications > Check for changes now** shows what the next notification would
say, without saving or emailing.

## Notes

- GitHub can start scheduled jobs a few minutes late, and pauses schedules on repositories
  with no activity for 60 days (a push or manual run restarts them).
- If a run fails for anyone, the job still processes the others, then fails, and GitHub
  emails you.
