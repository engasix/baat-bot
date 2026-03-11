import asyncio
import os
import queue
import threading

from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types.listen_v1results import ListenV1Results

SAMPLE_RATE = 8_000
FRAME_BYTES = int(SAMPLE_RATE * 20 / 1000) * 2   # 320 bytes (20ms @ 8kHz)


class DeepgramSTT:
    """
    Real-time streaming STT using Deepgram Nova-3 (deepgram-python-sdk v6).

    Opens one persistent WebSocket per call. All RTP frames are streamed
    directly to Deepgram — no client-side VAD needed. Deepgram handles
    endpointing internally (500ms silence → final result).

    NOTE: deepgram-python-sdk v6 requires ALL query params as strings.
    """

    def __init__(self) -> None:
        self._audio_q       : queue.Queue        = queue.Queue()
        self._transcript_q  : asyncio.Queue|None = None
        self._loop          : asyncio.AbstractEventLoop|None = None
        self._started       = False

    # ── Background thread — owns the Deepgram WebSocket ─────────────────

    def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        client = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY", ""))

        # v6: all parameters must be strings
        with client.listen.v1.connect(
            model="nova-3",
            language="ur",
            encoding="linear16",
            sample_rate=str(SAMPLE_RATE),
            smart_format="true",
            endpointing="500",        # ms — all numeric params as strings in v6
            interim_results="false",  # final transcripts only
        ) as conn:

            def on_open(_):
                print("[STT] Deepgram Nova-3 connected")

            def on_message(result):
                if not isinstance(result, ListenV1Results):
                    return
                try:
                    transcript = result.channel.alternatives[0].transcript
                    if transcript and result.is_final:
                        print(f"[STT] ▶ {transcript!r}")
                        loop.call_soon_threadsafe(
                            self._transcript_q.put_nowait, transcript.strip()
                        )
                except Exception:
                    pass

            def on_error(error):
                print(f"[STT] Deepgram error: {error}")

            conn.on(EventType.OPEN,    on_open)
            conn.on(EventType.MESSAGE, on_message)
            conn.on(EventType.ERROR,   on_error)

            # Separate sender thread feeds audio into the connection.
            # When no audio arrives for 7s (LLM + TTS phase), sends a silent
            # frame to keep the connection alive (prevents NET0001 timeout).
            _SILENCE = b"\x00" * FRAME_BYTES

            def _sender():
                while True:
                    try:
                        chunk = self._audio_q.get(timeout=7)
                    except queue.Empty:
                        conn.send_media(_SILENCE)  # keepalive — Deepgram ignores silence
                        continue
                    if chunk is None:
                        conn.send_close_stream()
                        return
                    conn.send_media(chunk)

            threading.Thread(target=_sender, daemon=True).start()
            conn.start_listening()   # blocks until connection closes

    # ── Public interface ─────────────────────────────────────────────────

    async def _ensure_started(self) -> None:
        if self._started:
            return
        self._started      = True
        self._loop         = asyncio.get_running_loop()
        self._transcript_q = asyncio.Queue()
        threading.Thread(
            target=self._run,
            args=(self._loop,),
            daemon=True,
        ).start()

    async def process(self, frame: bytes) -> str | None:
        """
        Feed one 20ms PCM frame (8 kHz, 16-bit LE, 320 bytes).
        Returns a final transcript when Deepgram detects end of utterance, else None.
        """
        await self._ensure_started()

        if len(frame) == FRAME_BYTES:
            self._audio_q.put(frame)

        if self._transcript_q and not self._transcript_q.empty():
            return await self._transcript_q.get()

        return None

    def close(self) -> None:
        """Gracefully close the Deepgram connection."""
        self._audio_q.put(None)
