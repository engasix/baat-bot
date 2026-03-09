import os

from elevenlabs.client import ElevenLabs
from elevenlabs.play import play
from dotenv import load_dotenv

load_dotenv()

eleven_api_key = os.getenv("ELEVENLABS_API_KEY")
eleven_voice_id = os.getenv("ELEVENLABS_VOICE_ID")

client = ElevenLabs(
    api_key=eleven_api_key
)

audio = client.text_to_speech.convert(
    text="السلام علیکم! Pure Scents کال کرنے کا شکریہ! ",
    voice_id=eleven_voice_id,
    model_id="eleven_multilingual_v2",
    output_format="mp3_44100_128",
)

play(audio)