"""
Embedding service: singleton with lazy model loading, LRU cache, and fallback chain.

Fallback chain: local sentence-transformers → OpenAI API → deterministic mock.
Module-level cosine_similarity() and _mock_encode() preserved for backward compatibility.
"""
import logging
import os
from collections import OrderedDict
from typing import Any, List, Optional

from app import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level backward-compatible functions
# ---------------------------------------------------------------------------

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Return cosine similarity between two vectors."""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mock_encode(texts: List[str]) -> List[List[float]]:
    """Deterministic mock: hash text to a small vector so same text -> same vector."""
    dim = 8
    out = []
    for t in texts:
        h = hash(t) % (2 ** 32)
        vec = [(float((h >> (i * 4)) % 16) / 15.0) - 0.5 for i in range(dim)]
        out.append(vec)
    return out


def encode(texts: List[str]) -> List[List[float]]:
    """Module-level encode (delegates to singleton). Backward compatible."""
    return EmbeddingService.get().encode(texts)


# ---------------------------------------------------------------------------
# Singleton EmbeddingService
# ---------------------------------------------------------------------------

# Built-in fallback model name, used only when neither the EMBEDDING_MODEL env
# var nor config.EMBEDDING_MODEL is set. A multilingual model so Chinese works
# out of the box.
_PREFERRED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_CACHE_MAX_SIZE = 1000


class EmbeddingService:
    """Singleton embedding service with lazy loading and LRU cache."""

    _instance: Optional["EmbeddingService"] = None

    def __init__(self) -> None:
        self._model: Any = None
        self._model_loaded = False
        self._load_attempted = False
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    @classmethod
    def get(cls) -> "EmbeddingService":
        """Return the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def is_real_model_loaded(self) -> bool:
        """Check if a real embedding model (not mock) is active."""
        if self._provider() != "local":
            return False
        if not self._load_attempted:
            self._try_load()
        return self._model_loaded

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to vectors. Uses cache; loads model lazily on first call."""
        if not texts:
            return []

        if not self._load_attempted:
            self._try_load()

        # Check cache and split into cached/uncached
        results: list[tuple[int, list[float]]] = []
        uncached: list[tuple[int, str]] = []

        for i, text in enumerate(texts):
            if text in self._cache:
                # Move to end (LRU)
                self._cache.move_to_end(text)
                results.append((i, self._cache[text]))
            else:
                uncached.append((i, text))

        if uncached:
            uncached_texts = [t for _, t in uncached]
            if self._model_loaded and self._model is not None:
                vectors = self._encode_local(uncached_texts)
            else:
                vectors = None
                if self._provider() != "mock":
                    vectors = self._try_api_encode(uncached_texts)
                if vectors is None:
                    vectors = _mock_encode(uncached_texts)

            for (idx, text), vec in zip(uncached, vectors):
                self._cache_put(text, vec)
                results.append((idx, vec))

        # Sort by original index and return
        results.sort(key=lambda x: x[0])
        return [vec for _, vec in results]

    def pairwise_similarity(self, texts: list[str]) -> float:
        """Average pairwise cosine similarity across all pairs."""
        if len(texts) < 2:
            return 0.0

        vectors = self.encode(texts)
        total = 0.0
        count = 0
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                total += cosine_similarity(vectors[i], vectors[j])
                count += 1

        return total / count if count > 0 else 0.0

    # -- Private methods --

    def _provider(self) -> str:
        """Return normalized embedding provider preference."""
        return (
            os.environ.get("EMBEDDING_PROVIDER")
            or getattr(config, "EMBEDDING_PROVIDER", "local")
            or "local"
        ).strip().lower()

    def _model_name(self) -> str:
        """Resolve the local model name: env > config > built-in default."""
        return (
            os.environ.get("EMBEDDING_MODEL")
            or getattr(config, "EMBEDDING_MODEL", _PREFERRED_MODEL)
            or _PREFERRED_MODEL
        ).strip()

    def _try_load(self) -> None:
        """Attempt to load local model once."""
        if self._provider() != "local":
            logger.debug("Skipping local embedding model load because EMBEDDING_PROVIDER=%s", self._provider())
            return

        self._load_attempted = True
        try:
            from sentence_transformers import SentenceTransformer
            model_name = self._model_name()
            logger.info("Loading embedding model: %s ...", model_name)
            self._model = SentenceTransformer(model_name)
            self._model_loaded = True
            logger.info("Embedding model loaded successfully.")
        except Exception as e:
            logger.info("Local embedding model unavailable (%s), using fallback chain.", e)
            self._model = None
            self._model_loaded = False

    def _encode_local(self, texts: list[str]) -> list[list[float]]:
        """Encode using loaded local model."""
        import numpy as np
        emb = self._model.encode(texts)
        if isinstance(emb, np.ndarray) and emb.ndim == 1:
            return [emb.tolist()]
        return [e.tolist() for e in emb]

    def _try_api_encode(self, texts: list[str]) -> Optional[list[list[float]]]:
        """Try OpenAI text-embedding-3-small API. Returns None if unavailable."""
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or getattr(config, "LLM_API_KEY", None)
        )
        if not api_key:
            return None

        try:
            import httpx
            base_url = (
                os.environ.get("LLM_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or getattr(config, "LLM_BASE_URL", None)
                or "https://api.openai.com/v1"
            )
            resp = httpx.post(
                f"{base_url}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "text-embedding-3-small", "input": texts},
                timeout=15.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return [item["embedding"] for item in data["data"]]
        except Exception as e:
            logger.debug("API embedding fallback failed: %s", e)

        return None

    def _cache_put(self, key: str, value: list[float]) -> None:
        """Add to cache with LRU eviction."""
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= _CACHE_MAX_SIZE:
                self._cache.popitem(last=False)  # Evict oldest
            self._cache[key] = value
