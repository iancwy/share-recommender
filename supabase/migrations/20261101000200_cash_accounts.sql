-- Week 4: cash in high-interest savings accounts, entered manually.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

create table if not exists public.cash_accounts (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
  category_id    uuid,
  institution    text not null check (length(btrim(institution)) between 1 and 50), -- bank or platform
  account_name   text check (account_name is null or length(account_name) <= 100),
  balance        numeric(14, 2) not null check (balance >= 0),
  interest_rate  numeric(6, 3) not null default 0
                   check (interest_rate >= 0 and interest_rate <= 100),  -- % a year, e.g. 5.250
  currency       text not null default 'AUD' check (currency ~ '^[A-Z]{3}$'),
  as_of          date not null default current_date,   -- date the balance was last checked
  notes          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  -- An account can only use its owner's categories; deleting a category keeps the account.
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id)
);

create index if not exists cash_accounts_user_id_idx on public.cash_accounts (user_id);
create index if not exists cash_accounts_category_id_idx on public.cash_accounts (category_id);

drop trigger if exists cash_accounts_set_updated_at on public.cash_accounts;
create trigger cash_accounts_set_updated_at
  before update on public.cash_accounts
  for each row execute function public.set_updated_at();

alter table public.cash_accounts enable row level security;

drop policy if exists "Own cash: read" on public.cash_accounts;
drop policy if exists "Own cash: insert" on public.cash_accounts;
drop policy if exists "Own cash: update" on public.cash_accounts;
drop policy if exists "Own cash: delete" on public.cash_accounts;
create policy "Own cash: read"   on public.cash_accounts for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own cash: insert" on public.cash_accounts for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own cash: update" on public.cash_accounts for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own cash: delete" on public.cash_accounts for delete to authenticated
  using ((select auth.uid()) = user_id);

revoke all on public.cash_accounts from anon;
