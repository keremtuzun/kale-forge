"""Retrieval tests using the deterministic HashingEmbedder (no torch needed)."""
from __future__ import annotations

from app.models.normalized import PinType
from app.retrieval.embeddings import HashingEmbedder
from app.retrieval.keyword import BM25
from app.retrieval.rerank import NoopReranker
from app.retrieval.retriever import ProjectRetriever
from app.rules.base import Evidence, RuleCategory, RuleFinding, Severity
from app.tests.factories import comp, make_project


def _project():
    return make_project(
        [
            comp("U3", "LM358", pins=[("1", PinType.OUTPUT), ("8", PinType.POWER_IN)]),
            comp("U1", "STM32", pins=[("1", PinType.POWER_IN)]),
            comp("R1", "10k"),
        ],
        {"+3V3": [("U3", "8"), ("U1", "1")], "VOUT": [("U3", "1"), ("R1", "1")], "GND": [("R1", "2")]},
    )


def _finding():
    return RuleFinding(
        rule_id="PWR-009", rule_version="1.0.0", category=RuleCategory.POWER, severity=Severity.WARNING,
        title="Regulator thermal risk", description="U3 may overheat",
        affected_components=["U3"], affected_nets=["+3V3"],
        evidence=[Evidence(kind="calculation", description="P=2W")],
    )


def test_deterministic_hash_embedder():
    emb = HashingEmbedder()
    a = emb.embed(["decoupling capacitor near U1"])
    b = emb.embed(["decoupling capacitor near U1"])
    assert a == b
    assert emb.provider == "hash-fallback"


def test_exact_component_match_surfaces():
    r = ProjectRetriever(embedder=HashingEmbedder(), reranker=NoopReranker())
    r.build(_project(), [_finding()])
    ctx = r.retrieve("Why could U3 be overheating?")
    assert "U3" in ctx.components
    assert any(f.rule_id == "PWR-009" for f in ctx.findings)
    assert any(note.startswith("embedder=hash-fallback") for note in ctx.method_notes)


def test_net_question_surfaces_net():
    r = ProjectRetriever(embedder=HashingEmbedder(), reranker=NoopReranker())
    r.build(_project(), [])
    ctx = r.retrieve("Which components are connected to the +3V3 rail?")
    assert "+3V3" in ctx.nets


def test_deterministic_across_runs():
    def run():
        r = ProjectRetriever(embedder=HashingEmbedder(), reranker=NoopReranker())
        r.build(_project(), [_finding()])
        return [s.source for s in r.retrieve("explain U3 thermal").snippets]

    assert run() == run()


def test_bm25_ranks_relevant_higher():
    bm = BM25(["the ground plane is unfilled", "the LED needs a resistor", "decoupling capacitor missing"])
    scores = bm.scores("LED resistor")
    assert scores[1] == max(scores)
