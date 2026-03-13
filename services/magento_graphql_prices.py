import os
import json
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

GRAPHQL_QUERY = """
query GetProductPrice($sku: String!) {
  products(filter: { sku: { eq: $sku } }) {
    items {
      sku
      name
      price_range {
        minimum_price {
          regular_price { value currency }
          final_price { value currency }
          discount { amount_off percent_off }
        }
        maximum_price {
          regular_price { value currency }
          final_price { value currency }
          discount { amount_off percent_off }
        }
      }
      price_tiers {
        quantity
        final_price { value currency }
        discount { amount_off percent_off }
      }
    }
  }
}
"""


def _get_graphql_url() -> str:
    base_url = os.getenv("MAGENTO_BASE_URL", "").rstrip("/")
    if base_url.endswith("/graphql"):
        return base_url
    return f"{base_url}/graphql"


def get_customer_product_price_graphql(
    sku: str,
    customer_token: Optional[str] = None,
) -> dict:
    url = _get_graphql_url()
    if not url or url == "/graphql":
        raise ValueError("MAGENTO_BASE_URL is not configured")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    context = "guest"
    if customer_token:
        headers["Authorization"] = f"Bearer {customer_token}"
        context = "customer"

    payload = {
        "query": GRAPHQL_QUERY,
        "variables": {"sku": sku},
    }

    logger.info("GraphQL price query: sku=%s context=%s url=%s", sku, context, url)

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    logger.info("GraphQL response: status=%d size=%d", resp.status_code, len(resp.text))

    if resp.status_code != 200:
        logger.error("GraphQL HTTP %d: %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"GraphQL returned HTTP {resp.status_code}")

    data = resp.json()

    if "errors" in data:
        logger.error("GraphQL errors: %s", json.dumps(data["errors"])[:500])
        raise RuntimeError(f"GraphQL errors: {json.dumps(data['errors'])[:300]}")

    items = (data.get("data") or {}).get("products", {}).get("items", [])
    if not items:
        logger.info("No product found for SKU=%s (context=%s)", sku, context)
        return {"sku": sku, "found": False}

    product = items[0]
    price_range = product.get("price_range", {})
    min_price = price_range.get("minimum_price", {})
    max_price = price_range.get("maximum_price", {})

    regular = min_price.get("regular_price", {})
    final = min_price.get("final_price", {})
    discount = min_price.get("discount", {})

    tier_prices = []
    for tp in product.get("price_tiers") or []:
        tier_prices.append({
            "quantity": tp.get("quantity"),
            "final_price": (tp.get("final_price") or {}).get("value"),
            "discount_amount": (tp.get("discount") or {}).get("amount_off"),
            "discount_percent": (tp.get("discount") or {}).get("percent_off"),
        })

    result = {
        "sku": product.get("sku"),
        "name": product.get("name"),
        "found": True,
        "regular_price": regular.get("value"),
        "final_price": final.get("value"),
        "currency": regular.get("currency") or final.get("currency"),
        "discount_amount": discount.get("amount_off"),
        "discount_percent": discount.get("percent_off"),
        "max_regular_price": (max_price.get("regular_price") or {}).get("value"),
        "max_final_price": (max_price.get("final_price") or {}).get("value"),
        "tier_prices": tier_prices,
        "context": context,
    }

    logger.info(
        "Price result: sku=%s context=%s regular=%.2f final=%.2f currency=%s tiers=%d",
        result["sku"], context,
        result["regular_price"] or 0, result["final_price"] or 0,
        result["currency"], len(tier_prices),
    )

    return result


def get_customer_token(email: str, password: str) -> str:
    url = _get_graphql_url()
    mutation = """
    mutation GenerateToken($email: String!, $password: String!) {
      generateCustomerToken(email: $email, password: $password) {
        token
      }
    }
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    payload = {"query": mutation, "variables": {"email": email, "password": password}}

    logger.info("Requesting customer token for email=%s", email)
    resp = requests.post(url, json=payload, headers=headers, timeout=15)

    if resp.status_code != 200:
        raise RuntimeError(f"Token request failed: HTTP {resp.status_code}")

    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Token errors: {json.dumps(data['errors'])[:300]}")

    token = (data.get("data") or {}).get("generateCustomerToken", {}).get("token")
    if not token:
        raise RuntimeError("No token returned from generateCustomerToken")

    logger.info("Customer token obtained for %s", email)
    return token
