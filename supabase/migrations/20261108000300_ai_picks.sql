-- Week 5: AI stock picker runs and their picks, per person.
alter table public.profiles
  add column if not exists picker_frequency text not null default 'weekly'
    check (picker_frequency in ('off', 'weekly'));

create table public.pick_runs (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
  trigger        text not null check (trigger in ('manual', 'scheduled')),
  status         text not null default 'ok' check (status in ('ok', 'failed')),
  model          text,
  input_hash     text,                         -- skip re-running on unchanged inputs
  pool_size      integer not null default 0,
  shortlisted    integer not null default 0,
  input_tokens   integer,
  output_tokens  integer,
  summary        jsonb not null default '{}'::jsonb,  -- per-goal summaries, warnings, filter counts
  error          text,
  created_at     timestamptz not null default now(),
  unique (id, user_id)
);

create index pick_runs_user_created_idx on public.pick_runs (user_id, created_at desc);

create table public.ai_picks (
  id                uuid primary key default gen_random_uuid(),
  run_id            uuid not null,
  user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
  category_id       uuid,
  ticker            text not null,
  exchange          text not null,
  name              text,
  kind              text check (kind is null or kind in ('share', 'etf')),
  action            text not null check (action in ('buy', 'hold', 'review', 'watch')),
  conviction        text not null check (conviction in ('high', 'medium', 'low')),
  rank              integer not null check (rank between 1 and 50),
  score             numeric(5, 1),
  suggested_weight  numeric(5, 2) check (suggested_weight is null or suggested_weight between 0 and 100),
  rationale         text not null check (length(rationale) <= 2000),
  risks             text check (risks is null or length(risks) <= 1000),
  metrics           jsonb not null default '{}'::jsonb,
  source            text not null check (source in ('screen', 'held', 'watchlist', 'universe')),
  created_at        timestamptz not null default now(),
  foreign key (run_id, user_id) references public.pick_runs (id, user_id) on delete cascade,
  foreign key (category_id, user_id)
    references public.categories (id, user_id)
    on delete set null (category_id)
);

create index ai_picks_run_idx on public.ai_picks (run_id);
create index ai_picks_user_idx on public.ai_picks (user_id);

alter table public.pick_runs enable row level security;
alter table public.ai_picks  enable row level security;

create policy "Own pick runs: read"   on public.pick_runs for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own pick runs: insert" on public.pick_runs for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own pick runs: delete" on public.pick_runs for delete to authenticated
  using ((select auth.uid()) = user_id);

create policy "Own picks: read"   on public.ai_picks for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own picks: insert" on public.ai_picks for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "Own picks: delete" on public.ai_picks for delete to authenticated
  using ((select auth.uid()) = user_id);

-- Runs and picks are a record: nobody edits them after they're written.
revoke all on public.pick_runs, public.ai_picks from anon;
revoke update on public.pick_runs, public.ai_picks from authenticated;

-- New notification kinds for item 6. The Week 4 check constraint is the inline one on
-- notifications.kind; check its name first with:
--   select conname from pg_constraint where conrelid = 'public.notifications'::regclass;
alter table public.notifications drop constraint if exists notifications_kind_check;
alter table public.notifications add constraint notifications_kind_check
  check (kind in ('recommendation', 'health', 'price_move', 'deadline', 'system',
                  'ai_pick', 'watchlist'));
