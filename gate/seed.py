"""
Seed the audiobook catalogue into Shopify, from the same CSVs the gate reads.

Idempotent by handle: run it twice and the second run updates instead of
duplicating. Everything it creates carries the `audiobook` tag, which is also
how the gate finds it — so an unrelated catalogue can live in the same store
without either one polluting the other's report.

Deliberately reversible. `--unpublish-others` sets non-audiobook products to
DRAFT so the storefront reads as a bookshop; it does not delete anything, and
`--republish-others` puts them back.
"""

from __future__ import annotations

import argparse
import sys

from .data import load_catalogue, load_rights
from .shopify import _post, ShopifyError

TAG = "audiobook"

_UPSERT = """
mutation Upsert($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id handle }
    userErrors { field message }
  }
}
"""

_CREATE = """
mutation Create($input: ProductInput!) {
  productCreate(input: $input) {
    product { id handle }
    userErrors { field message }
  }
}
"""

_BY_HANDLE = """
query ByHandle($handle: String!) {
  productByHandle(handle: $handle) { id handle status }
}
"""

_OTHERS = """
query Others($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id handle status tags }
  }
}
"""

_SET_STATUS = """
mutation Status($input: ProductInput!) {
  productUpdate(input: $input) { product { id status } userErrors { message } }
}
"""

_PRICE = """
mutation Price($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    userErrors { message }
  }
}
"""

_VARIANT = """
query Variant($handle: String!) {
  productByHandle(handle: $handle) { variants(first: 1) { nodes { id } } }
}
"""


def _blurb(title: str, imprint: str, isbn: str) -> str:
    return (
        f"<p><em>{title}</em> — an original title from {imprint}.</p>"
        f"<p>Synthetic catalogue entry used to exercise a rights and catalogue "
        f"integrity gate. ISBN {isbn}. Not a real publication.</p>"
    )


def seed(shop: str, token: str, dry_run: bool = False) -> int:
    catalogue = load_catalogue()
    rights = load_rights()
    windows = {}
    for r in rights:
        windows.setdefault(r.isbn, []).append(r.window_start)

    created = updated = 0
    for p in catalogue:
        metafields = [
            {"namespace": "custom", "key": "isbn", "type": "single_line_text_field",
             "value": p.isbn},
            {"namespace": "custom", "key": "imprint", "type": "single_line_text_field",
             "value": p.imprint},
            {"namespace": "custom", "key": "territories", "type": "single_line_text_field",
             "value": ",".join(p.territories)},
            {"namespace": "custom", "key": "audio_asset", "type": "single_line_text_field",
             "value": p.audio_asset},
        ]
        product_input = {
            "handle": p.handle,
            "title": p.title,
            "descriptionHtml": _blurb(p.title, p.imprint, p.isbn),
            "vendor": p.imprint,
            "productType": "Audiobook",
            "tags": [TAG, p.imprint.replace(" ", "-").lower()],
            "status": "ACTIVE" if p.published else "DRAFT",
            "metafields": metafields,
        }

        if dry_run:
            print(f"  would upsert {p.handle:34} {p.isbn}  {p.imprint}")
            continue

        existing = _post(shop, token, _BY_HANDLE, {"handle": p.handle})["productByHandle"]
        if existing:
            product_input["id"] = existing["id"]
            res = _post(shop, token, _UPSERT, {"input": product_input})["productUpdate"]
            action = "updated"
            updated += 1
        else:
            res = _post(shop, token, _CREATE, {"input": product_input})["productCreate"]
            action = "created"
            created += 1
        if res["userErrors"]:
            raise ShopifyError(f"{p.handle}: {res['userErrors']}")

        pid = res["product"]["id"]
        vid = _post(shop, token, _VARIANT, {"handle": p.handle})["productByHandle"]
        variants = vid["variants"]["nodes"] if vid else []
        if variants:
            err = _post(shop, token, _PRICE, {
                "productId": pid,
                "variants": [{"id": variants[0]["id"], "price": f"{p.price_usd:.2f}"}],
            })["productVariantsBulkUpdate"]["userErrors"]
            if err:
                raise ShopifyError(f"{p.handle} price: {err}")

        print(f"  {action:7} {p.handle:34} ${p.price_usd:>6.2f}  {p.imprint}")

    print(f"\n{created} created, {updated} updated.")
    return 0


def set_others(shop: str, token: str, status: str) -> int:
    """Hide or restore everything that is not part of the audiobook catalogue.

    Reversible on purpose: this changes status, never deletes.
    """
    cursor, touched = None, 0
    while True:
        page = _post(shop, token, _OTHERS, {"cursor": cursor})["products"]
        for n in page["nodes"]:
            if TAG in (n.get("tags") or []):
                continue
            if n["status"] == status:
                continue
            res = _post(shop, token, _SET_STATUS,
                        {"input": {"id": n["id"], "status": status}})["productUpdate"]
            if res["userErrors"]:
                raise ShopifyError(f"{n['handle']}: {res['userErrors']}")
            print(f"  {n['handle']:34} -> {status}")
            touched += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    print(f"\n{touched} product(s) set to {status}. Nothing was deleted.")
    return 0


def main(argv=None) -> int:
    import os

    ap = argparse.ArgumentParser(prog="gate.seed", description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--unpublish-others", action="store_true",
                    help="set every non-audiobook product to DRAFT (reversible)")
    ap.add_argument("--republish-others", action="store_true",
                    help="put them back to ACTIVE")
    args = ap.parse_args(argv)

    shop = os.environ.get("SHOPIFY_SHOP", "")
    token = os.environ.get("SHOPIFY_TOKEN", "")
    if not shop or not token:
        print("Set SHOPIFY_SHOP and SHOPIFY_TOKEN.", file=sys.stderr)
        return 2

    if args.republish_others:
        return set_others(shop, token, "ACTIVE")
    rc = seed(shop, token, dry_run=args.dry_run)
    if args.unpublish_others and not args.dry_run:
        print("\nHiding everything else (reversible):")
        set_others(shop, token, "DRAFT")
    return rc


if __name__ == "__main__":
    sys.exit(main())
