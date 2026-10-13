"""
A storefront latency baseline — measurement, not load testing.

The distinction is the whole point. This sends a few dozen polite, sequential
requests and records how long the first byte takes. It does not try to find a
breaking point, and it must not: hammering a Shopify-hosted storefront measures
their CDN rather than anything you control, and it is not yours to stress.

Finding the breaking point is the platform vendor's line in the SOW
(`docs/load-test-spec.md` says what to ask them for and how to check it).
Knowing what normal looks like before launch is this file's job, and without it
nobody can tell whether a slow page on launch day is new.

Writes `data/latency.json`, which the readiness board reads as evidence for the
checkout-performance row — so a human never types a number into that table.
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import sys
import time
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
LATENCY_JSON = ROOT / "data" / "latency.json"

# Deliberately small and deliberately slow. Twenty-five requests spread over
# about twenty seconds is a measurement; it is not traffic.
SAMPLES = 5
PAUSE_S = 0.4
TIMEOUT_S = 15

# The p95 a launch should hold for a product page. Not a guess: slower than this
# and the page is losing people before it has said anything.
TARGET_P95_MS = 800


def _ttfb_ms(url: str) -> tuple[int, int]:
    """(status, milliseconds to first byte). Connection is not reused, on
    purpose: a cold connection is closer to what a real visitor pays."""
    parts = urlparse(url)
    conn = HTTPSConnection(parts.netloc, timeout=TIMEOUT_S)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    start = time.perf_counter()
    try:
        conn.request("GET", path, headers={
            "User-Agent": "catalogue-gate/latency-baseline (measurement, not load)",
            "Accept": "text/html",
        })
        resp = conn.getresponse()
        resp.read(1)  # first byte
        elapsed = (time.perf_counter() - start) * 1000
        return resp.status, int(elapsed)
    finally:
        conn.close()


def _pct(values: list[int], p: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1])


def measure(urls: list[str], samples: int = SAMPLES) -> dict:
    per_url, errors = {}, []
    for url in urls:
        times: list[int] = []
        statuses: set[int] = set()
        for _ in range(samples):
            try:
                status, ms = _ttfb_ms(url)
                statuses.add(status)
                if status < 400:
                    times.append(ms)
            except (OSError, socket.timeout) as e:
                errors.append(f"{url}: {e}")
            time.sleep(PAUSE_S)
        if times:
            per_url[url] = {
                "samples": len(times),
                "statuses": sorted(statuses),
                "p50_ms": _pct(sorted(times), 50),
                "p95_ms": _pct(sorted(times), 95),
                "min_ms": min(times),
                "max_ms": max(times),
            }
        else:
            errors.append(f"{url}: no successful responses (statuses {sorted(statuses) or 'none'})")

    everything = [t for u in per_url.values() for t in [u["p50_ms"]] * 0] or []
    all_p95 = [u["p95_ms"] for u in per_url.values()]
    worst = max(all_p95) if all_p95 else 0

    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "method": f"{samples} sequential cold-connection GETs per URL, {PAUSE_S}s apart; "
                  f"time to first byte",
        "target_p95_ms": TARGET_P95_MS,
        "worst_p95_ms": worst,
        "meets_target": bool(all_p95) and worst <= TARGET_P95_MS,
        "urls": per_url,
        "errors": errors,
    }


def default_urls(shop: str) -> list[str]:
    base = f"https://{shop.replace('https://', '').strip('/')}"
    from .data import load_catalogue

    handles = [p.handle for p in load_catalogue() if p.published][:3]
    return [f"{base}/", f"{base}/collections/all"] + [
        f"{base}/products/{h}" for h in handles
    ]


def main(argv=None) -> int:
    import os

    ap = argparse.ArgumentParser(prog="gate.latency", description=__doc__)
    ap.add_argument("--shop", default=os.environ.get("SHOPIFY_SHOP", ""))
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--out", default=str(LATENCY_JSON))
    args = ap.parse_args(argv)

    if not args.shop:
        print("Set SHOPIFY_SHOP or pass --shop.", file=sys.stderr)
        return 2

    urls = default_urls(args.shop)
    print(f"Measuring {len(urls)} URLs, {args.samples} samples each "
          f"({len(urls) * args.samples} requests total). This is a baseline, not a load test.\n")
    result = measure(urls, args.samples)

    for url, s in result["urls"].items():
        flag = " " if s["p95_ms"] <= TARGET_P95_MS else "!"
        print(f" {flag} {s['p50_ms']:>5} ms p50   {s['p95_ms']:>5} ms p95   {url}")
    for e in result["errors"]:
        print(f"   error: {e}")

    print(f"\nWorst p95: {result['worst_p95_ms']} ms against a {TARGET_P95_MS} ms target "
          f"— {'within' if result['meets_target'] else 'OVER'}.")

    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}. The readiness board reads it as evidence.")
    return 0 if result["meets_target"] else 1


if __name__ == "__main__":
    sys.exit(main())
