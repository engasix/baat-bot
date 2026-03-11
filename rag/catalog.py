"""
Perfume catalog loader.
Reads data/perfumes.json and provides helpers for building
rich bilingual searchable text suitable for Urdu voice queries.
"""

import json
from pathlib import Path

_CATALOG_PATH = Path(__file__).parent.parent / "data" / "perfumes.json"
_catalog: list[dict] | None = None


def load() -> list[dict]:
    """Load and cache the full perfume catalog."""
    global _catalog
    if _catalog is None:
        _catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return _catalog


def to_document(p: dict) -> str:
    """
    Build a rich bilingual (Urdu + English) searchable string for a perfume.

    Designed so that Urdu voice queries like:
      "سستا پرفیوم" / "مردوں کے لیے" / "پریمیم خوشبو" / "عود والا"
    all hit the right entries via semantic similarity.
    """
    gender_ur = {
        "men":    "مردوں کے لیے مردانہ",
        "women":  "عورتوں خواتین لڑکیوں کے لیے زنانہ",
        "unisex": "مرد اور عورت دونوں کے لیے یونیسیکس",
    }

    price = p["price_pkr"]
    if price <= 8_000:
        price_tier = "سستا کم قیمت بجٹ affordable budget cheap"
    elif price <= 22_000:
        price_tier = "درمیانی قیمت mid-range moderate"
    else:
        price_tier = "مہنگا پریمیم لگژری premium luxury expensive"

    notes_str = " ".join(p["scent_notes"])

    sales = p.get("monthly_sales", 0)
    if sales >= 60:
        sales_tier = "سب سے زیادہ بکنے والا bestseller most popular top selling number one"
    elif sales >= 30:
        sales_tier = "مقبول popular selling well frequently bought"
    else:
        sales_tier = ""

    return (
        f"{p['name']} {p['brand']} "
        f"{gender_ur.get(p['gender'], p['gender'])} "
        f"{p['description_ur']} "
        f"{p['description_en']} "
        f"خوشبو scent notes: {notes_str} "
        f"{p['category']} "
        f"قیمت price {price} روپے PKR "
        f"{price_tier} "
        f"{sales_tier}"
    )


def get_by_id(perfume_id: str) -> dict | None:
    return next((p for p in load() if p["id"] == perfume_id), None)


def get_sorted_by_price(ascending: bool = True, in_stock_only: bool = True) -> list[dict]:
    """Return all perfumes sorted by price (cheapest or most expensive first)."""
    catalog = load()
    if in_stock_only:
        catalog = [p for p in catalog if p["in_stock"]]
    return sorted(catalog, key=lambda p: p["price_pkr"], reverse=not ascending)
