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

_token_cache = {
    "access_token": None,
    "expires_at": 0
}

# 🔥 NEW: normalize function
def normalize_text(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)   # remove punctuation
    text = re.sub(r'\s+', ' ', text).strip()   # fix spacing
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


@app.route("/")
def home():
    return "API is running"


@app.route("/get_inventory", methods=["GET"])
def get_inventory():
    product_name = request.args.get("product", "").strip()

    if not product_name:
        return jsonify({"error": "Missing product parameter"}), 400

    query = """
    query GetInventory($search: String!) {
      products(first: 20, query: $search) {
        edges {
          node {
            id
            title
            variants(first: 20) {
              edges {
                node {
                  id
                  displayName
                  inventoryQuantity
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

    # 🔥 normalize input before search
    search_term = normalize_text(product_name)

    data = shopify_graphql(query, {"search": search_term})
    products = data["products"]["edges"]

    if not products:
        return jsonify({"error": "Product not found"}), 404

    best_match = None
    best_score = -1

    for product_edge in products:
        product = product_edge["node"]
        title = product["title"]

        normalized_title = normalize_text(title)

        # 🔥 scoring logic
        score = 0
        if search_term == normalized_title:
            score = 100
        elif search_term in normalized_title:
            score = 80
        else:
            query_words = set(search_term.split())
            title_words = set(normalized_title.split())
            score = len(query_words & title_words)

        if score > best_score:
            best_score = score
            best_match = product

    if not best_match or best_score <= 0:
        return jsonify({"error": "Product not found"}), 404

    variants = best_match["variants"]["edges"]

    return jsonify({
        "title": best_match["title"],
        "variants": [
            {
                "variant_name": v["node"]["displayName"],
                "inventory": v["node"]["inventoryQuantity"],
                "inventory_item_id": v["node"]["inventoryItem"]["id"]
            }
            for v in variants
        ]
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
