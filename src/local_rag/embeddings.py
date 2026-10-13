"""Deterministic CPU test vectors. These are not semantic embeddings."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re


@dataclass(frozen=True)
class IndexProfile:
    provider: str
    model_revision: str
    dimensions: int
    normalization: str
    tokenizer: str
    document_format: str
    query_format: str
    extractor: str
    chunker: str
    max_chars: int

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


class DeterministicEmbedder:
    """Signed token hashing for repeatable storage tests, with no runtime calls."""

    def __init__(self, dimensions: int = 32):
        if type(dimensions) is not int or not 1 <= dimensions <= 4096:
            raise ValueError("test vector dimensions must be between 1 and 4096")
        self.dimensions = dimensions

    def profile(self, max_chars: int) -> IndexProfile:
        from .documents import CHUNKER_VERSION, EXTRACTOR_VERSION
        return IndexProfile("deterministic-test", "token-hash-v1", self.dimensions,
                            "l2", "unicode-word-v1", "raw", "raw",
                            EXTRACTOR_VERSION, CHUNKER_VERSION, max_chars)

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in re.findall(r"\w+", text.casefold()) or [text]:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                slot = int.from_bytes(digest[:8], "big") % self.dimensions
                vector[slot] += 1.0 if digest[8] & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vector))
            result.append([v / norm for v in vector] if norm else vector)
        return result


def validate_vectors(vectors, count: int, dimensions: int) -> None:
    if len(vectors) != count:
        raise ValueError("embedding response count does not match chunk count")
    for vector in vectors:
        if len(vector) != dimensions or any(type(v) not in (float, int) or not math.isfinite(v) for v in vector):
            raise ValueError("embedding response has invalid dimensions or values")
