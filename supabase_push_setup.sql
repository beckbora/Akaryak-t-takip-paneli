create extension if not exists pgcrypto;

create table if not exists public.push_subscriptions (
  endpoint_hash text primary key,
  endpoint text not null,
  p256dh text not null,
  auth text not null,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.push_subscriptions enable row level security;

revoke all on table public.push_subscriptions from anon, authenticated;
grant select, insert, update, delete on table public.push_subscriptions to service_role;

create or replace function public.register_push_subscription(
  p_endpoint text,
  p_p256dh text,
  p_auth text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_hash text;
begin
  if p_endpoint is null or length(p_endpoint) < 20 or length(p_endpoint) > 2500 or p_endpoint !~ '^https://' then
    raise exception 'invalid endpoint';
  end if;
  if p_p256dh is null or length(p_p256dh) < 20 or length(p_p256dh) > 500 then
    raise exception 'invalid p256dh';
  end if;
  if p_auth is null or length(p_auth) < 8 or length(p_auth) > 500 then
    raise exception 'invalid auth';
  end if;

  v_hash := encode(digest(p_endpoint, 'sha256'), 'hex');

  insert into public.push_subscriptions(endpoint_hash, endpoint, p256dh, auth, enabled, updated_at)
  values (v_hash, p_endpoint, p_p256dh, p_auth, true, now())
  on conflict (endpoint_hash) do update
    set endpoint = excluded.endpoint,
        p256dh = excluded.p256dh,
        auth = excluded.auth,
        enabled = true,
        updated_at = now();

  return jsonb_build_object('ok', true);
end;
$$;

create or replace function public.unregister_push_subscription(p_endpoint text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_hash text;
begin
  if p_endpoint is null or length(p_endpoint) < 20 then
    return jsonb_build_object('ok', true);
  end if;
  v_hash := encode(digest(p_endpoint, 'sha256'), 'hex');
  delete from public.push_subscriptions where endpoint_hash = v_hash;
  return jsonb_build_object('ok', true);
end;
$$;

revoke all on function public.register_push_subscription(text, text, text) from public;
revoke all on function public.unregister_push_subscription(text) from public;
grant execute on function public.register_push_subscription(text, text, text) to anon, authenticated, service_role;
grant execute on function public.unregister_push_subscription(text) to anon, authenticated, service_role;
