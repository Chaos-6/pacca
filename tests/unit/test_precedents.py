"""
Precedent write behaviour — institutional-memory RAG collection.

The precedent id was ``f"prec_{abs(hash(case_summary))}"`` with ``.add()``.
Python's ``hash(str)`` is per-process randomized (PYTHONHASHSEED), so the same
override produced a *different* id on every server restart — silently
accumulating duplicate precedents — and a *repeat* within one process collided
on ``.add()`` and raised. Same anti-pattern as the B6 ``decision_id`` bug:
a derived/unstable value used as a storage key.

The fix: a deterministic content id (stable across processes) + ``upsert`` so the
same override is idempotent and a genuine update overwrites in place.

Hermetic: a fake embedder injected so the test never downloads ChromaDB's model.
"""

from __future__ import annotations

import numpy as np
from chromadb import EmbeddingFunction

from pacca.integrations.vector_store import GuidelineRetriever


class _FakeEmbedding(EmbeddingFunction):
    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "fake-test-embedding"

    def __call__(self, input: list[str]) -> list[np.ndarray]:
        return [np.array([float(len(t) % 7), 1.0, 2.0, 3.0], dtype=np.float32) for t in input]


def _retriever(tmp_path: object) -> GuidelineRetriever:
    return GuidelineRetriever(db_path=str(tmp_path), embedding_function=_FakeEmbedding())


def test_same_precedent_is_idempotent(tmp_path: object) -> None:
    """Adding the identical override twice must not duplicate it."""
    r = _retriever(tmp_path)
    for _ in range(2):
        r.add_precedent(
            case_summary="acute lumbar pain, manual laborer, <6 weeks",
            rationale="occupational-necessity exception",
            outcome="AUTO_APPROVED",
        )
    assert r.precedent_count() == 1, "the same override was stored twice (unstable id)"


def test_distinct_precedents_are_kept_separately(tmp_path: object) -> None:
    r = _retriever(tmp_path)
    r.add_precedent(case_summary="case one", rationale="r1", outcome="AUTO_APPROVED")
    r.add_precedent(case_summary="case two", rationale="r2", outcome="DENIED")
    assert r.precedent_count() == 2


def test_re_adding_updates_in_place(tmp_path: object) -> None:
    """A corrected rationale for the same scenario overwrites, not appends."""
    r = _retriever(tmp_path)
    r.add_precedent(case_summary="same scenario", rationale="first take", outcome="IN_REVIEW")
    r.add_precedent(
        case_summary="same scenario", rationale="corrected take", outcome="AUTO_APPROVED"
    )
    assert r.precedent_count() == 1
    ctx = r.query("same scenario").text
    assert "corrected take" in ctx and "first take" not in ctx


def test_precedent_id_is_deterministic_and_content_keyed() -> None:
    """The id is stable across calls (would fail with the old hash()) and unique per scenario."""
    from pacca.integrations.vector_store import _precedent_id

    assert _precedent_id("scenario A") == _precedent_id("scenario A")
    assert _precedent_id(" scenario A ") == _precedent_id("scenario A")  # trimmed
    assert _precedent_id("scenario A") != _precedent_id("scenario B")
    assert _precedent_id("scenario A").startswith("prec_")


def test_precedent_round_trip_surfaces_in_agent_context(tmp_path: object) -> None:
    """Write -> retrieve: a stored override surfaces under the exact header the
    DecisionAgent prompt keys on ("PAST MEDICAL DIRECTOR DECISIONS"). This is the
    read half of the institutional-memory loop the e2e proved end-to-end
    (IN_REVIEW -> AUTO_APPROVED); pinning it hermetically guards against a
    regression that would silently stop precedents reaching the agent.
    """
    r = _retriever(tmp_path)
    r.add_precedent(
        case_summary="manual laborer, acute lumbar pain <6 weeks, no red flags",
        rationale="occupational-necessity exception approved by the Medical Director",
        outcome="AUTO_APPROVED",
    )
    context = r.query("construction worker acute lumbar pain MRI").text
    assert "PAST MEDICAL DIRECTOR DECISIONS" in context
    assert "occupational-necessity exception" in context
    assert "AUTO_APPROVED" in context
