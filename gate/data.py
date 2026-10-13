"""Load the rights table and the offline catalogue fixture from CSV."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .rules import Product, Right

ROOT = Path(__file__).resolve().parent.parent
RIGHTS_CSV = ROOT / "data" / "rights.csv"
CATALOGUE_CSV = ROOT / "data" / "catalogue.csv"


def _date(s: str) -> date | None:
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


def load_rights(path: Path | str = RIGHTS_CSV) -> list[Right]:
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            try:
                rows.append(
                    Right(
                        isbn=row["isbn"].strip(),
                        title=row["title"].strip(),
                        imprint=row["imprint"].strip(),
                        territory=row["territory"].strip().upper(),
                        window_start=_date(row["window_start"]),
                        window_end=_date(row["window_end"]),
                        price_floor_usd=float(row["price_floor_usd"]),
                        est_monthly_units=int(row["est_monthly_units"]),
                    )
                )
            except (KeyError, ValueError) as e:
                # A malformed rights row is a data incident, not something to
                # skip quietly — the whole point of the gate is catching these.
                raise ValueError(f"{path}:{i} is malformed: {e}") from e
    return rows


def load_catalogue(path: Path | str = CATALOGUE_CSV) -> list[Product]:
    """Offline stand-in for the Shopify read, so the gate runs with no network."""
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                Product(
                    handle=row["handle"].strip(),
                    title=row["title"].strip(),
                    isbn=row["isbn"].strip(),
                    imprint=row["imprint"].strip(),
                    territories=tuple(
                        t.strip().upper() for t in row["territories"].split("|") if t.strip()
                    ),
                    price_usd=float(row["price_usd"]),
                    published=row["published"].strip().lower() in ("1", "true", "yes"),
                    audio_asset=row["audio_asset"].strip(),
                )
            )
    return rows
