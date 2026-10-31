-- Week 2: answers from the guided preferences interview.
-- Stored as JSON so questions can change without another migration; the app validates
-- the shape. Existing row-level security policies already limit both tables to their owner.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

alter table public.profiles
  add column preferences jsonb not null default '{}'::jsonb
    check (jsonb_typeof(preferences) = 'object');

alter table public.categories
  add column preferences jsonb not null default '{}'::jsonb
    check (jsonb_typeof(preferences) = 'object');

comment on column public.profiles.preferences is
  'Interview answers about the person (risk tolerance, markets, exclusions...). Empty = not done.';
comment on column public.categories.preferences is
  'Interview answers about this goal (time horizon, flexibility). Empty = not done.';
