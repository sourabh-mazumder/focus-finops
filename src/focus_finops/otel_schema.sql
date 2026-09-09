-- Simulated OpenTelemetry-style resource utilization metrics.
--
-- Deliberately a SEPARATE table from focus_cost_and_usage (that table's
-- structure is untouched) so cost data and utilization telemetry can be
-- joined on (resource_id, metric_day) without any FOCUS schema changes.
--
-- Long/narrow format -- one row per metric per resource per day -- matching
-- how OpenTelemetry metrics actually arrive (one data point per metric per
-- resource per timestamp), rather than a wide per-resource table. Column
-- names loosely follow OTel semantic conventions (metric names like
-- `system.cpu.utilization`; resource attributes like cloud.provider /
-- cloud.account.id) so the shape is recognizable, even though this data is
-- simulated rather than collected via a real OTel Collector.

CREATE TABLE IF NOT EXISTS otel_resource_metrics (
    id                   BIGSERIAL PRIMARY KEY,

    -- Resource attributes (mirrors the FOCUS resource this metric belongs
    -- to, so it can be joined back to focus_cost_and_usage on resource_id).
    resource_id          TEXT NOT NULL,
    resource_name        TEXT,
    resource_type        TEXT,
    service_category     TEXT,
    cloud_provider        TEXT,
    cloud_account_id      TEXT,
    cloud_account_name    TEXT,
    region_id             TEXT,

    -- The metric data point.
    metric_name           TEXT NOT NULL,   -- e.g. system.cpu.utilization
    metric_day            DATE NOT NULL,
    value                 NUMERIC(12, 4) NOT NULL,
    unit                  TEXT NOT NULL,   -- '%', 'count', ...

    -- Ingest metadata (not part of any OTel schema).
    source_file           TEXT,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_otel_resource_id ON otel_resource_metrics (resource_id);
CREATE INDEX IF NOT EXISTS idx_otel_metric_day   ON otel_resource_metrics (metric_day);
CREATE INDEX IF NOT EXISTS idx_otel_metric_name  ON otel_resource_metrics (metric_name);
