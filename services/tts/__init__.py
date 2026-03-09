"""
TTS interface — swap the import below to change provider.
Each provider module must expose: synthesize(text: str) -> bytes
"""
from services.tts.google import synthesize   # active provider

WELCOME_MESSAGE = (
    "السلام علیکم! Pure Scents کال کرنے کا شکریہ! "
    "میں عائشہ بات کر رہی ہوں۔ میں آپ کی کیا مدد کر سکتی ہوں؟"
)

__all__ = ["synthesize", "WELCOME_MESSAGE"]
