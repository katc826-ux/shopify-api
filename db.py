import os
from decimal import Decimal
from psycopg.rows import dict_row
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise Exception("Missing DATABASE_URL environment variable")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    sql = """
    CREATE TABLE IF NOT EXISTS scheduled_promotions (
        id SERIAL PRIMARY KEY,
        product_id TEXT,
        variant_id TEXT NOT NULL,
        sku TEXT,
        product_title TEXT NOT NULL,
        variant_title TEXT NOT NULL,
        regular_price NUMERIC(10,2) NOT NULL,
        promo_price NUMERIC(10,2) NOT NULL,
        start_at TIMESTAMPTZ NOT NULL,
        end_at TIMESTAMPTZ NOT NULL,
        timezone TEXT NOT NULL DEFAULT 'America/Toronto',
        start_applied BOOLEAN NOT NULL DEFAULT FALSE,
        end_applied BOOLEAN NOT NULL DEFAULT FALSE,
        status TEXT NOT NULL DEFAULT 'approved',
        last_error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def create_promotion(
    product_id,
    variant_id,
    sku,
    product_title,
    variant_title,
    regular_price,
    promo_price,
    start_at,
    end_at,
    timezone_name="America/Toronto",
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scheduled_promotions (
                    product_id,
                    variant_id,
                    sku,
                    product_title,
                    variant_title,
                    regular_price,
                    promo_price,
                    start_at,
                    end_at,
                    timezone
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    product_id,
                    variant_id,
                    sku,
                    product_title,
                    variant_title,
                    Decimal(str(regular_price)),
                    Decimal(str(promo_price)),
                    start_at,
                    end_at,
                    timezone_name,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def list_promotions(status=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    """
                    SELECT *
                    FROM scheduled_promotions
                    WHERE status = %s
                    ORDER BY id DESC
                    """,
                    (status,),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM scheduled_promotions
                    ORDER BY id DESC
                    """
                )
            rows = cur.fetchall()
    return rows


def get_promotion_by_id(promotion_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM scheduled_promotions
                WHERE id = %s
                """,
                (promotion_id,),
            )
            return cur.fetchone()


def get_due_promotion_starts():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM scheduled_promotions
                WHERE status = 'approved'
                  AND start_applied = FALSE
                  AND start_at <= NOW()
                ORDER BY start_at ASC
                """
            )
            return cur.fetchall()


def get_due_promotion_ends():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM scheduled_promotions
                WHERE status = 'approved'
                  AND start_applied = TRUE
                  AND end_applied = FALSE
                  AND end_at <= NOW()
                ORDER BY end_at ASC
                """
            )
            return cur.fetchall()

def cancel_promotion(promotion_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scheduled_promotions
                SET status = 'cancelled',
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'approved'
                RETURNING *
                """,
                (promotion_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def mark_start_applied(promotion_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scheduled_promotions
                SET start_applied = TRUE,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (promotion_id,),
            )
        conn.commit()


def mark_end_applied(promotion_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scheduled_promotions
                SET end_applied = TRUE,
                    status = 'completed',
                    updated_at = NOW()
                WHERE id = %s
                """,
                (promotion_id,),
            )
        conn.commit()


def mark_failed(promotion_id: int, error_message: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scheduled_promotions
                SET status = 'failed',
                    last_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error_message[:2000], promotion_id),
            )
        conn.commit()
