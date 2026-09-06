"""
Integrity failures must report themselves — PRODUCTION_READINESS B6, part 2.

The secondary defect found while reproducing B6: when the decision INSERT hit a
UNIQUE violation, nothing rolled the async session back. The very next statement
(the audit write) then raised::

    sqlalchemy.exc.PendingRollbackError: This Session's transaction has been
    rolled back due to a previous exception during flush.

so the log showed a session-state error instead of the actual IntegrityError.
An operator reading that log learns nothing about the real cause.

chg-12 makes the repository roll back and re-raise, so the original exception is
what propagates. The rollback is *not* an attempt to recover — the write genuinely
failed and the caller must handle it. It exists so the failure is legible.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from pacca.db.repository import DecisionRepository
from pacca.models.authorization import AuthorizationDecision
from pacca.models.enums import AuthorizationStatus, ReviewTier


def _decision() -> AuthorizationDecision:
    return AuthorizationDecision(
        status=AuthorizationStatus.AUTO_APPROVED,
        confidence_score=0.98,
        rationale="synthetic rationale",
        review_tier_used=ReviewTier.AUTOMATED,
        model_id="claude-sonnet-4-5-20250929",
        prompt_version="v2.7",
    )


def _session_that_fails_on_flush() -> Any:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock(
        side_effect=IntegrityError("INSERT ...", params={}, orig=Exception("UNIQUE"))
    )
    session.rollback = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_integrity_error_rolls_the_session_back() -> None:
    """Without this, the next statement raises PendingRollbackError instead."""
    session = _session_that_fails_on_flush()

    with pytest.raises(IntegrityError):
        await DecisionRepository(session).create(_decision(), request_id="REQ-1")

    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_original_integrity_error_is_what_propagates() -> None:
    """The caller must see the real cause, not a masking session-state error."""
    session = _session_that_fails_on_flush()

    with pytest.raises(IntegrityError) as excinfo:
        await DecisionRepository(session).create(_decision(), request_id="REQ-1")

    assert "UNIQUE" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_successful_write_does_not_roll_back() -> None:
    """The rollback must be confined to the failure path."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()

    await DecisionRepository(session).create(_decision(), request_id="REQ-1")

    session.rollback.assert_not_awaited()
