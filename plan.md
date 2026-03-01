# Baat Bot — AI Voice Order-Taking Agent (Urdu / Pakistan)

An AI voice agent for a **perfume e-commerce store** that receives phone calls in real-time,
understands Urdu, answers product questions using RAG, and takes orders.
Target latency: **~1 second** end-to-end response (like talking to a human).

---

## Architecture Overview

```text
Caller (Urdu) → SIP Phone → Asterisk (SIP/RTP)
                                   │
                    ┌──────────────┴──────────────┐
                    │ Control Plane                │ Data Plane
                    │ ARI WebSocket                │ ExternalMedia RTP (UDP)
                    │ ws://localhost:8088/ari       │ udp://localhost:7000
                    └──────────────┬──────────────┘
                                   │
                          Python Main App (main.py)
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     Deepgram WS STT       LangGraph Agent        Google Cloud TTS
     (streaming, Urdu)   (Claude Sonnet 4.6)    (sentence-chunked)
                                   │
                          ┌────────┴────────┐
                          │   RAG Pipeline  │
                          │                 │
                          │  User query     │
                          │      ↓          │
                          │  Embed (mE5)    │
                          │      ↓          │
                          │  ChromaDB       │
                          │  (perfumes)     │
                          │      ↓          │
                          │  Top-K results  │
                          │  → Claude ctx   │
                          └─────────────────┘
                                   │
                          RTP Audio → Asterisk → Caller
                                   │
                             Order stored in DB
```

### Why ARI over AGI

| | AGI (old plan) | ARI + ExternalMedia (current) |
| --- | --- | --- |
| Audio model | Record full clip → process | Live RTP stream → process |
| Latency | 8–12 seconds | ~1 second |
| STT | Whisper (batch) | Deepgram (WebSocket streaming) |
| Overlap LLM + TTS | No | Yes (sentence chunking) |
| Skill demonstrated | Basic | Advanced (async, RTP, streaming) |

### Real-Time Pipeline (per turn)

```text
Caller speaks → RTP chunks → webrtcvad detects speech
                           → Deepgram WebSocket → interim transcripts
             silence (500ms) → final transcript
                           → LangGraph node → Claude streaming tokens
                           → sentence buffer → Google TTS per sentence
                           → PCM audio → RTP packets → Asterisk → Caller
                              ↑ starts playing while Claude still generating ↑
```

---

## Stack

| Layer | Tool | Why |
| --- | --- | --- |
| Telephony | Asterisk (ARI) | Self-hosted, free, shows telephony skill |
| SIP Client | Linphone / Zoiper (mobile) | Free, easy local testing |
| Audio Streaming | Asterisk ExternalMedia (RTP/UDP) | Live bidirectional audio |
| VAD | `webrtcvad` | Detects end-of-speech without fixed timeout |
| STT | Deepgram WebSocket (`language=ur`) | Streaming, 200 min/month free |
| AI Agent | LangGraph + Claude Sonnet 4.6 | State machine + streaming |
| RAG — Embeddings | `multilingual-e5-small` (local) | Free, supports Urdu queries |
| RAG — Vector DB | ChromaDB (local) | Free, persistent, easy setup |
| RAG — Catalog | `data/perfumes.json` | Men's + women's perfume data |
| TTS | Google Cloud TTS (`ur-PK`) | Free 1M chars/month, good Urdu |
| Audio Conversion | `ffmpeg` | PCM ↔ formats |
| Order Storage | PostgreSQL (Phase 2) | Persistent orders |

---

## Project File Structure

```text
baat_bot/
├── plan.md
├── phases.md
├── pyproject.toml              # managed by uv
├── .env                        # API keys
├── config/
│   ├── sip.conf                # copy → /usr/local/etc/asterisk/
│   ├── extensions.conf         # copy → /usr/local/etc/asterisk/
│   └── ari.conf                # copy → /usr/local/etc/asterisk/
├── data/
│   └── perfumes.json           # perfume catalog (men + women)
├── rag/
│   ├── __init__.py
│   ├── catalog.py              # load & chunk perfumes.json
│   ├── embedder.py             # multilingual-e5-small embeddings
│   └── retriever.py            # ChromaDB search → top-K perfumes
├── agent/
│   ├── __init__.py
│   ├── state.py                # OrderState TypedDict
│   ├── nodes.py                # LangGraph nodes (one per phase)
│   └── graph.py                # Compiled LangGraph graph
├── services/
│   ├── __init__.py
│   ├── stt.py                  # Deepgram WebSocket streaming
│   ├── tts.py                  # Google Cloud TTS, sentence-chunked
│   └── rtp.py                  # RTP packet encode / decode
└── main.py                     # ARI WebSocket app + pipeline orchestration
```

---

## Phase 1 — Local Setup (Build & Test)

### Step 1 — Install Asterisk on Mac

```bash
brew install asterisk
sudo asterisk -cvvv   # start in verbose console mode
```

---

### Step 2 — Enable ARI

**File:** `/usr/local/etc/asterisk/ari.conf`

```ini
[general]
enabled = yes
pretty = yes

[baat_bot]
type = user
read_only = no
password = ari_password123
```

---

### Step 3 — Configure SIP Extension

**File:** `/usr/local/etc/asterisk/sip.conf`

```ini
[general]
context = default
allowguest = no
bindport = 5060
bindaddr = 0.0.0.0

[mobile_client]
type = friend
secret = yourpassword123
host = dynamic
context = incoming
dtmfmode = rfc2833
allow = ulaw
allow = alaw
```

---

### Step 4 — Configure Dial Plan (Stasis, not AGI)

**File:** `/usr/local/etc/asterisk/extensions.conf`

```ini
[incoming]
exten => 1000,1,Answer()
exten => 1000,n,Stasis(baat_bot)
exten => 1000,n,Hangup()
```

> `Stasis(baat_bot)` hands the call to our ARI Python app.
> No audio recording or playback happens in the dialplan — all audio is handled via ExternalMedia RTP.

---

### Step 5 — Python App Components

#### `services/rtp.py` — RTP Packet Handling

```python
RTP_HEADER_SIZE = 12  # bytes

def decode_rtp(packet: bytes) -> bytes:
    """Strip 12-byte RTP header, return raw PCM payload."""
    return packet[RTP_HEADER_SIZE:]

def encode_rtp(payload: bytes, seq: int, timestamp: int, ssrc: int) -> bytes:
    """Wrap PCM payload in RTP header."""
    import struct
    header = struct.pack(
        "!BBHII",
        0x80,       # V=2, P=0, X=0, CC=0
        0x0B,       # M=0, PT=11 (slin16)
        seq,
        timestamp,
        ssrc
    )
    return header + payload
```

#### `services/stt.py` — Deepgram Streaming STT

```python
# Connects to Deepgram WebSocket
# Receives RTP audio chunks → sends to Deepgram
# Returns async generator of final transcripts
# Language: ur (Urdu), model: nova-2

async def stream_stt(audio_queue: asyncio.Queue) -> AsyncGenerator[str, None]:
    ...
```

#### `services/tts.py` — Google Cloud TTS (Sentence-Chunked)

```python
# Splits text on sentence boundaries (۔ . ? !)
# Synthesizes each sentence separately via Google Cloud TTS
# Returns audio as slin16 PCM bytes immediately per sentence
# This lets audio playback START while Claude is still generating

async def synthesize_sentence(text: str) -> bytes:  # returns PCM slin16
    ...

async def stream_tts(text_chunks: AsyncGenerator[str, None]) -> AsyncGenerator[bytes, None]:
    ...
```

#### `data/perfumes.json` — Perfume Catalog

```json
[
  {
    "id": "p001",
    "name": "Blue de Chanel",
    "brand": "Chanel",
    "gender": "men",
    "price_pkr": 18500,
    "size_ml": 100,
    "scent_notes": ["citrus", "cedar", "sandalwood"],
    "description": "تازہ اور لکڑی والی خوشبو جو مردوں کے لیے ہے۔ آفس اور شام کے لیے بہترین۔",
    "in_stock": true
  },
  {
    "id": "p002",
    "name": "La Vie Est Belle",
    "brand": "Lancome",
    "gender": "women",
    "price_pkr": 15000,
    "size_ml": 75,
    "scent_notes": ["iris", "vanilla", "patchouli"],
    "description": "میٹھی اور پھولوں والی خوشبو جو خواتین کے لیے ہے۔ خاص مواقع کے لیے بہترین۔",
    "in_stock": true
  }
  // ... more perfumes
]
```

#### `rag/catalog.py` — Load & Chunk Catalog

```python
# Loads data/perfumes.json
# Creates one text document per perfume combining all fields
# Document format (in Urdu + English for better embedding):
#   "Blue de Chanel by Chanel | مردوں کے لیے | قیمت: 18500 روپے |
#    خوشبو: citrus cedar sandalwood | تازہ اور لکڑی والی خوشبو..."
# Returns list of Document objects for ChromaDB ingestion

def load_catalog() -> list[Document]: ...
```

#### `rag/embedder.py` — Multilingual Embeddings

```python
# Uses sentence-transformers/multilingual-e5-small (free, local, ~120MB)
# Supports Urdu queries natively
# Embeds both catalog documents (at startup) and user queries (at runtime)

model = SentenceTransformer("intfloat/multilingual-e5-small")

def embed(texts: list[str]) -> list[list[float]]: ...
```

#### `rag/retriever.py` — ChromaDB Vector Search

```python
# ChromaDB persistent collection: "perfumes"
# At startup: load catalog → embed → store in ChromaDB (one-time)
# At runtime: embed user query → cosine similarity search → top-3 results

def build_index(documents: list[Document]) -> None: ...
    # called once at app startup

def search(query: str, gender_filter: str = None, top_k: int = 3) -> list[dict]: ...
    # gender_filter: "men" | "women" | None (search all)
    # returns list of matching perfume dicts
```

#### `agent/state.py` — LangGraph Order State

```python
from typing import Annotated, Literal
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

class OrderState(TypedDict):
    messages: Annotated[list, add_messages]
    order_items: list[dict]        # [{"name": "Blue de Chanel", "qty": 1, "price": 8500}]
    delivery_address: str
    retrieved_products: list[dict] # RAG results injected into Claude context
    current_phase: Literal[
        "greeting",
        "browsing",               # user asking about perfumes → RAG active
        "taking_order",
        "collecting_address",
        "confirming",
        "done"
    ]
    confirmed: bool
```

#### `agent/nodes.py` — LangGraph Nodes

```python
# One node per phase. Each node:
# 1. Builds a phase-specific system prompt in Urdu
# 2. For browsing_node: calls RAG retriever first, injects results into Claude context
# 3. Calls Claude (streaming)
# 4. Extracts structured data from response (items, address, confirmation)
# 5. Returns updated state + response text

def greeting_node(state: OrderState) -> OrderState: ...
def browsing_node(state: OrderState) -> OrderState: ...
    # ↑ retriever.search(user_query) → top-3 perfumes
    # ↑ injects results into Claude: "ہمارے پاس یہ خوشبو ہیں: ..."
def taking_order_node(state: OrderState) -> OrderState: ...
def collecting_address_node(state: OrderState) -> OrderState: ...
def confirming_node(state: OrderState) -> OrderState: ...
def done_node(state: OrderState) -> OrderState: ...
```

#### `agent/graph.py` — LangGraph Graph

```python
# State machine transitions:
#
#   [START]
#      ↓
#   greeting ──► browsing ──────────────────────────────────┐
#      │            ↑ (more questions)                       │
#      │            └──────────────────────────────────┐    │
#      └──────────────────────────────────────────►  taking_order
#                                                         ↓
#                                               collecting_address
#                                                         ↓
#                                                    confirming
#                                                    ↓        ↓
#                                              (yes) done  (no) taking_order
#
# browsing_node uses RAG — retrieves relevant perfumes before calling Claude
# Router decides: is user asking a question (→ browsing) or placing order (→ taking_order)

graph = StateGraph(OrderState)
graph.add_node("greeting", greeting_node)
graph.add_node("browsing", browsing_node)
graph.add_node("taking_order", taking_order_node)
graph.add_node("collecting_address", collecting_address_node)
graph.add_node("confirming", confirming_node)
graph.add_node("done", done_node)
graph.add_conditional_edges("greeting", route_after_greeting)
graph.add_conditional_edges("browsing", route_after_browsing)
graph.add_conditional_edges("confirming", route_after_confirm)
# ...compile
app = graph.compile()
```

#### `main.py` — ARI App + Pipeline Orchestration

```python
# 1. Connect to Asterisk ARI WebSocket
# 2. On StasisStart event:
#    a. Answer the call
#    b. Create ExternalMedia channel → Asterisk streams RTP to udp://localhost:7000
#    c. Bridge call channel + ExternalMedia channel
# 3. Start UDP socket on port 7000 to receive/send RTP
# 4. Pipeline loop:
#    a. RTP audio chunks → webrtcvad → Deepgram WebSocket
#    b. Final transcript → LangGraph agent → OrderState update
#    c. Agent response text → sentence chunker → Google TTS
#    d. TTS PCM audio → RTP encoder → UDP back to Asterisk
# 5. On phase=="done": hangup via ARI
```

---

### Step 6 — Mobile SIP Client Setup

Install **Linphone** (free) or **Zoiper** on mobile:

```text
SIP Server:    192.168.1.x   ← Mac's local WiFi IP
Username:      mobile_client
Password:      yourpassword123
Port:          5060
Transport:     UDP
Dial:          1000
```

Find Mac's local IP:

```bash
ipconfig getifaddr en0
```

---

### Step 7 — Install Dependencies

```bash
# Python packages (via uv)
uv add anthropic
uv add langgraph
uv add deepgram-sdk
uv add google-cloud-texttospeech
uv add webrtcvad
uv add websockets
uv add aiohttp
uv add python-dotenv
uv add chromadb
uv add sentence-transformers

# System tools
brew install ffmpeg

# Environment variables (.env)
ANTHROPIC_API_KEY=...
DEEPGRAM_API_KEY=...
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
ARI_URL=http://localhost:8088
ARI_USER=baat_bot
ARI_PASSWORD=ari_password123
RTP_HOST=127.0.0.1
RTP_PORT=7000
```

---

### Quick Test Checklist

```text
[ ] Asterisk running:          sudo asterisk -cvvv
[ ] ARI reachable:             curl -u baat_bot:ari_password123 http://localhost:8088/ari/asterisk/info
[ ] SIP peer registered:       Asterisk console → sip show peers
[ ] Python app running:        uv run python main.py
[ ] Linphone connected:        Shows "Registered" in app
[ ] Dial 1000:                 Hear Urdu greeting (< 2s delay)
[ ] Speak in Urdu:             Agent responds in < 1s after you stop
[ ] Order flow complete:       greeting → browsing → order → address → confirm → done
[ ] No audio gaps:             TTS sentence 1 plays while sentence 2 generates
[ ] Barge-in works:            Interrupt bot mid-sentence → bot stops, listens
[ ] No false barge-in:         Bot does not trigger itself (echo cooldown)
```

---

### Latency Budget (per turn)

```text
VAD silence detection:    ~500ms   (end-of-speech trigger)
Deepgram final result:    ~150ms   (after silence)
RAG retrieval:            ~100ms   (ChromaDB local search)
LangGraph + Claude:       ~300ms   (first token, streaming)
First TTS sentence:       ~200ms   (Google Cloud TTS)
RTP playback start:       ~50ms

Total to first audio:     ~1.3 seconds  ✓
```

---

## Barge-in Design (Caller Interrupts Bot)

Barge-in = caller starts speaking while bot is still talking → bot stops immediately and listens.
This is what makes the conversation feel natural instead of robotic.

### Why It Works With ExternalMedia

ExternalMedia is full-duplex — both directions flow at all times:

```text
Caller audio → Asterisk → UDP port 7000 → Python app   (always arriving, even during TTS)
Python app  → UDP port 7000 → Asterisk → Caller         (TTS audio sending)
```

We are **always receiving** the caller's audio. Barge-in just means we act on it while speaking.

### The 3 States

```text
State 1 — BOT LISTENING
  Incoming RTP: run VAD normally
  Outgoing RTP: silent
  Action: buffer speech, trigger LangGraph on silence

State 2 — BOT SPEAKING
  Incoming RTP: run VAD for barge-in detection
  Outgoing RTP: sending TTS audio chunks
  Action: if speech detected → cancel TTS → switch to State 1

State 3 — BARGE-IN DETECTED
  Incoming RTP: buffer from start of detected speech
  Outgoing RTP: stop immediately (drop remaining TTS queue)
  Action: process caller audio normally → LangGraph → new TTS
```

### asyncio Implementation (in `main.py`)

Two concurrent tasks share two asyncio primitives:

```python
is_agent_speaking = asyncio.Event()   # set while TTS is sending
barge_in_detected = asyncio.Event()   # set when caller speaks during TTS

# ── Task 1: Always running — receive RTP + VAD ──────────────────
async def receive_task():
    silence_frames = 0
    speech_buffer = []

    async for rtp_packet in udp_receive_stream():
        frame = decode_rtp(rtp_packet)          # 20ms PCM frame

        if is_agent_speaking.is_set():
            # Bot is talking — watch for barge-in
            if vad.is_speech(frame, sample_rate=16000):
                barge_in_detected.set()         # signal Task 2 to stop
                speech_buffer = [frame]         # start capturing from here
        else:
            # Bot is silent — normal end-of-speech detection
            if vad.is_speech(frame, sample_rate=16000):
                silence_frames = 0
                speech_buffer.append(frame)
            else:
                silence_frames += 1
                if silence_frames >= 25:        # 25 × 20ms = 500ms silence
                    if speech_buffer:
                        await process_speech(speech_buffer)
                    speech_buffer = []
                    silence_frames = 0

# ── Task 2: Send TTS audio — barge-in cancellable ───────────────
async def speak_task(tts_audio_chunks):
    is_agent_speaking.set()
    barge_in_detected.clear()
    try:
        async for chunk in tts_audio_chunks:
            if barge_in_detected.is_set():
                break                           # stop mid-sentence immediately
            await udp_send(encode_rtp(chunk))
    finally:
        is_agent_speaking.clear()

# ── Run both concurrently ────────────────────────────────────────
await asyncio.gather(receive_task(), speak_task(tts_stream))
```

### Echo Problem + Solution

When the bot is speaking, its audio leaks back into the incoming RTP
(caller's phone mic picks up the bot's voice). VAD falsely detects this as
a barge-in by the bot itself.

```text
Bot sends:  "آپ کا آرڈر کیا ہے؟"
Mic picks up bot voice → incoming RTP has bot's audio → VAD fires = false barge-in ✗
```

#### Fix — Cooldown window after TTS starts

```python
TTS_ECHO_COOLDOWN_MS = 200   # ignore incoming speech for first 200ms of TTS

async def receive_task():
    tts_started_at = None

    if is_agent_speaking.is_set():
        elapsed = (time.monotonic() - tts_started_at) * 1000
        if elapsed < TTS_ECHO_COOLDOWN_MS:
            continue                            # ignore — likely echo
        if vad.is_speech(frame, 16000):
            barge_in_detected.set()
```

For PSTN calls (Phase 2), the caller's phone hardware handles echo
cancellation automatically — this is mainly needed for local WiFi SIP testing.

### Updated `main.py` Pipeline

```python
# Old pipeline (no barge-in):
# listen → process → speak → listen → process → speak ...

# New pipeline (with barge-in):
# receive_task ──────────────────────────────────────────────► (always)
# speak_task   ──────────► [barge-in?] → cancel → back to receive_task
```

The `main.py` orchestration loop:

```python
# 1. Connect ARI WebSocket
# 2. On StasisStart: answer → ExternalMedia → bridge
# 3. Start UDP socket (port 7000)
# 4. Launch receive_task as persistent background coroutine
# 5. On each transcript from receive_task:
#    a. Run LangGraph → get response text
#    b. Chunk text into sentences → Google TTS per sentence
#    c. Launch speak_task with TTS audio stream
#    d. speak_task and receive_task run concurrently (barge-in ready)
# 6. On phase=="done": hangup via ARI
```

### Barge-in Test Checklist

```text
[ ] Bot starts greeting → interrupt it mid-sentence → bot stops immediately
[ ] Bot resumes listening → transcribes interruption correctly
[ ] No false barge-ins → bot does not interrupt itself (echo cooldown works)
[ ] Order flow survives barge-in → LangGraph state preserved across interruption
```

---

## Phase 2 — Live SIP / Pakistani Phone Number

### What Changes vs Phase 1

```text
LOCAL SETUP                          LIVE SIP SETUP
─────────────────────────────────────────────────────────────
Mobile SIP client (WiFi)    →       Anyone's phone (PSTN)
Local Asterisk (Mac)        →       Asterisk (VPS) or port-forwarded Mac

─────────────────────────────────────────────────────────────
          EVERYTHING BELOW STAYS IDENTICAL
─────────────────────────────────────────────────────────────
ari.conf                    →       Zero changes
extensions.conf             →       Zero changes
sip.conf                    →       Add trunk block (~15 lines)
main.py (ARI app)           →       Zero changes
Deepgram STT                →       Zero changes
RAG / ChromaDB              →       Zero changes
LangGraph Agent             →       Zero changes
Google TTS                  →       Zero changes
```

### Migration Steps

#### 1. Get a Pakistani DID Number (~30 min)

| Provider | Pakistan DID | Ease | Est. Cost |
| --- | --- | --- | --- |
| **Twilio** | Yes | Easiest | ~$1-2/month |
| **DIDWW** | Yes | Easy | ~$3-5/month |
| **Zadarma** | Yes | Easy | Cheap |

#### 2. Add SIP Trunk to `sip.conf` (~15 min)

```ini
[twilio_trunk]
type = peer
host = sip.twilio.com
username = your_account_sid
secret = your_auth_token
fromuser = +923001234567
insecure = port,invite
context = incoming
```

#### 3. Make Asterisk Publicly Reachable

#### Option A — Port Forward (free)

```text
Router: Forward UDP 5060 + UDP 10000-20000 → Mac's local IP
sip.conf: externip=<your.public.ip>
          localnet=192.168.1.0/255.255.255.0
```

#### Option B — VPS (~$5/month, recommended)

```bash
# DigitalOcean / Hetzner droplet
apt install asterisk
# Copy exact same configs from Phase 1
# Static public IP — no NAT issues
```

---

### Phase 2 Effort Estimate

| Task | Effort |
| --- | --- |
| Sign up for SIP trunk + get Pakistani number | 30 min |
| Add trunk config to sip.conf | 15 min |
| Network setup (port forward or VPS) | 1-2 hrs |
| Test inbound call end-to-end | 30 min |
| **Agent / AI / ARI / RAG code changes** | **0 min** |
| **Total** | **~2-4 hours** |

---

## Future Enhancements

- [ ] Order storage in PostgreSQL
- [ ] Order confirmation via SMS (Twilio SMS API)
- [ ] Admin dashboard to view orders
- [ ] Menu with item names + prices (validate orders against menu)
- [x] Barge-in support (caller interrupts bot mid-sentence) — see Phase 1 design
- [ ] Upgrade to ElevenLabs TTS for more natural Urdu voice
- [ ] Add support for multiple languages (Punjabi, Pashto)
