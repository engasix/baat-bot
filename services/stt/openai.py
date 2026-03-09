import asyncio
import io
import math
import struct
import wave

import webrtcvad
from openai import OpenAI

SAMPLE_RATE      = 8_000   # Hz — RTP stream rate
FRAME_MS         = 20      # ms per VAD frame
FRAME_BYTES      = int(SAMPLE_RATE * FRAME_MS / 1000) * 2   # 320 bytes
SILENCE_FRAMES   = 38      # 38 × 20ms ≈ 750ms silence → end of utterance
VAD_MODE         = 3
ENERGY_THRESHOLD = 1000
SPEECH_ONSET     = 3       # consecutive speech frames before buffering starts


def _rms(frame: bytes) -> float:
    samples = struct.unpack(f"<{len(frame) // 2}h", frame)
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _frames_to_wav(frames: list[bytes]) -> bytes:
    """Pack raw 8kHz 16-bit PCM frames into an in-memory WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))
    return buf.getvalue()


def _transcribe(frames: list[bytes]) -> str:
    """Send buffered frames to OpenAI Whisper and return Urdu transcript."""
    wav_bytes = _frames_to_wav(frames)
    client = OpenAI()
    response = client.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.wav", wav_bytes, "audio/wav"),
        language="ur",
    )
    return response.text.strip()


class OpenAISTT:
    """
    Utterance-level STT using OpenAI Whisper.

    Feed 20ms PCM frames via process(). VAD + energy gate detect speech
    boundaries. When ~750ms of silence follows speech, the full utterance
    is sent to Whisper and the transcript is returned.

    Whisper has much better Urdu accuracy than Google STT default model.
    Trade-off: small extra latency vs streaming (Whisper API call ~0.5–1s).
    """

    def __init__(self) -> None:
        self._vad           = webrtcvad.Vad(VAD_MODE)
        self._is_speaking   = False
        self._silence_count = 0
        self._onset_count   = 0
        self._pre_buffer:  list[bytes] = []
        self._speech_buf:  list[bytes] = []

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
                    self._speech_buf  = list(self._pre_buffer)
                    self._pre_buffer.clear()
                    print("[STT] Speech detected — buffering ...")
            else:
                self._speech_buf.append(frame)

        else:
            self._onset_count = 0
            self._pre_buffer.clear()

            if self._is_speaking:
                self._silence_count += 1
                self._speech_buf.append(frame)

                if self._silence_count >= SILENCE_FRAMES:
                    return await self._flush()

        return None

    async def _flush(self) -> str:
        frames = self._speech_buf.copy()
        self._is_speaking   = False
        self._silence_count = 0
        self._speech_buf.clear()

        print(f"[STT] Sending {len(frames)} frames to Whisper ...")
        try:
            transcript = await asyncio.to_thread(_transcribe, frames)
        except Exception as exc:
            print(f"[STT] Whisper error: {exc}")
            return ""

        if transcript:
            print(f"[STT] ▶ {transcript!r}")
        return transcript
