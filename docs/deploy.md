# Deploy to Streamlit Community Cloud (for the iPhone)

The app runs free on Streamlit Community Cloud from the `main` branch. Cloud runs
`streamlit_app.py`, which runs the real app (`share_recommender/ui/app.py`) on every rerun,
and installs `requirements.txt`.

## Steps

1. **Merge `week-4` into `main`** and run every new migration in the Supabase SQL Editor
   (see step 2 of [supabase-setup.md](supabase-setup.md)).
2. Go to <https://share.streamlit.io>, sign in with GitHub, and allow access to private
   repositories when asked.
3. **Create app**, deploying from GitHub: repository `iancwy/share-recommender`, branch
   `main`, main file `streamlit_app.py`, and pick a custom subdomain (e.g. `ian-shares`).
   Under **Advanced settings**, choose Python 3.12 and paste the secrets:

   ```toml
   SUPABASE_URL = "https://<project-ref>.supabase.co"
   SUPABASE_ANON_KEY = "<anon / publishable key>"
   MARKET_DATA_PROVIDER = "yahoo"
   DEFAULT_EXCHANGE = "ASX"
   ANTHROPIC_API_KEY = "<optional>"
   ```

   **Never add the service role key here.** It's only for the notifications job, as a
   GitHub Actions secret. Cloud exposes these root-level secrets as environment variables,
   which is where the app reads its settings, so no code change is needed. Check it on the
   first deploy: if the app says Supabase isn't configured, the secrets didn't load.
4. In Supabase, **Authentication > URL Configuration**: set **Site URL** to the app's URL
   (e.g. `https://ian-shares.streamlit.app`) and add both that and `http://localhost:8501`
   under **Redirect URLs**, so confirmation and reset links work from either.
5. Confirm **Authentication > Sign In / Providers > Allow new users to sign up** is off
   (supabase-setup.md step 6), since the app's URL can be reached from the internet. Sign-in
   and row-level security protect the data. In the app's **Share** settings on Community
   Cloud, check who can view it; if it's private, add your and your wife's emails as viewers.
6. Add the app's URL as the `APP_URL` GitHub Actions secret, so notification emails link to it
   (see [notifications-setup.md](notifications-setup.md)).
7. On the iPhone: open the URL in **Safari**, sign in, then tap **Share > Add to Home Screen**.

## Checking it works

- The app loads from the home-screen icon.
- You and your wife can each sign in and see only your own data.
- Adding a holding and a cash account works.
- The dashboard shows prices.

## Known limits

- Community Cloud apps go to sleep after a while without visits; the first open after that
  takes up to a minute to wake.
- Yahoo Finance sometimes rate-limits cloud servers. If prices are blank on the deployed app
  but fine locally, that's the likely cause; the shared price cache helps for history.
- The home-screen icon comes from Streamlit, not the app's page icon.
- Sign-in lasts for a browser session, so reopening the app from the home screen may ask you
  to sign in again. Remembering the sign-in is a possible later improvement.
- An early commit (`ebaf962`) held a `.env` with only the Supabase URL and the publishable
  anon key, which are designed to be public behind row-level security. No action is needed,
  but it's a reminder never to commit the service role key.
