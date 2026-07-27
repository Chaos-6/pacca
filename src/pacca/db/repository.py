"""
Repository pattern for database operations.

Provides a clean abstraction layer between the business logic
and database operations, with async support throughout.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, desc, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def uuid7() -> str:
    """Generate a time-sortable unique ID using uuid4 as fallback.

    The uuid7 package provides true UUIDv7 (time-ordered). When unavailable,
    uuid4 (random) is used instead — functionally identical for our purposes
    since we only need globally unique IDs, not strict time ordering.
    """
    try:
        from uuid7 import uuid7 as _uuid7

        return str(_uuid7())
    except ImportError:
        return str(uuid4())


# Re-export as the same name callers expect
__all__ = ["uuid7"]

from pacca.config import get_logger
from pacca.db.models import (
    AuditLogModel,
    AuthorizationDecisionModel,
    AuthorizationRequestModel,
    GuidelineModel,
)
from pacca.models.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from pacca.models.enums import AuthorizationStatus, ReviewTier

logger = get_logger(__name__)


class AuthorizationRepository:
    """Repository for authorization request operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, request: AuthorizationRequest) -> AuthorizationRequestModel:
        """
        Create a new authorization request in the database.

        Args:
            request: The domain model to persist

        Returns:
            The created database model
        """
        # Map the minimal submission API (AuthorizationRequest + its ClinicalCase)
        # onto the storage model. Fields the API does not collect (gender,
        # descriptions, treatment category, provider name, payer/member) are stored
        # as NULL, never fabricated — honest data for a healthcare audit store. The
        # richer columns are populated when upstream systems provide them.
        case = request.clinical_case
        db_request = AuthorizationRequestModel(
            request_id=request.request_id,
            patient_id=request.patient_id,
            patient_age=case.patient_age,
            primary_diagnosis_code=case.primary_diagnosis_code,
            treatment_code=case.procedure_code,
            estimated_cost=case.estimated_annual_cost,
            provider_id=request.provider_npi,
            complexity=case.complexity_score,
            # Retain the submitted evidence as JSON for audit traceability.
            evidence_data={"evidence": [e.model_dump(mode="json") for e in case.evidence]}
            if case.evidence
            else None,
            # status defaults to "submitted"; urgency defaults to "routine";
            # submitted_at/updated_at default to now(). Everything else is NULL.
        )

        self.session.add(db_request)
        await self.session.flush()

        logger.info(
            "authorization_request_created",
            request_id=request.request_id,
        )

        return db_request

    async def get_by_id(self, request_id: str) -> AuthorizationRequestModel | None:
        """
        Get an authorization request by ID.

        Args:
            request_id: The unique request identifier

        Returns:
            The database model or None if not found
        """
        result = await self.session.execute(
            select(AuthorizationRequestModel).where(
                AuthorizationRequestModel.request_id == request_id
            )
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        request_id: str,
        status: AuthorizationStatus,
        **additional_fields: Any,
    ) -> bool:
        """
        Update the status of an authorization request.

        Args:
            request_id: The request to update
            status: The new status
            **additional_fields: Additional fields to update

        Returns:
            True if updated, False if not found
        """
        update_data = {
            "status": status.value,
            "updated_at": datetime.utcnow(),
            **additional_fields,
        }

        result = await self.session.execute(
            update(AuthorizationRequestModel)
            .where(AuthorizationRequestModel.request_id == request_id)
            .values(**update_data)
        )

        if result.rowcount > 0:
            logger.info(
                "authorization_status_updated",
                request_id=request_id,
                new_status=status.value,
            )
            return True
        return False

    async def list_requests(
        self,
        status: AuthorizationStatus | None = None,
        payer_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuthorizationRequestModel]:
        """
        List authorization requests with optional filtering.

        Args:
            status: Filter by status
            payer_id: Filter by payer
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of matching requests
        """
        query = select(AuthorizationRequestModel)

        conditions = []
        if status:
            conditions.append(AuthorizationRequestModel.status == status.value)
        if payer_id:
            conditions.append(AuthorizationRequestModel.payer_id == payer_id)

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(desc(AuthorizationRequestModel.submitted_at))
        query = query.limit(limit).offset(offset)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_escalated_for_review(
        self,
        limit: int = 50,
    ) -> list[tuple[AuthorizationDecisionModel, AuthorizationRequestModel]]:
        """
        List decisions awaiting Medical Director review, with their requests.

        Keyed on the DECISION's outcome, not the request's `status` column. The
        submit route never updates that column after the orchestrator runs, so
        every row still reads "submitted" and filtering requests by
        AuthorizationStatus.IN_REVIEW would return an empty queue forever.

        Oldest first: a review queue is worked front to back, unlike
        `list_requests`, which shows the most recent submissions.

        Returns:
            (decision, request) pairs, oldest decision first.
        """
        query = (
            select(AuthorizationDecisionModel, AuthorizationRequestModel)
            .join(
                AuthorizationRequestModel,
                AuthorizationDecisionModel.request_id == AuthorizationRequestModel.request_id,
            )
            .where(AuthorizationDecisionModel.outcome == AuthorizationStatus.IN_REVIEW.value)
            .order_by(AuthorizationDecisionModel.decided_at)
            .limit(limit)
        )

        result = await self.session.execute(query)
        return [(decision, request) for decision, request in result.all()]

    async def count_requests(
        self,
        status: AuthorizationStatus | None = None,
        payer_id: str | None = None,
    ) -> int:
        """Count authorization requests with optional filtering."""
        from sqlalchemy import func

        query = select(func.count(AuthorizationRequestModel.id))

        conditions = []
        if status:
            conditions.append(AuthorizationRequestModel.status == status.value)
        if payer_id:
            conditions.append(AuthorizationRequestModel.payer_id == payer_id)

        if conditions:
            query = query.where(and_(*conditions))

        result = await self.session.execute(query)
        return result.scalar() or 0


class DecisionRepository:
    """Repository for authorization decision operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        decision: AuthorizationDecision,
        request_id: str,
        processing_time_ms: int | None = None,
        total_tokens: int | None = None,
    ) -> AuthorizationDecisionModel:
        """
        Persist an authorization decision.

        The domain AuthorizationDecision carries no request_id, so the caller
        passes the run's request_id. `outcome` stores the decision status;
        `rationale` (free text) is wrapped into the rationale_data JSON column.
        `is_autonomous` / `was_escalated` are derived from the review tier and
        status. The other columns (conditions, dates, quantities) are not part of
        the current decision model and stay NULL.

        Args:
            decision: The domain decision to persist
            request_id: The run's request identifier (decision has none)
            processing_time_ms: Time taken to process
            total_tokens: Total LLM tokens used

        Returns:
            The created database model
        """
        db_decision = AuthorizationDecisionModel(
            decision_id=decision.decision_id,
            request_id=request_id,
            outcome=decision.status.value,
            confidence_score=decision.confidence_score,
            rationale_data={"text": decision.rationale} if decision.rationale else None,
            decided_at=decision.timestamp,
            decided_by=decision.review_tier_used.value,
            is_autonomous=decision.review_tier_used == ReviewTier.AUTOMATED,
            was_escalated=decision.status == AuthorizationStatus.IN_REVIEW,
            processing_time_ms=processing_time_ms,
            total_tokens_used=total_tokens,
        )

        self.session.add(db_decision)
        # chg-12 (B6): roll back before the exception leaves this method. Without
        # it the session stays poisoned, and the caller's next statement — the
        # audit write — raises PendingRollbackError, masking the real cause. This
        # is not error recovery: the write failed and the error still propagates.
        # It only ensures the failure is legible in the log.
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            logger.error(
                "authorization_decision_write_failed",
                decision_id=decision.decision_id,
                request_id=request_id,
                reason="integrity_error",
            )
            raise

        logger.info(
            "authorization_decision_created",
            decision_id=decision.decision_id,
            request_id=request_id,
            outcome=decision.status.value,
        )

        return db_decision

    async def get_by_request_id(self, request_id: str) -> AuthorizationDecisionModel | None:
        """Get decision by request ID."""
        result = await self.session.execute(
            select(AuthorizationDecisionModel).where(
                AuthorizationDecisionModel.request_id == request_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, decision_id: str) -> AuthorizationDecisionModel | None:
        """Get decision by decision ID."""
        result = await self.session.execute(
            select(AuthorizationDecisionModel).where(
                AuthorizationDecisionModel.decision_id == decision_id
            )
        )
        return result.scalar_one_or_none()


class AuditRepository:
    """Repository for audit log operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(
        self,
        action: str,
        actor: str,
        actor_type: str,
        request_id: str | None = None,
        decision_id: str | None = None,
        correlation_id: str | None = None,
        details: dict[str, Any] | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        success: bool = True,
        error_message: str | None = None,
        duration_ms: int | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> AuditLogModel:
        """
        Create an audit log entry.

        Args:
            action: The action performed
            actor: Who performed the action
            actor_type: Type of actor (agent, user, system)
            request_id: Related request ID
            decision_id: Related decision ID
            correlation_id: Correlation ID for tracing
            details: Additional details
            input_summary: Summary of input
            output_summary: Summary of output
            success: Whether action succeeded
            error_message: Error message if failed
            duration_ms: Duration in milliseconds
            token_usage: LLM token usage

        Returns:
            The created audit log entry
        """
        entry = AuditLogModel(
            entry_id=str(uuid7()),
            request_id=request_id,
            decision_id=decision_id,
            correlation_id=correlation_id,
            action=action,
            actor=actor,
            actor_type=actor_type,
            details=details,
            input_summary=input_summary,
            output_summary=output_summary,
            success=success,
            error_message=error_message,
            duration_ms=duration_ms,
            token_usage=token_usage,
        )

        self.session.add(entry)
        await self.session.flush()

        return entry

    async def get_by_request_id(
        self,
        request_id: str,
        limit: int = 100,
    ) -> list[AuditLogModel]:
        """Get audit logs for a request."""
        result = await self.session.execute(
            select(AuditLogModel)
            .where(AuditLogModel.request_id == request_id)
            .order_by(desc(AuditLogModel.timestamp))
            .limit(limit)
        )
        return list(result.scalars().all())


class GuidelineRepository:
    """Repository for clinical guideline operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        guideline_id: str,
        name: str,
        version: str,
        source: str,
        summary: str,
        effective_date: datetime,
        **kwargs: Any,
    ) -> GuidelineModel:
        """Create a new guideline record."""
        guideline = GuidelineModel(
            guideline_id=guideline_id,
            name=name,
            version=version,
            source=source,
            summary=summary,
            effective_date=effective_date,
            **kwargs,
        )

        self.session.add(guideline)
        await self.session.flush()

        return guideline

    async def get_by_id(self, guideline_id: str) -> GuidelineModel | None:
        """Get guideline by ID."""
        result = await self.session.execute(
            select(GuidelineModel).where(GuidelineModel.guideline_id == guideline_id)
        )
        return result.scalar_one_or_none()

    async def list_active(
        self,
        specialty: str | None = None,
        limit: int = 100,
    ) -> list[GuidelineModel]:
        """List active guidelines, optionally filtered by specialty."""
        query = select(GuidelineModel).where(GuidelineModel.is_active)

        # Note: specialty filtering would require JSONB contains operator
        # For simplicity, filtering by specialty is left for the application layer

        query = query.order_by(GuidelineModel.name).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())
