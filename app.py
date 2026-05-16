import os
from flask import Flask, request, jsonify

from shopify_client import (
    search_products,
    search_products_with_inventory,
    get_locations_data,
    adjust_inventory_by_product,
    get_top_selling_products,
    update_price_by_product,
    get_weekly_dji_consumer_sales_report,
    get_sold_products_by_order_numbers,
)
from db import (
    init_db,
    create_promotion,
    list_promotions,
    get_promotion_by_id,
    cancel_promotion,
)

app = Flask(__name__)

with app.app_context():
    init_db()


@app.route("/")
def home():
    return "API is running"


@app.route("/locations", methods=["GET"])
def get_locations():
    try:
        return jsonify(get_locations_data())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get_inventory", methods=["GET"])
def get_inventory():
    try:
        product_name = request.args.get("product", "").strip()

        if not product_name:
            return jsonify({"error": "Missing product parameter"}), 400

        matched_products = search_products_with_inventory(product_name)

        if not matched_products:
            return jsonify({"error": "Product not found"}), 404

        return jsonify({
            "search_term": product_name,
            "matches": [
                {
                    "title": product["title"],
                    "variants": [
                        {
                            "variant_name": v["node"]["displayName"],
                            "variant_title": v["node"]["title"],
                            "sku": v["node"].get("sku"),
                            "inventory": v["node"]["inventoryQuantity"],
                            "price": v["node"]["price"],
                            "compare_at_price": v["node"]["compareAtPrice"],
                        }
                        for v in product["variants"]["edges"][:10]
                    ],
                }
                for product in matched_products[:5]
            ],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adjust_inventory", methods=["POST"])
def adjust_inventory():
    try:
        body = request.get_json() or {}

        product_name = (body.get("product") or "").strip()
        variant_name = (body.get("variant") or "").strip()
        delta = body.get("delta")
        location_id = (body.get("location_id") or "").strip()

        if not product_name:
            return jsonify({"error": "Missing product"}), 400

        if delta is None:
            return jsonify({"error": "Missing delta"}), 400

        result = adjust_inventory_by_product(
            product_name=product_name,
            variant_name=variant_name,
            delta=delta,
            location_id=location_id or None,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/top_selling", methods=["GET"])
def top_selling():
    try:
        days = request.args.get("days", default=7, type=int)
        if days < 1:
            return jsonify({"error": "days must be >= 1"}), 400

        results = get_top_selling_products(days=days, limit=5)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/orders/sold_products", methods=["POST"])
def sold_products_by_orders():
    try:
        body = request.get_json() or {}
        order_numbers = body.get("order_numbers")

        if not isinstance(order_numbers, list):
            return jsonify({"error": "order_numbers must be a list"}), 400

        return jsonify(get_sold_products_by_order_numbers(order_numbers))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reports/weekly_dji_consumer_sales", methods=["GET"])
def weekly_dji_consumer_sales():
    try:
        days = request.args.get("days", default=7, type=int)
        start_date = (request.args.get("start_date") or "").strip() or None
        end_date = (request.args.get("end_date") or "").strip() or None

        if not start_date and not end_date and days < 1:
            return jsonify({"error": "days must be >= 1"}), 400

        return jsonify(
            get_weekly_dji_consumer_sales_report(
                days=days,
                start_date=start_date,
                end_date=end_date,
            )
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/update_price", methods=["POST"])
def update_price():
    try:
        body = request.get_json() or {}

        product_name = (body.get("product") or "").strip()
        variant_name = (body.get("variant") or "").strip()
        new_price = body.get("price")
        compare_price = body.get("compare_at_price")

        if not product_name:
            return jsonify({"error": "Missing product"}), 400

        if new_price is None:
            return jsonify({"error": "Missing price"}), 400

        result = update_price_by_product(
            product_name=product_name,
            variant_name=variant_name,
            new_price=new_price,
            compare_price=compare_price,
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/promotions", methods=["POST"])
def add_promotion():
    """
    Creates a scheduled promotion record in the database.
    This does NOT immediately update Shopify.
    """
    try:
        body = request.get_json() or {}

        required_fields = [
            "product_title",
            "variant_title",
            "regular_price",
            "promo_price",
            "start_at",
            "end_at",
        ]
        missing = [f for f in required_fields if body.get(f) in (None, "")]
        if missing:
            return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

        promo_id = create_promotion(
            product_title=body["product_title"],
            variant_title=body["variant_title"],
            sku=body.get("sku"),
            regular_price=body["regular_price"],
            promo_price=body["promo_price"],
            start_at=body["start_at"],
            end_at=body["end_at"],
            timezone_name=body.get("timezone", "America/Toronto"),
            product_id=body.get("product_id"),
            variant_id=body.get("variant_id"),
        )

        return jsonify({
            "success": True,
            "promotion_id": promo_id,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/promotions", methods=["GET"])
def promotions():
    try:
        status = request.args.get("status")
        results = list_promotions(status=status)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/promotions/<int:promotion_id>", methods=["GET"])
def promotion_detail(promotion_id):
    try:
        promo = get_promotion_by_id(promotion_id)
        if not promo:
            return jsonify({"error": "Promotion not found"}), 404
        return jsonify(promo)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/promotions/<int:promotion_id>/cancel", methods=["POST"])
def cancel_promotion_route(promotion_id):
    try:
        promo = cancel_promotion(promotion_id)
        if not promo:
            return jsonify({"error": "Promotion not found or not cancellable"}), 404
        return jsonify({
            "success": True,
            "promotion_id": promotion_id,
            "status": promo["status"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
