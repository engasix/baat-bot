import os

from elevenlabs import ElevenLabs

# eleven_multilingual_v2 supports Urdu natively
MODEL_ID  = "eleven_multilingual_v2"
VOICE_ID  = os.getenv("ELEVENLABS_VOICE_ID", "")

_client = None


def _get_client() -> ElevenLabs:
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    return _client


def synthesize(text: str) -> bytes:
    """
    Synthesize text → raw 8 kHz 16-bit mono PCM using ElevenLabs.
    pcm_8000 returns raw PCM directly — no header stripping needed.
    """
    audio_chunks = _get_client().text_to_speech.convert(
        voice_id=VOICE_ID,
        text=text,
        model_id=MODEL_ID,
        output_format="pcm_8000",
    )
    return b"".join(audio_chunks)
