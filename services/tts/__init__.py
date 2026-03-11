"""
TTS interface — swap the import below to change provider.
Each provider module must expose: synthesize(text: str) -> bytes
"""
import re

from services.tts.google import synthesize   # active provider

WELCOME_MESSAGE = (
    "السلام علیکم! Pure Scents کال کرنے کا شکریہ! "
    "میں عائشہ بات کر رہی ہوں۔ میں آپ کی کیا مدد کر سکتی ہوں؟"
)


def split_sentences(text: str) -> list[str]:
    """
    Split Urdu/English reply into individual sentences for chunked TTS.
    Splits on:  ۔  ؟  !  .  ?
    Caller hears sentence 1 while sentence 2 is being synthesized.
    """
    parts = re.split(r'(?<=[۔؟!.?])\s*', text.strip())
    return [s.strip() for s in parts if s.strip()]


__all__ = ["synthesize", "split_sentences", "WELCOME_MESSAGE"]
