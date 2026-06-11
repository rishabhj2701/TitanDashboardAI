-- Users table for local email/password and OAuth identities.
-- The app_data tables are created at runtime by
-- dynamic_analyst/storage/postgis/schema.py and do not need ALTERs here.

CREATE TABLE IF NOT EXISTS public.users (
    user_id       text PRIMARY KEY,
    provider      text NOT NULL,
    provider_id   text NOT NULL,
    email         text,
    name          text,
    avatar_url    text,
    password_hash text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_id)
);

CREATE INDEX IF NOT EXISTS users_provider_idx
    ON public.users (provider, provider_id);
