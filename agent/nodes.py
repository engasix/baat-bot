"""
LangGraph nodes for Baat Bot.

assistant_node — handles every turn: greeting, Q&A, RAG lookup, order detection.
transfer_node  — placeholder for call transfer (prints message in terminal mode).
"""

import os

from anthropic import Anthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.state import State
from rag import build_index, search
from rag.retriever import cheapest, most_premium

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    return _client


# ── System prompt ──────────────────────────────────────────────────────────────

_BASE_SYSTEM = """\
آپ عائشہ ہیں — Pure Scents کی AI اسسٹنٹ جو فون پر اردو میں بات کرتی ہیں۔
Pure Scents پاکستان کا ایک معیاری پرفیوم اسٹور ہے۔

آپ کا کام:
- صرف اردو میں جواب دیں (رومن اردو نہیں، اصل اردو رسم الخط)
- گاہک کے سوالوں کا جواب نیچے دیے گئے کیٹالاگ سے دیں
- قیمت، خوشبو، برانڈ کے بارے میں درست معلومات دیں
- گرم جوشی اور تہذیب سے بات کریں جیسے ایک اصلی دکاندار
- جواب مختصر اور واضح رکھیں — جیسے فون پر بولتے ہیں
- ایک وقت میں زیادہ سے زیادہ 2-3 پرفیوم تجویز کریں

اگر گاہک آرڈر کرنا چاہے، خریدنا چاہے، یا واضح طور پر کہے:
  "لینا ہے" / "آرڈر کرنا ہے" / "خریدنا ہے" / "بھیج دیں" / "le lo" وغیرہ
تو اپنا جواب بالکل اس طرح شروع کریں:
  [TRANSFER]
اور پھر کہیں: "بہت اچھا! میں ابھی آپ کو ہمارے آرڈر ڈیپارٹمنٹ سے connect کرتی ہوں۔"

{rag_section}
"""

_RAG_SECTION = """\
آج کے دستیاب پرفیوم (اس سوال سے متعلق):
{context}

(اگر گاہک کچھ اور پوچھے جو اوپر نہ ہو تو عمومی معلومات دیں۔)
"""


def _build_system(rag_context: str) -> str:
    if rag_context:
        rag_section = _RAG_SECTION.format(context=rag_context)
    else:
        rag_section = ""
    return _BASE_SYSTEM.format(rag_section=rag_section)


# ── RAG helper ─────────────────────────────────────────────────────────────────

def _run_rag(query: str) -> str:
    """
    Decide which retrieval function to use based on the query,
    then format results as a compact text block for the system prompt.
    """
    q_lower = query.lower()

    # Price-sorted queries — use metadata sort, not semantic search
    if any(w in q_lower for w in ["سستا", "سستی", "کم قیمت", "cheap", "budget", "سب سے کم"]):
        perfumes = cheapest(n=3)
    elif any(w in q_lower for w in ["مہنگا", "مہنگی", "پریمیم", "premium", "luxury", "سب سے اچھا", "بہترین"]):
        perfumes = most_premium(n=3)
    else:
        # Detect gender filter
        gender = None
        if any(w in q_lower for w in ["مرد", "مردوں", "مردانہ", "men", "man", "لڑکے", "boys"]):
            gender = "men"
        elif any(w in q_lower for w in ["عورت", "عورتوں", "زنانہ", "خواتین", "women", "woman", "لڑکی", "girls"]):
            gender = "women"
        perfumes = search(query, n_results=3, gender=gender)

    if not perfumes:
        return ""

    lines = []
    for p in perfumes:
        stock = "دستیاب" if p["in_stock"] else "دستیاب نہیں"
        lines.append(
            f"• {p['name']} از {p['brand']} | {p['size_ml']}ml | "
            f"Rs {p['price_pkr']:,} | {stock}\n"
            f"  {p['description_ur']}"
        )
    return "\n".join(lines)


# ── Nodes ──────────────────────────────────────────────────────────────────────

def assistant_node(state: State) -> dict:
    """
    Main Q&A node. Runs every turn:
      1. Pull last user message
      2. Run RAG retrieval
      3. Build system prompt with catalog context
      4. Call Claude Sonnet 4.6
      5. Detect [TRANSFER] marker → set transfer=True
    """
    # Get last human message for RAG
    user_msg = ""
    for msg in reversed(state["convo"]):
        if isinstance(msg, HumanMessage):
            user_msg = msg.content
            break

    # RAG retrieval
    rag_context = _run_rag(user_msg) if user_msg else ""

    # Build messages for Claude (convert LangChain messages → Anthropic format)
    messages = []
    for msg in state["convo"]:
        if isinstance(msg, HumanMessage):
            messages.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            messages.append({"role": "assistant", "content": msg.content})

    system_prompt = _build_system(rag_context)

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=system_prompt,
        messages=messages,
    )

    reply = response.content[0].text.strip()

    # Detect transfer intent
    transfer = reply.startswith("[TRANSFER]")
    if transfer:
        reply = reply[len("[TRANSFER]"):].strip()

    return {
        "convo":       [AIMessage(content=reply)],
        "rag_context": rag_context,
        "transfer":    transfer,
    }


def transfer_node(state: State) -> dict:
    """
    In terminal mode: just prints the handoff message.
    In phone mode: will trigger ARI call redirect.
    """
    # Message was already added by assistant_node
    return {}
