"""Retriever adapters over the index's durable active SQLite snapshot."""

from __future__ import annotations


class PersistentLexicalRetriever:
    def __init__(self, index):
        self.index = index
        self.snippets = index.snippets

    def search(self, query, limit=10):
        return self.index.search_lexical(query, limit)


class PersistentDenseRetriever:
    def __init__(self, index):
        self.index = index
        self.snippets = index.snippets

    def search(self, query_vector, limit=10):
        return self.index.search_dense(query_vector, limit)
