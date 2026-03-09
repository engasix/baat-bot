from google.cloud import texttospeech

# Chirp3-HD voices for ur-IN:
#   Female: Zephyr (warm), Aoede (professional), Leda (soft), Schedar (crisp)
#   Male:   Charon, Fenrir, Orus
VOICE_NAME = "ur-IN-Chirp3-HD-Aoede"

_client = None


def _get_client() -> texttospeech.TextToSpeechClient:
    global _client
    if _client is None:
        _client = texttospeech.TextToSpeechClient()
    return _client


def synthesize(text: str) -> bytes:
    """
    Synthesize text → raw 8 kHz 16-bit mono PCM using Google Chirp3-HD.

    Returns raw LINEAR16 bytes (WAV header stripped) ready for RTP transmission.
    """
    client = _get_client()

    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code="ur-IN",
            name=VOICE_NAME,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=8_000,
            speaking_rate=1.4,
        ),
    )

    # Strip the 44-byte WAV header — return raw PCM
    return response.audio_content[44:]
