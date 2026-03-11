"""
RAG interface — import this everywhere outside the rag/ package.

Usage:
    from rag import build_index, search, cheapest, most_premium

    build_index()                                  # call once at startup
    results = search("مردوں کے لیے بہترین خوشبو")  # returns list[dict]
    results = search("floral women", gender="women")
    cheap   = cheapest(n=3)
    premium = most_premium(n=3)
"""

from rag.retriever import bestsellers, build_index, cheapest, most_premium, search

__all__ = ["build_index", "search", "cheapest", "most_premium", "bestsellers"]
