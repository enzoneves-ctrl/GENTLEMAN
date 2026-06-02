"""
GeoQueue Weekly Report — Retail BR
Run: python geoqueue_weekly.py

Required env vars:
  SNOWFLAKE_USER
  SNOWFLAKE_PASSWORD        (or SNOWFLAKE_AUTHENTICATOR=externalbrowser for SSO)
  SNOWFLAKE_ACCOUNT         (e.g. RAPPIORG-HG51401)
  SNOWFLAKE_WAREHOUSE
  SNOWFLAKE_ROLE            (e.g. CPGS_ROLE)
"""
import os
import sys
import snowflake.connector

QUERY = """
WITH base AS (
    SELECT
        DAY,
        IS_GEO_QUEUE,
        IS_CANCELLED,
        VERTICAL_SUB_GROUP,
        BRAND,
        CASE
            WHEN DAY BETWEEN CURRENT_DATE - 7  AND CURRENT_DATE - 1 THEN 'wk'
            WHEN DAY BETWEEN CURRENT_DATE - 14 AND CURRENT_DATE - 8 THEN 'lw'
        END AS period
    FROM RP_SILVER_DB_PROD.OPS_BR.BR_ORDERS_PBI
    WHERE VERTICAL = 'RETAIL'
      AND DAY BETWEEN CURRENT_DATE - 14 AND CURRENT_DATE - 1
      AND IS_GEO_QUEUE = TRUE
)
SELECT
    period,
    COUNT(*)                                                        AS orders,
    SUM(CASE WHEN IS_CANCELLED THEN 1 ELSE 0 END)                   AS cancelled,
    ROUND(SUM(CASE WHEN IS_CANCELLED THEN 1 ELSE 0 END) * 100.0
        / NULLIF(COUNT(*), 0), 2)                                   AS cancel_rate
FROM base
WHERE period IS NOT NULL
GROUP BY period
ORDER BY period DESC;
"""

QUERY_BY_SUBVERTICAL = """
SELECT
    VERTICAL_SUB_GROUP,
    COUNT(*)                                                        AS orders,
    SUM(CASE WHEN IS_CANCELLED THEN 1 ELSE 0 END)                   AS cancelled,
    ROUND(SUM(CASE WHEN IS_CANCELLED THEN 1 ELSE 0 END) * 100.0
        / NULLIF(COUNT(*), 0), 2)                                   AS cancel_rate
FROM RP_SILVER_DB_PROD.OPS_BR.BR_ORDERS_PBI
WHERE VERTICAL = 'RETAIL'
  AND IS_GEO_QUEUE = TRUE
  AND DAY BETWEEN CURRENT_DATE - 7 AND CURRENT_DATE - 1
GROUP BY VERTICAL_SUB_GROUP
ORDER BY orders DESC;
"""


def connect():
    kwargs = dict(
        user=os.environ["SNOWFLAKE_USER"],
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
        role=os.environ.get("SNOWFLAKE_ROLE"),
    )
    auth = os.environ.get("SNOWFLAKE_AUTHENTICATOR")
    if auth:
        kwargs["authenticator"] = auth
    else:
        kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kwargs)


def run(conn, sql, title):
    print(f"\n=== {title} ===")
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)] or [0] * len(cols)
    print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)))
    return cols, rows


def main():
    try:
        conn = connect()
    except KeyError as e:
        sys.exit(f"missing env var: {e}")
    try:
        run(conn, QUERY, "GeoQueue — WoW (D-7..D-1 vs D-14..D-8)")
        run(conn, QUERY_BY_SUBVERTICAL, "GeoQueue — última semana por subvertical")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
