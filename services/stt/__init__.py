"""
STT interface — swap the import below to change provider.
Each provider module must expose a class with:
    async def process(frame: bytes) -> str | None
"""
from services.stt.deepgram import DeepgramSTT   # active provider

__all__ = ["DeepgramSTT"]
