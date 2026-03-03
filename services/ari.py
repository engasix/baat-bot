import asyncio
import json
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

ARI_URL  = os.getenv("ARI_URL", "http://localhost:8088")
ARI_USER = os.getenv("ARI_USER", "baat_bot")
ARI_PASS = os.getenv("ARI_PASSWORD", "")
APP_NAME = "baat_bot"

WS_URL = (
    f"{ARI_URL.replace('http', 'ws')}/ari/events"
    f"?api_key={ARI_USER}:{ARI_PASS}&app={APP_NAME}"
)

RECONNECT_DELAY     = 3
RECONNECT_DELAY_MAX = 30

async def verify_connection(session: aiohttp.ClientSession) -> None:
    async with session.get(f"{ARI_URL}/ari/asterisk/info") as resp:
        data = await resp.json()
        version = data["system"]["version"]
        print(f"[ARI] Connected — Asterisk {version}")

async def answer_channel(session: aiohttp.ClientSession, channel_id: str) -> None:
    async with session.post(f"{ARI_URL}/ari/channels/{channel_id}/answer") as resp:
        print(f"[ARI] Answered channel {channel_id} ({resp.status})")


async def hangup_channel(session: aiohttp.ClientSession, channel_id: str) -> None:
    async with session.delete(f"{ARI_URL}/ari/channels/{channel_id}") as resp:
        if resp.status == 404:
            print(f"[ARI] Channel {channel_id} already gone")
        else:
            print(f"[ARI] Hung up channel {channel_id} ({resp.status})")

async def handle_stasis_start(session: aiohttp.ClientSession, event: dict) -> None:
    channel    = event["channel"]
    channel_id = channel["id"]
    caller_num = channel.get("caller", {}).get("number", "unknown")

    print(f"[EVT] StasisStart  channel={channel_id}  caller={caller_num}")

    await answer_channel(session, channel_id)

    await asyncio.sleep(3)

    await hangup_channel(session, channel_id)


async def run_websocket(session: aiohttp.ClientSession) -> None:
    """Single WebSocket session. Raises on disconnect so the caller can retry."""
    async with session.ws_connect(WS_URL) as ws:
        print("[ARI] WebSocket connected\n")

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                event      = json.loads(msg.data)
                event_type = event.get("type", "")

                if event_type == "StasisStart":
                    asyncio.create_task(handle_stasis_start(session, event))

                elif event_type == "StasisEnd":
                    channel_id = event["channel"]["id"]
                    print(f"[EVT] StasisEnd    channel={channel_id}")

            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError(f"WebSocket error: {ws.exception()}")

            elif msg.type == aiohttp.WSMsgType.CLOSED:
                raise ConnectionError(f"WebSocket closed — code={ws.close_code}")


async def run() -> None:
    """Entry point: connects to ARI and keeps reconnecting on failure."""
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
