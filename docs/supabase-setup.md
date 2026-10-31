# Supabase setup (about 10 minutes, once)

1. **Create a project.** Go to <https://supabase.com/dashboard>, sign in (GitHub login is fine,
   the same account as your Chews app works), click **New project**. Name it
   `share-recommender`, set a strong database password (save it in your password manager),
   pick the region closest to you (e.g. Sydney), and create it. It takes a minute or two.

2. **Run the schema.** In the project, open **SQL Editor > New query**, paste the whole of
   `supabase/migrations/20261024000000_initial_schema.sql`, and click **Run**. You should see
   "Success. No rows returned". Under **Table Editor** you will now see `profiles`,
   `categories` and `holdings`, each marked as RLS enabled.

   Then do the same with each later migration in `supabase/migrations/`, oldest first.
   Week 2 adds `20261025000000_preferences.sql` (interview answers); after running it,
   `profiles` and `categories` each have a `preferences` column. Week 3 adds
   `20261031000000_price_cache.sql`, which creates `price_history` and `price_sync`: a daily
   price cache shared by everyone who signs in (it holds market prices, not portfolios).
   Week 4 adds, in this order:
   - `20261101000100_default_platform.sql`: each person's default platform for new
     holdings.
   - `20261101000200_cash_accounts.sql`: cash in savings accounts (owner-only, like
     holdings).

   (If you prefer the Supabase CLI: `supabase link --project-ref <ref>` then `supabase db push`.)

3. **Copy the keys into `.env`.** Open **Project Settings > API** (or **API Keys**). Copy
   - the **Project URL** into `SUPABASE_URL`
   - the **anon / publishable** key into `SUPABASE_ANON_KEY`

   Do **not** use the `service_role` / secret key: it bypasses row-level security.

4. **Auth settings.** Under **Authentication > Sign In / Providers > Email**:
   - Email sign-in is on by default. Leave **Confirm email** on; Supabase's built-in email
     sender is rate limited but fine for two people.
   - Under **Authentication > URL Configuration**, set **Site URL** to
     `http://localhost:8501` so the confirmation link brings you back to the app.

5. **Create the two accounts.** Run the app (see README), use **Create account** for yourself,
   confirm the email, sign in. Repeat for your wife with her own email.

6. **Lock sign-ups (recommended).** Once both accounts exist, turn off
   **Authentication > Sign In / Providers > Allow new users to sign up**, so nobody else can
   create an account if the app is ever deployed publicly.

## Checking it works

- In **Table Editor > profiles** you should see one row per person.
- Add a holding as each person: each should only ever see their own.
- In the SQL editor, `select * from holdings;` shows everyone's rows because the editor runs
  as an admin. That is expected; the app always runs as the signed-in user.
