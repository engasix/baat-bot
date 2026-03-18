# Baat Bot — AI Voice Agent for E-Commerce (Urdu)

An autonomous voice agent that handles customer calls for e-commerce stores in real-time Urdu — answers product questions using RAG, takes orders, and confirms delivery. Plug in any product catalog and it works for any store.

---

## Demo

[![Baat Bot — AI Voice Agent Demo](https://img.youtube.com/vi/OYgPYx1ogZg/maxresdefault.jpg)](https://www.youtube.com/watch?v=OYgPYx1ogZg)

---

## How It Works

> Customer calls → greeting → product Q&A (RAG) → place order → confirm items + address → save order → goodbye

Barge-in supported — customer can interrupt the bot mid-sentence at any time.

---

## Architecture

### System Overview

```text
                        SIP / RTP
  Caller (Urdu)  ──────────────────►  Asterisk (ARI)
       ▲                                    │
       │                         ┌──────────┴──────────┐
       │                         │ WebSocket           │ RTP/UDP
       │                         │ (call control)      │ (raw audio)
       │                         ▼                     ▼
       │                    ┌─────────────────────────────┐
       └────────────────────│      Python App             │
          audio response    │      main.py                │
                            └─────────────────────────────┘
```

### Processing Pipeline

```text
  Incoming RTP audio
         │
         ▼
  ┌─────────────────┐
  │   webrtcvad     │  detects end of speech  (~500ms silence)
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ Deepgram  STT   │  streaming Urdu transcription  (~150ms)
  └────────┬────────┘
           │  Urdu transcript
           ▼
  ┌──────────────────────────────────┐
  │         LangGraph Agent          │
  │                                  │
  │  ┌────────────────────────────┐  │
  │  │  RAG  (browsing phase)     │  │
  │  │  ChromaDB + mE5-small      │  │  semantic product search
  │  │  top-3 matches → context   │  │
  │  └────────────────────────────┘  │
  │                                  │
  │  Claude Sonnet 4.6  (streaming)  │  generates Urdu response
  └────────┬─────────────────────────┘
           │  Urdu response text
           ▼
  ┌─────────────────┐
  │ Google Cloud    │  sentence-by-sentence synthesis  (~200ms first chunk)
  │ TTS  (ur-PK)    │
  └────────┬────────┘
           │
           ▼
  Outgoing RTP audio  ──►  Asterisk  ──►  Caller hears response
```

**Barge-in:** `receive_task` and `speak_task` run concurrently. If the caller speaks while the bot is talking, TTS stops within ~200ms and the bot listens.

---

## Tech Stack

| Layer | Tool |
| --- | --- |
| Telephony | Asterisk (ARI + ExternalMedia) |
| STT | Deepgram WebSocket (`language=ur`) |
| VAD | `webrtcvad` |
| AI Agent | LangGraph + Claude Sonnet 4.6 |
| RAG — Embeddings | `multilingual-e5-small` (local, free) |
| RAG — Vector DB | ChromaDB (local, persistent) |
| TTS | Google Cloud TTS (`ur-PK`) |
| Package Manager | `uv` |

---

## Project Structure

```text
baat_bot/
├── config/
│   ├── ari.conf            # Asterisk ARI config
│   ├── sip.conf            # SIP extension config
│   └── extensions.conf     # Dialplan (Stasis)
├── data/
│   └── perfumes.json       # Men + women perfume catalog
├── rag/
│   ├── catalog.py          # Load & prepare catalog documents
│   ├── embedder.py         # multilingual-e5-small embeddings
│   └── retriever.py        # ChromaDB semantic search
├── agent/
│   ├── state.py            # LangGraph OrderState
│   ├── nodes.py            # One node per conversation phase
│   └── graph.py            # Compiled state machine
├── services/
│   ├── rtp.py              # RTP packet encode/decode
│   ├── stt.py              # Deepgram streaming STT
│   └── tts.py              # Google Cloud TTS, sentence-chunked
├── main.py                 # ARI app + pipeline orchestration
└── phases.md               # Step-by-step build guide
```

---

## Conversation Phases

```text
greeting → browsing → taking_order → collecting_address → confirming → done
               ↑           ↑
               └── RAG ────┘  (product questions trigger semantic search)
```

| Phase | What Happens |
| --- | --- |
| `greeting` | Bot welcomes caller in Urdu |
| `browsing` | User asks about perfumes → RAG retrieves top-3 matches → Claude answers |
| `taking_order` | User picks a product → bot records item + quantity |
| `collecting_address` | Bot asks for delivery address |
| `confirming` | Bot reads back full order, asks for yes/no |
| `done` | Order saved, bot says goodbye |

---

## Latency Budget

```text
VAD silence detection   ~500ms
Deepgram STT result     ~150ms
RAG retrieval           ~100ms
Claude first token      ~300ms
Google TTS sentence 1   ~200ms

Total to first audio    ~1.3 seconds
```

---

## Setup

### 1. Install system dependencies

```bash
brew install asterisk ffmpeg
```

### 2. Copy Asterisk configs

```bash
sudo cp config/ari.conf        /usr/local/etc/asterisk/
sudo cp config/sip.conf        /usr/local/etc/asterisk/
sudo cp config/extensions.conf /usr/local/etc/asterisk/
```

### 3. Install Python dependencies

```bash
uv sync
```

### 4. Configure environment

```bash
cp .env.example .env
# fill in your API keys
```

```env
ANTHROPIC_API_KEY=
DEEPGRAM_API_KEY=
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
ARI_URL=http://localhost:8088
ARI_USER=baat_bot
ARI_PASSWORD=ari_password123
RTP_HOST=127.0.0.1
RTP_PORT=7000
```

### 5. Run

```bash
sudo asterisk -cvvv          # terminal 1
uv run python main.py        # terminal 2
```

Then connect **Linphone** or **Zoiper** (same WiFi) and dial `1000`.

---

## Build Phases

See [`phases.md`](./phases.md) for the full step-by-step build guide (10 phases from Asterisk install to live Pakistani phone number).

---

## Skills Demonstrated

- Asterisk ARI + ExternalMedia (real-time RTP audio streaming)
- Raw RTP packet handling in Python (asyncio UDP)
- Deepgram WebSocket streaming STT in Urdu
- LangGraph state machine with conditional routing
- RAG pipeline: multilingual embeddings + ChromaDB vector search
- Claude Sonnet 4.6 streaming with sentence-chunked TTS
- Concurrent asyncio tasks for barge-in support
