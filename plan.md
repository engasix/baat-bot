# Baat Bot — AI Voice Agent (Urdu / Pakistan)

An AI voice agent for a perfume e-commerce store. Caller speaks Urdu → agent answers product questions using RAG → transfers to a human agent when caller is ready to order.

---

## Architecture

```
Caller → SIP Phone → Asterisk (Docker)
                          │
               ARI WebSocket + ExternalMedia RTP
                          │
                    Python App (main.py)
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   Deepgram STT     LangGraph Agent    Google TTS
   (streaming, Urdu)  (RAG + LLM)     (ur-IN, 8kHz)
                          │
                    ChromaDB (perfumes)
                          │
              order intent? → Transfer to Human Agent
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
