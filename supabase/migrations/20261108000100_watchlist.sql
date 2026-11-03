-- Week 5: shares and ETFs a person tracks without owning.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

create table public.watchlist (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  ticker       text not null check (ticker = upper(ticker) and length(btrim(ticker)) between 1 and 20),
  exchange     text not null check (exchange = upper(exchange)),
  category_id  uuid,                                   -- optional: the goal it's being watched for
  name         text check (name is null or length(name) <= 200),
  kind         text check (kind is null or kind in ('share', 'etf')),
  added_price  numeric(20, 6) check (added_price is null or added_price > 0),  -- price when added
  alert_below  numeric(20, 6) check (alert_below is null or alert_below > 0),  -- tell me at or below
  alert_above  numeric(20, 6) check (alert_above is null or alert_above > 0),  -- tell me at or above
  source       text not null default 'manual' check (source in ('manual', 'picker')),
  notes        text check (notes is null or length(notes) <= 1000),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (user_id, ticker, exchange),
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id)
);

create index watchlist_user_id_idx on public.watchlist (user_id);

create trigger watchlist_set_updated_at
  before update on public.watchlist
  for each row execute function public.set_updated_at();

alter table public.watchlist enable row level security;

create policy "Own watchlist: read"   on public.watchlist for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own watchlist: insert" on public.watchlist for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own watchlist: update" on public.watchlist for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own watchlist: delete" on public.watchlist for delete to authenticated
  using ((select auth.uid()) = user_id);

revoke all on public.watchlist from anon;
