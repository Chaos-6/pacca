"""
The /feedback (institutional-learning) endpoint must be governed like submit (#3).

Before this change, learn_from_feedback wrote a precedent to the case_precedents
RAG collection with **no IntentRecord, no scope guard, and the write happening
before the audit** — the one RAG mutation that escaped the P-3/P-4/P-5 governance
the submit route enforces. These tests pin the fix: an intent.declared first, a
scope-guarded precedent write, and the audit recorded before the state change.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_feedback_is_governed_and_audited_before_write() -> None:
    from pacca.api.routes.authorizations import FeedbackRequest, learn_from_feedback

    events: list[str] = []

    async def capture_log(**kwargs: object) -> object:
        events.append(f"audit:{kwargs['action']}")
        m = MagicMock()
        m.entry_id = f"A{len(events)}"
        return m

    def capture_add_precedent(**kwargs: object) -> None:
        events.append("precedent_write")

    fb = FeedbackRequest(
        case_summary="acute lumbar pain, manual laborer, <6 weeks",
        decision="AUTO_APPROVED",
        rationale="occupational-necessity exception",
    )

    with (
        patch("pacca.db.repository.AuditRepository.log", side_effect=capture_log),
        patch(
            "pacca.api.routes.authorizations.rag_engine.add_precedent",
            side_effect=capture_add_precedent,
        ),
    ):
        await learn_from_feedback(feedback=fb, session=AsyncMock())

    # 1. intent.declared is the FIRST event.
    assert events[0] == "audit:intent.declared", f"first event was {events[0]}"
    # 2. The precedent write is scope-guarded (a scope.allow is recorded for it).
    assert "audit:scope.allow" in events, "the precedent write was not scope-guarded"
    # 3. The learning event is audited BEFORE the state change (pre-write-audit).
    learn_idx = next(i for i, e in enumerate(events) if e == "audit:precedent_learned")
    write_idx = events.index("precedent_write")
    assert learn_idx < write_idx, "precedent was written before it was audited"


@pytest.mark.asyncio
async def test_feedback_intent_is_scoped_to_case_precedents_only() -> None:
    from pacca.models.intent import IntentRecord

    intent = IntentRecord.for_feedback(
        correlation_id="c", request_id="FB-1", subject_ref="learning"
    )
    assert intent.purpose == "institutional_learning"
    assert intent.allowed_collections == ["case_precedents"]
    assert "rag.write_precedent" in intent.allowed_actions
    # A learning run must NOT be able to touch the guideline collection or DB writes.
    assert "nccn_guidelines" not in intent.allowed_collections
    assert "db.write_decision" not in intent.allowed_actions
