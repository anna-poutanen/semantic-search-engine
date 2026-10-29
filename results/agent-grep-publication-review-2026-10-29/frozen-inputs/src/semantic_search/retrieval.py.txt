"""Lexical, cosine, and reciprocal-rank-fused snippet retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from semantic_search.corpus import Snippet


def tokenize(text: str) -> list[str]:
    """Split punctuation, snake_case, camelCase, and acronym boundaries."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25Retriever:
    def __init__(self, snippets: Iterable[Snippet], k1: float = 1.5, b: float = 0.75):
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Require k1 > 0 and 0 <= b <= 1")
        self.snippets = list(snippets)
        self.k1 = k1
        self.b = b
        self.lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for index, snippet in enumerate(self.snippets):
            counts = Counter(tokenize(f"{snippet.path} {snippet.symbol}\n{snippet.text}"))
            self.lengths.append(sum(counts.values()))
            for term, count in counts.items():
                self.postings[term].append((index, count))
        self.average_length = sum(self.lengths) / max(1, len(self.lengths)) or 1.0
        size = len(self.snippets)
        self.idf = {
            term: math.log(1 + (size - len(postings) + 0.5) / (len(postings) + 0.5))
            for term, postings in self.postings.items()
        }

    def search(self, query: str, limit: int = 10) -> list[tuple[Snippet, float]]:
        """Return positive matches by score, resolving ties by stable snippet ID."""
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        if limit == 0:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term, query_count in Counter(tokenize(query)).items():
            for index, frequency in self.postings.get(term, ()):
                normalization = 1 - self.b + self.b * self.lengths[index] / self.average_length
                scores[index] += (query_count * self.idf[term] * frequency * (self.k1 + 1)
                                  / (frequency + self.k1 * normalization))
        ranked = sorted(scores, key=lambda index: (-scores[index], self.snippets[index].id))
        return [(self.snippets[index], scores[index]) for index in ranked[:limit]]


class DenseRetriever:
    """Rank snippets by cosine similarity; importing this module needs no NumPy."""

    def __init__(self, snippets: Iterable[Snippet], vectors):
        import numpy as np

        self.snippets = list(snippets)
        if len({snippet.id for snippet in self.snippets}) != len(self.snippets):
            raise ValueError("Dense snippets must have unique IDs")
        try:
            if np.iscomplexobj(vectors):
                raise ValueError("Vectors must be real numbers")
            matrix = np.asarray(vectors, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Vectors must form a numeric matrix") from error
        if matrix.ndim != 2 or matrix.shape[1] == 0:
            raise ValueError("Vectors must be a matrix with a positive embedding dimension")
        if matrix.shape[0] != len(self.snippets):
            raise ValueError("Each snippet must have exactly one embedding vector")
        if not np.isfinite(matrix).all():
            raise ValueError("Vectors must contain only finite values")
        scales = np.max(np.abs(matrix), axis=1, keepdims=True)
        if np.any(scales == 0):
            raise ValueError("Embedding vectors must be nonzero")
        # Scaling first avoids overflowing or underflowing the norm of finite inputs.
        scaled = matrix / scales
        self.matrix = scaled / np.linalg.norm(scaled, axis=1, keepdims=True)
        self.dimension = matrix.shape[1]

    def search(self, query_vector, limit: int = 10) -> list[tuple[Snippet, float]]:
        import numpy as np

        if limit < 0:
            raise ValueError("limit must be nonnegative")
        if limit == 0:
            return []
        try:
            if np.iscomplexobj(query_vector):
                raise ValueError("Query vectors must be real numbers")
            query = np.asarray(query_vector, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("The query must be a numeric vector") from error
        if query.ndim != 1 or query.shape[0] != self.dimension:
            raise ValueError(f"Query vector must have shape ({self.dimension},)")
        if not np.isfinite(query).all():
            raise ValueError("Query vector must contain only finite values")
        scale = np.max(np.abs(query))
        if scale == 0:
            raise ValueError("Query vector must be nonzero")
        query = query / scale
        query = query / np.linalg.norm(query)
        scores = self.matrix @ query
        ranked = sorted(range(len(self.snippets)),
                        key=lambda index: (-float(scores[index]), self.snippets[index].id))
        return [(self.snippets[index], float(scores[index])) for index in ranked[:limit]]


class HybridRetriever:
    """Fuse fixed-depth lexical and dense rankings with equal-weight RRF."""

    def __init__(
        self,
        lexical: BM25Retriever,
        dense: DenseRetriever,
        rrf_k: float = 60,
        fetch_depth: int = 50,
    ):
        if not math.isfinite(rrf_k) or rrf_k < 0:
            raise ValueError("rrf_k must be finite and nonnegative")
        if not isinstance(fetch_depth, int) or fetch_depth < 1:
            raise ValueError("fetch_depth must be a positive integer")
        self.lexical = lexical
        self.dense = dense
        self.rrf_k = rrf_k
        self.fetch_depth = fetch_depth
        self.snippets: dict[str, Snippet] = {}
        for retriever in (lexical, dense):
            seen: set[str] = set()
            for snippet in retriever.snippets:
                if snippet.id in seen:
                    raise ValueError("Each retrieval corpus must have unique snippet IDs")
                seen.add(snippet.id)
                previous = self.snippets.get(snippet.id)
                if previous is not None and previous != snippet:
                    raise ValueError("A shared snippet ID must identify the same source window")
                self.snippets[snippet.id] = snippet

    def search(self, query: str, query_vector, limit: int = 10) -> list[tuple[Snippet, float]]:
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        if limit == 0:
            return []
        scores: dict[str, float] = defaultdict(float)
        rankings = (
            self.lexical.search(query, limit=self.fetch_depth),
            self.dense.search(query_vector, limit=self.fetch_depth),
        )
        for ranking in rankings:
            for rank, (snippet, _) in enumerate(ranking, start=1):
                scores[snippet.id] += 1 / (self.rrf_k + rank)
        ranked = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))
        return [(self.snippets[identifier], scores[identifier]) for identifier in ranked[:limit]]
