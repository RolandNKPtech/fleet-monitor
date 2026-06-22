-- DuckDB view definitions for the analytics data lake.
-- Run with:  duckdb -init skills/analytics/views.sql data/analytics/views.duckdb
-- Or from Python:
--   import duckdb
--   con = duckdb.connect('data/analytics/views.duckdb')
--   con.execute(open('skills/analytics/views.sql').read())
--
-- Views read directly from Parquet so they always reflect the latest data lake state.

-- =========================================================================
-- Base views
-- =========================================================================

CREATE OR REPLACE VIEW gsc_search_analytics AS
SELECT *
FROM read_parquet('data/analytics/gsc/search_analytics/**/*.parquet', union_by_name=true);

CREATE OR REPLACE VIEW meta_sites AS
SELECT * FROM read_parquet('data/analytics/meta/sites.parquet');

CREATE OR REPLACE VIEW meta_properties AS
SELECT * FROM read_parquet('data/analytics/meta/properties.parquet');

CREATE OR REPLACE VIEW meta_token_health AS
SELECT * FROM read_parquet('data/analytics/meta/token_health.parquet');

-- GA4 base views
CREATE OR REPLACE VIEW ga4_property_metrics AS
SELECT * FROM read_parquet('data/analytics/ga4/property_metrics/**/*.parquet', union_by_name=true);

CREATE OR REPLACE VIEW ga4_traffic_sources AS
SELECT * FROM read_parquet('data/analytics/ga4/traffic_sources/**/*.parquet', union_by_name=true);

CREATE OR REPLACE VIEW ga4_top_pages AS
SELECT * FROM read_parquet('data/analytics/ga4/top_pages/**/*.parquet', union_by_name=true);

-- =========================================================================
-- Canned reports
-- =========================================================================

-- Daily totals per site
CREATE OR REPLACE VIEW gsc_daily_site_totals AS
SELECT
    host,
    date,
    sum(clicks)      AS clicks,
    sum(impressions) AS impressions,
    CASE WHEN sum(impressions) = 0 THEN 0
         ELSE sum(clicks) * 1.0 / sum(impressions) END AS ctr,
    avg(position)    AS avg_position
FROM gsc_search_analytics
GROUP BY host, date;

-- Top queries by clicks, fleet-wide, last 28 days
CREATE OR REPLACE VIEW gsc_top_queries_28d AS
SELECT
    query,
    sum(clicks)      AS clicks,
    sum(impressions) AS impressions,
    count(DISTINCT host) AS sites_ranking_for_query
FROM gsc_search_analytics
WHERE date >= current_date - INTERVAL 28 DAY
  AND query IS NOT NULL AND query <> ''
GROUP BY query
ORDER BY clicks DESC
LIMIT 500;

-- Per-site top queries last 28 days
CREATE OR REPLACE VIEW gsc_site_top_queries_28d AS
SELECT
    host,
    query,
    sum(clicks)      AS clicks,
    sum(impressions) AS impressions,
    CASE WHEN sum(impressions) = 0 THEN 0
         ELSE sum(clicks) * 1.0 / sum(impressions) END AS ctr,
    avg(position)    AS avg_position
FROM gsc_search_analytics
WHERE date >= current_date - INTERVAL 28 DAY
GROUP BY host, query;

-- GA4: fleet daily totals
CREATE OR REPLACE VIEW ga4_fleet_daily AS
SELECT
    date,
    count(DISTINCT property_id) AS active_properties,
    sum(sessions) AS sessions,
    sum(total_users) AS users,
    sum(screen_page_views) AS page_views,
    sum(conversions) AS conversions
FROM ga4_property_metrics
GROUP BY date
ORDER BY date;

-- GA4: top traffic sources fleet-wide last 28 days
CREATE OR REPLACE VIEW ga4_top_sources_28d AS
SELECT
    session_source,
    session_medium,
    session_default_channel_group,
    sum(sessions) AS sessions,
    sum(active_users) AS users,
    count(DISTINCT property_id) AS properties
FROM ga4_traffic_sources
WHERE date >= current_date - INTERVAL 28 DAY
GROUP BY session_source, session_medium, session_default_channel_group
ORDER BY sessions DESC
LIMIT 50;

-- Week-over-week clicks per site
CREATE OR REPLACE VIEW gsc_wow_clicks AS
WITH last_14 AS (
    SELECT host, date, sum(clicks) AS clicks
    FROM gsc_search_analytics
    WHERE date >= current_date - INTERVAL 14 DAY
    GROUP BY host, date
)
SELECT
    host,
    sum(CASE WHEN date >= current_date - INTERVAL 7 DAY THEN clicks ELSE 0 END) AS clicks_last_7d,
    sum(CASE WHEN date <  current_date - INTERVAL 7 DAY THEN clicks ELSE 0 END) AS clicks_prev_7d,
    sum(CASE WHEN date >= current_date - INTERVAL 7 DAY THEN clicks ELSE 0 END)
        - sum(CASE WHEN date <  current_date - INTERVAL 7 DAY THEN clicks ELSE 0 END) AS delta
FROM last_14
GROUP BY host
ORDER BY delta DESC;
