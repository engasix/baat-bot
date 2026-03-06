import io
import wave

from google.cloud import texttospeech

WELCOME_MESSAGE = (
    "السلام علیکم! Pure Scents کال کرنے کا شکریہ!۔ "
    "میں عائشہ بات کر رہی ہوں۔آپ کی کیا مدد کر سکتی ہوں؟"
    # "Assalam o Alikum!, Pure Scents call karny ka buhat shukriya!"
)

_client = None


def _get_client() -> texttospeech.TextToSpeechClient:
    global _client
    if _client is None:
        _client = texttospeech.TextToSpeechClient()
    return _client


def synthesize(text: str) -> bytes:
    """
    Synthesize text to raw slin16 PCM (16 kHz, 16-bit, mono).

    Google TTS returns a WAV-wrapped LINEAR16 file.
    We strip the WAV header and return bare PCM bytes ready for RTP.
    """
    response = _get_client().synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code="ur-IN",
            name="ur-IN-Chirp3-HD-Zephyr",
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=8_000,
            speaking_rate=1.5,
        ),
    )

    with wave.open(io.BytesIO(response.audio_content)) as wf:
        return wf.readframes(wf.getnframes())
