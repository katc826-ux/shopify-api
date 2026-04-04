import os
from flask import Flask, request, jsonify
import requests
import time
from datetime import datetime, timedelta, timezone
import re

app = Flask(__name__)

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
SHOP_DOMAIN = os.environ.get("SHOP_DOMAIN")

API_VERSION = "2026-01"

# Put your main Shopify location ID here after you fetch it once from /locations
DEFAULT_LOCATION_ID = os.environ.get("DEFAULT_LOCATION_ID")

_token_cache = {
    "access_token": None,
    "expires_at": 0
}


def normalize_text(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def get_access_token():
    now = time.time()

    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    token_url = f"https://{SHOP_DOMAIN}/admin/oauth/access_token"

    resp = requests.post(
        token_url,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=20,
    )

    if not resp.ok:
        raise Exception(f"Token request failed: {resp.status_code} {resp.text}")

    data = resp.json()

    access_token = data["access_token"]
    expires_in = data.get("expires_in", 86399)

    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + expires_in

    return access_token


def shopify_graphql(query, variables=None):
    token = get_access_token()
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/graphql.json"

    resp = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
        json={
            "query": query,
            "variables": variables or {}
        },
        timeout=30,
    )

    if not resp.ok:
        raise Exception(f"GraphQL request failed: {resp.status_code} {resp.text}")

    data = resp.json()

    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")

    return data["data"]


def search_products(product_name):
    query = """
    query GetInventory($search: String!) {
      products(first: 50, query: $search) {
        edges {
          node {
            id
            title
            variants(first: 50) {
              edges {
                node {
                  id
                  displayName
                  inventoryQuantity
                  price
                  compareAtPrice
                  inventoryItem {
                    id
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    search_term = normalize_text(product_name)
    data = shopify_graphql(query, {"search": search_term})
    products = data["products"]["edges"]

    matched_products = []

    for product_edge in products:
        product = product_edge["node"]
        normalized_title = normalize_text(product["title"])

        query_words = set(search_term.split())
        title_words = set(normalized_title.split())

        if search_term in normalized_title or len(query_words & title_words) > 0:
            matched_products.append(product)

    return matched_products


@app.route("/")
def home():
    return "API is running"


@app.route("/locations", methods=["GET"])
def get_locations():
    query = """
    query {
      locations(first: 20) {
        edges {
          node {
            id
            name
            isActive
          }
        }
      }
    }
    """

    data = shopify_graphql(query)

    return jsonify([
        {
            "id": edge["node"]["id"],
            "name": edge["node"]["name"],
            "is_active": edge["node"]["isActive"]
        }
        for edge in data["locations"]["edges"]
    ])


@app.route("/get_inventory", methods=["GET"])
def get_inventory():
    product_name = request.args.get("product", "").strip()

    if not product_name:
        return jsonify({"error": "Missing product parameter"}), 400

    matched_products = search_products(product_name)

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
                        "inventory": v["node"]["inventoryQuantity"],
                        "inventory_item_id": v["node"]["inventoryItem"]["id"],
                        "price": v["node"]["price"],
                        "compare_at_price": v["node"]["compareAtPrice"]
                    }
                    for v in product["variants"]["edges"]
                ]
            }
            for product in matched_products
        ]
    })


@app.route("/adjust_inventory", methods=["POST"])
def adjust_inventory():
    body = request.get_json() or {}

    product_name = (body.get("product") or "").strip()
    variant_name = (body.get("variant") or "").strip()
    delta = body.get("delta")
    location_id = (body.get("location_id") or DEFAULT_LOCATION_ID or "").strip()

    if not product_name:
        return jsonify({"error": "Missing product"}), 400

    if delta is None:
        return jsonify({"error": "Missing delta"}), 400

    try:
        delta = int(delta)
    except ValueError:
        return jsonify({"error": "delta must be an integer"}), 400

    if not location_id:
        return jsonify({"error": "Missing location_id and no DEFAULT_LOCATION_ID is set"}), 400

    matched_products = search_products(product_name)

    if not matched_products:
        return jsonify({"error": "Product not found"}), 404

    if len(matched_products) > 1 and not variant_name:
        return jsonify({
            "error": "Multiple matching products found. Please specify product more clearly or provide a variant.",
            "matches": [
                {
                    "title": product["title"],
                    "variants": [
                        {
                            "variant_name": v["node"]["displayName"],
                            "inventory": v["node"]["inventoryQuantity"]
                        }
                        for v in product["variants"]["edges"]
                    ]
                }
                for product in matched_products
            ]
        }), 409

    chosen_product = matched_products[0]

    # If there are multiple variants, try to match the requested variant
    variant_edges = chosen_product["variants"]["edges"]

    if not variant_edges:
        return jsonify({"error": "No variants found for this product"}), 404

    chosen_variant = None

    if variant_name:
        normalized_variant_query = normalize_text(variant_name)
        for v in variant_edges:
            display_name = v["node"]["displayName"]
            normalized_display_name = normalize_text(display_name)
            if (
                normalized_variant_query in normalized_display_name
                or normalized_display_name in normalized_variant_query
            ):
                chosen_variant = v["node"]
                break

        if not chosen_variant:
            return jsonify({
                "error": f"Variant '{variant_name}' not found",
                "product": chosen_product["title"],
                "available_variants": [
                    {
                        "variant_name": v["node"]["displayName"],
                        "inventory": v["node"]["inventoryQuantity"]
                    }
                    for v in variant_edges
                ]
            }), 404
    else:
        if len(variant_edges) == 1:
            chosen_variant = variant_edges[0]["node"]
        else:
            return jsonify({
                "error": "Multiple variants found. Please specify variant.",
                "product": chosen_product["title"],
                "available_variants": [
                    {
                        "variant_name": v["node"]["displayName"],
                        "inventory": v["node"]["inventoryQuantity"]
                    }
                    for v in variant_edges
                ]
            }), 409

    inventory_item_id = chosen_variant["inventoryItem"]["id"]

    mutation = """
    mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!) {
      inventoryAdjustQuantities(input: $input) {
        userErrors {
          field
          message
        }
        inventoryAdjustmentGroup {
          reason
          changes {
            name
            delta
          }
        }
      }
    }
    """

    variables = {
        "input": {
            "reason": "correction",
            "name": "available",
            "changes": [
                {
                    "inventoryItemId": inventory_item_id,
                    "locationId": location_id,
                    "delta": delta
                }
            ]
        }
    }

    result = shopify_graphql(mutation, variables)
    payload = result["inventoryAdjustQuantities"]

    if payload["userErrors"]:
        return jsonify({"error": payload["userErrors"]}), 400

    return jsonify({
        "success": True,
        "product": chosen_product["title"],
        "variant": chosen_variant["displayName"],
        "delta": delta,
        "location_id": location_id,
        "adjustment": payload["inventoryAdjustmentGroup"]
    })


@app.route("/top_selling", methods=["GET"])
def top_selling():
    since = datetime.now(timezone.utc) - timedelta(days=7)
    since_str = since.strftime("%Y-%m-%d")

    query = """
    query TopSelling($search: String!) {
      orders(first: 100, query: $search, sortKey: CREATED_AT, reverse: true) {
        edges {
          node {
            id
            name
            createdAt
            lineItems(first: 100) {
              edges {
                node {
                  name
                  quantity
                }
              }
            }
          }
        }
      }
    }
    """

    search_query = f"created_at:>={since_str}"

    data = shopify_graphql(query, {"search": search_query})

    sales = {}

    for order_edge in data["orders"]["edges"]:
        order = order_edge["node"]
        for item_edge in order["lineItems"]["edges"]:
            item = item_edge["node"]
            sales[item["name"]] = sales.get(item["name"], 0) + item["quantity"]

    top_items = sorted(sales.items(), key=lambda x: x[1], reverse=True)[:5]

    return jsonify([
        {"product": name, "units_sold": qty}
        for name, qty in top_items
    ])

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

        matched_products = search_products(product_name)

        if not matched_products:
            return jsonify({"error": "Product not found"}), 404

        if len(matched_products) > 1:
            return jsonify({
                "error": "Multiple matching products found. Please specify product more clearly or provide a variant.",
                "matches": [
                    {
                        "title": product["title"],
                        "variants": [
                            {"variant_name": v["node"]["displayName"]}
                            for v in product["variants"]["edges"]
                        ]
                    }
                    for product in matched_products
                ]
            }), 409

        chosen_product = matched_products[0]
        variant_edges = chosen_product["variants"]["edges"]

        chosen_variant = None
        if variant_name:
            normalized_query = normalize_text(variant_name)
            for v in variant_edges:
                name = v["node"]["displayName"]
                if normalized_query in normalize_text(name):
                    chosen_variant = v["node"]
                    break
        else:
            if len(variant_edges) == 1:
                chosen_variant = variant_edges[0]["node"]
            else:
                return jsonify({"error": "Multiple variants, specify one"}), 409

        if not chosen_variant:
            return jsonify({"error": "Variant not found"}), 404

        mutation = """
        mutation UpdateVariantPrice(
          $productId: ID!,
          $variants: [ProductVariantsBulkInput!]!
        ) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            product {
              id
              title
            }
            productVariants {
              id
              title
              price
              compareAtPrice
            }
            userErrors {
              field
              message
            }
          }
        }
        """

        variables = {
            "productId": chosen_product["id"],
            "variants": [
                {
                    "id": chosen_variant["id"],
                    "price": str(new_price),
                    "compareAtPrice": str(compare_price) if compare_price is not None else None
                }
            ]
        }

        result = shopify_graphql(mutation, variables)
        payload = result["productVariantsBulkUpdate"]

        if payload["userErrors"]:
            return jsonify({"error": payload["userErrors"]}), 400

        return jsonify({
            "success": True,
            "product": chosen_product["title"],
            "variant": chosen_variant["displayName"],
            "new_price": payload["productVariant"]["price"],
            "compare_at_price": payload["productVariant"]["compareAtPrice"]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
