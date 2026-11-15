-- Hearth Home schema for Tiger Cloud (PostgreSQL 18 + TimescaleDB 2.32).
-- Everything lives in the `hearth` schema. Applied at startup, statement by statement; each
-- statement is idempotent (IF NOT EXISTS / if_not_exists). Statements are separated by a blank
-- line after the terminating semicolon.

CREATE SCHEMA IF NOT EXISTS hearth;

-- The only durable record of a check-in. Columns = HearthEvent.
-- Hypertable on ts, 7-day chunks, columnstore segmented by resident (TimescaleDB 2.20+ syntax;
-- the columnstore policy is created automatically, after one chunk interval).
CREATE TABLE IF NOT EXISTS hearth.events (
    id               text        NOT NULL,
    ts               timestamptz NOT NULL,
    resident_id      text        NOT NULL,
    checkin_id       text,
    for_kind         text,
    kind             text        NOT NULL,
    source           text        NOT NULL,
    confidence       double precision NOT NULL DEFAULT 1.0,
    summary          text        NOT NULL,
    evidence         text        NOT NULL DEFAULT '',
    amount           text,
    volume_ml_est    integer,
    med_steps        text[],
    said             text,
    concern_category text,
    PRIMARY KEY (id, ts)
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'ts',
    tsdb.chunk_interval = '7 days',
    tsdb.segmentby = 'resident_id',
    tsdb.orderby = 'ts DESC'
);

-- Migrate existing databases in place, preserving values and continuous-aggregate dependencies.
-- PostgreSQL tracks those dependencies by column identity, so a rename keeps the views valid.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'hearth' AND table_name = 'events' AND column_name = 'checkin_kind')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'hearth' AND table_name = 'events' AND column_name = 'for_kind') THEN
        ALTER TABLE hearth.events RENAME COLUMN checkin_kind TO for_kind;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS events_resident_ts ON hearth.events (resident_id, ts DESC);

CREATE TABLE IF NOT EXISTS hearth.residents (
    id   text PRIMARY KEY,
    doc  jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS hearth.schedule_items (
    id          text PRIMARY KEY,
    resident_id text NOT NULL,
    doc         jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS hearth.cues (
    id   text PRIMARY KEY,
    doc  jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS hearth.checkins (
    id          text PRIMARY KEY,
    resident_id text NOT NULL,
    created_at  timestamptz NOT NULL,
    status      text NOT NULL,
    doc         jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS checkins_status ON hearth.checkins (status, created_at DESC);

CREATE TABLE IF NOT EXISTS hearth.alerts (
    id          text PRIMARY KEY,
    resident_id text NOT NULL,
    ts          timestamptz NOT NULL,
    status      text NOT NULL,
    doc         jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS hearth.push_subscriptions (
    endpoint text PRIMARY KEY,
    doc      jsonb NOT NULL
);

-- Small settings (demo clock offset, which schedule items already ran today, fired retries).
CREATE TABLE IF NOT EXISTS hearth.kv (
    key   text PRIMARY KEY,
    value jsonb
);

-- Daily fluids, bucketed by the resident's local day (America/New_York). Real-time aggregation
-- (materialized_only = false) so today's events show up before the next refresh.
CREATE MATERIALIZED VIEW IF NOT EXISTS hearth.fluids_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 day', ts, 'America/New_York') AS day,
       resident_id,
       coalesce(sum(volume_ml_est) FILTER (WHERE kind = 'drink'), 0)::bigint AS fluid_ml,
       count(*) FILTER (WHERE kind = 'drink') AS drinks_confirmed,
       count(*) FILTER (WHERE kind = 'refused' AND coalesce(for_kind, 'drink') = 'drink') AS drinks_refused,
       count(*) FILTER (WHERE kind = 'missed') AS checkins_missed
FROM hearth.events
GROUP BY 1, 2
WITH NO DATA;

-- Daily medication / meal adherence (observed taking), same buckets.
CREATE MATERIALIZED VIEW IF NOT EXISTS hearth.adherence_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 day', ts, 'America/New_York') AS day,
       resident_id,
       count(*) FILTER (WHERE kind = 'med') AS meds_confirmed,
       count(*) FILTER (WHERE kind = 'meal') AS meals_confirmed,
       count(*) FILTER (WHERE kind = 'refused' AND for_kind = 'med') AS meds_refused
FROM hearth.events
GROUP BY 1, 2
WITH NO DATA;

SELECT add_continuous_aggregate_policy('hearth.fluids_daily',
    start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '10 minutes', if_not_exists => true);

SELECT add_continuous_aggregate_policy('hearth.adherence_daily',
    start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '10 minutes', if_not_exists => true);

-- Raw events are deleted after 90 days (the number on the privacy page).
SELECT add_retention_policy('hearth.events', drop_after => INTERVAL '90 days', if_not_exists => true);
