"""API Key Masking & Partial Update Utilities.

Protects credentials via write-only / masked-read semantics.
"""
from __future__ import annotations

from typing import Optional


def mask_api_key(key: Optional[str]) -> str:
    """Mask API key for display.
    
    e.g. 'sk-1234567890abcdef' -> 'sk-****cdef'
    """
    if not key or not key.strip():
        return ""
    stripped = key.strip()
    if len(stripped) <= 6:
        return "****"
    return f"sk-****{stripped[-4:]}"


def merge_api_key(incoming_key: Optional[str], existing_key: Optional[str]) -> str:
    """Merge incoming key update safely without overriding existing secret on mask."""
    if not incoming_key or not incoming_key.strip():
        # Keep existing key if empty
        return existing_key or ""
    incoming = incoming_key.strip()
    if "****" in incoming:
        # Front-end submitted masked placeholder; preserve original
        return existing_key or ""
    # Full new key submitted
    return incoming
