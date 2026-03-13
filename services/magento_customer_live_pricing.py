import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_cache: dict = {}
CACHE_TTL = 180

PRODUCT_PRICE_QUERY = """
query GetProductPrice($sku: String!) {
  products(filter: { sku: { eq: $sku } }) {
    items {
      sku
      name
      price_range {
        minimum_price {
          regular_price { value currency }
          final_price { value currency }
        }
      }
      ... on SimpleProduct {
        price_tiers {
          quantity
          final_price { value currency }
          discount { amount_off percent_off }
        }
      }
    }
  }
}
"""

ADMIN_TOKEN_MUTATION = """
mutation GetCustomerTokenAsAdmin($email: String!) {
  generateCustomerTokenAsAdmin(input: { customer_email: $email }) {
    customer_token
  }
}
"""


def _get_graphql_url() -> str:
    base_url = os.getenv("MAGENTO_BASE_URL", "").rstrip("/")
    if base_url.endswith("/graphql"):
        return base_url
    return f"{base_url}/graphql"


def _get_cached(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def get_magento_customer_email_by_id(magento_customer_id: int) -> str:
    from integrations.magento_rest_oauth import magento_rest_get

    cache_key = f"mage_email:{magento_customer_id}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    logger.info("Resolving Magento customer ID %d to email via REST API", magento_customer_id)

    status, text = magento_rest_get(f"/rest/V1/customers/{magento_customer_id}")
    if status == 404:
        raise ValueError(f"Magento customer ID {magento_customer_id} not found")
    if status != 200:
        raise RuntimeError(f"Failed to fetch Magento customer {magento_customer_id}: HTTP {status}")

    data = json.loads(text)
    email = data.get("email")
    if not email:
        raise ValueError(f"Magento customer ID {magento_customer_id} has no email")

    logger.info("Resolved Magento customer %d → %s", magento_customer_id, email)
    _set_cache(cache_key, email)
    return email


class TokenResult:
    def __init__(self, token=None, status="ok", reason=None):
        self.token = token
        self.status = status
        self.reason = reason

    @property
    def available(self):
        return self.token is not None


def generate_customer_token_as_admin(customer_email: str) -> TokenResult:
    from requests_oauthlib import OAuth1

    url = _get_graphql_url()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "query": ADMIN_TOKEN_MUTATION,
        "variables": {"email": customer_email},
    }

    auth = OAuth1(
        os.getenv("M2_CONSUMER_KEY"),
        os.getenv("M2_CONSUMER_SECRET"),
        os.getenv("M2_ACCESS_TOKEN"),
        os.getenv("M2_ACCESS_TOKEN_SECRET"),
    )

    logger.info("generateCustomerTokenAsAdmin for %s", customer_email)
    try:
        resp = requests.post(url, json=payload, headers=headers, auth=auth, timeout=15)
    except requests.RequestException as e:
        logger.error("Admin token mutation request failed: %s", e)
        return TokenResult(status="upstream_error", reason=f"Request failed: {e}")

    logger.info("Admin token mutation response: status=%d size=%d", resp.status_code, len(resp.text))

    if resp.status_code != 200:
        logger.error("Admin token mutation HTTP %d: %s", resp.status_code, resp.text[:500])
        return TokenResult(status="upstream_error", reason=f"HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return TokenResult(status="upstream_error", reason="Non-JSON response from Magento")

    if "errors" in data:
        error_msgs = [e.get("message", "") for e in data["errors"]]
        if any("Cannot query field" in m for m in error_msgs):
            logger.warning(
                "generateCustomerTokenAsAdmin is not available — requires Adobe Commerce."
            )
            return TokenResult(status="mutation_unsupported",
                               reason="generateCustomerTokenAsAdmin requires Adobe Commerce (paid edition).")
        logger.warning("generateCustomerTokenAsAdmin errors: %s", error_msgs)
        return TokenResult(status="auth_failed", reason="; ".join(error_msgs))

    token = (data.get("data") or {}).get("generateCustomerTokenAsAdmin", {}).get("customer_token")
    if not token:
        logger.warning("No customer_token returned from generateCustomerTokenAsAdmin")
        return TokenResult(status="auth_failed", reason="No customer_token in response")

    logger.info("Customer token obtained for %s", customer_email)
    return TokenResult(token=token, status="ok")


def _fetch_product_price(sku: str, token: Optional[str] = None) -> dict:
    url = _get_graphql_url()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    context = "guest"
    if token:
        headers["Authorization"] = f"Bearer {token}"
        context = "customer"

    payload = {
        "query": PRODUCT_PRICE_QUERY,
        "variables": {"sku": sku},
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    logger.info("GraphQL price query (%s): sku=%s status=%d size=%d", context, sku, resp.status_code, len(resp.text))

    if resp.status_code != 200:
        raise RuntimeError(f"GraphQL HTTP {resp.status_code} for {context} price query")

    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {json.dumps(data['errors'])[:300]}")

    items = (data.get("data") or {}).get("products", {}).get("items", [])
    if not items:
        return {"sku": sku, "found": False}

    product = items[0]
    price_range = product.get("price_range", {})
    min_price = price_range.get("minimum_price", {})
    regular = min_price.get("regular_price", {})
    final = min_price.get("final_price", {})

    tier_prices = []
    for tp in product.get("price_tiers") or []:
        tier_prices.append({
            "quantity": tp.get("quantity"),
            "final_price": (tp.get("final_price") or {}).get("value"),
            "discount_amount": (tp.get("discount") or {}).get("amount_off"),
            "discount_percent": (tp.get("discount") or {}).get("percent_off"),
        })

    return {
        "sku": product.get("sku"),
        "name": product.get("name"),
        "found": True,
        "regular_price": regular.get("value"),
        "final_price": final.get("value"),
        "currency": regular.get("currency") or final.get("currency"),
        "tier_prices": tier_prices,
        "context": context,
    }


def get_guest_product_price(sku: str) -> dict:
    return _fetch_product_price(sku, token=None)


def get_customer_product_price(customer_token: str, sku: str) -> dict:
    return _fetch_product_price(sku, token=customer_token)


def get_customer_live_price_comparison(magento_customer_id: int, sku: str) -> dict:
    cache_key = f"price_cmp:{magento_customer_id}:{sku}"
    cached = _get_cached(cache_key)
    if cached is not None:
        logger.debug("Returning cached price comparison for %s", cache_key)
        return cached

    email = get_magento_customer_email_by_id(magento_customer_id)

    token_result = generate_customer_token_as_admin(email)

    guest_price = get_guest_product_price(sku)
    if not guest_price.get("found"):
        raise ValueError(f"SKU {sku} not found in Magento")

    if token_result.available:
        customer_price = get_customer_product_price(token_result.token, sku)
    else:
        customer_price = None

    g_regular = guest_price.get("regular_price") or 0
    g_final = guest_price.get("final_price") or 0

    if customer_price and customer_price.get("found"):
        c_regular = customer_price.get("regular_price") or 0
        c_final = customer_price.get("final_price") or 0
    else:
        c_regular = None
        c_final = None

    if c_final is not None:
        price_diff = round(g_final - c_final, 4)
        discount_pct = round((price_diff / g_final) * 100, 2) if g_final else 0
        is_different = abs(price_diff) > 0.001
    else:
        price_diff = None
        discount_pct = None
        is_different = None

    result = {
        "magento_customer_id": magento_customer_id,
        "customer_email": email,
        "sku": sku,
        "product_name": guest_price.get("name"),
        "guest_regular_price": g_regular,
        "guest_final_price": g_final,
        "customer_regular_price": c_regular,
        "customer_final_price": c_final,
        "currency": guest_price.get("currency"),
        "price_difference": price_diff,
        "discount_percent_vs_guest": discount_pct,
        "is_customer_specific_price_detected": is_different,
        "guest_tier_prices": guest_price.get("tier_prices", []),
        "customer_tier_prices": (customer_price or {}).get("tier_prices", []),
        "admin_token_available": token_result.available,
        "token_status": token_result.status,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    if not token_result.available:
        status_msgs = {
            "mutation_unsupported": (
                "generateCustomerTokenAsAdmin is not available on this Magento instance "
                "(requires Adobe Commerce). Customer-specific BSS pricing cannot be compared. "
                "Guest price is shown."
            ),
            "auth_failed": (
                f"Customer token authentication failed: {token_result.reason or 'unknown'}. "
                "Customer-specific BSS pricing cannot be compared. Guest price is shown."
            ),
            "upstream_error": (
                f"Magento upstream error: {token_result.reason or 'unknown'}. "
                "Customer pricing check was skipped. Guest price is shown."
            ),
        }
        result["note"] = status_msgs.get(token_result.status,
            f"Token unavailable ({token_result.status}). Guest price is shown."
        )

    logger.info(
        "Price comparison: customer=%d sku=%s guest_final=%.2f customer_final=%s diff=%s token=%s",
        magento_customer_id, sku, g_final,
        f"{c_final:.2f}" if c_final is not None else "N/A",
        f"{price_diff:.2f}" if price_diff is not None else "N/A",
        token_result.status,
    )

    _set_cache(cache_key, result)
    return result
