-- Tour of Belize — Supabase schema.
-- Run once in Supabase: Dashboard → SQL Editor → New query → paste → Run.

create table if not exists kv (
  key   text primary key,
  value jsonb
);

create table if not exists daily_sales (
  day      text,
  rider_id text,
  ld       integer,
  primary key (day, rider_id)
);

create table if not exists profiles (
  id       text primary key,
  nickname text default '',
  quote    text default '',
  photo    text default ''
);

create table if not exists ingest_log (
  ts          text primary key,
  riders_json jsonb
);

-- The backend uses the SECRET (service) key, which bypasses Row Level Security,
-- so no RLS policies are required. Viewers never hit Supabase directly.
