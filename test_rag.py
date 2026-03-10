"""
Phase 6 verification script — run with:  uv run python test_rag.py

Tests:
  1. Index builds successfully
  2. Cheapest perfumes query
  3. Most premium perfumes query
  4. Men's fragrance query (Urdu)
  5. Women's fragrance query (Urdu)
  6. Specific scent query — oud
  7. Best smelling for gifting query
"""

import sys
from rag import build_index, cheapest, most_premium, search


def fmt(p: dict) -> str:
    stock = "✓" if p["in_stock"] else "✗"
    return (
        f"  [{stock}] {p['name']} by {p['brand']} "
        f"| {p['gender']} | Rs {p['price_pkr']:,} | {p['size_ml']}ml"
    )


def run_query(label: str, results: list[dict]) -> None:
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    if not results:
        print("  (no results)")
    for p in results:
        print(fmt(p))


def main() -> None:
    print("=" * 60)
    print("  Baat Bot — RAG Phase 6 Verification")
    print("=" * 60)

    print("\n[1] Building index ...")
    build_index()

    # Price-based queries (metadata)
    run_query(
        "سب سے سستا پرفیوم — Cheapest perfumes",
        cheapest(n=3),
    )
    run_query(
        "سب سے مہنگا / پریمیم پرفیوم — Most premium perfumes",
        most_premium(n=3),
    )

    # Semantic queries in Urdu
    run_query(
        "مردوں کے لیے بہترین خوشبو — Best for men (Urdu)",
        search("مردوں کے لیے بہترین خوشبو", n_results=3, gender="men"),
    )
    run_query(
        "عورتوں کے لیے بہترین پرفیوم — Best for women (Urdu)",
        search("عورتوں کے لیے بہترین خوشبو", n_results=3, gender="women"),
    )
    run_query(
        "عود والا پرفیوم — Oud fragrance query",
        search("عود والا مہنگا پرفیوم", n_results=3),
    )
    run_query(
        "تحفے کے لیے بہترین — Best for gifting",
        search("کسی کو تحفے میں دینے کے لیے بہترین پرفیوم", n_results=3),
    )
    run_query(
        "Sauvage price query — انگریزی سوال",
        search("What is the price of Sauvage by Dior?", n_results=1),
    )
    run_query(
        "گرمیوں کے لیے تازہ خوشبو — Fresh summer fragrance",
        search("گرمیوں میں تازہ اور ہلکی خوشبو", n_results=3),
    )

    print(f"\n{'='*60}")
    print("  All queries completed successfully ✓")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
