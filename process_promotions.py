from db import (
    init_db,
    get_due_promotion_starts,
    get_due_promotion_ends,
    mark_start_applied,
    mark_end_applied,
    mark_failed,
)
from shopify_client import update_price_by_variant_id


def process_start_promotions():
    due = get_due_promotion_starts()

    for promo in due:
        try:
            result = update_price_by_variant_id(
                product_id=promo["product_id"],
                variant_id=promo["variant_id"],
                product_title=promo["product_title"],
                variant_title=promo["variant_title"],
                new_price=promo["promo_price"],
                compare_price=promo["regular_price"],
            )
            mark_start_applied(promo["id"])
            print(f"START OK | promo_id={promo['id']} | {result}")
        except Exception as e:
            mark_failed(promo["id"], f"start failed: {str(e)}")
            print(f"START FAILED | promo_id={promo['id']} | error={e}")


def process_end_promotions():
    due = get_due_promotion_ends()

    for promo in due:
        try:
            result = update_price_by_variant_id(
                product_id=promo["product_id"],
                variant_id=promo["variant_id"],
                product_title=promo["product_title"],
                variant_title=promo["variant_title"],
                new_price=promo["regular_price"],
                compare_price=None,
            )
            mark_end_applied(promo["id"])
            print(f"END OK | promo_id={promo['id']} | {result}")
        except Exception as e:
            mark_failed(promo["id"], f"end failed: {str(e)}")
            print(f"END FAILED | promo_id={promo['id']} | error={e}")


def main():
    init_db()
    process_start_promotions()
    process_end_promotions()


if __name__ == "__main__":
    main()
