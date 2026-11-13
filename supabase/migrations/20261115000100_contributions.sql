-- Week 6: regular contributions, used in projections.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

-- Lets a contribution point at its owner's savings accounts only.
alter table public.cash_accounts
  add constraint cash_accounts_id_user_id_key unique (id, user_id);

create table public.contributions (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
  label            text check (label is null or length(label) <= 100),
  amount           numeric(14, 2) not null check (amount > 0),        -- base currency, each payment
  frequency        text not null
                     check (frequency in ('weekly', 'fortnightly', 'monthly', 'quarterly', 'yearly')),
  category_id      uuid,          -- the goal; null means the whole portfolio
  cash_account_id  uuid,          -- set when it's paid into a savings account
  start_date       date not null default current_date,
  end_date         date check (end_date is null or end_date >= start_date),
  active           boolean not null default true,   -- untick to leave it out of projections
  notes            text check (notes is null or length(notes) <= 1000),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id),
  foreign key (cash_account_id, user_id)
    references public.cash_accounts (id, user_id)
    on delete set null (cash_account_id)
);

create index contributions_user_id_idx on public.contributions (user_id);

create trigger contributions_set_updated_at
  before update on public.contributions
  for each row execute function public.set_updated_at();

alter table public.contributions enable row level security;

create policy "Own contributions: read"   on public.contributions for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own contributions: insert" on public.contributions for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own contributions: update" on public.contributions for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own contributions: delete" on public.contributions for delete to authenticated
  using ((select auth.uid()) = user_id);

revoke all on public.contributions from anon;
