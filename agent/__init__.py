import time

from langchain_core.messages import HumanMessage, SystemMessage

from agent.graph import app
from agent.nodes import _llm
from agent.state import State
from rag import build_index
from rag.embedder import embed_query
__all__ = ["app", "State", "warmup"]


def warmup() -> None:
    """
    Preload everything before the first caller connects:
      1. Build / verify RAG index (loads embedding model into RAM)
      2. Send a dummy query through the embedding model
      3. Send a dummy message to the LLM (establishes connection, warms cache)
      4. Pre-synthesize all filler phrases

    Call this once at startup. First real caller gets instant responses.
    """
    print("[WARMUP] Starting ...")
    t0 = time.monotonic()

    # ── Step 1: RAG index + embedding model ───────────────────────────────────
    print("[WARMUP] Loading RAG index and embedding model ...")
    build_index()
    embed_query("warmup")          # forces model weights into RAM
    print("[WARMUP] Embedding model ready")

    # ── Step 2: LLM warm-up ────────────────────────────────────────────────────
    print("[WARMUP] Pinging LLM ...")
    _llm.invoke([
        SystemMessage(content="You are a helpful assistant. Reply with one word only."),
        HumanMessage(content="ready?"),
    ])
    print("[WARMUP] LLM ready")

    elapsed = time.monotonic() - t0
    print(f"[WARMUP] Done in {elapsed:.1f}s — system ready for calls\n")
