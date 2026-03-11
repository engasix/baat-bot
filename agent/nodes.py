"""
LangGraph nodes for Baat Bot.

assistant_node — handles every turn: greeting, Q&A, RAG lookup, order detection.
transfer_node  — placeholder for call transfer (prints message in terminal mode).
"""

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.state import State
from rag import search
from rag.retriever import cheapest, most_premium

# To switch model, change only this string — e.g. "gpt-4o", "gemini-1.5-pro"
# claude-sonnet-4-6
_llm = init_chat_model(
    "claude-sonnet-4-6", 
    max_tokens=512
)


# ── System prompt ──────────────────────────────────────────────────────────────

_BASE_SYSTEM = """\
آپ عائشہ ہیں — Pure Scents کی AI اسسٹنٹ جو فون پر اردو میں بات کرتی ہیں۔
Pure Scents پاکستان کا ایک معیاری پرفیوم اسٹور ہے۔

آپ کا کام:
- صرف اردو میں جواب دیں (رومن اردو نہیں، اصل اردو رسم الخط)
- صرف نیچے دیے گئے کیٹالاگ میں موجود پرفیوم کے بارے میں بات کریں
- کیٹالاگ سے باہر کوئی پرفیوم خود سے مت بنائیں یا تجویز کریں — اگر کیٹالاگ میں نہیں ہے تو کہیں "یہ ہمارے پاس دستیاب نہیں"
- قیمت، خوشبو، برانڈ صرف کیٹالاگ کی معلومات کے مطابق بتائیں
- گرم جوشی اور تہذیب سے بات کریں جیسے ایک اصلی دکاندار
- جواب صرف ایک یا دو مختصر جملوں میں دیں — جیسے فون پر بولتے ہیں
- کوئی بھی formatting مت کریں — نہ bullets، نہ numbers، نہ bold، نہ emoji
- ایک ہی مسلسل جملے میں جواب دیں، معلومات دہرائیں نہیں
- سوال کے آخر میں اضافی سوال مت پوچھیں جب تک گاہک خود رہنمائی نہ مانگے

اگر گاہک ذاتی یا دوستانہ سوال پوچھے جیسے "آپ کیسی ہیں؟" / "آپ کا نام کیا ہے؟" / "کیا آپ انسان ہیں؟":
- گرم جوشی سے مختصر جواب دیں جیسے ایک اصلی دکاندار دیتا ہے
- یہ سوالات بالکل ٹھیک ہیں، معذرت مت کریں

اگر گاہک بالکل غیر متعلقہ سوال پوچھے (کھانا، کرکٹ، ملازمت، سیاست وغیرہ):
- شائستگی سے ایک جملے میں کہیں کہ آپ صرف پرفیوم کے بارے میں مدد کر سکتی ہیں
- فوری طور پر گفتگو کو پرفیوم کی طرف موڑ دیں

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

_NO_RAG_PATTERNS = [
    # Greetings
    "سلام", "السلام", "ہیلو", "hello", "hi ", "assalam",
    # Thanks / farewell
    "شکریہ", "مہربانی", "خدا حافظ", "bye", "thanks",
    # Identity / chitchat
    "آپ کا نام", "تم کون", "کیا آپ", "کیسی ہیں", "کیسے ہو",
    # Order intent (Claude handles TRANSFER — no catalog data needed)
    "لینا ہے", "خریدنا", "آرڈر", "بھیج دیں",
]


def _needs_rag(query: str) -> bool:
    """Return False for greetings, chitchat, and order intent — no retrieval needed."""
    q_lower = query.lower()
    return not any(p in q_lower for p in _NO_RAG_PATTERNS)


def _run_rag(query: str) -> str:
    """
    Decide which retrieval function to use based on the query,
    then format results as a compact text block for the system prompt.
    """
    q_lower = query.lower()

    # Detect gender first — applies to both price-sorted and semantic queries
    gender = None
    if any(w in q_lower for w in ["مرد", "مردوں", "مردانہ", "men", "man", "لڑکے", "boys"]):
        gender = "men"
    elif any(w in q_lower for w in ["عورت", "عورتوں", "زنانہ", "خواتین", "women", "woman", "لڑکی", "girls"]):
        gender = "women"

    # Price-sorted queries — use metadata sort but respect gender filter
    if any(w in q_lower for w in ["سستا", "سستی", "کم قیمت", "cheap", "budget", "سب سے کم"]):
        all_cheap = cheapest(n=10)
        perfumes = [p for p in all_cheap if not gender or p["gender"] in (gender, "unisex")][:3]
    elif any(w in q_lower for w in ["مہنگا", "مہنگی", "پریمیم", "premium", "luxury", "سب سے اچھا", "بہترین"]):
        all_premium = most_premium(n=10)
        perfumes = [p for p in all_premium if not gender or p["gender"] in (gender, "unisex")][:3]
    else:
        perfumes = search(query, n_results=3, gender=gender)

    if not perfumes:
        return ""

    lines = []
    for p in perfumes:
        stock = "دستیاب" if p["in_stock"] else "دستیاب نہیں"
        lines.append(
            f"• {p['name']} by {p['brand']} | {p['size_ml']}ml | "
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

    # RAG retrieval — only when the query is actually about perfumes
    rag_context = _run_rag(user_msg) if (user_msg and _needs_rag(user_msg)) else ""

    # Build message list: SystemMessage + full conversation history
    messages = [SystemMessage(content=_build_system(rag_context))] + list(state["convo"])

    response = _llm.invoke(messages)
    reply = response.content.strip()

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
