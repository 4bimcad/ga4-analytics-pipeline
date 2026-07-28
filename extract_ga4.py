# extract_ga4.py
import os
import json
from datetime import date, timedelta
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric
from google.oauth2 import service_account
import psycopg2
from psycopg2.extras import execute_values

GA4_PROPERTY_ID = os.environ["GA4_PROPERTY_ID"]  # без "properties/"
GA4_CREDENTIALS_JSON = os.environ["GA4_CREDENTIALS_JSON"]  # весь JSON как строка (секрет в Actions)
DATABASE_URL = os.environ["DATABASE_URL"]

def get_ga4_client():
    creds_dict = json.loads(GA4_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    return BetaAnalyticsDataClient(credentials=credentials)

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
        report_date = f"{dims[0][:4]}-{dims[0][4:6]}-{dims[0][6:8]}"  # GA4 отдаёт YYYYMMDD
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

def main():
    client = get_ga4_client()
    # окно в 3 дня назад — под "дозревание" данных GA4
    date_to = date.today().isoformat()
    date_from = (date.today() - timedelta(days=3)).isoformat()

    response = fetch_daily_traffic(client, date_from, date_to)
    rows = parse_traffic_rows(response)
    count = upsert_traffic(rows)
    print(f"Upserted {count} rows into ga4_raw.daily_traffic")

if __name__ == "__main__":
    main()