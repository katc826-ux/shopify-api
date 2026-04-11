import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
SHOP_DOMAIN = os.environ.get("SHOP_DOMAIN")
DEFAULT_LOCATION_ID = os.environ.get("DEFAULT_LOCATION_ID")
API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-01")

_token_cache = {
    "access_token": None,
    "expires_at": 0,
}


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_access_token() -> str:
    now = time.time()

    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    if not CLIENT_ID or not CLIENT_SECRET or not SHOP_DOMAIN:
        raise Exception("Missing CLIENT_ID, CLIENT_SECRET, or SHOP_DOMAIN environment variables")

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


def shopify_graphql(query: str, variables=None):
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
            "variables": variables or {},
        },
        timeout=30,
    )

    if not resp.ok:
        raise Exception(f"GraphQL request failed: {resp.status_code} {resp.text}")

    data = resp.json()

    if "errors" in data:
        raise Exception(f"GraphQL errors: {data['errors']}")

    return data["data"]


def search_products(product_name: str, limit_products: int = 15, limit_variants: int = 15):
    query = """
    query SearchProducts($search: String!, $productLimit: Int!, $variantLimit: Int!) {
      products(first: $productLimit, query: $search) {
        edges {
          node {
            id
            title
            variants(first: $variantLimit) {
              edges {
                node {
                  id
                  title
                  displayName
                  sku
                  price
                  compareAtPrice
                }
              }
            }
          }
        }
      }
    }
    """

    search_term = normalize_text(product_name)
    data = shopify_graphql(
        query,
        {
            "search": search_term,
            "productLimit": limit_products,
            "variantLimit": limit_variants,
        },
    )

    products = data["products"]["edges"]
    matched_products = []

    for product_edge in products:
        product = product_edge["node"]
        normalized_title = normalize_text(product["title"])

        if normalized_title == search_term or search_term in normalized_title:
            matched_products.append(product)

    return matched_products


def search_products_with_inventory(product_name: str, limit_products: int = 15, limit_variants: int = 25):
    query = """
    query SearchProductsWithInventory($search: String!, $productLimit: Int!, $variantLimit: Int!) {
      products(first: $productLimit, query: $search) {
        edges {
          node {
            id
            title
            variants(first: $variantLimit) {
              edges {
                node {
                  id
                  title
                  displayName
                  sku
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
    data = shopify_graphql(
        query,
        {
            "search": search_term,
            "productLimit": limit_products,
            "variantLimit": limit_variants,
        },
    )

    products = data["products"]["edges"]
    matched_products = []

    for product_edge in products:
        product = product_edge["node"]
        normalized_title = normalize_text(product["title"])

        if normalized_title == search_term or search_term in normalized_title:
            matched_products.append(product["node"] if "node" in product else product)

    return matched_products
    

def get_locations_data():
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

    return [
        {
            "id": edge["node"]["id"],
            "name": edge["node"]["name"],
            "is_active": edge["node"]["isActive"],
        }
        for edge in data["locations"]["edges"]
    ]


def _variant_title_matches(variant_node, variant_name: str) -> bool:
    if not variant_name:
        return True

    normalized_variant_query = normalize_text(variant_name)
    candidate_values = [
        variant_node.get("title", ""),
        variant_node.get("displayName", ""),
    ]

    for value in candidate_values:
        normalized_value = normalize_text(value)
        if (
            normalized_variant_query == normalized_value
            or normalized_variant_query in normalized_value
            or normalized_value in normalized_variant_query
        ):
            return True

    return False


def _choose_variant(product, variant_name: str = "", sku: str = None):
    variant_edges = product["variants"]["edges"]

    if not variant_edges:
        raise LookupError("No variants found for this product")

    if sku:
        normalized_sku = normalize_text(sku)
        sku_matches = [
            edge["node"]
            for edge in variant_edges
            if normalize_text(edge["node"].get("sku", "")) == normalized_sku
        ]

        if variant_name:
            sku_and_variant_matches = [
                node for node in sku_matches if _variant_title_matches(node, variant_name)
            ]
            if len(sku_and_variant_matches) == 1:
                return sku_and_variant_matches[0]
            if len(sku_and_variant_matches) > 1:
                raise RuntimeError(
                    f"Multiple variants matched SKU '{sku}' and variant '{variant_name}'"
                )

        if len(sku_matches) == 1:
            return sku_matches[0]
        if len(sku_matches) > 1:
            raise RuntimeError(f"Multiple variants matched SKU '{sku}'")

    if variant_name:
        variant_matches = [
            edge["node"]
            for edge in variant_edges
            if _variant_title_matches(edge["node"], variant_name)
        ]

        if len(variant_matches) == 1:
            return variant_matches[0]
        if len(variant_matches) > 1:
            raise RuntimeError(f"Multiple variants matched variant '{variant_name}'")

        raise LookupError(f"Variant '{variant_name}' not found")

    if len(variant_edges) == 1:
        return variant_edges[0]["node"]

    raise RuntimeError("Multiple variants found. Please specify variant or SKU.")


def _choose_product(matched_products, product_name: str, variant_name: str = "", sku: str = None):
    if not matched_products:
        raise LookupError("Product not found")

    normalized_product_query = normalize_text(product_name)

    exact_title_matches = [
        product
        for product in matched_products
        if normalize_text(product["title"]) == normalized_product_query
    ]

    candidates = exact_title_matches or matched_products

    if len(candidates) == 1:
        return candidates[0]

    narrowed = []

    for product in candidates:
        try:
            _choose_variant(product, variant_name=variant_name, sku=sku)
            narrowed.append(product)
        except Exception:
            continue

    if len(narrowed) == 1:
        return narrowed[0]

    if len(narrowed) > 1:
        raise RuntimeError(
            "Multiple matching products found. Please use a more exact product title or add SKU."
        )

    raise LookupError("Product found, but no matching variant/SKU was found")


def adjust_inventory_by_product(product_name: str, variant_name: str, delta, location_id: str = None):
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        raise ValueError("delta must be an integer")

    chosen_location_id = (location_id or DEFAULT_LOCATION_ID or "").strip()
    if not chosen_location_id:
        raise ValueError("Missing location_id and no DEFAULT_LOCATION_ID is set")

    matched_products = search_products(product_name)
    chosen_product = _choose_product(matched_products, product_name, variant_name)
    chosen_variant = _choose_variant(chosen_product, variant_name)

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
                    "locationId": chosen_location_id,
                    "delta": delta,
                }
            ],
        }
    }

    result = shopify_graphql(mutation, variables)
    payload = result["inventoryAdjustQuantities"]

    if payload["userErrors"]:
        raise Exception(payload["userErrors"])

    return {
        "success": True,
        "product": chosen_product["title"],
        "variant": chosen_variant["displayName"],
        "delta": delta,
        "location_id": chosen_location_id,
        "adjustment": payload["inventoryAdjustmentGroup"],
    }


def get_top_selling_products(days: int = 7, limit: int = 5):
    since = datetime.now(timezone.utc) - timedelta(days=days)
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

    top_items = sorted(sales.items(), key=lambda x: x[1], reverse=True)[:limit]

    return [{"product": name, "units_sold": qty} for name, qty in top_items]


def update_price_by_product(product_name: str, variant_name: str, new_price, compare_price=None):
    if new_price is None:
        raise ValueError("Missing price")

    matched_products = search_products(product_name)
    chosen_product = _choose_product(matched_products, product_name, variant_name)
    chosen_variant = _choose_variant(chosen_product, variant_name)

    return update_price_by_variant_id(
        product_id=chosen_product["id"],
        variant_id=chosen_variant["id"],
        product_title=chosen_product["title"],
        variant_title=chosen_variant["displayName"],
        new_price=new_price,
        compare_price=compare_price,
    )


def update_price_by_match(product_title: str, variant_title: str, sku: str, new_price, compare_price=None):
    if not product_title:
        raise ValueError("Missing product_title")
    if new_price is None:
        raise ValueError("Missing new_price")

    matched_products = search_products(product_title)
    chosen_product = _choose_product(
        matched_products,
        product_name=product_title,
        variant_name=variant_title,
        sku=sku,
    )
    chosen_variant = _choose_variant(
        chosen_product,
        variant_name=variant_title,
        sku=sku,
    )

    return update_price_by_variant_id(
        product_id=chosen_product["id"],
        variant_id=chosen_variant["id"],
        product_title=chosen_product["title"],
        variant_title=chosen_variant["displayName"],
        new_price=new_price,
        compare_price=compare_price,
    )


def update_price_by_variant_id(
    product_id: str,
    variant_id: str,
    product_title: str,
    variant_title: str,
    new_price,
    compare_price=None,
):
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

    variant_payload = {
        "id": variant_id,
        "price": str(new_price),
    }

    # Explicitly clear compare-at price when ending a promotion
    variant_payload["compareAtPrice"] = str(compare_price) if compare_price is not None else None

    variables = {
        "productId": product_id,
        "variants": [variant_payload],
    }

    result = shopify_graphql(mutation, variables)
    payload = result["productVariantsBulkUpdate"]

    if payload["userErrors"]:
        raise Exception(payload["userErrors"])

    if not payload.get("productVariants"):
        raise Exception("No product variants were returned from Shopify")

    updated_variant = payload["productVariants"][0]

    return {
        "success": True,
        "product": product_title,
        "variant": variant_title,
        "new_price": updated_variant["price"],
        "compare_at_price": updated_variant["compareAtPrice"],
    }
