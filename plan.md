# Baat Bot — AI Voice Agent (Urdu / Pakistan)

An AI voice agent for a perfume e-commerce store. Caller speaks Urdu → agent answers product questions using RAG → transfers to a human agent when caller is ready to order.

---

## Architecture

### High-Level Call Flow

```
Pakistani Caller
      │  SIP / RTP (voice)
      ▼
┌─────────────────────────────────────┐
│         Asterisk  (Docker)          │
│  PJSIP — SIP registration           │
│  extensions.conf — ext 1000         │
│    → Stasis(baat_bot)               │
│                                     │
│  Control plane: ARI WebSocket       │   ws://localhost:8088/ari/events
│  Audio plane:   ExternalMedia RTP   │   UDP  host:7000  ←→  Docker
└──────────┬──────────────────────────┘
           │  StasisStart event (ARI WS)
           │  Raw RTP audio (UDP :7000)
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        main.py  (Python app)                        │
│                                                                     │
│  on StasisStart:                                                    │
│    answer_channel()          POST /ari/channels/{id}/answer         │
│    setup_media_bridge()      POST /ari/channels/externalMedia       │
│                              POST /ari/bridges  (mixing)            │
│                              POST /ari/bridges/{id}/addChannel × 2  │
│    warmup() already ran at startup — RAG + LLM hot in RAM          │
│    play welcome TTS immediately                                     │
│                                                                     │
│  main loop:  receive_task ──────────────────────────────────────►  │
│              (reads UDP frames)          speak_task                 │
│                    │                  (sends UDP frames)            │
│                    ▼                         ▲                      │
│              ┌───────────┐           ┌───────────────┐             │
│              │  STT svc  │           │    TTS svc    │             │
│              └───────────┘           └───────────────┘             │
│                    │  transcript             ▲                      │
│                    ▼                         │ Urdu reply text      │
│              ┌─────────────────────────────────────────────┐       │
│              │              LangGraph Agent                │       │
│              │                                             │       │
│              │  assistant_node:                            │       │
│              │    1. get last user message                 │       │
│              │    2. RAG retrieval (if needed)             │       │
│              │    3. build system prompt + catalog context │       │
│              │    4. call LLM                              │       │
│              │    5. detect [TRANSFER] marker              │       │
│              │                                             │       │
│              │  transfer=True? → transfer_node             │       │
│              │    → ARI redirect to human agent ext        │       │
│              └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────┘
```

---

### STT — Speech to Text

```
UDP :7000
  │  raw RTP frames (8kHz, 16-bit PCM, 20ms/frame = 320 bytes)
  │  byte-swap big-endian → little-endian
  ▼
services/stt/deepgram.py
  │  persistent WebSocket per call
  │  ALL frames streamed in real-time — no client-side VAD
  │  Deepgram endpointing=500ms → fires final transcript
  │  model=nova-3, language=ur, encoding=linear16, sample_rate=8000
  ▼
transcript string (Urdu text)
  │
  └──► passed to LangGraph agent
```

Key detail: Deepgram handles silence detection internally. We stream every frame
and wait for `is_final=True` — no WebRTC VAD needed on the Python side.

---

### RAG — Retrieval Augmented Generation

```
data/perfumes.json  (20 perfumes, source of truth)
  │
  ▼  rag/catalog.py → to_document()
  │  builds bilingual text per perfume:
  │    Urdu name + brand + gender keywords + scent + price tier word
  │    English name + brand + scent notes
  │  (bilingual so Urdu STT transcripts match English brand names)
  │
  ▼  rag/embedder.py → embed_documents()
  │  model: intfloat/multilingual-e5-small  (~120MB, local, free)
  │  prefix: "passage: " + text  (required by e5 models)
  │
  ▼  rag/retriever.py → build_index()
  │  ChromaDB (persistent, cosine similarity)
  │  stored in:  data/chroma_db/
  │  collection: "perfumes"
  │
  ├── search(query, n=3, gender=None)
  │     embed_query("query: " + text) → cosine nearest neighbours
  │     optional metadata filter: {gender: {$in: [gender, "unisex"]}}
  │
  ├── cheapest(n=3)   → sorted by price_pkr ASC  (metadata sort, no embedding)
  └── most_premium(n) → sorted by price_pkr DESC
```

RAG gating — `_needs_rag(query)` returns False for:
- Greetings (`سلام`, `hello`) → skip embedding, answer directly
- Thanks / farewell → skip
- Order intent (`لینا ہے`, `آرڈر`) → skip, Claude handles [TRANSFER]

This saves ~30ms per turn on non-product queries.

---

### LangGraph Agent

```
State:
  convo        list[BaseMessage]  — full conversation history (add_messages reducer)
  rag_context  str                — top-3 perfumes formatted for system prompt
  transfer     bool               — True = hand off to human agent

Graph:
  [START] → assistant_node → _route
                               ├── transfer=True  → transfer_node → [END]
                               └── transfer=False → [END]  (loop managed externally)

assistant_node (runs every turn):
  1. extract last HumanMessage from convo
  2. _needs_rag(query)?
       yes → _run_rag(query):
               detect gender keyword → set gender filter
               detect price keyword  → cheapest() or most_premium() + filter
               else                  → search() semantic
             → format top-3 as compact text block
       no  → rag_context = ""
  3. build system prompt:
       _BASE_SYSTEM + (RAG section if rag_context else "")
  4. LLM call:
       [SystemMessage(system_prompt)] + list(state["convo"])
       model = init_chat_model("gpt-4o", max_tokens=512)
               ↑ change one string to swap model
  5. detect [TRANSFER] prefix in reply → transfer=True
  6. return {convo: [AIMessage(reply)], rag_context, transfer}

transfer_node:
  terminal mode  → prints handoff message
  phone mode     → ARI POST /ari/channels/{id}/redirect → human agent ext
```

System prompt (Urdu) instructs the LLM to:
- Reply only in Urdu script (not Roman Urdu)
- Answer only from the provided catalog — no invented perfumes
- One or two short sentences — voice-friendly, no bullets, no emoji
- Handle personal questions warmly ("آپ کیسی ہیں" → warm one-line reply)
- Prefix reply with `[TRANSFER]` on any order intent

---

### TTS — Text to Speech

```
Urdu reply text (from LangGraph)
  │
  ▼  services/tts/google.py
  │  Google Chirp3-HD  (ur-IN-Chirp3-HD-Aoede)
  │  output: LINEAR16, 8kHz, mono
  │  WAV header stripped (first 44 bytes) → raw PCM bytes
  │
  ▼  sentence-chunked streaming (Phase 8+):
  │    split on  ۔  .  ?  !
  │    synthesize sentence 1 → start playing immediately
  │    synthesize sentence 2 in parallel
  │    caller hears sentence 1 while sentence 2 synthesizes
  │
  ▼  services/rtp.py → encode_rtp()
  │  chunk into 320-byte frames (160 samples × 2 bytes)
  │  byte-swap little-endian → big-endian (RTP wire format)
  │  add 12-byte RTP header (PT=10, seq, timestamp, SSRC)
  │
  ▼  UDP socket → Asterisk ExternalMedia → Caller hears Urdu reply
```

---

### Turn-by-Turn Conversation Flow

```
1. Caller dials 1000
2. Asterisk fires StasisStart → Python answers + bridges ExternalMedia
3. Welcome TTS plays immediately  ("السلام علیکم! Pure Scents...")
4. Caller speaks Urdu
5. RTP frames → Deepgram WebSocket (streaming)
6. Deepgram endpointing fires → transcript arrives
7. _needs_rag(transcript)?
     yes → embed transcript → ChromaDB → top-3 perfumes
     no  → skip
8. System prompt built with catalog context (or empty)
9. LLM called with full conversation history
10. LLM reply (Urdu, 1–2 sentences)
    → [TRANSFER]? → ARI call redirect → human agent picks up
    → else        → TTS → RTP → caller hears answer
11. Go to step 4 (next turn)
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
