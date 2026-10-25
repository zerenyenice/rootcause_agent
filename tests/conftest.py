"""Shared fixtures for workspace-level tests."""

from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# --- catalog: offline embeddings (feat/catalog) ------------------------------

CATALOG_KEYWORDS = ("plant", "scrap", "machine", "temperature", "shift", "order", "region", "units")
CATALOG_EMBEDDING_DIMENSIONS = len(CATALOG_KEYWORDS)


class KeywordEmbeddings(Embeddings):
    """Deterministic, offline embeddings: one dimension per keyword in ``CATALOG_KEYWORDS``.

    A text's vector counts each keyword's occurrences (plus a small floor so no vector
    is all zeros), so a query for ``"scrap"`` is most similar to texts mentioning
    scrap. Records every document batch and query. ``error`` makes every call raise;
    ``dimensions`` other than 8 truncates or pads the vector (a misconfigured model).
    """

    def __init__(self, dimensions: int = CATALOG_EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.error: Exception | None = None
        self.documents: list[list[str]] = []
        self.queries: list[str] = []

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        base = [float(lowered.count(word)) + 0.01 for word in CATALOG_KEYWORDS]
        padded = base + [0.01] * max(0, self.dimensions - len(base))
        return padded[: self.dimensions]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        self.documents.append(list(texts))
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        if self.error is not None:
            raise self.error
        self.queries.append(text)
        return self._vector(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)

    @property
    def embedded_texts(self) -> list[str]:
        return [text for batch in self.documents for text in batch]


@pytest.fixture
def make_embeddings() -> type[KeywordEmbeddings]:
    """Factory for :class:`KeywordEmbeddings` (``make_embeddings(dimensions=...)``)."""
    return KeywordEmbeddings
