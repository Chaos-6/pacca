"""
The scope guard's collection model must match the real RAG collections (#2).

Before this change, `IntentRecord` allowed a phantom collection `clinical_guidelines`
while the live retriever reads `nccn_guidelines` and `case_precedents` — so the
submit route's RAG scope check named a collection that is never queried and the
two real collections (including institutional-memory precedents) were effectively
ungoverned. These tests pin the model to reality.
"""

from __future__ import annotations

import pytest

from pacca.agents.scope_guard import ScopeViolation, enforce_scope
from pacca.integrations.vector_store import GUIDELINE_COLLECTION, PRECEDENT_COLLECTION
from pacca.models.intent import IntentRecord


def _intent() -> IntentRecord:
    return IntentRecord.for_prior_auth(
        correlation_id="c", request_id="AUTH-1", subject_ref="SUBJ-1"
    )


def test_intent_allows_the_real_rag_collections_not_the_phantom() -> None:
    allowed = _intent().allowed_collections
    assert GUIDELINE_COLLECTION in allowed
    assert PRECEDENT_COLLECTION in allowed
    assert "clinical_guidelines" not in allowed, "the phantom collection is still declared"


def test_allowed_collections_match_the_retrievers_actual_collections() -> None:
    """Drift guard: the governed set is exactly what the retriever reads."""
    from pacca.integrations.vector_store import RAG_COLLECTIONS

    assert set(_intent().allowed_collections) == set(RAG_COLLECTIONS)


@pytest.mark.asyncio
async def test_guard_allows_the_real_collections() -> None:
    for coll in (GUIDELINE_COLLECTION, PRECEDENT_COLLECTION):
        # enforce mode, correct collection → no raise
        await enforce_scope(_intent(), "rag.query", mode="enforce", collection_name=coll)


@pytest.mark.asyncio
async def test_guard_denies_an_unlisted_collection() -> None:
    with pytest.raises(ScopeViolation):
        await enforce_scope(
            _intent(), "rag.query", mode="enforce", collection_name="exfiltration_collection"
        )
