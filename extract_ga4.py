import os
import json
from datetime import date, timedelta

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric
from google.oauth2 import service_account

import psycopg2
from psycopg2.extras import execute_values

GA4_PROPERTY_ID = os.environ["GA4_PROPERTY_ID"]          # без "properties/"
GA4_CREDENTIALS_JSON = os.environ["GA4_CREDENTIALS_JSON"]  # весь JSON service account как строка
DATABASE_URL = os.environ["DATABASE_URL"]


def get_ga4_client():
    creds_dict = json.loads(GA4_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def ga4_date_to_iso(d: str) -> str:
    """GA4 отдаёт дату как YYYYMMDD -> конвертируем в YYYY-MM-DD."""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


# ============================================================
# TRAFFIC
# ============================================================

def fetch_daily_traffic(client, date_from: str, date_to: str):
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
            Dimension(name="sessionCampaignName"),
            Dimension(name="deviceCategory"),
            Dimension(name="country"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="newUsers"),
            Metric(name="engagedSessions"),
            Metric(name="engagementRate"),
            Metric(name="averageSessionDuration"),
            Metric(name="bounceRate"),
            Metric(name="screenPageViews"),
        ],
    )
    return client.run_report(request)


def parse_traffic_rows(response):
    rows = []
    for row in response.rows:
        dims = [d.value for d in row.dimension_values]
        mets = [m.value for m in row.metric_values]
        report_date = ga4_date_to_iso(dims[0])
        rows.append((
            report_date, dims[1], dims[2], dims[3], dims[4], dims[5],
            int(mets[0]), int(mets[1]), int(mets[2]), int(mets[3]),
            float(mets[4]), float(mets[5]), float(mets[6]), int(mets[7]),
        ))
    return rows


def upsert_traffic(rows):
    if not rows:
        return 0
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    query = """
        INSERT INTO ga4_raw.daily_traffic
        (report_date, source, medium, campaign, device_category, country,
         sessions, total_users, new_users, engaged_sessions,
         engagement_rate, avg_session_duration, bounce_rate, screen_page_views)
        VALUES %s
        ON CONFLICT (report_date, source, medium, campaign, device_category, country)
        DO UPDATE SET
            sessions = EXCLUDED.sessions,
            total_users = EXCLUDED.total_users,
            new_users = EXCLUDED.new_users,
            engaged_sessions = EXCLUDED.engaged_sessions,
            engagement_rate = EXCLUDED.engagement_rate,
            avg_session_duration = EXCLUDED.avg_session_duration,
            bounce_rate = EXCLUDED.bounce_rate,
            screen_page_views = EXCLUDED.screen_page_views,
            ingested_at = now();
    """
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


# ============================================================
# EVENTS
# ============================================================

def fetch_daily_events(client, date_from: str, date_to: str):
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="eventName"),
        ],
        metrics=[
            Metric(name="eventCount"),
            Metric(name="totalUsers"),
        ],
    )
    return client.run_report(request)


def parse_events_rows(response):
    rows = []
    for row in response.rows:
        dims = [d.value for d in row.dimension_values]
        mets = [m.value for m in row.metric_values]
        report_date = ga4_date_to_iso(dims[0])
        rows.append((
            report_date, dims[1],
            int(mets[0]), int(mets[1]),
        ))
    return rows


def upsert_events(rows):
    if not rows:
        return 0
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    query = """
        INSERT INTO ga4_raw.daily_events
        (report_date, event_name, event_count, total_users)
        VALUES %s
        ON CONFLICT (report_date, event_name)
        DO UPDATE SET
            event_count = EXCLUDED.event_count,
            total_users = EXCLUDED.total_users,
            ingested_at = now();
    """
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


# ============================================================
# PAGES
# ============================================================

def fetch_daily_pages(client, date_from: str, date_to: str):
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="pagePath"),
        ],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="totalUsers"),
            Metric(name="userEngagementDuration"),
        ],
    )
    return client.run_report(request)


def parse_pages_rows(response):
    rows = []
    for row in response.rows:
        dims = [d.value for d in row.dimension_values]
        mets = [m.value for m in row.metric_values]
        report_date = ga4_date_to_iso(dims[0])
        views = int(mets[0])
        users = int(mets[1])
        engagement_duration = float(mets[2])
        avg_engagement = engagement_duration / users if users > 0 else 0
        rows.append((
            report_date, dims[1],
            views, users, round(avg_engagement, 2),
        ))
    return rows


def upsert_pages(rows):
    if not rows:
        return 0
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    query = """
        INSERT INTO ga4_raw.daily_pages
        (report_date, page_path, screen_page_views, total_users, avg_engagement_time)
        VALUES %s
        ON CONFLICT (report_date, page_path)
        DO UPDATE SET
            screen_page_views = EXCLUDED.screen_page_views,
            total_users = EXCLUDED.total_users,
            avg_engagement_time = EXCLUDED.avg_engagement_time,
            ingested_at = now();
    """
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


# ============================================================
# CONVERSIONS
# ============================================================

def fetch_daily_conversions(client, date_from: str, date_to: str):
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=date_from, end_date=date_to)],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="eventName"),
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
        ],
        metrics=[
            Metric(name="conversions"),
            Metric(name="totalRevenue"),
        ],
    )
    return client.run_report(request)


def parse_conversions_rows(response):
    rows = []
    for row in response.rows:
        dims = [d.value for d in row.dimension_values]
        mets = [m.value for m in row.metric_values]
        conversions = int(float(mets[0]))
        if conversions == 0:
            continue  # пропускаем строки без конверсий
        report_date = ga4_date_to_iso(dims[0])
        rows.append((
            report_date, dims[1], dims[2], dims[3],
            conversions, float(mets[1]),
        ))
    return rows


def upsert_conversions(rows):
    if not rows:
        return 0
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    query = """
        INSERT INTO ga4_raw.daily_conversions
        (report_date, conversion_event, source, medium, conversions, total_revenue)
        VALUES %s
        ON CONFLICT (report_date, conversion_event, source, medium)
        DO UPDATE SET
            conversions = EXCLUDED.conversions,
            total_revenue = EXCLUDED.total_revenue,
            ingested_at = now();
    """
    execute_values(cur, query, rows)
    conn.commit()
    cur.close()
    conn.close()
    return len(rows)


# ============================================================
# MARTS: daily_summary
# ============================================================

def refresh_daily_summary(date_from: str, date_to: str):
    """Пересчитывает агрегат ga4_marts.daily_summary за диапазон дат из raw-таблиц."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    query = """
        INSERT INTO ga4_marts.daily_summary
            (report_date, sessions, total_users, new_users, engagement_rate,
             conversions, conversion_rate, top_source, updated_at)
        SELECT
            t.report_date,
            SUM(t.sessions) AS sessions,
            SUM(t.total_users) AS total_users,
            SUM(t.new_users) AS new_users,
            AVG(t.engagement_rate) AS engagement_rate,
            COALESCE(c.total_conversions, 0) AS conversions,
            CASE WHEN SUM(t.sessions) > 0
                 THEN COALESCE(c.total_conversions, 0)::numeric / SUM(t.sessions)
                 ELSE 0 END AS conversion_rate,
            (SELECT source FROM ga4_raw.daily_traffic t2
             WHERE t2.report_date = t.report_date
             GROUP BY source ORDER BY SUM(sessions) DESC LIMIT 1) AS top_source,
            now()
        FROM ga4_raw.daily_traffic t
        LEFT JOIN (
            SELECT report_date, SUM(conversions) AS total_conversions
            FROM ga4_raw.daily_conversions
            WHERE report_date BETWEEN %s AND %s
            GROUP BY report_date
        ) c ON c.report_date = t.report_date
        WHERE t.report_date BETWEEN %s AND %s
        GROUP BY t.report_date, c.total_conversions
        ON CONFLICT (report_date) DO UPDATE SET
            sessions = EXCLUDED.sessions,
            total_users = EXCLUDED.total_users,
            new_users = EXCLUDED.new_users,
            engagement_rate = EXCLUDED.engagement_rate,
            conversions = EXCLUDED.conversions,
            conversion_rate = EXCLUDED.conversion_rate,
            top_source = EXCLUDED.top_source,
            updated_at = now();
    """
    cur.execute(query, (date_from, date_to, date_from, date_to))
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return updated


# ============================================================
# MAIN
# ============================================================

def main():
    client = get_ga4_client()

    # окно в 3 дня назад — под "дозревание" данных GA4 (data freshness lag)
    date_to = date.today().isoformat()
    date_from = (date.today() - timedelta(days=3)).isoformat()

    print(f"Extracting GA4 data for property {GA4_PROPERTY_ID}: {date_from} -> {date_to}")

    traffic_rows = parse_traffic_rows(fetch_daily_traffic(client, date_from, date_to))
    print(f"Upserted {upsert_traffic(traffic_rows)} rows into ga4_raw.daily_traffic")

    events_rows = parse_events_rows(fetch_daily_events(client, date_from, date_to))
    print(f"Upserted {upsert_events(events_rows)} rows into ga4_raw.daily_events")

    pages_rows = parse_pages_rows(fetch_daily_pages(client, date_from, date_to))
    print(f"Upserted {upsert_pages(pages_rows)} rows into ga4_raw.daily_pages")

    conv_rows = parse_conversions_rows(fetch_daily_conversions(client, date_from, date_to))
    print(f"Upserted {upsert_conversions(conv_rows)} rows into ga4_raw.daily_conversions")

    updated = refresh_daily_summary(date_from, date_to)
    print(f"Refreshed {updated} rows in ga4_marts.daily_summary")

    print("Done.")


if __name__ == "__main__":
    main()