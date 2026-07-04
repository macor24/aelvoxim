"""
metacore.storage.embedding — Text embedding with LRU cache.

Lightweight mode: uses hashlib-based pseudo-embedding when
sentence-transformers is not installed.
"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache

try:
    import os
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from sentence_transformers import SentenceTransformer
    _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    _DIM = 384
    _USE_REAL = True
except ImportError:
    _DIM = 384
    _MODEL = None
    _USE_REAL = False


def _pseudo_embedding(text: str, dim: int = 384) -> list[float]:
    """Hash-based pseudo-embedding (repeatable, normalized) when no model available."""
    h = hashlib.shake_256(text.encode()).hexdigest(dim * 2)
    vals = [int(h[i*4:(i*4+4)], 16) / 65535.0 * 2 - 1 for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vals))
    if norm > 0:
        vals = [v / norm for v in vals]
    return vals


@lru_cache(maxsize=10000)
def get_embedding(text: str) -> list[float]:
    """Generate embedding vector for text."""
    if _USE_REAL and _MODEL is not None:
        return _MODEL.encode(text).tolist()
    return _pseudo_embedding(text, dim=_DIM)


def get_embedding_dim() -> int:
    return _DIM


def is_real_embedding() -> bool:
    return _USE_REAL
