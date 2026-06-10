import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

load_dotenv(Path(__file__).resolve().parent / ".env")

app = FastAPI(title="Change Card Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_QUERY = """
SELECT
  CASE
    WHEN s.name ILIKE '%%ufone%%' OR s.name ILIKE '%%zong%%'
      OR s.name ILIKE '%%telenor%%' OR s.name ILIKE '%%jazz%%'
    THEN 'Telco'
    ELSE 'UBP'
  END AS category,
  CASE cc.status
    WHEN 0 THEN 'Active'
    WHEN 1 THEN 'Blocked'
    WHEN 2 THEN 'Redeemed'
    WHEN 3 THEN 'Voided'
    WHEN 4 THEN 'Blocked'
    ELSE 'Unknown'
  END AS status_label,
  COUNT(cc.id_cashback_card) AS quantity,
  CASE
    WHEN cc.status = 2 THEN COALESCE(SUM(cc.nominal), 0) / 100.0
    ELSE COALESCE(SUM(cc.balance), 0) / 100.0
  END AS amount_pkr
FROM public.cashback_cards cc
JOIN public.cashback_card_operations cco ON cc.id_cashback_card = cco.id_cashback_card
JOIN operations.master om ON cco.id_operation = om.id_operation
JOIN public.services s ON om.id_service = s.id_service
WHERE cco.operation_type = 1
GROUP BY category, status_label, cc.status
ORDER BY category, status_label;
"""


def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5433")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        connect_timeout=30,
    )


def empty_bucket():
    return {"qty": 0, "balance": 0}


def empty_category():
    return {
        "active": empty_bucket(),
        "blocked": empty_bucket(),
        "redeemed": empty_bucket(),
        "voided": empty_bucket(),
    }


def rows_to_payload(rows):
    raw = {"telco": empty_category(), "ubp": empty_category()}
    status_map = {
        "Active": "active",
        "Blocked": "blocked",
        "Redeemed": "redeemed",
        "Voided": "voided",
    }

    for row in rows:
        category_key = "telco" if row["category"] == "Telco" else "ubp"
        status_key = status_map.get(row["status_label"])
        if not status_key:
            continue

        bucket = raw[category_key][status_key]
        bucket["qty"] += int(row["quantity"])
        bucket["balance"] += float(row["amount_pkr"] or 0)

    total_amount = 0.0
    for cat in raw.values():
        for status in cat.values():
            total_amount += status["balance"]

    return {"categories": raw, "totalAmount": total_amount}


@app.get("/api/health")
def health():
    return {"status": "ok"}


def fetch_rows():
    last_error = None
    for attempt in range(3):
        try:
            with get_connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(DASHBOARD_QUERY)
                    return cur.fetchall()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)
    raise last_error


@app.get("/api/dashboard")
def dashboard():
    try:
        rows = fetch_rows()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    payload = rows_to_payload(rows)
    return {
        "data": payload["categories"],
        "totalAmount": payload["totalAmount"],
        "fetchedAt": datetime.now(timezone.utc).astimezone().isoformat(),
    }
