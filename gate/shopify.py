"""
Shopify Admin API adapter — read the storefront, map it to Product.

Thin on purpose. Everything that can be wrong about the rules lives in
rules.py, which is tested offline; everything that can be wrong about the
network lives here, where it fails loudly.

Metafields the gate reads (namespace `custom`):
    isbn          single_line_text_field
    imprint       single_line_text_field
    territories   single_line_text_field, comma separated, e.g. "US,CA,GB"
    audio_asset   single_line_text_field, empty == no deliverable file
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .rules import Product

API_VERSION = "2026-07"

# The gate reads only what carries the audiobook tag. An unrelated catalogue can
# then live in the same store without either one polluting the other's report —
# which is also why seeding never had to delete anything.
CATALOGUE_TAG = "audiobook"

_QUERY = """
query Catalogue($cursor: String, $query: String) {
  products(first: 100, after: $cursor, query: $query) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      handle
      title
      status
      publishedAt
      variants(first: 1) { nodes { price } }
      metafields(namespace: "custom", first: 10) { nodes { key value } }
    }
  }
}
"""


class ShopifyError(RuntimeError):
    pass


def _endpoint(shop: str) -> str:
    shop = shop.replace("https://", "").replace("http://", "").strip("/")
    return f"https://{shop}/admin/api/{API_VERSION}/graphql.json"


def _post(shop: str, token: str, query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        _endpoint(shop),
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ShopifyError(
            f"Shopify returned HTTP {e.code}. "
            f"401/403 usually means the Admin API token is wrong or the app is "
            f"missing read_products. Body: {e.read()[:400]!r}"
        ) from e
    except urllib.error.URLError as e:
        raise ShopifyError(
            f"Could not reach {_endpoint(shop)} ({e.reason}). "
            f"Run this from a machine with plain internet access."
        ) from e

    if payload.get("errors"):
        raise ShopifyError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


def _as_product(node: dict) -> Product:
    meta = {m["key"]: (m["value"] or "") for m in node["metafields"]["nodes"]}
    variants = node["variants"]["nodes"]
    price = float(variants[0]["price"]) if variants else 0.0
    territories = tuple(
        t.strip().upper() for t in meta.get("territories", "").split(",") if t.strip()
    )
    return Product(
        handle=node["handle"],
        title=node["title"],
        isbn=meta.get("isbn", "").strip(),
        imprint=meta.get("imprint", "").strip(),
        territories=territories,
        price_usd=price,
        # "Buyable" means ACTIVE and actually published to a channel.
        published=node["status"] == "ACTIVE" and bool(node.get("publishedAt")),
        audio_asset=meta.get("audio_asset", ""),
        shopify_gid=node["id"],
    )


def fetch_catalogue(shop: str | None = None, token: str | None = None) -> list[Product]:
    shop = shop or os.environ.get("SHOPIFY_SHOP", "")
    token = token or os.environ.get("SHOPIFY_TOKEN", "")
    if not shop or not token:
        raise ShopifyError(
            "Set SHOPIFY_SHOP (e.g. slompoboutique.myshopify.com) and SHOPIFY_TOKEN."
        )

    products: list[Product] = []
    cursor = None
    while True:
        data = _post(shop, token, _QUERY,
                     {"cursor": cursor, "query": f"tag:{CATALOGUE_TAG}"})
        page = data["products"]
        products.extend(_as_product(n) for n in page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    if not products:
        raise ShopifyError(
            f"No products tagged `{CATALOGUE_TAG}` in {shop}. Run "
            f"`python3 -m gate.seed` first, or check the token's scopes."
        )

    missing = [p.handle for p in products if not p.isbn]
    if missing:
        # Worth saying out loud: a listing with no ISBN is invisible to every
        # rule, so a silent skip would make the report look cleaner than it is.
        print(
            f"warning: {len(missing)} listing(s) have no custom.isbn metafield and "
            f"were not checked: {', '.join(missing[:5])}"
            + (" ..." if len(missing) > 5 else "")
        )
    return products
