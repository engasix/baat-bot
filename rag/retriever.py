"""
ChromaDB-backed retriever for the perfume catalog.

build_index()  — call once at startup (idempotent)
search()       — semantic search, returns list[dict] perfume records
cheapest()     — sorted by price ascending
most_premium() — sorted by price descending
"""

from __future__ import annotations

from pathlib import Path

import chromadb

from rag import catalog, embedder

COLLECTION_NAME = "perfumes"
_CHROMA_PATH    = str(Path(__file__).parent.parent / "data" / "chroma_db")

_client:     chromadb.PersistentClient | None = None
_collection: chromadb.Collection       | None = None


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        _client     = chromadb.PersistentClient(path=_CHROMA_PATH)
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def build_index() -> None:
    """
    Embed all perfumes from data/perfumes.json and upsert into ChromaDB.
    Safe to call on every startup — only new/missing entries are added.
    """
    perfumes = catalog.load()
    col      = _get_collection()

    existing_ids = set(col.get(include=[])["ids"])
    to_add       = [p for p in perfumes if p["id"] not in existing_ids]

    if not to_add:
        print(f"[RAG] Index current — {len(existing_ids)} perfumes already indexed")
        return

    docs       = [catalog.to_document(p) for p in to_add]
    embeddings = embedder.embed_documents(docs)

    col.add(
        ids        = [p["id"] for p in to_add],
        documents  = docs,
        embeddings = embeddings,
        metadatas  = [
            {
                "name":          p["name"],
                "brand":         p["brand"],
                "gender":        p["gender"],
                "price_pkr":     p["price_pkr"],
                "size_ml":       p["size_ml"],
                "in_stock":      p["in_stock"],
                "category":      p["category"],
                "monthly_sales": p.get("monthly_sales", 0),
            }
            for p in to_add
        ],
    )
    print(f"[RAG] Indexed {len(to_add)} new perfumes (total={len(existing_ids) + len(to_add)})")


def search(
    query:     str,
    n_results: int       = 3,
    gender:    str | None = None,
    in_stock:  bool      = True,
) -> list[dict]:
    """
    Semantic search over the perfume catalog.

    Args:
        query:     User's Urdu (or English) question
        n_results: How many results to return
        gender:    Optional filter — "men", "women", or "unisex"
        in_stock:  If True, only return available perfumes

    Returns:
        List of full perfume dicts from the catalog, ranked by relevance.
    """
    col             = _get_collection()
    query_embedding = embedder.embed_query(query)

    # Build ChromaDB where filter
    where_conditions = []
    if gender:
        where_conditions.append({"gender": {"$eq": gender}})
    if in_stock:
        where_conditions.append({"in_stock": {"$eq": True}})

    if len(where_conditions) == 1:
        where = where_conditions[0]
    elif len(where_conditions) > 1:
        where = {"$and": where_conditions}
    else:
        where = None

    kwargs = dict(
        query_embeddings = [query_embedding],
        n_results        = n_results,
        include          = ["metadatas", "distances"],
    )
    if where:
        kwargs["where"] = where

    results = col.query(**kwargs)

    # Map matched IDs back to full catalog dicts
    perfume_map = {p["id"]: p for p in catalog.load()}
    return [perfume_map[pid] for pid in results["ids"][0] if pid in perfume_map]


def cheapest(n: int = 3) -> list[dict]:
    """Return the n cheapest in-stock perfumes sorted by price."""
    return catalog.get_sorted_by_price(ascending=True, in_stock_only=True)[:n]


def most_premium(n: int = 3) -> list[dict]:
    """Return the n most expensive in-stock perfumes sorted by price."""
    return catalog.get_sorted_by_price(ascending=False, in_stock_only=True)[:n]


def bestsellers(n: int = 3) -> list[dict]:
    """Return the n best-selling in-stock perfumes sorted by monthly_sales descending."""
    perfumes = [p for p in catalog.load() if p.get("in_stock", True)]
    return sorted(perfumes, key=lambda p: p.get("monthly_sales", 0), reverse=True)[:n]
