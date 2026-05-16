import os
import re
import smtplib
import time
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage

import requests

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
SHOP_DOMAIN = os.environ.get("SHOP_DOMAIN")
DEFAULT_LOCATION_ID = os.environ.get("DEFAULT_LOCATION_ID")
API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-01")
REPORT_EMAIL_TO = os.environ.get("REPORT_EMAIL_TO", "sales@speedydrone.ca")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").strip().lower() != "false"
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL") or SMTP_USERNAME or "no-reply@localhost"
DJI_CONSUMER_INCLUDE_KEYWORDS = [
    keyword.strip().lower()
    for keyword in os.environ.get("DJI_CONSUMER_INCLUDE_KEYWORDS", "dji,osmo").split(",")
    if keyword.strip()
]
DJI_CONSUMER_EXCLUDE_KEYWORDS = [
    keyword.strip().lower()
    for keyword in os.environ.get(
        "DJI_CONSUMER_EXCLUDE_KEYWORDS",
        "enterprise,matrice,agrass,dock,docks,zenmuse,teras,delivery,dji care",
    ).split(",")
    if keyword.strip()
]
MISSING_SKU_LABEL = os.environ.get("MISSING_SKU_LABEL", "(missing SKU)")

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


def _money_amount(money_set) -> Decimal:
    try:
        amount = ((money_set or {}).get("shopMoney") or {}).get("amount")
        return Decimal(str(amount or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _money_currency(money_set) -> str:
    return ((money_set or {}).get("shopMoney") or {}).get("currencyCode") or ""


def _format_money_amount(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01")))


def _normalize_order_number(order_number) -> str:
    value = str(order_number or "").strip()
    if not value:
        return ""
    if value.startswith("#"):
        return value
    return f"#{value}"


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


def get_sold_products_by_order_numbers(order_numbers):
    normalized_order_numbers = []
    seen = set()

    for order_number in order_numbers or []:
        normalized = _normalize_order_number(order_number)
        if normalized and normalized not in seen:
            normalized_order_numbers.append(normalized)
            seen.add(normalized)

    if not normalized_order_numbers:
        raise ValueError("order_numbers must include at least one order number")

    query = """
    query SoldProductsForOrders($search: String!, $cursor: String) {
      orders(first: 100, after: $cursor, query: $search, sortKey: CREATED_AT, reverse: true) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            id
            name
            createdAt
            displayFinancialStatus
            lineItems(first: 100) {
              edges {
                node {
                  id
                  name
                  title
                  sku
                  quantity
                  currentQuantity
                  discountedUnitPriceSet {
                    shopMoney {
                      amount
                      currencyCode
                    }
                  }
                  discountedTotalSet {
                    shopMoney {
                      amount
                      currencyCode
                    }
                  }
                  taxLines {
                    priceSet {
                      shopMoney {
                        amount
                        currencyCode
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    search_query = " OR ".join(f"name:{order_number}" for order_number in normalized_order_numbers)
    cursor = None
    orders_by_name = {}

    while True:
        data = shopify_graphql(query, {"search": search_query, "cursor": cursor})
        orders = data["orders"]

        for order_edge in orders["edges"]:
            order = order_edge["node"]
            if order["name"] not in seen:
                continue

            line_items = []
            for item_edge in order["lineItems"]["edges"]:
                item = item_edge["node"]
                quantity = int(item.get("quantity") or 0)
                current_quantity = int(item.get("currentQuantity") or 0)
                refunded_quantity = max(quantity - current_quantity, 0)
                subtotal = _money_amount(item.get("discountedTotalSet"))
                tax_total = sum(
                    _money_amount(tax_line.get("priceSet"))
                    for tax_line in item.get("taxLines", [])
                )
                total_after_tax = subtotal + tax_total
                unit_price_set = item.get("discountedUnitPriceSet")
                currency = (
                    _money_currency(item.get("discountedTotalSet"))
                    or _money_currency(unit_price_set)
                    or "CAD"
                )

                if refunded_quantity == 0:
                    refund_status = "not_refunded"
                elif current_quantity == 0:
                    refund_status = "refunded"
                else:
                    refund_status = "partially_refunded"

                line_items.append(
                    {
                        "product_title": item.get("title") or item.get("name") or "",
                        "line_item_name": item.get("name") or "",
                        "sku": item.get("sku") or "",
                        "quantity_ordered": quantity,
                        "quantity_current": current_quantity,
                        "quantity_refunded": refunded_quantity,
                        "unit_price": _format_money_amount(_money_amount(unit_price_set)),
                        "total_before_tax": _format_money_amount(subtotal),
                        "tax_total": _format_money_amount(tax_total),
                        "total_after_tax": _format_money_amount(total_after_tax),
                        "currency": currency,
                        "refund_status": refund_status,
                        "refunded": refunded_quantity > 0,
                    }
                )

            orders_by_name[order["name"]] = {
                "order_number": order["name"],
                "order_id": order["id"],
                "created_at": order["createdAt"],
                "financial_status": order.get("displayFinancialStatus"),
                "line_items": line_items,
            }

        if not orders["pageInfo"]["hasNextPage"]:
            break
        cursor = orders["pageInfo"]["endCursor"]

    found_orders = [
        orders_by_name[order_number]
        for order_number in normalized_order_numbers
        if order_number in orders_by_name
    ]
    missing_orders = [
        order_number
        for order_number in normalized_order_numbers
        if order_number not in orders_by_name
    ]

    return {
        "requested_order_numbers": normalized_order_numbers,
        "found_order_count": len(found_orders),
        "missing_order_numbers": missing_orders,
        "orders": found_orders,
    }


def _is_dji_consumer_product(product_title: str, sku: str = "") -> bool:
    haystack = f"{product_title or ''} {sku or ''}".lower()

    if not any(keyword in haystack for keyword in DJI_CONSUMER_INCLUDE_KEYWORDS):
        return False

    if any(keyword in haystack for keyword in DJI_CONSUMER_EXCLUDE_KEYWORDS):
        return False

    return True


def _format_markdown_table(rows):
    headers = [
        "Net Sold Units",
        "Variant SKU",
        "Product Title",
        "Inventory On Hand",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| --- | --- | --- | --- |",
    ]

    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["net_sold_units"]),
                    str(row["sku"] or MISSING_SKU_LABEL),
                    str(row["product_title"] or ""),
                    str(row["inventory_on_hand"]),
                ]
            )
            + " |"
        )

    return "\n".join(lines)


def _parse_report_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format")


def _resolve_report_range(start_date: str = None, end_date: str = None, days: int = 7):
    if start_date or end_date:
        if not start_date or not end_date:
            raise ValueError("start_date and end_date must both be provided")

        start_day = _parse_report_date(start_date, "start_date")
        end_day = _parse_report_date(end_date, "end_date")

        if end_day < start_day:
            raise ValueError("end_date must be on or after start_date")

        start_dt = datetime.combine(start_day, dt_time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_day, dt_time.max, tzinfo=timezone.utc)
        return {
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "start_at": start_dt,
            "end_at": end_dt,
            "days": (end_day - start_day).days + 1,
        }

    if days < 1:
        raise ValueError("days must be >= 1")

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    return {
        "start_date": start_dt.date().isoformat(),
        "end_date": end_dt.date().isoformat(),
        "start_at": start_dt,
        "end_at": end_dt,
        "days": days,
    }


def get_weekly_dji_consumer_sales_report(days: int = 7, start_date: str = None, end_date: str = None):
    report_range = _resolve_report_range(start_date=start_date, end_date=end_date, days=days)
    start_at = report_range["start_at"]
    end_at = report_range["end_at"]
    start_date_value = report_range["start_date"]
    end_date_value = report_range["end_date"]

    query = """
    query WeeklyDJIConsumerSales($search: String!, $cursor: String) {
      orders(first: 100, after: $cursor, query: $search, sortKey: CREATED_AT, reverse: true) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            id
            createdAt
            lineItems(first: 100) {
              edges {
                node {
                  name
                  sku
                  quantity
                  currentQuantity
                  variant {
                    id
                    sku
                    inventoryQuantity
                    product {
                      title
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    search_query = f"created_at:>={start_at.strftime('%Y-%m-%d')} created_at:<={end_at.strftime('%Y-%m-%d')}"
    cursor = None
    sales_by_variant = defaultdict(
        lambda: {
            "product_title": "",
            "sku": "",
            "inventory_on_hand": 0,
            "net_sold_units": 0,
        }
    )

    while True:
        data = shopify_graphql(
            query,
            {
                "search": search_query,
                "cursor": cursor,
            },
        )
        orders = data["orders"]

        for order_edge in orders["edges"]:
            order = order_edge["node"]
            for item_edge in order["lineItems"]["edges"]:
                item = item_edge["node"]
                variant = item.get("variant") or {}
                product = variant.get("product") or {}
                sku = variant.get("sku") or item.get("sku") or ""
                product_title = product.get("title") or item.get("name") or ""

                if not _is_dji_consumer_product(product_title, sku):
                    continue

                variant_id = variant.get("id") or f"title:{product_title}|sku:{sku}"
                inventory_on_hand = variant.get("inventoryQuantity")
                net_sold_units = item.get("currentQuantity")
                if net_sold_units is None:
                    net_sold_units = item.get("quantity") or 0

                sales_by_variant[variant_id]["product_title"] = product_title
                sales_by_variant[variant_id]["sku"] = sku or MISSING_SKU_LABEL
                sales_by_variant[variant_id]["inventory_on_hand"] = (
                    inventory_on_hand if inventory_on_hand is not None else 0
                )
                sales_by_variant[variant_id]["net_sold_units"] += int(net_sold_units)

        if not orders["pageInfo"]["hasNextPage"]:
            break
        cursor = orders["pageInfo"]["endCursor"]

    rows = sorted(
        sales_by_variant.values(),
        key=lambda row: (-row["net_sold_units"], row["product_title"], row["sku"]),
    )

    return {
        "report_name": "weekly_dji_consumer_sales",
        "days": report_range["days"],
        "start_date": start_date_value,
        "end_date": end_date_value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "include_keywords": DJI_CONSUMER_INCLUDE_KEYWORDS,
            "exclude_keywords": DJI_CONSUMER_EXCLUDE_KEYWORDS,
        },
        "rows": rows,
        "markdown_table": _format_markdown_table(rows),
    }


def send_weekly_dji_consumer_sales_report(
    days: int = 7,
    recipient_email: str = None,
    start_date: str = None,
    end_date: str = None,
):
    report = get_weekly_dji_consumer_sales_report(
        days=days,
        start_date=start_date,
        end_date=end_date,
    )
    destination = (recipient_email or REPORT_EMAIL_TO or "").strip()

    if not destination:
        return {
            "success": False,
            "sent": False,
            "reason": "Missing report recipient email",
            "report": report,
        }

    if not SMTP_HOST:
        return {
            "success": False,
            "sent": False,
            "reason": "Missing SMTP_HOST",
            "report": report,
        }

    message = EmailMessage()
    message["Subject"] = (
        "DJI consumer sales report - "
        f"{report['start_date']} to {report['end_date']}"
    )
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = destination
    message.set_content(
        "\n".join(
            [
                f"DJI consumer products sold from {report['start_date']} to {report['end_date']}",
                "",
                report["markdown_table"],
            ]
        )
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)

    return {
        "success": True,
        "sent": True,
        "destination": destination,
        "report": report,
    }


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
