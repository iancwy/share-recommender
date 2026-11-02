-- Week 4: dividends and distributions, one row per payment, entered by the person.
-- Written so it can be run even if an earlier draft already created the table.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

create table if not exists public.dividends (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
  ticker            text not null check (ticker = upper(btrim(ticker)) and length(ticker) between 1 and 20),
  exchange          text not null check (exchange = upper(exchange)),
  pay_date          date not null,
  amount            numeric(14, 2) not null check (amount > 0),   -- cash paid, exchange currency
  franking_credits  numeric(14, 2) not null default 0 check (franking_credits >= 0),
  withholding_tax   numeric(14, 2) not null default 0 check (withholding_tax >= 0),
  reinvested        boolean not null default false,              -- taken as new units (DRP)
  platform          text check (length(platform) <= 50),
  notes             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create index if not exists dividends_user_idx on public.dividends (user_id, pay_date desc);

drop trigger if exists dividends_set_updated_at on public.dividends;
create trigger dividends_set_updated_at
  before update on public.dividends
  for each row execute function public.set_updated_at();

alter table public.dividends enable row level security;

drop policy if exists "Own dividends: read" on public.dividends;
drop policy if exists "Own dividends: insert" on public.dividends;
drop policy if exists "Own dividends: update" on public.dividends;
drop policy if exists "Own dividends: delete" on public.dividends;
create policy "Own dividends: read"   on public.dividends for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own dividends: insert" on public.dividends for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own dividends: update" on public.dividends for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own dividends: delete" on public.dividends for delete to authenticated
  using ((select auth.uid()) = user_id);

revoke all on public.dividends from anon;
