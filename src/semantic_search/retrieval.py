"""A dependency-free BM25 baseline for code retrieval experiments."""

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
