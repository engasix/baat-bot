"""
Embedding model wrapper using intfloat/multilingual-e5-small.

- Supports 100+ languages including Urdu
- ~120 MB download, runs locally (no API cost)
- multilingual-e5 requires "passage: " / "query: " prefixes
- Loaded once and cached for the process lifetime
"""

from sentence_transformers import SentenceTransformer

_MODEL_NAME = "intfloat/multilingual-e5-small"
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"[RAG] Loading embedding model ({_MODEL_NAME}) — first run downloads ~120 MB ...")
        _model = SentenceTransformer(_MODEL_NAME)
        print("[RAG] Embedding model ready")
    return _model


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed catalog documents (use 'passage:' prefix as required by e5 models)."""
    prefixed = [f"passage: {t}" for t in texts]
    return _get_model().encode(prefixed, show_progress_bar=False).tolist()


def embed_query(text: str) -> list[float]:
    """Embed a user query (use 'query:' prefix as required by e5 models)."""
    return _get_model().encode(f"query: {text}", show_progress_bar=False).tolist()
