"""BM25-flavored keyword scoring over the retrieval corpus (pure Python, no dependencies)."""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9_+.\-]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25:
    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(d) for d in corpus]
        self.doc_len = [len(d) for d in self.docs]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.docs else 0.0
        self.df: Counter[str] = Counter()
        for doc in self.docs:
            for term in set(doc):
                self.df[term] += 1
        self.n = len(self.docs)

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc_index: int) -> float:
        if not self.docs or self.avg_len == 0:
            return 0.0
        q_terms = tokenize(query)
        doc = self.docs[doc_index]
        counts = Counter(doc)
        dl = self.doc_len[doc_index]
        total = 0.0
        for term in q_terms:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf(term)
            denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avg_len)
            total += idf * (tf * (self.k1 + 1)) / denom
        return total

    def scores(self, query: str) -> list[float]:
        return [self.score(query, i) for i in range(self.n)]
