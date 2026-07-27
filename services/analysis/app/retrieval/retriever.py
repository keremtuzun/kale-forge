"""Project retriever: turns a NormalizedProject + rule findings + datasheet text into an
indexed corpus, then answers questions by combining exact structured matches, keyword
scoring, local vector similarity, and local reranking. Only relevant slices reach the model."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalized import NormalizedProject
from app.retrieval.embeddings import Embedder, get_embedder
from app.retrieval.keyword import BM25
from app.retrieval.rerank import Reranker, get_reranker
from app.retrieval.store import ScoredItem, VectorStore
from app.rules.base import RuleFinding


class Snippet(BaseModel):
    source: str
    text: str
    score: float


class RetrievedContext(BaseModel):
    components: list[str] = Field(default_factory=list)
    nets: list[str] = Field(default_factory=list)
    findings: list[RuleFinding] = Field(default_factory=list)
    snippets: list[Snippet] = Field(default_factory=list)
    method_notes: list[str] = Field(default_factory=list)


def _word_present(name: str, question: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", question, re.IGNORECASE) is not None


class ProjectRetriever:
    def __init__(self, embedder: Optional[Embedder] = None, reranker: Optional[Reranker] = None):
        self.embedder = embedder or get_embedder()
        self.reranker = reranker or get_reranker()
        self.store = VectorStore()
        self._corpus: list[tuple[str, str]] = []  # (source_ref, text)
        self._bm25: Optional[BM25] = None
        self._findings_by_ref: dict[str, list[RuleFinding]] = {}
        self.project: Optional[NormalizedProject] = None

    def build(
        self,
        project: NormalizedProject,
        findings: list[RuleFinding],
        datasheet_texts: Optional[dict[str, str]] = None,
    ) -> "ProjectRetriever":
        self.project = project
        texts: list[str] = []
        kinds: list[tuple[str, str]] = []  # (kind, ref)

        for comp in project.components:
            nets = sorted({p.net for p in comp.pins if p.net})
            text = (
                f"Component {comp.reference}: value {comp.value or '?'}, footprint "
                f"{comp.footprint or '?'}, part {comp.mpn or comp.lib_id or '?'}. "
                f"Connected nets: {', '.join(nets) or 'none'}."
            )
            texts.append(text)
            kinds.append(("component", comp.reference))

        for net in project.nets:
            flags = []
            if net.is_power:
                flags.append(f"power rail{f' {net.inferred_voltage:g}V' if net.inferred_voltage else ''}")
            if net.is_ground:
                flags.append("ground")
            refs = sorted({p.component for p in net.pins})
            text = f"Net {net.name}: {', '.join(flags) or 'signal'}. Pins on: {', '.join(refs)}."
            texts.append(text)
            kinds.append(("net", net.name))

        for f in findings:
            text = (
                f"Finding {f.rule_id} [{f.severity.value}] {f.title}: {f.description} "
                f"Components: {', '.join(f.affected_components)}. Nets: {', '.join(f.affected_nets)}. "
                f"Fix: {f.suggested_fix}"
            )
            texts.append(text)
            kinds.append(("rule", f.rule_id))
            for ref in f.affected_components + f.affected_nets:
                self._findings_by_ref.setdefault(ref, []).append(f)

        for fname, dtext in (datasheet_texts or {}).items():
            for i, chunk in enumerate(_chunk(dtext, 600)):
                texts.append(f"Datasheet {fname} section {i}: {chunk}")
                kinds.append(("datasheet_section", f"{fname}#{i}"))

        summary = (
            f"Project {project.project.name or 'unnamed'} ({project.project.source_format}): "
            f"{len(project.components)} components, {len(project.nets)} nets, "
            f"{len(findings)} rule findings."
        )
        texts.append(summary)
        kinds.append(("project_summary", "summary"))

        vectors = self.embedder.embed(texts) if texts else []
        for (kind, ref), text, vec in zip(kinds, texts, vectors):
            self.store.add(kind, ref, text, vec)
            self._corpus.append((f"{kind}:{ref}", text))
        self._bm25 = BM25([t for _, t in self._corpus]) if self._corpus else None
        return self

    def retrieve(self, question: str, k: int = 8) -> RetrievedContext:
        notes = [f"embedder={self.embedder.provider}", f"reranker={self.reranker.provider}"]
        ctx = RetrievedContext(method_notes=notes)
        if self.project is None or not self._corpus:
            return ctx

        # 1. exact structured hits
        exact_components = [c.reference for c in self.project.components if _word_present(c.reference, question)]
        exact_nets = [n.name for n in self.project.nets if _word_present(n.name, question)]
        if exact_components:
            notes.append(f"exact components: {', '.join(exact_components)}")
        if exact_nets:
            notes.append(f"exact nets: {', '.join(exact_nets)}")

        # 2. keyword scores  3. vector similarity
        kw_scores = self._bm25.scores(question) if self._bm25 else [0.0] * len(self._corpus)
        q_vec = self.embedder.embed([question])[0]
        vec_hits: list[ScoredItem] = self.store.search(q_vec, k=k * 2)
        vec_by_text = {it.item.text: it.score for it in vec_hits}

        combined: list[tuple[float, int]] = []
        for i, (_src, text) in enumerate(self._corpus):
            score = 0.6 * kw_scores[i] + 0.4 * vec_by_text.get(text, 0.0)
            combined.append((score, i))
        combined.sort(reverse=True)
        top = [i for _s, i in combined[: k * 2]]

        # 4. rerank top candidates
        cand_texts = [self._corpus[i][1] for i in top]
        rerank_scores = self.reranker.rerank(question, cand_texts)
        reranked = sorted(zip(top, rerank_scores, cand_texts), key=lambda t: t[1], reverse=True) \
            if any(rerank_scores) else [(i, combined_score(combined, i), self._corpus[i][1]) for i in top]

        snippets: list[Snippet] = []
        surfaced_components: set[str] = set(exact_components)
        surfaced_nets: set[str] = set(exact_nets)
        for idx, score, text in reranked[:k]:
            src = self._corpus[idx][0]
            snippets.append(Snippet(source=src, text=text, score=round(float(score), 4)))
            kind, _, ref = src.partition(":")
            if kind == "component":
                surfaced_components.add(ref)
            elif kind == "net":
                surfaced_nets.add(ref)

        # findings tied to surfaced entities (+ exact matches always included first)
        finding_set: dict[str, RuleFinding] = {}
        for ref in list(surfaced_components) + list(surfaced_nets):
            for f in self._findings_by_ref.get(ref, []):
                finding_set[f.rule_id + "|" + f.title] = f

        ctx.components = sorted(surfaced_components)
        ctx.nets = sorted(surfaced_nets)
        ctx.snippets = snippets
        ctx.findings = list(finding_set.values())
        return ctx


def combined_score(combined: list[tuple[float, int]], target: int) -> float:
    for score, i in combined:
        if i == target:
            return score
    return 0.0


def _chunk(text: str, size: int) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)] or [""]
