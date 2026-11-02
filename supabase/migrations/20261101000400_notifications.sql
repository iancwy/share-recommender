-- Week 4: notification settings, last recommendation snapshot, and the in-app inbox.
--
-- Run this once in the Supabase dashboard (SQL Editor > New query > paste > Run),
-- or with the Supabase CLI: `supabase db push`.

-- An earlier draft of Week 4 used different tables. Remove them if they exist.
drop table if exists public.notification_log;
drop table if exists public.notification_state;
alter table public.profiles drop column if exists notification_settings;

alter table public.profiles
  add column if not exists notify_email     boolean not null default true,
  add column if not exists notify_frequency text    not null default 'weekly'
    check (notify_frequency in ('off', 'daily', 'weekly'));

-- Latest snapshot per person and scope ('portfolio' or a category id). Written only by the
-- scheduled job (service role); people can read their own.
create table if not exists public.recommendation_snapshots (
  user_id     uuid not null references auth.users (id) on delete cascade,
  scope       text not null,
  payload     jsonb not null check (jsonb_typeof(payload) = 'object'),
  created_at  timestamptz not null default now(),
  primary key (user_id, scope)
);

create table if not exists public.notifications (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users (id) on delete cascade,
  category_id  uuid references public.categories (id) on delete set null,
  -- 'price_move' and 'deadline' are extras beyond recommendation changes, added for Ian.
  kind         text not null default 'recommendation'
                 check (kind in ('recommendation', 'health', 'price_move', 'deadline', 'system')),
  title        text not null,
  body         text not null,
  payload      jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  read_at      timestamptz,
  emailed_at   timestamptz
);

create index if not exists notifications_user_created_idx
  on public.notifications (user_id, created_at desc);

alter table public.recommendation_snapshots enable row level security;
alter table public.notifications            enable row level security;

drop policy if exists "Own snapshots: read" on public.recommendation_snapshots;
create policy "Own snapshots: read" on public.recommendation_snapshots for select
  to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "Own notifications: read" on public.notifications;
drop policy if exists "Own notifications: update" on public.notifications;
drop policy if exists "Own notifications: delete" on public.notifications;
create policy "Own notifications: read"   on public.notifications for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "Own notifications: update" on public.notifications for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Own notifications: delete" on public.notifications for delete to authenticated
  using ((select auth.uid()) = user_id);

-- People can only mark notifications read; only the job (service role) creates them.
revoke all on public.recommendation_snapshots, public.notifications from anon;
revoke insert, update on public.notifications from authenticated;
grant update (read_at) on public.notifications to authenticated;
revoke insert, update, delete on public.recommendation_snapshots from authenticated;
