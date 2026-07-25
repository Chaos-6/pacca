"""Minimum-necessary scope guard (P-4 / chg-8).

Expresses the HIPAA *minimum-necessary* standard in code: for a given run, may
this *action*, for this *purpose*, on this *resource* proceed? The answer is
read from the run's declared ``IntentRecord`` (P-3) — the guard grants nothing
on its own.

Because PACCA has no middleware layer yet (a roadmap component), this is a
call-site **wrapper**, not middleware: each guarded tool/DB/RAG call passes
through ``enforce_scope`` before it runs. Every evaluation appends a
``scope.allow`` / ``scope.deny`` audit event (arg **names**, never values — no
PHI in audit details). A denial raises ``ScopeViolation``; the route catches it
and fail-closes the run to human review (``EscalationReason.SCOPE_VIOLATION``).

Two modes, per PACCA's trust-the-eval doctrine: ``warn`` audits a would-be
denial but lets the call proceed (one eval round to surface false denials);
``enforce`` raises. All rules are fail-closed — an unknown action, a mismatched
identifier, or a missing collection all deny.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # avoid import cycle; only needed for type hints
    from pacca.db.repository import AuditRepository
    from pacca.models.intent import IntentRecord

# ── Case-identifier SSOT (mirrors the scan_for_phi pattern in
# sme_authoring/validators.py). These kwarg names, if present on a guarded call,
# MUST equal the corresponding IntentRecord field — a mismatch is a cross-case
# leak. `member_id` and `patient_ref` both bind to the run's subject_ref.
IDENTIFIER_TO_INTENT_FIELD: dict[str, str] = {
    "request_id": "request_id",
    "correlation_id": "correlation_id",
    "patient_ref": "subject_ref",
    "member_id": "subject_ref",
}

Mode = Literal["warn", "enforce"]


class ScopeViolation(Exception):
    """Raised (in ``enforce`` mode) when a call falls outside the run's intent scope.

    ``violations`` are ``rule:arg_name`` strings — names only, no PHI.
    """

    def __init__(self, action: str, violations: list[str]) -> None:
        self.action = action
        self.violations = violations
        super().__init__(f"scope violation on action '{action}': {', '.join(violations)}")


def _evaluate(intent: IntentRecord, action: str, call_args: dict[str, Any]) -> list[str]:
    """Return the list of ``rule:arg_name`` violations (empty = allowed)."""
    violations: list[str] = []

    # Rule 1 — action must be declared.
    if action not in intent.allowed_actions:
        violations.append(f"action_not_allowed:{action}")

    # Rule 2 — any case-identifier kwarg must match the run's intent.
    for arg_name, intent_field in IDENTIFIER_TO_INTENT_FIELD.items():
        if arg_name in call_args and str(call_args[arg_name]) != str(getattr(intent, intent_field)):
            violations.append(f"identifier_mismatch:{arg_name}")

    # Rule 3 — any RAG access (read or write) must target an allowed collection;
    # no default bypass. Applies to rag.query and rag.write_precedent (#3).
    if action.startswith("rag."):
        collection = call_args.get("collection_name")
        if collection is None:
            violations.append("collection_missing:collection_name")
        elif collection not in intent.allowed_collections:
            violations.append("collection_not_allowed:collection_name")

    return violations


async def enforce_scope(
    intent: IntentRecord,
    action: str,
    *,
    audit: AuditRepository | None = None,
    mode: Mode = "enforce",
    **call_args: Any,
) -> None:
    """Guard one call against the run's IntentRecord scope (Rule 4 audits every eval).

    Raises ``ScopeViolation`` on a denial in ``enforce`` mode; in ``warn`` mode it
    audits the would-be denial and returns. ``audit=None`` skips the audit write
    (used by the deterministic unit probes).
    """
    violations = _evaluate(intent, action, call_args)

    if violations:
        if audit is not None:
            await audit.log(
                action="scope.deny",
                actor="scope_guard",
                actor_type="system",
                request_id=intent.request_id,
                correlation_id=intent.correlation_id,
                success=False,
                details={"guarded_action": action, "mode": mode, "violations": violations},
            )
        if mode == "enforce":
            raise ScopeViolation(action=action, violations=violations)
        return  # warn mode: audited, not blocked

    if audit is not None:
        await audit.log(
            action="scope.allow",
            actor="scope_guard",
            actor_type="system",
            request_id=intent.request_id,
            correlation_id=intent.correlation_id,
            details={"guarded_action": action},
        )
