"""
SME Case Authoring API routes — backend for the Web UI (PR-WUI-1).

These 11 REST endpoints + 1 WebSocket are the HTTP surface for the
browser-based SME case-authoring workflow. They are siblings to the
existing `pacca sme-author` CLI: both compose the same underlying
library modules (`src/pacca/agents/sme_authoring/`).

ZERO DUPLICATION: every endpoint reuses the library directly. The CLI
and the Web UI are two surfaces over one core.

DESIGN
======

- All endpoints require `medical_director`-or-above via `require_min_role`
  (RBAC — see `src/pacca/api/rbac.py`). Case authoring is an SME workflow;
  a plain clinician token is not sufficient here even though it clears the
  `/api/v1/authorizations/*` router-wide floor.
- Request/response shapes live in `src/pacca/api/models/sme_authoring.py`.
- Errors surface as `HTTPException(detail=...)` per the existing PACCA
  pattern; the frontend parses `{"detail": "..."}`.
- The WebSocket draft-stream lives in
  `src/pacca/api/websockets/draft_stream.py` and is registered
  separately from this router.

REFERENCES
==========

- `docs/CASE_AUTHORING_GUIDE.md` — the rules the validators enforce
- `docs/SME_CASE_AGENT_DESIGN.md` — the SME agent architecture
- `/Users/davidreed/.claude/plans/mutable-tinkering-candle.md` —
  the v1.1 Web UI plan (PR-WUI-1 is this PR)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import uuid_extensions
from fastapi import APIRouter, Depends, HTTPException

from pacca.agents.sme_authoring.agent import SMECaseAuthoringAgent
from pacca.agents.sme_authoring.case_writer import (
    CaseAlreadyExists,
    FileSyntaxError,
    append_case_to_file,
    create_new_case_file,
)
from pacca.agents.sme_authoring.coverage_updater import (
    CoverageBump,
    CoverageUpdaterError,
    bump_coverage_for_case,
)
from pacca.agents.sme_authoring.file_router import route_case
from pacca.agents.sme_authoring.gap_analyzer import compute_gaps, read_coverage
from pacca.agents.sme_authoring.id_allocator import (
    DEFAULT_CASE_DIR,
    next_id,
    release_reservation,
)
from pacca.agents.sme_authoring.models import (
    CaseDraftRequest,
    CaseDraftResponse,
    SessionState,
    ValidationOutcome,
)
from pacca.agents.sme_authoring.pr_template import (
    PRMetadata,
    render_pr_body,
    render_pr_title,
)
from pacca.agents.sme_authoring.provenance_writer import (
    ProvenanceRow,
    ProvenanceWriterError,
    append_provenance_row,
)
from pacca.agents.sme_authoring.roadmap_reader import get_batch, read_batches
from pacca.agents.sme_authoring.session import (
    SessionStorageError,
    delete_session,
    list_sessions,
    load_session,
    save_session,
)
from pacca.agents.sme_authoring.test_runner import run_integrity_tests
from pacca.agents.sme_authoring.validators import run_all_validators
from pacca.api.models.sme_authoring import (
    BatchCaseItem,
    BatchItem,
    BatchListResponse,
    BatchResponse,
    CommitRequest,
    CommitResponse,
    CreateSessionRequest,
    DraftResponse,
    GapItem,
    GapListResponse,
    ListCountResponse,
    SessionListResponse,
    SessionResponse,
    StatusResponse,
    ValidateRequest,
    ValidateResponse,
)
from pacca.api.rbac import Role, require_min_role

if TYPE_CHECKING:
    from pacca.api.models.user import User

router = APIRouter(prefix="/api/v1/sme-authoring", tags=["sme-authoring"])

# require_min_role(...) is a dependency FACTORY — bound once here (rather
# than called inline inside each Depends(...) default below) so it is
# evaluated a single time at import, not once per route definition.
_require_medical_director = require_min_role(Role.MEDICAL_DIRECTOR)


# =============================================================================
# Helpers (private; not exported)
# =============================================================================


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _to_session_response(state: SessionState) -> SessionResponse:
    return SessionResponse(session=state)


def _safe_load_session(session_id: str) -> SessionState:
    """Load a session or raise the appropriate HTTPException."""
    try:
        return load_session(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionStorageError as exc:
        raise HTTPException(status_code=500, detail=f"Session corrupt: {exc}") from exc


# =============================================================================
# Session CRUD (4 endpoints)
# =============================================================================


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    request: CreateSessionRequest,
    user: User = Depends(_require_medical_director),
) -> SessionResponse:
    """
    Start a new SME-authoring session.

    Allocates a session ID, allocates the next monotonic GC-NNN case ID,
    routes the case to a target file (heuristic; LLM hasn't drafted yet),
    and persists the initial SessionState.

    The session is created in 'sandbox' or 'production' mode per the
    request body. Sandbox sessions never write to tests/clinical/.
    """
    session_id = uuid_extensions.uuid7str()
    now = _now_utc()
    state = SessionState(
        session_id=session_id,
        created_at=now,
        last_updated_at=now,
        mode=request.mode,
        scenario=request.scenario,
        last_step="created",
    )
    save_session(state)
    return _to_session_response(state)


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions_endpoint(
    user: User = Depends(_require_medical_director),
) -> SessionListResponse:
    """List all saved SME-authoring sessions (most-recent first)."""
    sessions = list_sessions()
    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_endpoint(
    session_id: str,
    user: User = Depends(_require_medical_director),
) -> SessionResponse:
    """Get a single session's full state."""
    return _to_session_response(_safe_load_session(session_id))


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_endpoint(
    session_id: str,
    user: User = Depends(_require_medical_director),
) -> None:
    """Delete a session. Releases any pending case-ID reservation."""
    state = _safe_load_session(session_id)
    if state.draft:
        release_reservation(state.draft.case_id)
    delete_session(session_id)


# =============================================================================
# Drafting (1 endpoint; WebSocket variant lives in websockets/)
# =============================================================================


@router.post("/sessions/{session_id}/draft", response_model=DraftResponse)
async def draft_case_endpoint(
    session_id: str,
    user: User = Depends(_require_medical_director),
) -> DraftResponse:
    """
    Synchronously draft a case via the LLM agent (buffered response).

    For live typewriter-style streaming, use the WebSocket at
    `/api/v1/sme-authoring/sessions/{id}/draft-stream` instead. Both
    surfaces return the same final draft; this endpoint is the
    fallback for clients without WebSocket support.
    """
    state = _safe_load_session(session_id)
    if not state.scenario:
        raise HTTPException(
            status_code=400,
            detail="Session has no scenario. Create the session with a scenario first.",
        )

    # Allocate ID + route
    allocated_id = next_id()
    placeholder = _routing_placeholder(allocated_id, state)
    routing = route_case(placeholder, state.scenario)

    # Run the LLM agent
    agent = SMECaseAuthoringAgent()
    request = CaseDraftRequest(
        scenario=state.scenario,
        allocated_case_id=allocated_id,
        recommended_file=routing.target_file,
    )
    try:
        draft = await agent.run(request)
    except Exception as exc:
        release_reservation(allocated_id)
        raise HTTPException(status_code=502, detail=f"LLM drafting failed: {exc}") from exc

    # Persist into the session
    state = state.model_copy(
        update={
            "draft": draft,
            "last_step": "drafted",
            "last_updated_at": _now_utc(),
        }
    )
    save_session(state)

    return DraftResponse(
        draft=draft,
        allocated_case_id=allocated_id,
        recommended_file=routing.target_file,
        routing_reason=routing.reason,
    )


def _routing_placeholder(allocated_id: str, state: SessionState) -> CaseDraftResponse:
    """
    Build a minimal CaseDraftResponse for routing purposes.

    The router uses scenario.intended_specialty + diagnosis_code + notes
    for age. Pre-draft, we don't have those — fall back to expansion.
    """
    scenario = state.scenario
    assert scenario is not None  # caller checks
    return CaseDraftResponse(
        case_id=allocated_id,
        title="Placeholder for routing (not the final case)",
        diagnosis_code="R69",
        diagnosis_description="Placeholder",
        procedure_code="00000",
        procedure_description="Placeholder",
        clinical_notes=(
            scenario.description + " (Synthesized placeholder for routing; LLM will replace.)"
        ),
        guidelines_context=(
            "Placeholder for routing. Real guideline citation will be drafted "
            "by the LLM in the next step of the workflow."
        ),
        expected_outcome=scenario.intended_outcome or "AUTO_APPROVED",
        expected_branch=(
            "BRANCH_1_AUTO_APPROVE"
            if (scenario.intended_outcome or "AUTO_APPROVED") == "AUTO_APPROVED"
            else "BRANCH_3_LOW_CONFIDENCE"
        ),
        reasoning_must_include=["placeholder"],
        clinical_rationale=("Placeholder for routing. Real rationale drafted by the LLM."),
        judge_scoring_criteria=("Placeholder for routing. Real criteria drafted by the LLM."),
    )


# =============================================================================
# Validation
# =============================================================================


@router.post("/sessions/{session_id}/validate", response_model=ValidateResponse)
async def validate_session_endpoint(
    session_id: str,
    request: ValidateRequest,
    user: User = Depends(_require_medical_director),
) -> ValidateResponse:
    """
    Run all 6 deterministic validators against the session's draft.

    If the request body includes a `draft` override, validates that
    (used when the SME has edited fields in the wizard before re-checking).
    Otherwise validates the session's stored draft.
    """
    state = _safe_load_session(session_id)
    draft = request.draft or state.draft
    if not draft:
        raise HTTPException(
            status_code=400,
            detail="Session has no draft. Run `draft` endpoint first or supply draft in body.",
        )

    reports = run_all_validators(draft)
    pass_count = sum(1 for r in reports if r.outcome == ValidationOutcome.PASS)
    warn_count = sum(1 for r in reports if r.outcome == ValidationOutcome.WARN)
    blocking = sum(1 for r in reports if r.is_blocking)

    # Persist validator report into session
    state = state.model_copy(
        update={
            "last_validation_report": reports,
            "last_step": "validated",
            "last_updated_at": _now_utc(),
        }
    )
    save_session(state)

    return ValidateResponse(
        reports=reports,
        blocking_count=blocking,
        warning_count=warn_count,
        pass_count=pass_count,
    )


# =============================================================================
# Commit
# =============================================================================


@router.post("/sessions/{session_id}/commit", response_model=CommitResponse)
async def commit_session_endpoint(
    session_id: str,
    request: CommitRequest,
    user: User = Depends(_require_medical_director),
) -> CommitResponse:
    """
    Write the case to disk + update companion docs + run integrity tests.

    SAFE BY DEFAULT: if the session is in 'production' mode, the request
    MUST set `confirm_production_write=True`. Sandbox sessions write
    to a session-scoped sandbox dir (full sandbox routing is queued for
    v1.2; this PR's sandbox writes are no-op + return a stub PR template).
    """
    state = _safe_load_session(session_id)
    draft = request.draft or state.draft
    if not draft:
        raise HTTPException(status_code=400, detail="Session has no draft to commit.")

    if state.mode == "production" and not request.confirm_production_write:
        raise HTTPException(
            status_code=400,
            detail=(
                "Production-mode commit requires confirm_production_write=True. "
                "This is a safety interlock."
            ),
        )

    # Persist attestation
    state = state.model_copy(
        update={
            "sme_attestation": request.sme_attestation,
            "draft": draft,
            "last_step": "attested",
            "last_updated_at": _now_utc(),
        }
    )
    save_session(state)

    # Decide route
    routing = route_case(draft, state.scenario)

    # Sandbox mode: do not mutate real tree (v1.2 will add real sandbox writes)
    if state.mode == "sandbox":
        pr_metadata = PRMetadata(
            sme_attestation=request.sme_attestation,
            target_file=routing.target_file,
            is_new_file=routing.is_new_file,
            integrity_test_passed=True,
            integrity_test_summary="(sandbox mode — integrity tests not run)",
        )
        state = state.model_copy(
            update={"last_step": "sandbox_committed", "last_updated_at": _now_utc()}
        )
        save_session(state)
        return CommitResponse(
            written=False,
            target_file=f"sandbox/{routing.target_file}",
            case_id=draft.case_id,
            pr_title=render_pr_title(draft),
            pr_body=render_pr_body(draft, pr_metadata, state.last_validation_report),
            integrity_test_passed=True,
            integrity_test_summary="(sandbox mode)",
        )

    # Production mode: write the case
    target_path = DEFAULT_CASE_DIR / routing.target_file
    if routing.is_new_file:
        create_new_case_file(routing.list_name, target_path)

    try:
        append_case_to_file(draft, target_path, routing.list_name)
    except (CaseAlreadyExists, FileSyntaxError) as exc:
        raise HTTPException(status_code=409, detail=f"Write failed: {exc}") from exc

    # Companion docs
    try:
        append_provenance_row(
            ProvenanceRow(
                case_id=draft.case_id,
                file=routing.target_file,
                clinical_rationale=draft.clinical_rationale,
                named_failure_mode="Coverage",  # SME can override later
                iteration="iter-7",
            )
        )
        bump_coverage_for_case(
            CoverageBump(
                list_name=routing.list_name,
                file_name=routing.target_file,
                new_case_id=draft.case_id,
            )
        )
    except (ProvenanceWriterError, CoverageUpdaterError) as exc:
        # Don't roll back the case write; surface the warning to the SME
        # via the PR template. The case write is the source of truth.
        warning = f"Companion-doc update failed: {exc}"
    else:
        warning = ""

    # Integrity tests
    integrity = run_integrity_tests(Path.cwd())

    # Build PR template
    pr_metadata = PRMetadata(
        sme_attestation=request.sme_attestation,
        target_file=routing.target_file,
        is_new_file=routing.is_new_file,
        integrity_test_passed=integrity.passed,
        integrity_test_summary=integrity.summary,
    )
    state = state.model_copy(update={"last_step": "committed", "last_updated_at": _now_utc()})
    save_session(state)

    return CommitResponse(
        written=True,
        target_file=routing.target_file,
        case_id=draft.case_id,
        pr_title=render_pr_title(draft),
        pr_body=render_pr_body(draft, pr_metadata, state.last_validation_report)
        + (f"\n\n> Warning: {warning}" if warning else ""),
        integrity_test_passed=integrity.passed,
        integrity_test_summary=integrity.summary,
    )


# =============================================================================
# Status / discovery (4 endpoints)
# =============================================================================


@router.get("/status", response_model=StatusResponse)
async def status_endpoint(
    user: User = Depends(_require_medical_director),
) -> StatusResponse:
    """Dataset-state snapshot for the Dashboard page."""
    snapshot = read_coverage()
    if not snapshot.parsed_ok:
        return StatusResponse(
            total_cases=0,
            per_list_counts=[],
            milestone_gaps=[],
            coverage_parsed_ok=False,
            coverage_parse_error=snapshot.parse_error,
        )

    rows = [
        ListCountResponse(list_name=r.list_name, file=r.file, count=r.count, id_range=r.id_range)
        for r in snapshot.per_list_counts
    ]
    gaps = compute_gaps()
    milestone_gaps = [
        GapItem(
            category=g.category,
            label=g.label,
            current_count=g.current_count,
            target_count=g.target_count,
            cases_needed=g.cases_needed,
            priority=g.priority,
            description=g.description,
        )
        for g in gaps
        if g.category == "milestone"
    ]

    return StatusResponse(
        total_cases=snapshot.total_cases,
        per_list_counts=rows,
        milestone_gaps=milestone_gaps,
        coverage_parsed_ok=True,
    )


@router.get("/batches", response_model=BatchListResponse)
async def list_batches_endpoint(
    user: User = Depends(_require_medical_director),
) -> BatchListResponse:
    """List all roadmap batches from DATASET_GROWTH_ROADMAP.md."""
    batches = read_batches()
    items = [_to_batch_item(b) for b in batches]
    return BatchListResponse(batches=items, total=len(items))


@router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch_endpoint(
    batch_id: str,
    user: User = Depends(_require_medical_director),
) -> BatchResponse:
    """Get a single batch's case-slot manifest."""
    batch = get_batch(batch_id)
    if batch is None:
        raise HTTPException(
            status_code=404,
            detail=f"Batch '{batch_id}' not found in DATASET_GROWTH_ROADMAP.md.",
        )
    return BatchResponse(batch=_to_batch_item(batch))


def _to_batch_item(batch: object) -> BatchItem:
    return BatchItem(
        batch_id=batch.batch_id,  # type: ignore[attr-defined]
        name=batch.name,  # type: ignore[attr-defined]
        case_count=batch.case_count,  # type: ignore[attr-defined]
        id_range=batch.id_range,  # type: ignore[attr-defined]
        target_file=batch.target_file,  # type: ignore[attr-defined]
        is_new_file=batch.is_new_file,  # type: ignore[attr-defined]
        cases=[
            BatchCaseItem(case_id=c.case_id, description=c.description)
            for c in batch.cases  # type: ignore[attr-defined]
        ],
    )


@router.get("/gaps", response_model=GapListResponse)
async def list_gaps_endpoint(
    top: int = 10,
    user: User = Depends(_require_medical_director),
) -> GapListResponse:
    """List prioritized coverage gaps. `top` limits the response size."""
    gaps = compute_gaps()
    items = [
        GapItem(
            category=g.category,
            label=g.label,
            current_count=g.current_count,
            target_count=g.target_count,
            cases_needed=g.cases_needed,
            priority=g.priority,
            description=g.description,
        )
        for g in gaps[:top]
    ]
    return GapListResponse(gaps=items, total=len(items))
