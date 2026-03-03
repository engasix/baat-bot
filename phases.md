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
Phase 5  ──►  STT — Deepgram streaming (speech → text)
Phase 6  ──►  RAG — perfume catalog + ChromaDB + retrieval
Phase 7  ──►  LangGraph agent (text → Urdu response)
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

#### 2.5 Final folder structure ⬜ Pending

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

## Phase 4 — RTP Audio Bridge + Welcome Message

**Goal:** Python opens a UDP socket on port 7000. Asterisk streams caller's
raw audio to it via ExternalMedia. Immediately on call answer, the bot plays
a hardcoded TTS welcome message. We also verify inbound RTP is flowing.
This establishes both audio directions (in + out) before adding STT/agent logic.

### Welcome message

```
"Assalam o Alikum, Pure Scents call karny ka buhat shukriya,
 me Aysha baat kar rahi hon, me aap ki kia madad kar salti hon"
```

> **Note:** Google TTS `ur-PK-Standard-A` voice works best with Urdu script (نستعلیق).
> For Phase 4 we use this Roman Urdu string directly to get audio flowing fast.
> Phase 8 will refine pronunciation using proper Urdu script for agent responses.

### What `services/rtp.py` contains

```python
# decode_rtp(packet: bytes) -> bytes
#   strips 12-byte RTP header, returns raw PCM payload

# encode_rtp(payload, seq, timestamp, ssrc) -> bytes
#   wraps PCM payload in RTP header (PT=11 for slin16)

# UdpAudioStream
#   asyncio UDP server on RTP_PORT
#   async receive() -> bytes     one RTP packet at a time
#   async send(payload: bytes)   send audio back to Asterisk
```

### What `services/tts.py` contains (basic, Phase 4 subset)

```python
# synthesize(text: str) -> bytes
#   calls Google Cloud TTS (ur-PK-Standard-A, LINEAR16, 16000Hz)
#   returns raw slin16 PCM bytes (no RTP header)
#   used to generate welcome message audio at startup
```

> Full sentence-chunked streaming TTS is added in Phase 8.
> Phase 4 only needs a single blocking `synthesize()` call.

### Call flow in this phase

```
Dial 1000
  → ARI answers call
  → Create ExternalMedia channel + bridge
  → synthesize(WELCOME_MESSAGE) → PCM bytes
  → chunk into 320-byte frames → encode_rtp() → UdpAudioStream.send()
  → caller hears welcome message
  → inbound RTP packets logged to console (packet count + payload size)
  → call stays open until caller hangs up
```

### ARI calls added in this phase

```
POST /ari/channels/externalMedia
     body: { app: "baat_bot", external_host: "host.docker.internal:7000", format: "slin16" }

POST /ari/bridges
POST /ari/bridges/{id}/addChannel   (caller channel + externalMedia channel)
```

### Verification

```
[ ] Dial 1000 — call connects
[ ] Caller hears "Assalam o Alikum..." welcome message within ~2s of answering
[ ] Console prints inbound RTP packet count incrementing (~50 packets/sec)
[ ] Payload size = 320 bytes  (20ms × 16000Hz × 2 bytes = 640 bytes / 2 = 320)
[ ] No "bridge dropped" errors in Asterisk console
[ ] Hang up from caller — UDP stream stops cleanly
```

---

## Phase 5 — STT: Deepgram Streaming (Speech → Text)

**Goal:** Caller's audio from the UDP stream is fed into Deepgram's WebSocket
API in real-time. webrtcvad detects end-of-speech (500ms silence). Final
transcript is printed to console in Urdu.

### What `services/stt.py` contains

```python
# DeepgramSTT
#   WebSocket: wss://api.deepgram.com/v1/listen
#   params: language=ur, model=nova-2, encoding=linear16, sample_rate=16000
#   async send_audio(frame: bytes)         feed 20ms PCM frames
#   async transcripts() -> AsyncGenerator  yields final transcript strings

# VoiceActivityDetector  (wraps webrtcvad)
#   async process(frame: bytes) -> str | None
#   returns transcript string when 500ms silence detected, else None
```

### Verification

```
[ ] Dial 1000 — call connects
[ ] Speak Urdu into Linphone
[ ] Console prints Urdu transcript within ~1s of stopping speech
[ ] Silence does not produce empty transcripts
[ ] Multiple turns in a row work correctly
```

---

## Phase 6 — RAG: Perfume Catalog + ChromaDB + Retrieval

**Goal:** Build the RAG pipeline. Perfume catalog is loaded from JSON, embedded
with multilingual-e5-small, stored in ChromaDB. A retrieval function accepts
an Urdu user query and returns the top 3 matching perfumes. Tested independently
before wiring into the LangGraph agent.

### What `data/perfumes.json` contains

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
    "description": "تازہ اور لکڑی والی خوشبو، آفس اور شام کے لیے بہترین۔",
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
    "description": "میٹھی اور پھولوں والی خوشبو، خاص مواقع کے لیے۔",
    "in_stock": true
  }
]
```

### What `rag/catalog.py` contains

```python
# load_catalog() -> list[dict]
#   reads data/perfumes.json
#   creates one searchable text string per perfume:
#   "Blue de Chanel Chanel men مردوں citrus cedar sandalwood تازہ لکڑی آفس"
#   (bilingual text improves Urdu query matching)
```

### What `rag/embedder.py` contains

```python
# Uses: intfloat/multilingual-e5-small  (free, local, ~120MB, supports Urdu)
# embed(texts: list[str]) -> list[list[float]]
# Called at startup (catalog) and per query (user questions)
```

### What `rag/retriever.py` contains

```python
# ChromaDB persistent collection: "perfumes"
#
# build_index()
#   called once at app startup
#   loads catalog → embeds → upserts into ChromaDB
#
# search(query: str, gender: str = None, top_k: int = 3) -> list[dict]
#   embeds the Urdu query
#   optional gender filter: "men" | "women"
#   returns top_k matching perfume dicts
```

### Verification

```python
# uv run python -c "
# from rag.retriever import build_index, search
# build_index()
# results = search('مردوں کے لیے تازہ خوشبو')
# for r in results: print(r['name'], r['price_pkr'])
# "
```

```
[ ] build_index() runs without errors
[ ] ChromaDB collection created with N perfume documents
[ ] search('مردوں کے لیے خوشبو')  → returns men's perfumes
[ ] search('خواتین کے لیے')        → returns women's perfumes
[ ] search('سستی خوشبو')           → returns lowest-price items
[ ] gender filter works correctly
```

---

## Phase 7 — LangGraph Agent (Text → Urdu Response)

**Goal:** Transcript from Phase 5 passes into the LangGraph state machine.
The browsing node calls RAG before Claude. Tested independently with hardcoded
input before wiring to the phone pipeline.

### `agent/state.py`

```python
class OrderState(TypedDict):
    messages: Annotated[list, add_messages]
    order_items: list[dict]        # [{"name": "Blue de Chanel", "qty": 1, "price": 18500}]
    delivery_address: str
    retrieved_products: list[dict] # injected by browsing_node from RAG
    current_phase: Literal[
        "greeting", "browsing",
        "taking_order", "collecting_address",
        "confirming", "done"
    ]
    confirmed: bool
```

### `agent/nodes.py` — one node per phase

```python
def greeting_node(state)           -> OrderState: ...
    # welcomes caller, asks what they're looking for

def browsing_node(state)           -> OrderState: ...
    # 1. calls retriever.search(user_query)
    # 2. stores results in state["retrieved_products"]
    # 3. builds context: "ہمارے پاس یہ خوشبو ہیں: ..."
    # 4. calls Claude with catalog context + user question
    # 5. Claude answers in Urdu using retrieved perfume data

def taking_order_node(state)       -> OrderState: ...
    # extracts item name + qty from user speech, appends to order_items

def collecting_address_node(state) -> OrderState: ...
    # asks for and stores delivery address

def confirming_node(state)         -> OrderState: ...
    # reads back order + address, asks for yes/no confirmation

def done_node(state)               -> OrderState: ...
    # thanks caller, saves order
```

### `agent/graph.py` — state machine

```python
# Transitions:
#
#   [START] → greeting
#   greeting → browsing      (user asking a question)
#   greeting → taking_order  (user directly places order)
#   browsing → browsing      (more questions)
#   browsing → taking_order  (ready to order)
#   taking_order → collecting_address
#   collecting_address → confirming
#   confirming → done         (confirmed = True)
#   confirming → taking_order (confirmed = False, re-ask)
```

### Verification

```bash
uv run python -c "
from agent.graph import app
result = app.invoke({'current_phase': 'greeting', 'messages': [], 'order_items': [],
                     'delivery_address': '', 'retrieved_products': [], 'confirmed': False})
print(result['messages'][-1].content)
"
```

```
[ ] Greeting returns Urdu welcome message
[ ] 'مردوں کے لیے خوشبو بتائیں' → browsing_node → RAG → Claude answers with product details
[ ] 'دو Blue de Chanel چاہیے' → taking_order_node → order_items updated
[ ] Address collected correctly
[ ] 'ہاں' confirmation → confirmed=True, phase=done
[ ] 'نہیں' → returns to taking_order
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
| 4 | RTP UDP bridge + TTS welcome message | ⬜ Pending |
| 5 | Deepgram streaming STT + VAD | ⬜ Pending |
| 6 | RAG — ChromaDB + perfume catalog | ⬜ Pending |
| 7 | LangGraph state machine + Claude | ⬜ Pending |
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
