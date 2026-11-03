-- Week 5: shared cache of company and fund fundamentals (market data only, never anyone's
-- holdings). Every signed-in user can read and add to it; nobody can delete from it.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

create table public.fundamentals_cache (
  exchange    text not null,
  ticker      text not null,
  data        jsonb not null check (jsonb_typeof(data) = 'object'),
  fetched_at  timestamptz not null default now(),
  primary key (exchange, ticker)
);

alter table public.fundamentals_cache enable row level security;

create policy "Fundamentals: read"   on public.fundamentals_cache for select to authenticated using (true);
create policy "Fundamentals: add"    on public.fundamentals_cache for insert to authenticated with check (true);
create policy "Fundamentals: update" on public.fundamentals_cache for update to authenticated
  using (true) with check (true);

revoke all on public.fundamentals_cache from anon;
revoke delete on public.fundamentals_cache from authenticated;
