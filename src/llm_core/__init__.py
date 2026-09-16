"""Model protocol support shared by NoneBot and standalone services."""

from .gateway import (
    ModelSpec,
    ModelTurn,
    Gateway,
    normalize_openai,
    gemini_payload,
    normalize_gemini,
    openai_messages,
    normalize_openai_embeddings,
)

__all__ = [
    "ModelSpec",
    "ModelTurn",
    "Gateway",
    "normalize_openai",
    "gemini_payload",
    "normalize_gemini",
    "openai_messages",
    "normalize_openai_embeddings",
]
