"""Embedding model wrapper using fastembed (ONNX-based, no PyTorch)."""

from functools import lru_cache
from fastembed import TextEmbedding
import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"  # 384-dim, ~45MB ONNX model
EMBEDDING_DIM = 384


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    return TextEmbedding(model_name=MODEL_NAME)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a list of texts. Returns (N, 384) numpy array."""
    model = get_model()
    embeddings = list(model.embed(texts))
    return np.array(embeddings, dtype=np.float32)


def embed_single(text: str) -> list[float]:
    """Embed a single text. Returns 384-dim list."""
    arr = embed_texts([text])
    return arr[0].tolist()
