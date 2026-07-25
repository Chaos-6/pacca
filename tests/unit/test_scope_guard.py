"""Deterministic scope-guard probes (P-4 / chg-8).

Cross-case adversarial probes for the minimum-necessary guard, run WITHOUT any
LLM so they stay in the fast `make test` suite. Each asserts a fail-closed
denial (enforce mode) for an out-of-scope call, that legitimate in-scope calls
pass, that warn mode audits-but-does-not-block, and that scope.deny audit
details carry arg NAMES only (no PHI values).
"""

import pytest

from pacca.agents.scope_guard import ScopeViolation, enforce_scope
from pacca.models.intent import IntentRecord


def _intent() -> IntentRecord:
    return IntentRecord.for_prior_auth(
        correlation_id="COR-A", request_id="REQ-A", subject_ref="PAT-A"
    )


# ── Fail-closed denials (enforce mode) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_action_denied():
    with pytest.raises(ScopeViolation) as exc:
        await enforce_scope(_intent(), "db.delete_everything")
    assert any("action_not_allowed" in v for v in exc.value.violations)


@pytest.mark.asyncio
async def test_cross_case_request_id_denied():
    # A call carrying a DIFFERENT request_id than the run's intent = cross-case leak.
    with pytest.raises(ScopeViolation) as exc:
        await enforce_scope(_intent(), "db.write_request", request_id="REQ-B")
    assert any(v == "identifier_mismatch:request_id" for v in exc.value.violations)


@pytest.mark.asyncio
async def test_cross_case_patient_ref_denied():
    with pytest.raises(ScopeViolation) as exc:
        await enforce_scope(_intent(), "db.write_request", patient_ref="PAT-OTHER")
    assert any(v == "identifier_mismatch:patient_ref" for v in exc.value.violations)


@pytest.mark.asyncio
async def test_disallowed_collection_denied():
    # An unlisted collection is NOT in the intent's allowed_collections. (The two
    # real collections — nccn_guidelines, case_precedents — ARE allowed; #2.)
    with pytest.raises(ScopeViolation) as exc:
        await enforce_scope(_intent(), "rag.query", collection_name="unlisted_collection")
    assert any("collection_not_allowed" in v for v in exc.value.violations)


@pytest.mark.asyncio
async def test_missing_collection_denied_no_default_bypass():
    with pytest.raises(ScopeViolation) as exc:
        await enforce_scope(_intent(), "rag.query")  # no collection_name
    assert any("collection_missing" in v for v in exc.value.violations)


# ── Legitimate in-scope calls pass ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allowed_rag_query_passes():
    await enforce_scope(_intent(), "rag.query", collection_name="nccn_guidelines")


@pytest.mark.asyncio
async def test_matching_identifier_passes():
    await enforce_scope(_intent(), "db.write_request", request_id="REQ-A", patient_ref="PAT-A")


@pytest.mark.asyncio
async def test_audit_append_passes():
    await enforce_scope(_intent(), "audit.append")


# ── Warn mode audits but does not block ───────────────────────────────────────


@pytest.mark.asyncio
async def test_warn_mode_does_not_raise():
    # Same out-of-scope call that raises in enforce mode returns quietly in warn.
    await enforce_scope(_intent(), "rag.query", collection_name="unlisted_collection", mode="warn")


# ── Audit event shape: scope.deny logs NAMES, never values ────────────────────


class _CapturingAudit:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def log(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_scope_deny_audits_names_not_values():
    audit = _CapturingAudit()
    with pytest.raises(ScopeViolation):
        await enforce_scope(
            _intent(), "rag.query", audit=audit, collection_name="unlisted_collection"
        )
    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert call["action"] == "scope.deny"
    assert call["success"] is False
    # request_id/correlation_id come from the run intent (allowed to appear);
    # the disallowed VALUE 'unlisted_collection' must NOT leak into the audit details.
    assert "unlisted_collection" not in str(call["details"]["violations"])
    assert "collection_not_allowed:collection_name" in call["details"]["violations"]


@pytest.mark.asyncio
async def test_scope_allow_audited():
    audit = _CapturingAudit()
    await enforce_scope(_intent(), "rag.query", audit=audit, collection_name="nccn_guidelines")
    assert audit.calls[0]["action"] == "scope.allow"
