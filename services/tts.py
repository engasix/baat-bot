from google.cloud import texttospeech

WELCOME_MESSAGE = (
    "السلام علیکم! Pure Scents کال کرنے کا شکریہ! "
    "میں عائشہ بات کر رہی ہوں۔ میں آپ کی کیا مدد کر سکتی ہوں؟"
)

"""
  ┌─────────────────────────┬────────────────────────────────────┐
  │          Voice          │             Character              │
  ├─────────────────────────┼────────────────────────────────────┤
  │ ur-IN-Chirp3-HD-Zephyr  │ Warm, conversational (used before) │
  ├─────────────────────────┼────────────────────────────────────┤
  │ ur-IN-Chirp3-HD-Aoede   │ Clear, professional                │
  ├─────────────────────────┼────────────────────────────────────┤
  │ ur-IN-Chirp3-HD-Leda    │ Soft, natural                      │
  ├─────────────────────────┼────────────────────────────────────┤
  │ ur-IN-Chirp3-HD-Schedar │ Crisp, confident                   │
  └─────────────────────────┴────────────────────────────────────┘
"""

VOICE_NAME = "ur-IN-Chirp3-HD-Aoede"

_client = None

def _get_client() -> texttospeech.TextToSpeechClient:
    global _client
    if _client is None:
        _client = texttospeech.TextToSpeechClient()
    return _client


def synthesize(text: str) -> bytes:
    """
    Synthesize text to raw slin PCM (8 kHz, 16-bit, mono).

    Google Chirp3-HD returns LINEAR16 at the requested sample rate directly —
    no resampling needed.
    """
    client = _get_client()

    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="ur-IN",
        name=VOICE_NAME,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=8_000,
    )

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
    )

    # Google returns LINEAR16 WAV — strip the 44-byte WAV header
    return response.audio_content[44:]
