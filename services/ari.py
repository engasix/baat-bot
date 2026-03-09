import asyncio
import json
import os

import aiohttp
from dotenv import load_dotenv

from services import rtp as rtp_svc
from services import tts

load_dotenv()

ARI_URL  = os.getenv("ARI_URL", "http://localhost:8088")
ARI_USER = os.getenv("ARI_USER", "baat_bot")
ARI_PASS = os.getenv("ARI_PASSWORD", "")
RTP_HOST = os.getenv("RTP_HOST", "host.docker.internal")
RTP_PORT = int(os.getenv("RTP_PORT", 7000))
APP_NAME = "baat_bot"

WS_URL = (
    f"{ARI_URL.replace('http', 'ws')}/ari/events"
    f"?api_key={ARI_USER}:{ARI_PASS}&app={APP_NAME}"
)

RECONNECT_DELAY     = 3
RECONNECT_DELAY_MAX = 30

# Single shared UDP stream — started once, reused across calls
audio_stream = rtp_svc.UdpAudioStream()

# ExternalMedia channel IDs we created — must not handle their StasisStart events
_ext_channels: set[str] = set()

# Maps caller channel_id → {"bridge_id": ..., "ext_id": ...} for cleanup on hangup
_active_calls: dict[str, dict] = {}


# ── ARI helpers ───────────────────────────────────────────────────────────────

async def verify_connection(session: aiohttp.ClientSession) -> None:
    async with session.get(f"{ARI_URL}/ari/asterisk/info") as resp:
        data    = await resp.json()
        version = data["system"]["version"]
        print(f"[ARI] Connected — Asterisk {version}")


async def answer_channel(session: aiohttp.ClientSession, channel_id: str) -> None:
    async with session.post(f"{ARI_URL}/ari/channels/{channel_id}/answer") as resp:
        print(f"[ARI] Answered  channel={channel_id} ({resp.status})")


async def cleanup_call(session: aiohttp.ClientSession, channel_id: str) -> None:
    """Delete the bridge and ExternalMedia channel when a call ends."""
    call = _active_calls.pop(channel_id, None)
    if not call:
        return
    async with session.delete(f"{ARI_URL}/ari/bridges/{call['bridge_id']}") as resp:
        print(f"[ARI] Bridge deleted ({resp.status})")
    async with session.delete(f"{ARI_URL}/ari/channels/{call['ext_id']}") as resp:
        print(f"[ARI] ExternalMedia deleted ({resp.status})")
    _ext_channels.discard(call["ext_id"])


async def hangup_channel(session: aiohttp.ClientSession, channel_id: str) -> None:
    async with session.delete(f"{ARI_URL}/ari/channels/{channel_id}") as resp:
        if resp.status == 404:
            print(f"[ARI] Channel already gone: {channel_id}")
        else:
            print(f"[ARI] Hung up  channel={channel_id} ({resp.status})")


async def setup_media_bridge(
    session: aiohttp.ClientSession, channel_id: str
) -> str:
    """
    Create an ExternalMedia channel + mixing bridge, then add both the caller
    channel and the ExternalMedia channel to the bridge.
    Returns the bridge ID.
    """
    # 1. ExternalMedia channel — Asterisk will send RTP to RTP_HOST:RTP_PORT
    async with session.post(
        f"{ARI_URL}/ari/channels/externalMedia",
        json={
            "app":           APP_NAME,
            "external_host": f"{RTP_HOST}:{RTP_PORT}",
            "format":        "slin",
        },
    ) as resp:
        ext = await resp.json()
        ext_id = ext["id"]
        _ext_channels.add(ext_id)   # ignore its StasisStart event
        print(f"[ARI] ExternalMedia channel={ext_id}")

    # 2. Mixing bridge
    async with session.post(
        f"{ARI_URL}/ari/bridges",
        json={"type": "mixing"},
    ) as resp:
        bridge    = await resp.json()
        bridge_id = bridge["id"]
        print(f"[ARI] Bridge created bridge={bridge_id}")

    # 3. Add channels one at a time (comma-separated format → 400 in Asterisk 22)
    async with session.post(
        f"{ARI_URL}/ari/bridges/{bridge_id}/addChannel",
        json={"channel": channel_id},
    ) as resp:
        print(f"[ARI] Caller added to bridge ({resp.status})")

    async with session.post(
        f"{ARI_URL}/ari/bridges/{bridge_id}/addChannel",
        json={"channel": ext_id},
    ) as resp:
        print(f"[ARI] ExternalMedia added to bridge ({resp.status})")

    return bridge_id, ext_id


# ── Audio helpers ─────────────────────────────────────────────────────────────

async def play_audio(pcm: bytes) -> None:
    """Send raw PCM as RTP frames paced at 20 ms intervals using absolute timing."""
    loop        = asyncio.get_event_loop()
    start_time  = loop.time()
    frames_sent = 0
    frame_dur   = rtp_svc.FRAME_MS / 1000   # 0.020 s

    for i in range(0, len(pcm), rtp_svc.FRAME_BYTES):
        frame = pcm[i : i + rtp_svc.FRAME_BYTES]
        if len(frame) < rtp_svc.FRAME_BYTES:
            frame = frame + b"\x00" * (rtp_svc.FRAME_BYTES - len(frame))
        audio_stream.send(frame)
        frames_sent += 1

        # Sleep until the exact moment the next frame should go out.
        # This prevents drift from accumulating over hundreds of frames.
        next_send = start_time + frames_sent * frame_dur
        sleep_for = next_send - loop.time()
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    print(f"[RTP] Sent {frames_sent} frames ({frames_sent * rtp_svc.FRAME_MS} ms)")


# ── Call handler ──────────────────────────────────────────────────────────────

async def handle_stasis_start(session: aiohttp.ClientSession, event: dict) -> None:
    channel    = event["channel"]
    channel_id = channel["id"]
    caller_num = channel.get("caller", {}).get("number", "unknown")

    print(f"[EVT] StasisStart  channel={channel_id}  caller={caller_num}")

    await answer_channel(session, channel_id)
    bridge_id, ext_id = await setup_media_bridge(session, channel_id)
    _active_calls[channel_id] = {"bridge_id": bridge_id, "ext_id": ext_id}

    # Synthesize welcome message and wait for first RTP packet concurrently.
    # By the time TTS is done (~0.5-1s), Asterisk has started sending RTP
    # and we know the remote address to send back to.
    print("[TTS] Synthesizing welcome message ...")
    try:
        pcm, _ = await asyncio.gather(
            asyncio.to_thread(tts.synthesize, tts.WELCOME_MESSAGE),
            asyncio.wait_for(audio_stream.receive(), timeout=5.0),
        )
    except asyncio.TimeoutError:
        print("[RTP] Timed out waiting for first RTP packet — is the bridge up?")
        await hangup_channel(session, channel_id)
        return
    except Exception as e:
        print(f"[TTS] Error synthesizing welcome message: {e}")
        await hangup_channel(session, channel_id)
        return

    print(f"[TTS] {len(pcm):,} bytes PCM — playing welcome message ...")
    await play_audio(pcm)
    print("[TTS] Welcome message done")

    # Phase 4 verification: count inbound RTP packets until caller hangs up
    count = 0
    print("[RTP] Monitoring inbound audio (hang up to stop) ...")
    while True:
        try:
            frame = await asyncio.wait_for(audio_stream.receive(), timeout=5.0)
            count += 1
            if count % 50 == 0:   # log every ~1 second (50 × 20ms)
                print(f"[RTP] {count} packets received  payload={len(frame)} bytes")
        except asyncio.TimeoutError:
            print(f"[RTP] Stream ended — {count} total packets received")
            break


# ── WebSocket loop ────────────────────────────────────────────────────────────

async def run_websocket(session: aiohttp.ClientSession) -> None:
    """Single WebSocket session. Raises on disconnect so the caller can retry."""
    async with session.ws_connect(WS_URL) as ws:
        print("[ARI] WebSocket connected\n")

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                event      = json.loads(msg.data)
                event_type = event.get("type", "")

                if event_type == "StasisStart":
                    ch_id = event["channel"]["id"]
                    if ch_id in _ext_channels:
                        print(f"[EVT] StasisStart (ExternalMedia, skipping) channel={ch_id}")
                    else:
                        asyncio.create_task(handle_stasis_start(session, event))

                elif event_type == "StasisEnd":
                    channel_id = event["channel"]["id"]
                    print(f"[EVT] StasisEnd    channel={channel_id}")
                    asyncio.create_task(cleanup_call(session, channel_id))

            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError(f"WebSocket error: {ws.exception()}")

            elif msg.type == aiohttp.WSMsgType.CLOSED:
                raise ConnectionError(f"WebSocket closed — code={ws.close_code}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def run() -> None:
    """Start the UDP server then connect to ARI with automatic reconnection."""
    await audio_stream.start(host="0.0.0.0", port=RTP_PORT)

    auth  = aiohttp.BasicAuth(ARI_USER, ARI_PASS)
    delay = RECONNECT_DELAY

    async with aiohttp.ClientSession(auth=auth) as session:
        while True:
            try:
                await verify_connection(session)
                print("[ARI] Listening for calls on extension 1000 ...")
                await run_websocket(session)

            except ConnectionError as e:
                print(f"[ARI] {e}")

            except aiohttp.ClientConnectorError:
                print(f"[ARI] Cannot reach Asterisk at {ARI_URL}")

            except Exception as e:
                print(f"[ARI] Unexpected error: {e}")

            print(f"[ARI] Reconnecting in {delay}s ...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_DELAY_MAX)
