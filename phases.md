# Baat Bot — Build Phases

Perfume e-commerce voice agent for Pakistan. Caller speaks Urdu, agent answers
product questions via RAG, takes order, confirms delivery address.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CALLER (Urdu)                              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  SIP / RTP
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        ASTERISK SERVER                              │
│                   (Docker: andrius/asterisk:latest)                 │
│                                                                     │
│  pjsip.conf ──► SIP registration & call routing (PJSIP, no chan_sip)│
│  extensions.conf ──► Stasis(baat_bot) ──► hands call to ARI        │
│  ari.conf ──► ARI enabled on port 8088                              │
│                                                                     │
│  Control Plane:  ARI WebSocket   ws://localhost:8088/ari/events     │
│  Data Plane:     ExternalMedia   RTP ◄──► UDP localhost:7000        │
└───────────────┬─────────────────────────────┬───────────────────────┘
                │ ARI WebSocket (control)      │ RTP UDP (audio)
                ▼                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       PYTHON APP (main.py)                          │
│                                                                     │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │  RTP Server │   │  STT Service │   │      TTS Service         │ │
│  │  rtp.py     │──►│  stt.py      │   │      tts.py              │ │
│  │  UDP :7000  │   │  Deepgram WS │   │  Google Cloud TTS        │ │
│  └─────────────┘   └──────┬───────┘   └──────────────────────────┘ │
│                           │ transcript                    ▲         │
│                           ▼                               │         │
│                  ┌────────────────────┐    response text  │         │
│                  │   LangGraph Agent  │──────────────────►│         │
│                  │                   │                              │
│                  │  greeting          │◄── RAG (browsing phase)     │
│                  │  browsing    ◄─────┼─── rag/retriever.py         │
│                  │  taking_order      │    ChromaDB + mE5           │
│                  │  collecting_addr   │    data/perfumes.json       │
│                  │  confirming        │                             │
│                  │  done              │                             │
│                  └────────────────────┘                             │
│                                                                     │
│  Barge-in: receive_task + speak_task run concurrently (asyncio)     │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Order Storage     │
                    │   (PostgreSQL)      │
                    └─────────────────────┘
```

---

## Phase Overview

```
Phase 1  ──►  Install & configure Asterisk
Phase 2  ──►  Python project setup (uv)
Phase 3  ──►  Connect Python to Asterisk via ARI
Phase 4  ──►  RTP audio bridge + TTS welcome message (first audio out)
Phase 5  ──►  STT — Deepgram Nova-3 streaming (speech → text) ✅ DONE
Phase 6  ──►  RAG — perfume catalog + ChromaDB + retrieval ✅ DONE
Phase 7  ──►  LangGraph agent (Q&A + RAG + human handoff on order intent) ✅ DONE
Phase 8  ──►  TTS — Google Cloud (text → audio back to caller)
Phase 9  ──►  Barge-in (caller interrupts bot)
Phase 10 ──►  Live deployment (Pakistani phone number)
```

---

## Phase 1 — Install & Configure Asterisk ✅ DONE

**Goal:** Asterisk is running in Docker. Two SIP clients (1001 Mac, 1002 mobile)
can register and call each other. Extension 1000 routes to Stasis(baat_bot).
ARI is reachable via HTTP on port 8088.

> **Note:** Asterisk 20+ (used by andrius/asterisk:latest) dropped chan_sip entirely.
> `sip.conf` does not exist. Use `pjsip.conf` with separate endpoint/auth/aor sections.
> `http.conf` is required to enable the ARI HTTP server.

### Files created

```
docker-compose.yml
config/
├── ari.conf          — ARI user: baat_bot / baat-1001
├── http.conf         — HTTP server on port 8088
├── pjsip.conf        — PJSIP endpoints 1001 + 1002
└── extensions.conf   — internal dial plan + extension 1000 → Stasis
```

#### `docker-compose.yml`

```yaml
services:
  asterisk:
    image: andrius/asterisk:latest
    container_name: baat_bot
    ports:
      - "5060:5060/udp"
      - "5060:5060/tcp"
      - "8088:8088/tcp"
      - "10000-10010:10000-10010/udp"
    volumes:
      - ./config/ari.conf:/etc/asterisk/ari.conf:ro
      - ./config/http.conf:/etc/asterisk/http.conf:ro
      - ./config/pjsip.conf:/etc/asterisk/pjsip.conf:ro
      - ./config/extensions.conf:/etc/asterisk/extensions.conf:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
```

#### `config/pjsip.conf` (key structure — PJSIP requires 3 sections per peer)

```ini
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0

[1001]
type=endpoint
context=default
disallow=all
allow=ulaw
transport=transport-udp
auth=1001
aors=1001
direct_media=no
force_rport=yes
rewrite_contact=yes
rtp_symmetric=yes

[1001]
type=auth
auth_type=userpass
username=1001
password=1001

[1001]
type=aor
max_contacts=1
qualify_frequency=30

; same pattern repeated for [1002]
```

#### `config/extensions.conf`

```ini
[default]
exten => 1001,1,Dial(PJSIP/1001)
exten => 1001,n,Hangup()

exten => 1002,1,Dial(PJSIP/1002)
exten => 1002,n,Hangup()

exten => 1000,1,Answer()
exten => 1000,n,Stasis(baat_bot)
exten => 1000,n,Hangup()
```

### Start Asterisk

```bash
docker compose up -d
```

> **Config reload tip:** Volume-mounted files can lag on Mac Docker Desktop.
> After any config change use `docker cp` to force update:
> ```bash
> docker cp ./config/extensions.conf baat_bot:/etc/asterisk/extensions.conf
> docker exec -it baat_bot asterisk -rx "core reload"
> ```

### SIP client settings (Linphone / Zoiper)

```
Username:  1001  (Mac desktop)  or  1002  (mobile)
Password:  1001                 or  1002
Domain:    <Mac WiFi IP>  (ipconfig getifaddr en0)
Port:      5060
```

### Verification

```
[✓] docker compose up  — container baat_bot starts without errors
[✓] SIP client 1001 (Mac) shows Registered
[✓] SIP client 1002 (mobile) shows Registered
[✓] 1002 calls 1001 — rings and connects
[✓] 1001 calls 1002 — rings and connects
[✓] curl -u baat_bot:baat-1001 http://localhost:8088/ari/asterisk/info  — returns JSON
[✓] Dial 1000 from either client — connects (silence OK, no ARI app yet)
[ ] Hangup notification: 1002→1001, 1002 hangs — works ✓
[~] Hangup notification: other 3 scenarios — partial (Docker NAT limitation, acceptable for dev)
```

### Known limitation

Docker Desktop for Mac NAT translates all external SIP packets to `192.168.65.1`
inside the container. This means Asterisk cannot send BYE back to the correct
client IP for the callee side. Only the case where the mobile (caller) hangs up
works reliably. This does not affect the AI bot flow (ARI/ExternalMedia uses
a different signaling path) and will be resolved in Phase 10 (VPS deployment).

---

## Phase 2 — Python Project Setup (uv) ✅ DONE

**Goal:** Python project initialized with uv, all dependencies installed,
environment variables configured, and folder structure in place.

### Steps

#### 2.1 Initialize project ✅ Done

```bash
cd baat_bot
uv init .
```

#### 2.2 Add dependencies ⬜ Pending

```bash
uv add anthropic
uv add langgraph
uv add deepgram-sdk
uv add google-cloud-texttospeech
uv add webrtcvad
uv add aiohttp
uv add websockets
uv add python-dotenv
uv add chromadb
uv add sentence-transformers
```

#### 2.3 Create folder structure ⬜ Pending

```bash
mkdir -p agent services rag data config
touch agent/__init__.py agent/state.py agent/nodes.py agent/graph.py
touch services/__init__.py services/rtp.py services/stt.py services/tts.py
touch rag/__init__.py rag/catalog.py rag/embedder.py rag/retriever.py
touch main.py .env
```

#### 2.4 Write `.env` ✅ Done (created, keys need filling)

```
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=...
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json
ARI_URL=http://localhost:8088
ARI_USER=baat_bot
ARI_PASSWORD=baat-1001
RTP_HOST=host.docker.internal   # Asterisk (Docker) sends RTP to Mac host
RTP_PORT=7000
```

#### 2.5 Final folder structure ✅ Pending

```
baat_bot/
├── plan.md
├── phases.md
├── pyproject.toml          ← created by uv ✅
├── docker-compose.yml      ✅
├── .env                    ✅ (API keys still need filling)
├── service-account.json    ← Google Cloud credentials (not yet)
├── config/                 ✅
│   ├── ari.conf
│   ├── http.conf
│   ├── pjsip.conf
│   └── extensions.conf
├── data/
│   └── perfumes.json       ← men + women perfume catalog
├── rag/
│   ├── __init__.py
│   ├── catalog.py
│   ├── embedder.py
│   └── retriever.py
├── agent/
│   ├── __init__.py
│   ├── state.py
│   ├── nodes.py
│   └── graph.py
├── services/
│   ├── __init__.py
│   ├── rtp.py
│   ├── stt.py
│   └── tts.py
└── main.py
```

### Verification

```
[✓] uv init done — pyproject.toml exists
[✓] uv run python -c "import anthropic; print('ok')"
[✓] uv run python -c "import deepgram; print('ok')"
[✓] uv run python -c "import langgraph; print('ok')"
[✓] uv run python -c "import chromadb; print('ok')"
[✓] uv run python -c "import sentence_transformers; print('ok')"
[✓] Folder structure created (agent/, services/, rag/, data/)
[✓] ANTHROPIC_API_KEY — filled
[✓] ARI_URL, ARI_USER, ARI_PASSWORD, RTP_HOST, RTP_PORT — filled
[~] DEEPGRAM_API_KEY — needs real key
[~] GOOGLE_APPLICATION_CREDENTIALS — needs service-account.json from Google Cloud
```

---

## Phase 3 — Connect Python to Asterisk via ARI ✅ DONE

**Goal:** Python app connects to Asterisk ARI WebSocket, receives `StasisStart`
event when Linphone calls 1000, answers the call, and hangs up cleanly.
No audio yet — just call control working end-to-end.

### What `main.py` does in this phase

```python
# 1. Load .env
# 2. Connect to ws://localhost:8088/ari/events?api_key=baat_bot:ari_password123&app=baat_bot
# 3. Listen for StasisStart event → log channel ID
# 4. POST /ari/channels/{id}/answer  → answer the call
# 5. Wait 3 seconds
# 6. DELETE /ari/channels/{id}       → hang up
# 7. Log StasisEnd event
```

### ARI REST calls used in this phase

```
GET    /ari/asterisk/info            verify connection
POST   /ari/channels/{id}/answer     answer incoming call
DELETE /ari/channels/{id}            hang up
WS     /ari/events                   event stream
```

### Verification

```
[✓] uv run python main.py  — logs "Connected to ARI — Asterisk 22.8.2"
[✓] ARI HTTP GET /ari/asterisk/info → 200 OK
[✓] Dial 1000 from SIP client
[✓] Console shows: StasisStart  channel=1772525105.20  caller=1002
[✓] Call is answered (204)
[✓] After 3s: call hangs up cleanly (204)
[✓] Console shows: StasisEnd  channel=1772525105.20
```

---

## Phase 4 — RTP Audio Bridge + Welcome Message ✅ DONE

**Goal:** Python opens a UDP socket on port 7000. Asterisk streams caller's
raw audio to it via ExternalMedia. Immediately on call answer, the bot plays
a hardcoded TTS welcome message. We also verify inbound RTP is flowing.
This establishes both audio directions (in + out) before adding STT/agent logic.

### Welcome message (Urdu script)

```
"السلام علیکم! Pure Scents کال کرنے کا شکریہ!
 میں عائشہ بات کر رہی ہوں۔ میں آپ کی کیا مدد کر سکتی ہوں؟"
```

> **Note:** Urdu script (نستعلیق) is required for correct TTS pronunciation.
> Roman Urdu text causes TTS models to treat it as English and produce broken audio.

### What `services/rtp.py` contains

```python
# PAYLOAD_TYPE = 10  (slin/8kHz — verified from live Asterisk packet capture)
# FRAME_SAMPLES = 160  (8kHz × 20ms)
# FRAME_BYTES   = 320  (160 samples × 2 bytes)

# _byteswap16(data) → bytes
#   RTP wire format is big-endian; TTS/STT use little-endian. Swap on both send/receive.

# decode_rtp(packet) → bytes
#   strips 12-byte header, byteswaps payload → LE PCM for STT

# encode_rtp(payload, seq, timestamp, ssrc) → bytes
#   wraps LE PCM (after byteswap) in RTP header (PT=10/slin)

# UdpAudioStream
#   asyncio UDP server on RTP_PORT
#   receive() → bytes           one decoded PCM frame
#   send(payload: bytes)        encode + send one 20ms frame to Asterisk
#   Remote addr learned from first inbound packet (Asterisk's RTP source port)
```

### What `services/tts/` contains

TTS is structured as a modular package — swap providers by changing one import line.

```
services/tts/
  __init__.py     ← interface: exposes synthesize() + WELCOME_MESSAGE
                     change one line here to switch provider
  google.py       ← Google Chirp3-HD (ur-IN-Chirp3-HD-Aoede, 8kHz LINEAR16)
  elevenlabs.py   ← ElevenLabs eleven_multilingual_v2 (pcm_8000)
```

```python
# synthesize(text: str) -> bytes
#   returns raw 8 kHz 16-bit mono PCM (no RTP header, no WAV header)
#   Google: WAV header stripped (first 44 bytes)
#   ElevenLabs: pcm_8000 format returns raw PCM directly
```

> Full sentence-chunked streaming TTS is added in Phase 8.
> Phase 4 only needs a single blocking `synthesize()` call.

### Call flow in this phase

```
Dial 1000
  → ARI WebSocket: StasisStart received
  → answer_channel()
  → setup_media_bridge():
      POST /ari/channels/externalMedia  (format=slin, external_host=host.docker.internal:7000)
      POST /ari/bridges  (type=mixing)
      POST /ari/bridges/{id}/addChannel  (caller — separate call, not comma-separated)
      POST /ari/bridges/{id}/addChannel  (externalMedia — separate call)
  → asyncio.gather(synthesize(WELCOME_MESSAGE), wait_for_first_rtp_packet)
  → first inbound packet → learn Asterisk's RTP source address
  → PCM → 320-byte frames → byteswap → encode_rtp → UdpAudioStream.send()
  → caller hears welcome message
  → inbound RTP packets counted until caller hangs up
  → StasisEnd → cleanup_call() → bridge + ExternalMedia deleted
```

### Key fixes discovered during implementation

- **RTP port range**: Added `config/rtp.conf` (`rtpstart=10000`, `rtpend=10099`) to keep
  Asterisk RTP within the docker-compose port mapping. Without this Asterisk uses
  16384–32767 which is outside Docker's mapped range.
- **Byte order**: RTP wire format is big-endian; TTS output and STT input are little-endian.
  `_byteswap16()` is required on both send and receive paths.
- **Payload type**: Asterisk ExternalMedia uses PT=10 for slin/8kHz (not PT=11).
  Verified from live packet capture.
- **ExternalMedia StasisStart**: ExternalMedia channels fire their own StasisStart.
  Must track channel IDs in `_ext_channels` set and skip them in the handler.
- **addChannel format**: Asterisk 22 rejects comma-separated IDs. Use two separate
  POST calls — one per channel.
- **Zombie channels**: Without cleanup on StasisEnd, ExternalMedia channels accumulate
  across calls. Added `_active_calls` dict + `cleanup_call()` on StasisEnd.
- **Absolute timing**: `asyncio.sleep(20ms)` drifts across 500+ frames causing audio
  breaks. Fixed with `next_send = start_time + n * frame_dur` per frame.
- **Docker restart**: Stale Asterisk state after config changes requires
  `docker compose down && docker compose up -d` (not just reload).

### ARI calls added in this phase

```
POST /ari/channels/externalMedia
     body: { app: "baat_bot", external_host: "host.docker.internal:7000", format: "slin" }

POST /ari/bridges
     body: { type: "mixing" }

POST /ari/bridges/{id}/addChannel   × 2  (one per channel — not comma-separated)

DELETE /ari/bridges/{id}            (on StasisEnd)
DELETE /ari/channels/{ext_id}       (on StasisEnd)
```

### Verification

```
[✓] Dial 1000 — call connects
[✓] Console: ExternalMedia channel created, bridge created, both channels added (204)
[✓] Console: [RTP] Asterisk RTP source: ('192.168.65.x', PORT)  PT=10 seq=N payload=320b
[✓] Caller hears Urdu welcome message within ~1.5s of answering
[✓] Console: [RTP] Sent N frames — no breaks or audio glitches
[✓] Console: [RTP] N packets received  payload=320 bytes  (incrementing every ~1s)
[✓] Hang up from caller — stream stops, bridge + ExternalMedia deleted cleanly
[✓] Second call immediately after first works correctly (no zombie channels)
```

---

## Phase 5 — STT: Deepgram Streaming (Speech → Text) ✅ DONE

**Goal:** Caller's audio from the UDP stream is fed into Deepgram Nova-3's
WebSocket API in real-time. Deepgram handles endpointing internally (500ms
silence → final result). Final transcript is echoed back via TTS to confirm
the pipeline is working end-to-end.

### What was built

**`services/stt/` — modular STT package** (same pattern as `services/tts/`):
- `__init__.py` — interface file; swap one import line to change provider
- `deepgram.py` — **active provider**: Deepgram Nova-3 (deepgram-python-sdk v6)
- `google.py` — fallback: Google STT with 8kHz→16kHz internal upsampling + webrtcvad
- `openai.py` — fallback: OpenAI Whisper (batch mode, VAD + energy gate)

**Deepgram Nova-3 design (`services/stt/deepgram.py`):**
```python
# One persistent WebSocket per call (opened lazily on first frame)
# Background thread owns the connection; asyncio queue bridges to the event loop
# All RTP 8kHz frames streamed directly — no client-side VAD needed
# Deepgram endpointing=500 (500ms silence) → final transcript
# deepgram-python-sdk v6: ALL query params must be strings
#   model="nova-3", language="ur", encoding="linear16"
#   sample_rate="8000", smart_format="true"
#   endpointing="500", interim_results="false"
```

**`services/ari.py` Phase 5 loop:**
```python
stt = stt_svc.DeepgramSTT()
while True:
    frame = await asyncio.wait_for(audio_stream.receive(), timeout=5.0)
    transcript = await stt.process(frame)
    if transcript:
        pcm, _ = tts.synthesize(transcript)   # echo back via TTS
        await play_audio(pcm)
stt.close()
```

### Verification

```
[✓] Dial 1000 — welcome message plays in Urdu
[✓] "[STT] Deepgram Nova-3 connected" appears in console
[✓] Speak Urdu → console prints "[STT] ▶ <transcript>" within ~1s of stopping
[✓] Transcript is echoed back via Google TTS (confirms full duplex pipeline)
[✓] Silence / background noise does not produce false transcripts
[✓] Multiple turns in a row work (persistent WebSocket handles all turns)
[✓] Call hangup → "[STT] No audio — call ended" printed, stt.close() called
```

### Key implementation notes
- deepgram-python-sdk v6 is a Fern-generated SDK — completely different from v2/v3 docs
- Use `client.listen.v1.connect()` context manager (NOT `LiveOptions`)
- Use `EventType.MESSAGE` from `deepgram.core.events`
- Use `ListenV1Results` from `deepgram.listen.v1.types.listen_v1results`
- HTTP 400 on connect = a param was passed as int/bool instead of string
- RTP stays at 8kHz throughout (slin, FRAME_BYTES=320) — no pipeline change needed

---

## Phase 6 — RAG: Perfume Catalog + ChromaDB + Retrieval ✅ DONE

**Goal:** Build the RAG pipeline. Perfume catalog loaded from JSON, embedded
with multilingual-e5-small, stored in ChromaDB. Retrieval handles semantic
Urdu queries + price-sorted lookups. Tested via `test_rag.py`.

### Catalog: `data/perfumes.json`
20 real perfumes covering the full Pakistani market:
- **Price range**: Rs 4,500 (Joop! Homme) → Rs 52,000 (Creed Aventus)
- **Price tiers**: Budget (≤8k), Mid-range (≤22k), Premium (>22k)
- **Gender**: 10 men, 8 women, 2 unisex
- **Brands**: Chanel, Dior, Armani, Tom Ford, Creed, Versace, Lancôme, YSL, Davidoff, Calvin Klein, etc.
- **Fields**: id, name, brand, gender, category, price_pkr, size_ml, scent_notes, description_ur, description_en, in_stock

### Architecture

```
data/perfumes.json
    ↓  rag/catalog.py — load() + to_document() (bilingual Urdu+English text)
    ↓  rag/embedder.py — intfloat/multilingual-e5-small (local, ~120MB, 100+ langs)
    ↓  ChromaDB (./chroma_db)  —  cosine similarity, persisted to disk
    ↓  rag/retriever.py — build_index() / search() / cheapest() / most_premium()
```

### Key design: bilingual document text
Each perfume is stored as a rich bilingual string so Urdu queries from STT
match correctly via semantic similarity:
```
"Bleu de Chanel Chanel مردوں کے لیے مردانہ تازہ اور لکڑی والی مردانہ خوشبو ...
 خوشبو scent notes: citrus cedar sandalwood ... قیمت price 32000 روپے مہنگا پریمیم لگژری"
```

### API
```python
from rag import build_index, search, cheapest, most_premium

build_index()                              # startup — idempotent, skips if already indexed
search("مردوں کے لیے بہترین خوشبو")       # semantic search, returns list[dict]
search("floral women", gender="women")    # with gender filter
cheapest(n=3)                             # sorted by price_pkr ascending
most_premium(n=3)                         # sorted by price_pkr descending
```

### Verification (`uv run python test_rag.py`)

```
[✓] build_index() — 20 perfumes indexed in ChromaDB (cosine space)
[✓] cheapest(3)        → Joop Rs 4,500 / Cool Water Rs 5,500 / CK One Rs 6,500
[✓] most_premium(3)    → Aventus Rs 52,000 / Oud Wood Rs 48,000 / Bleu de Chanel Rs 32,000
[✓] "مردوں کے لیے بہترین خوشبو"  → men's perfumes (1 Million, Sauvage, Eros)
[✓] "عورتوں کے لیے بہترین خوشبو" → women's perfumes (La Vie Est Belle, Si, Light Blue)
[✓] "عود والا مہنگا پرفیوم"       → Oud Wood (Tom Ford) ranked first
[✓] "Sauvage by Dior price?"      → Sauvage Rs 28,000 (English query also works)
[✓] "گرمیوں میں تازہ ہلکی خوشبو" → Light Blue, Acqua di Gio (aquatic/fresh)
[✓] Second run: "Index current — 20 perfumes already indexed" (idempotent)
```

---

## Phase 7 — LangGraph Agent (Text → Urdu Response) ✅ DONE

**Goal:** Transcript from Phase 5 passes into the LangGraph agent. The agent
answers product questions using RAG + Claude Sonnet 4.6 in Urdu. When the caller
shows purchase intent, the agent says "connecting you now" and transfers the call
to a human agent. Tested independently before wiring to the phone pipeline.

### Why we transfer to a human agent for orders

Taking an order fully over voice requires collecting a perfume name, quantity,
full delivery address, and payment method — all in Urdu. Any STT error in the
address (a single wrong digit in a house number or street name) means a wrong
delivery. For high-value perfumes (Rs 14,000–52,000), this is a real business risk.

A human agent verifies everything before processing. The bot's job is to answer
questions so well that by the time the caller speaks to a human, they already
know exactly what they want. This reduces the human agent's workload while
keeping orders accurate and customers confident.

This decision can be revisited once STT accuracy is proven reliable on real calls.

### `agent/state.py`

```python
class State(TypedDict):
    convo:       Annotated[list[BaseMessage], add_messages]
    rag_context: str   # top-K perfume results injected into system prompt each turn
    transfer:    bool  # True = caller ready to order → hand off to human agent
```

### `agent/nodes.py`

```python
def assistant_node(state: State) -> State:
    # 1. Extract last user message
    # 2. RAG: retriever.search(query) → top-3 perfumes → store in rag_context
    # 3. Build system prompt with perfume catalog context
    # 4. Call Claude Sonnet 4.6 with full convo history
    # 5. Detect order intent ("لینا ہے", "آرڈر کرنا ہے", "خریدنا ہے")
    # 6. If intent detected: set transfer=True, reply "connecting you now..."
    # 7. Return updated state with Claude's Urdu reply

def transfer_node(state: State) -> State:
    # Plays TTS "ابھی آپ کو ہمارے آرڈر ڈیپارٹمنٹ سے connect کرتی ہوں"
    # Triggers ARI call redirect to human agent extension
```

### `agent/graph.py` — state machine

```python
# [START] → assistant ──► (transfer=False) ──► assistant   (Q&A loop)
#                     └──► (transfer=True)  ──► transfer → [END]
#
# Single assistant node handles greeting + all Q&A turns.
# No phase routing needed — Claude manages context via conversation history.
```

### Verification

```bash
uv run python -c "
from agent.graph import app
from langchain_core.messages import HumanMessage
result = app.invoke({
    'convo': [HumanMessage('السلام علیکم، مردوں کے لیے اچھی خوشبو بتائیں')],
    'rag_context': '',
    'transfer': False,
})
print(result['convo'][-1].content)
"
```

```
[ ] Urdu greeting returned on first turn
[ ] 'مردوں کے لیے خوشبو بتائیں' → RAG runs → Claude answers with perfume names + prices
[ ] 'عود والا کوئی ہے؟' → Oud Wood (Tom Ford) recommended
[ ] 'Sauvage کتنے کا ہے؟' → Rs 28,000 price returned
[ ] 'لینا ہے' / 'آرڈر کرنا ہے' → transfer=True, handoff message spoken
[ ] transfer=False turns loop correctly (no premature exit)
```

---

## Phase 8 — TTS: Google Cloud + Full Pipeline

**Goal:** Extend `services/tts.py` (started in Phase 4) to support streaming
sentence-by-sentence synthesis. Wire full pipeline: call → STT → RAG → LangGraph → TTS → caller.
Welcome message from Phase 4 continues to play unchanged.

### What `services/tts.py` gains in this phase

```python
# (synthesize() already exists from Phase 4 — no changes needed)

# split_sentences(text: str) -> list[str]
#   splits on: ۔  .  ?  !
#   Note: agent responses use Urdu script (نستعلیق) for correct TTS pronunciation

# stream_tts(text: str) -> AsyncGenerator[bytes, None]
#   splits into sentences → yields PCM per sentence immediately
#   caller hears sentence 1 while sentence 2 is still synthesizing
```

### Full pipeline at end of this phase

```
Dial 1000
  → ARI answers + bridges ExternalMedia
  → welcome message plays (from Phase 4, unchanged)
  → receive_task feeds audio to Deepgram via VAD
  → Urdu transcript
  → LangGraph (browsing_node if question → RAG → Claude, else order node)
  → Urdu response text
  → sentence split → Google TTS per sentence
  → PCM → RTP → Asterisk → Caller hears Urdu response
  → loop (next turn)
```

### Verification

```
[ ] Dial 1000 — hear welcome message (from Phase 4) immediately ✓
[ ] Ask "آپ کے پاس کون سی خوشبو ہے؟" → bot replies with perfume names + prices
[ ] Ask "مردوں کے لیے کیا ہے؟" → bot filters and replies with men's perfumes only
[ ] Say "Blue de Chanel ایک چاہیے" → bot moves to address collection
[ ] Complete order flow: greeting → browsing → order → address → confirm → goodbye
[ ] Multi-sentence replies: sentence 1 plays before sentence 2 is synthesized
[ ] End-to-end latency < 2 seconds per turn
```

---

## Phase 9 — Barge-in (Caller Interrupts Bot)

**Goal:** While bot is speaking (sending TTS audio), if caller starts talking,
bot stops immediately and starts listening. LangGraph state is preserved.
Echo cooldown prevents false triggers.

### Changes to `main.py`

```python
# Two asyncio primitives shared between tasks:
is_agent_speaking = asyncio.Event()
barge_in_detected = asyncio.Event()

# receive_task  (always running)
#   during bot speech: VAD for barge-in + 200ms echo cooldown
#   during bot silence: VAD for end-of-speech detection

# speak_task  (cancellable TTS sender)
#   checks barge_in_detected before each 20ms audio chunk
#   breaks immediately on barge-in, clears speaking flag

# asyncio.gather(receive_task(), speak_task(tts_stream))
```

### Echo cooldown (prevents self-triggering)

```python
TTS_ECHO_COOLDOWN_MS = 200
# Ignore incoming speech for first 200ms of TTS playback
# Stops bot's own voice (bounced from caller's mic) from triggering barge-in
```

### Verification

```
[ ] Bot mid-greeting → speak → bot stops within ~200ms
[ ] Caller audio buffered from point of barge-in → transcribed correctly
[ ] LangGraph state unchanged → conversation continues from correct phase
[ ] No false barge-ins when bot speaks into silence
[ ] Barge-in during product description → bot stops, listens to user's choice
```

---

## Phase 10 (Final) — Live Deployment: Pakistani Phone Number

**Goal:** Anyone in Pakistan can call a real phone number and talk to the bot.
Zero changes to Python code — only Asterisk network config and a SIP trunk.

### Steps

#### 10.1 Get a Pakistani DID number

| Provider | Cost | Notes |
|---|---|---|
| Twilio | ~$1-2/month | Easiest, good docs |
| DIDWW | ~$3-5/month | Direct carrier |
| Zadarma | Cheap | Simple setup |

#### 10.2 Add SIP trunk to `sip.conf`

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

#### 10.3 Make Asterisk publicly reachable

Option A — Port Forward (free)

```
Router: UDP 5060 + UDP 10000–20000 → Mac's local IP
sip.conf: externip = <your.public.ip>
          localnet = 192.168.1.0/255.255.255.0
```

Option B — VPS (~$5/month, recommended)

```bash
# DigitalOcean / Hetzner
apt install asterisk
# Copy configs from Phase 1 — identical
# Static public IP, no NAT issues
```

#### 10.4 Update `extensions.conf` for trunk calls

```ini
[incoming]
exten => +923001234567,1,Answer()
exten => +923001234567,n,Stasis(baat_bot)
exten => +923001234567,n,Hangup()
```

#### 10.5 Code changes needed

```
main.py         — zero changes
stt.py          — zero changes
tts.py          — zero changes
rag/            — zero changes
agent/          — zero changes
```

### Final Verification

```
[ ] Call Pakistani number from any phone — bot answers in Urdu
[ ] "آپ کے پاس عورتوں کے لیے کیا ہے؟" → bot answers with women's perfumes
[ ] Full order flow works over PSTN
[ ] Barge-in works on real cellular call
[ ] Order data logged/stored
```

---

## Summary

| Phase | Builds | Status |
|---|---|---|
| 1 | Asterisk Docker + PJSIP + ARI config | ✅ Done |
| 2 | uv project + folder structure + deps | ✅ Done |
| 3 | ARI WebSocket — call control | ✅ Done |
| 4 | RTP UDP bridge + TTS welcome message | ✅ Done |
| 5 | Deepgram streaming STT + VAD | ✅ Done |
| 6 | RAG — ChromaDB + perfume catalog | ✅ Done |
| 7 | LangGraph state machine + Claude | ✅ Done |
| 8 | Google TTS sentence-chunked + end-to-end | ⬜ Pending |
| 9 | Barge-in with asyncio concurrency | ⬜ Pending |
| 10 | Live Pakistani number deployment | ⬜ Pending |

_Old summary (skill reference):_

| Phase | Skill Demonstrated |
|---|---|
| 1 | Telephony, VoIP, Docker |
| 2 | Python project setup |
| 3 | ARI WebSocket — call control | Async Python, REST + WebSocket |
| 4 | RTP UDP bridge — raw audio | Network programming, audio protocols |
| 5 | Deepgram streaming STT + VAD | Real-time audio, streaming APIs |
| 6 | RAG — ChromaDB + perfume catalog | Vector DB, embeddings, semantic search |
| 7 | LangGraph state machine + Claude | AI agents, state management |
| 8 | Google TTS sentence-chunked + end-to-end | Full pipeline integration |
| 9 | Barge-in with asyncio concurrency | Concurrent programming |
| 10 | Live Pakistani number deployment | Production deployment, SIP trunking |
