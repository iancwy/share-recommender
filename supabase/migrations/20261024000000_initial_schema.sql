-- Share recommender: initial schema (Week 1)
-- Tables: profiles, categories, holdings. Every row belongs to one auth user and
-- row-level security (RLS) makes sure people only ever see and change their own rows.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- profiles: one per login (e.g. Ian, and separately his wife)
-- ---------------------------------------------------------------------------

create table public.profiles (
  id             uuid primary key references auth.users (id) on delete cascade,
  display_name   text,
  base_currency  text not null default 'AUD' check (base_currency ~ '^[A-Z]{3}$'),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- categories: goal-based buckets such as "Kids private school investment fund"
-- ---------------------------------------------------------------------------

create table public.categories (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name           text not null check (length(btrim(name)) between 1 and 100),
  description    text,
  target_amount  numeric(14, 2) check (target_amount >= 0),
  target_date    date,
  risk_level     text not null default 'medium' check (risk_level in ('low', 'medium', 'high')),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (user_id, name),
  -- lets holdings reference (id, user_id) so a holding can only use its owner's categories
  unique (id, user_id)
);

create index categories_user_id_idx on public.categories (user_id);

create trigger categories_set_updated_at
  before update on public.categories
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- holdings: one row per purchase (parcel), entered manually
-- ---------------------------------------------------------------------------

create table public.holdings (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  category_id  uuid,
  ticker       text not null check (ticker = upper(btrim(ticker)) and length(ticker) between 1 and 20),
  exchange     text not null check (exchange = upper(exchange)),
  units        numeric(20, 6) not null check (units > 0),
  buy_price    numeric(20, 6) not null check (buy_price > 0),
  buy_date     date not null,
  fees         numeric(14, 2) not null default 0 check (fees >= 0),
  platform     text check (length(platform) <= 50),
  notes        text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  -- Deleting a category keeps its holdings and just clears category_id.
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id)
);

create index holdings_user_id_idx on public.holdings (user_id);
create index holdings_category_id_idx on public.holdings (category_id);
create index holdings_ticker_idx on public.holdings (exchange, ticker);

create trigger holdings_set_updated_at
  before update on public.holdings
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Row-level security: each person sees and edits only their own rows
-- ---------------------------------------------------------------------------

alter table public.profiles   enable row level security;
alter table public.categories enable row level security;
alter table public.holdings   enable row level security;

create policy "Own profile: read"   on public.profiles for select to authenticated
  using ((select auth.uid()) = id);
create policy "Own profile: update" on public.profiles for update to authenticated
  using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

create policy "Own categories: read"   on public.categories for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own categories: insert" on public.categories for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own categories: update" on public.categories for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own categories: delete" on public.categories for delete to authenticated
  using ((select auth.uid()) = user_id);

create policy "Own holdings: read"   on public.holdings for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own holdings: insert" on public.holdings for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own holdings: update" on public.holdings for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own holdings: delete" on public.holdings for delete to authenticated
  using ((select auth.uid()) = user_id);

-- Profiles are created by the trigger below, never directly by users.
revoke insert, delete on public.profiles from anon, authenticated;
-- Nothing is visible before sign-in.
revoke all on public.profiles, public.categories, public.holdings from anon;

-- ---------------------------------------------------------------------------
-- New sign-ups get a profile and a starter category automatically
-- ---------------------------------------------------------------------------

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1)));

  insert into public.categories (user_id, name, description, risk_level)
  values (new.id, 'General investing', 'Long-term wealth building with no fixed deadline.', 'medium');

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
