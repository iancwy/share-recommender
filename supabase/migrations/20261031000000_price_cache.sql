-- Week 3: daily price and exchange-rate cache, so charts don't refetch years of history.
-- This is shared market data, not anyone's portfolio: every signed-in user can read and add
-- to it, and nobody can delete from it. Nothing is visible before sign-in.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

-- One closing price per instrument per trading day. Exchange rates use exchange 'FX' and a
-- ticker like 'AUDUSD' (units of USD per 1 AUD).
create table public.price_history (
  exchange  text not null,
  ticker    text not null,
  day       date not null,
  close     numeric(20, 8) not null check (close > 0),
  primary key (exchange, ticker, day)
);

-- Which date range has been fetched for each instrument, and when, so gaps such as
-- weekends and holidays aren't fetched again and again.
create table public.price_sync (
  exchange   text not null,
  ticker     text not null,
  first_day  date not null,
  last_day   date not null,
  synced_at  timestamptz not null default now(),
  primary key (exchange, ticker),
  check (first_day <= last_day)
);

alter table public.price_history enable row level security;
alter table public.price_sync    enable row level security;

create policy "Prices: read"   on public.price_history for select to authenticated using (true);
create policy "Prices: add"    on public.price_history for insert to authenticated with check (true);
create policy "Prices: update" on public.price_history for update to authenticated
  using (true) with check (true);

create policy "Price sync: read"   on public.price_sync for select to authenticated using (true);
create policy "Price sync: add"    on public.price_sync for insert to authenticated with check (true);
create policy "Price sync: update" on public.price_sync for update to authenticated
  using (true) with check (true);

revoke all on public.price_history, public.price_sync from anon;
revoke delete on public.price_history, public.price_sync from authenticated;
