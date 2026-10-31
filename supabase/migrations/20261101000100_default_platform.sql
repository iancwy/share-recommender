-- Week 4: each person's default platform for new holdings and imports (e.g. CMC Markets).
-- Empty means the app falls back to CMC Markets.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

alter table public.profiles
  add column if not exists default_platform text
    check (default_platform is null or length(btrim(default_platform)) between 1 and 50);
