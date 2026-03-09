import asyncio
import audioop
import math
import queue
import struct
import threading

import webrtcvad
from google.cloud import speech

SAMPLE_RATE       = 8_000   # Hz — RTP stream rate (slin/8kHz)
STT_RATE          = 16_000  # Hz — upsample to 16kHz for better Google STT accuracy
FRAME_MS          = 20      # ms per VAD frame (must be 10/20/30 for webrtcvad)
FRAME_BYTES       = int(SAMPLE_RATE * FRAME_MS / 1000) * 2   # 320 bytes at 8kHz
SILENCE_FRAMES    = 38      # 38 × 20ms ≈ 750ms silence → end of utterance
VAD_MODE          = 3       # 0 = least aggressive … 3 = most aggressive
ENERGY_THRESHOLD  = 1000    # RMS below this = silence/noise (range 0–32767; speech ≈ 1000+)
SPEECH_ONSET      = 3       # consecutive speech frames required to trigger STT stream


def _rms(frame: bytes) -> float:
    """Root-mean-square energy of a 16-bit PCM frame."""
    samples = struct.unpack(f"<{len(frame) // 2}h", frame)
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class GoogleSTT:
    """
    Stateful streaming STT processor.

    Feed 20ms PCM frames via process(). Returns a transcript string when
    webrtcvad detects ~750ms of silence after speech, else returns None.

    Internally runs Google streaming_recognize in a background thread so
    it doesn't block the asyncio event loop.
    """

    def __init__(self) -> None:
        self._vad    = webrtcvad.Vad(VAD_MODE)
        self._client = speech.SpeechClient()
        self._streaming_config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=STT_RATE,   # 16kHz — upsampled before sending
                language_code="ur-IN",
                alternative_language_codes=["ur-PK"],
                model="default",
                enable_automatic_punctuation=True,
            ),
            interim_results=False,
        )
        self._is_speaking    = False
        self._silence_count  = 0
        self._onset_count    = 0
        self._pre_buffer: list[bytes] = []
        self._audio_q: queue.Queue = queue.Queue()
        self._future: asyncio.Future | None = None

    # ── Public interface ─────────────────────────────────────────────────

    async def process(self, frame: bytes) -> str | None:
        """
        Feed one 20ms PCM frame (8 kHz, 16-bit LE, 320 bytes).
        Returns transcript when utterance ends, else None.
        """
        if len(frame) != FRAME_BYTES:
            return None

        try:
            is_speech = self._vad.is_speech(frame, SAMPLE_RATE)
        except Exception:
            return None

        if is_speech and _rms(frame) >= ENERGY_THRESHOLD:
            self._silence_count = 0

            if not self._is_speaking:
                self._onset_count += 1
                self._pre_buffer.append(frame)

                if self._onset_count >= SPEECH_ONSET:
                    self._is_speaking = True
                    self._onset_count = 0
                    print("[STT] Speech detected — streaming to Google ...")
                    loop = asyncio.get_running_loop()
                    self._start_stream(loop)
                    for buffered in self._pre_buffer:
                        self._audio_q.put(buffered)
                    self._pre_buffer.clear()
            else:
                self._audio_q.put(frame)

        else:
            self._onset_count = 0
            self._pre_buffer.clear()

            if self._is_speaking:
                self._silence_count += 1
                self._audio_q.put(frame)

                if self._silence_count >= SILENCE_FRAMES:
                    return await self._end_stream()

        return None

    # ── Google STT streaming (runs in a background thread) ───────────────

    def _request_generator(self):
        """Yield StreamingRecognizeRequests from audio_q until sentinel.
        Each chunk is upsampled 8kHz→16kHz before sending to Google."""
        state = None
        while True:
            chunk = self._audio_q.get()
            if chunk is None:
                return
            upsampled, state = audioop.ratecv(chunk, 2, 1, SAMPLE_RATE, STT_RATE, state)
            yield speech.StreamingRecognizeRequest(audio_content=upsampled)

    def _run_recognition(
        self,
        loop: asyncio.AbstractEventLoop,
        future: asyncio.Future,
    ) -> None:
        """Blocking Google STT call — runs in a daemon thread."""
        try:
            responses  = self._client.streaming_recognize(
                self._streaming_config,
                self._request_generator(),
            )
            transcript = ""
            for response in responses:
                for result in response.results:
                    if result.is_final:
                        transcript += result.alternatives[0].transcript
            loop.call_soon_threadsafe(future.set_result, transcript.strip())
        except Exception as exc:
            loop.call_soon_threadsafe(future.set_exception, exc)

    def _start_stream(self, loop: asyncio.AbstractEventLoop) -> None:
        self._future = loop.create_future()
        threading.Thread(
            target=self._run_recognition,
            args=(loop, self._future),
            daemon=True,
        ).start()

    async def _end_stream(self) -> str:
        self._is_speaking   = False
        self._silence_count = 0
        self._onset_count   = 0
        self._pre_buffer.clear()
        self._audio_q.put(None)
        try:
            transcript = await self._future
        except Exception as exc:
            print(f"[STT] Recognition error: {exc}")
            transcript = ""
        self._future = None
        if transcript:
            print(f"[STT] ▶ {transcript!r}")
        return transcript
