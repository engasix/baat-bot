# Baat Bot — AI Voice Agent (Urdu / Pakistan)

An AI voice agent for a perfume e-commerce store. Caller speaks Urdu → agent answers product questions using RAG → transfers to a human agent when caller is ready to order.

---

## Architecture

```
  Caller (Urdu)
       │
       │  SIP  (call setup)
       ▼
  ┌─────────────┐
  │   Asterisk  │  receives the call, routes it to our Python app
  └──────┬──────┘
         │
         ├─── ARI WebSocket ──► call events  (answer, hangup, transfer)
         │
         └─── RTP (raw audio) ─────────────────────────────────────┐
                                                                   ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │                          Python App                                 │
  │                                                                     │
  │   ┌──────────────────┐   Urdu    ┌───────────────────┐              │
  │   │   Deepgram STT   │ ────────► │  LangGraph Agent  │              │
  │   │  (WebSocket API) │  text     │                   │              │
  │   └──────────────────┘           │  ┌─────────────┐  │              │
  │   streams raw audio              │  │     RAG     │  │              │
  │   in real time →                 │  │  ChromaDB   │  │              │
  │   returns Urdu transcript        │  │  perfumes   │  │              │
  │                                  │  └─────────────┘  │              │
  │                                  │  looks up catalog │              │
  │                                  │  builds Urdu reply│              │
  │                                  └────────┬──────────┘              │
  │                                           │ Urdu text               │
  │                                  ┌────────▼──────────┐              │
  │                                  │    Google TTS     │              │
  │                                  │  (WebSocket API)  │              │
  │                                  └────────┬──────────┘              │
  │                                           │ raw audio               │
  └───────────────────────────────────────────┼─────────────────────────┘
                                              │
                                              │  RTP  (raw audio back)
                                              ▼
                                         Asterisk → Caller hears
                                           the Urdu reply
```

---

### How Each Piece Works

**Asterisk** receives the incoming SIP call. It uses ARI (Asterisk REST Interface)
over a WebSocket to send call events (answered, hung up) to our Python app.
The actual voice audio travels separately over RTP — a lightweight protocol
designed for real-time audio streaming.

**Deepgram STT** receives the raw RTP audio and streams it to Deepgram's cloud
over a WebSocket. Deepgram detects when the caller has stopped talking and
returns a clean Urdu transcript in real time — no pre-recording, no waiting.

**LangGraph Agent** receives the transcript and decides what to do:

- If the question is about perfumes → asks RAG for relevant products
- If it's a greeting or chitchat → answers directly, no catalog lookup
- If the caller wants to buy → says "connecting you now" and transfers the call

**RAG (ChromaDB + multilingual-e5-small)** finds the most relevant perfumes
from our catalog of 20 products. It understands Urdu queries and can also
sort by price (cheapest / most premium) or filter by gender.

**LLM (GPT-4o / Claude / any model)** takes the catalog results and the full
conversation history and writes a natural, warm Urdu reply — one or two sentences,
no lists, no formatting, just how a real shopkeeper would talk on the phone.

**Google TTS** converts the Urdu text into voice audio over a WebSocket API.
The audio is sent back to Asterisk as RTP, which plays it directly to the caller.

---

### Conversation Flow

```
  Caller speaks
       │
       ▼
  Deepgram hears it → Urdu text
       │
       ▼
  Is it about perfumes?
    ├── Yes → RAG finds matching products → LLM answers with catalog info
    ├── No  → LLM answers directly (greetings, chitchat)
    └── Wants to buy? → "connecting you now" → call transfers to human agent
       │
       ▼
  Google TTS → caller hears the reply
       │
       ▼
  (repeat for next turn)
```

---

## Stack

| Layer | Tool |
|---|---|
| Telephony | Asterisk 20+ via Docker (ARI + PJSIP) |
| SIP Client | Linphone / Zoiper (mobile) |
| Audio Bridge | ExternalMedia RTP → UDP port 7000 |
| STT | Deepgram Nova-3 (WebSocket, language=ur) |
| Agent | LangGraph + `init_chat_model` (swap model in one line) |
| RAG Embeddings | `multilingual-e5-small` (local, free, supports Urdu) |
| RAG Vector DB | ChromaDB (local, persistent) |
| TTS | Google Chirp3-HD (ur-IN, 8kHz) / ElevenLabs (alternative) |
| Call Transfer | Asterisk ARI redirect → human agent |

---

## Project Structure

```
baat_bot/
├── docker-compose.yml       # Asterisk container
├── config/                  # mounted into Asterisk container
│   ├── pjsip.conf           # SIP endpoints
│   ├── extensions.conf      # dialplan: ext 1000 → Stasis(baat_bot)
│   ├── ari.conf             # ARI credentials
│   └── http.conf            # ARI HTTP on port 8088
├── data/
│   ├── perfumes.json        # catalog — source of truth
│   └── chroma_db/           # generated at startup, NOT in git
├── rag/
│   ├── catalog.py           # load catalog + build bilingual search text
│   ├── embedder.py          # multilingual-e5-small embeddings
│   └── retriever.py         # build_index() / search() / cheapest() / most_premium()
├── agent/
│   ├── __init__.py          # warmup() — preloads model + LLM at startup
│   ├── state.py             # State: convo, rag_context, transfer
│   ├── nodes.py             # assistant_node (RAG + LLM), transfer_node
│   ├── graph.py             # START → assistant → (transfer | loop)
│   └── agent.py             # standalone terminal test
├── services/
│   ├── rtp.py               # UDP audio stream
│   ├── tts/                 # modular TTS (google.py, elevenlabs.py)
│   └── stt/                 # modular STT (deepgram.py, google.py, openai.py)
├── main.py                  # ARI WebSocket + full pipeline
├── test_rag.py              # RAG verification script
└── .env                     # API keys (never committed)
```

---

## Running Asterisk

```bash
docker compose up -d       # start
docker compose logs -f     # watch logs
docker compose down        # stop
```

Config files in `config/` are mounted directly — edit them there and restart Docker. Never edit inside the container.

---

## Running the Python App

```bash
# Install dependencies
uv sync

# Terminal agent test (no phone needed)
uv run python agent/agent.py

# Full phone pipeline
uv run python main.py
```

---

## ChromaDB Index

`data/chroma_db/` is not in git — it is built from `data/perfumes.json` at startup.

**Build and verify:**
```bash
uv run python test_rag.py
```

**Or just start the app** — `warmup()` calls `build_index()` automatically.

**After editing `perfumes.json`:**
```bash
rm -rf data/chroma_db/
uv run python test_rag.py
```

---

## System Warmup

The embedding model (~120MB) and LLM both have cold-start costs. Without warmup the first caller hears 10-15 seconds of silence.

`warmup()` runs at startup and:
1. Loads ChromaDB index + embedding model into RAM
2. Sends a dummy query to the LLM to establish connection

After warmup (~12s first run, ~3s subsequent), every caller gets <1s responses.

```python
# In main.py (phone system)
await asyncio.to_thread(warmup)
```

---

## Human Agent Handoff

The bot answers questions only. When the caller says "لینا ہے" / "آرڈر کرنا ہے" / any clear purchase intent, it says "connecting you now" and transfers the call to a human agent via ARI.

**Why not let the bot take the order:**
- STT errors in address digits → wrong delivery
- High-value perfumes (Rs 14k–52k) — customers prefer a human for payment
- A human verifies everything before processing, zero wrong orders

---

## Environment Variables (`.env`)

```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPGRAM_API_KEY=...
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
ARI_URL=http://localhost:8088
ARI_USER=baat_bot
ARI_PASSWORD=...
RTP_HOST=host.docker.internal
RTP_PORT=7000
```

---

## Live Deployment (Pakistani Phone Number)

Get a Pakistani DID from Twilio / DIDWW / Zadarma (~$1-5/month), add a SIP trunk to `pjsip.conf`, and make Asterisk publicly reachable (port forward or VPS). The Python app, agent, RAG, and TTS require zero changes.
