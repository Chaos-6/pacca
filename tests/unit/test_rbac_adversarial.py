"""
ADVERSARIAL RBAC probes — written by the Validator, not the Executor.

These deliberately target what the Executor's own suite does NOT cover.
Every test here maps to a numbered probe in RBAC_VALIDATION_CHARTER.md.

Design note on why this file rebuilds its own client fixture rather than
reusing `tests/unit/api/conftest.py::client`: that fixture seeds TEST_USERNAME
with role **admin**, which is the maximally permissive principal. A guard that
was dropped or set too low would still pass under it. Every probe here that
tests a guard uses a principal at or below the boundary being probed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, text

import pacca.api.models.user as user_module
import pacca.db.session as db_session_module
from pacca.api.auth import ALGORITHM, SECRET_KEY, get_password_hash
from pacca.api.database import Base as AuthBase
from pacca.api.main import app
from pacca.api.rbac import Role, get_current_user
from pacca.config.settings import get_settings
from pacca.db.models import Base as DomainBase

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# =============================================================================
# Fixtures — real DB seam, never a mocked dependency
# =============================================================================


@pytest.fixture
def adv_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Full app against an isolated on-disk SQLite DB. No users pre-seeded."""
    db_path = tmp_path / "rbac_adversarial.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    sync_engine = create_engine(f"sqlite:///{db_path}")
    try:
        AuthBase.metadata.create_all(sync_engine)
        DomainBase.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    yield TestClient(app)

    db_session_module._engine = None
    db_session_module._session_factory = None
    get_settings.cache_clear()


def _seed(username: str, role: str | None) -> None:
    engine = create_engine(os.environ["DATABASE_URL"].replace("+aiosqlite", ""))
    try:
        with engine.begin() as conn:
            conn.execute(
                user_module.User.__table__.insert().values(
                    username=username,
                    hashed_password=get_password_hash("pw"),
                    role=role,
                )
            )
    finally:
        engine.dispose()


def _sql(stmt: str, **params: Any) -> Any:
    engine = create_engine(os.environ["DATABASE_URL"].replace("+aiosqlite", ""))
    try:
        with engine.begin() as conn:
            result = conn.execute(text(stmt), params)
            return result.fetchall() if result.returns_rows else []
    finally:
        engine.dispose()


def _tok(username: str, **extra: Any) -> dict[str, str]:
    payload: dict[str, Any] = {
        "sub": username,
        "exp": datetime.now(UTC) + timedelta(minutes=30),
    }
    payload.update(extra)
    return {"Authorization": f"Bearer {jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)}"}


# =============================================================================
# Probe C8 — runtime route enumeration (the decisive coverage probe)
# =============================================================================

PUBLIC_BY_DESIGN = {
    "/api/v1/register/",
    "/api/v1/login/",
    "/health",
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
}

# Spec §2 endpoint -> minimum-role map. The STRICTEST role that must apply.
EXPECTED_MIN_ROLE = {
    "/api/v1/authorizations/": Role.CLINICIAN,
    "/api/v1/authorizations/review-queue": Role.MEDICAL_DIRECTOR,
    "/api/v1/authorizations/feedback": Role.MEDICAL_DIRECTOR,
}


def _resolved_min_roles(route: Any) -> list[Role]:
    """Every Role floor resolved anywhere in a route's flattened dependency tree."""
    found: list[Role] = []

    def walk(dep: Any) -> None:
        for sub in dep.dependencies:
            call = sub.call
            if call is not None and getattr(call, "__name__", "") == "_dependency":
                closure = call.__closure__ or ()
                for name, cell in zip(call.__code__.co_freevars, closure, strict=False):
                    if name == "minimum" and isinstance(cell.cell_contents, Role):
                        found.append(cell.cell_contents)
            walk(sub)

    walk(route.dependant)
    return found


def _resolves_get_current_user(route: Any) -> bool:
    seen = False

    def walk(dep: Any) -> None:
        nonlocal seen
        for sub in dep.dependencies:
            if sub.call is get_current_user:
                seen = True
            walk(sub)

    walk(route.dependant)
    return seen


def _iter_http_routes(routes: list[Any]) -> Iterator[Any]:
    """Every HTTP route the app actually serves, across FastAPI's two layouts.

    Each yielded object exposes `.path`, `.methods` and `.dependant` — the only
    things C8 reads — with router-level dependencies already merged in.

    Why this is not just `isinstance(r, APIRoute)` any more. Through FastAPI
    0.139, `include_router` COPIED each route into `app.routes`, so a flat
    isinstance scan saw everything. From 0.140 it appends a single private
    `_IncludedRouter` instead, and the real routes live behind its
    `effective_route_contexts()` (which applies the prefix, merges the
    router-level `dependencies=[...]`, and flattens nested includes).

    A flat scan therefore finds ZERO of PACCA's routes on FastAPI >= 0.140 —
    every one of them arrives via `include_router`. That is exactly what
    happened: CI resolves 0.140.13, C8 enumerated 0 routes, and the security
    guard silently passed vacuously. Only `assert checked >= 25` caught it.
    Observed 2026-07-29; see docs/AGENT_LESSONS.md P-016.

    Deliberately duck-typed rather than importing `_IncludedRouter`: it is
    private, absent before 0.140, and may be renamed. Both layouts are
    supported because the repo currently runs 0.135 locally and 0.140 in CI.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()


def _find_unguarded_routes(routes: list[Any], public_paths: set[str]) -> tuple[int, list[str]]:
    """
    The C8 detection algorithm, extracted so it can be run against a
    throwaway app too (see test_c8_negative_control_detector_can_actually_fail
    below) — identical logic to what this function replaced inline in
    test_c8_every_non_public_http_route_resolves_an_rbac_dependency, not a
    weakened or narrowed version of it.

    Returns (non-public routes checked, description of each unguarded one).
    """
    unguarded: list[str] = []
    checked = 0
    for r in _iter_http_routes(routes):
        if r.path in public_paths:
            continue
        checked += 1
        if not _resolved_min_roles(r) or not _resolves_get_current_user(r):
            unguarded.append(f"{sorted(r.methods)} {r.path}")
    return checked, unguarded


def test_c8_every_non_public_http_route_resolves_an_rbac_dependency() -> None:
    """CHARTER C8: no HTTP route may be reachable without an RBAC floor."""
    checked, unguarded = _find_unguarded_routes(app.routes, PUBLIC_BY_DESIGN)
    assert checked >= 25, f"enumeration collapsed — only {checked} routes checked"
    assert not unguarded, f"UNGUARDED non-public routes: {unguarded}"


def test_c8_negative_control_detector_can_actually_fail() -> None:
    """
    VALIDATOR-FLAGGED GAP, CLOSED: the route-enumeration detector above had
    never been proven capable of going red — "zero unguarded routes" rested
    on a detector nobody had seen fail. This builds a throwaway FastAPI app
    with one properly-guarded route and one DELIBERATELY unguarded route (no
    Depends at all — the exact shape of a dropped guard) and runs the SAME
    `_find_unguarded_routes` logic used against the real app, proving it
    correctly flags the unguarded route and clears the guarded one.
    """
    from fastapi import Depends, FastAPI

    from pacca.api.rbac import Role, require_min_role

    # Bound once, not called inline inside the Depends(...) default below —
    # matches the module-level singleton pattern used in the real routers
    # (ruff B008: a nested function call in a parameter default).
    _require_clinician = require_min_role(Role.CLINICIAN)

    throwaway = FastAPI()

    @throwaway.get("/guarded")
    async def _guarded_endpoint(_user: Any = Depends(_require_clinician)) -> dict[str, str]:
        return {"ok": "true"}

    @throwaway.get("/lost-guard")
    async def _unguarded_endpoint() -> dict[str, str]:
        return {"ok": "true"}

    checked, unguarded = _find_unguarded_routes(throwaway.routes, public_paths=set())

    assert checked == 2, f"expected both throwaway routes to be checked, got {checked}"
    assert len(unguarded) == 1, f"expected exactly one flagged route, got: {unguarded}"
    assert "/lost-guard" in unguarded[0], f"detector flagged the wrong route: {unguarded}"
    assert not any(entry.split(" ", 1)[1] == "/guarded" for entry in unguarded), (
        f"detector false-flagged the properly-guarded route: {unguarded}"
    )


def test_c8_enumeration_descends_into_included_routers() -> None:
    """The enumeration must not stop at the top level of `app.routes`.

    FastAPI >= 0.140 stops copying included routes into `app.routes` and appends
    a single private `_IncludedRouter` instead, so a flat `isinstance(r,
    APIRoute)` scan finds none of PACCA's routes — every one arrives via
    `include_router`. CI resolves 0.140.13 and C8 duly enumerated 0 routes while
    reporting no unguarded ones: a security check passing vacuously.

    This pins the descent with a stub rather than a real app, so it fails on the
    flat implementation no matter which FastAPI is installed. The repo currently
    runs 0.135 locally and 0.140 in CI; a test that needed 0.140 to go red would
    be green on the very machine the fix is written on.
    """

    class _StubContext:
        path = "/api/v1/authorizations/nested"
        methods = frozenset({"GET"})
        dependant = None

    class _StubIncludedRouter:
        """Duck-types the one method `_iter_http_routes` relies on."""

        def effective_route_contexts(self) -> Iterator[Any]:
            return iter([_StubContext()])

    found = list(_iter_http_routes([_StubIncludedRouter()]))

    assert [r.path for r in found] == ["/api/v1/authorizations/nested"], (
        "routes reachable only through an included router were skipped — this is "
        "the shape that silently emptied the C8 guard on FastAPI >= 0.140"
    )


def test_c8_route_min_roles_match_design_spec_section_2() -> None:
    """CHARTER C8: the floor must be the CORRECT one, not merely present."""
    wrong: list[str] = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or r.path in PUBLIC_BY_DESIGN:
            continue
        floors = _resolved_min_roles(r)
        effective = max(floors, key=lambda x: list(Role).index(x)) if floors else None

        if r.path.startswith("/api/v1/admin/"):
            expected = Role.ADMIN
        elif r.path.startswith("/api/v1/sme-authoring/"):
            expected = Role.MEDICAL_DIRECTOR
        else:
            expected = EXPECTED_MIN_ROLE.get(r.path)
        if expected is None:
            wrong.append(f"{r.path}: not in spec §2 map — unclassified route")
            continue
        if effective != expected:
            wrong.append(f"{r.path}: expected {expected}, resolved {effective}")
    assert not wrong, f"routes disagreeing with design spec §2: {wrong}"


def test_c8_websocket_route_has_no_dependency_and_must_guard_in_handler() -> None:
    """The WS route carries NO FastAPI dependency — its guard is in-handler only.

    Documented here so the fact is asserted, not assumed: if the in-handler
    check in draft_stream.py is ever removed, nothing at the routing layer
    would catch it. See test_b_websocket_* below for the behavioural probe.
    """
    ws = [r for r in app.routes if isinstance(r, APIWebSocketRoute)]
    assert len(ws) == 1
    assert ws[0].path == "/api/v1/sme-authoring/sessions/{session_id}/draft-stream"
    assert _resolved_min_roles(ws[0]) == []


# =============================================================================
# Probe C10 — HTTP method coverage on shared paths
# =============================================================================


def test_c10_all_methods_on_a_shared_path_are_guarded_identically(
    adv_client: TestClient,
) -> None:
    """CHARTER C10: GET and PATCH on /admin/config must both 403 a clinician."""
    _seed("clin", "clinician")
    h = _tok("clin")
    assert adv_client.get("/api/v1/admin/config", headers=h).status_code == 403
    assert adv_client.patch("/api/v1/admin/config", json={}, headers=h).status_code == 403
    assert adv_client.delete("/api/v1/admin/config/overrides", headers=h).status_code == 403


# =============================================================================
# Probe A1 — forged role claim in a genuinely-signed JWT
# =============================================================================


def test_a1_forged_role_claim_in_real_signed_jwt_grants_no_elevation(
    adv_client: TestClient,
) -> None:
    """CHARTER A1: a REAL SECRET_KEY-signed token carrying role=admin must 403."""
    _seed("forger", "clinician")
    r = adv_client.get("/api/v1/admin/config", headers=_tok("forger", role="admin"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Insufficient role for this operation"


def test_a1_forged_claim_also_fails_on_medical_director_surface(
    adv_client: TestClient,
) -> None:
    _seed("forger2", "clinician")
    r = adv_client.get(
        "/api/v1/sme-authoring/status", headers=_tok("forger2", role="medical_director")
    )
    assert r.status_code == 403, r.text


# =============================================================================
# Probe A2 — role injected via the registration body, every shape
# =============================================================================


@pytest.mark.parametrize(
    "body",
    [
        {"username": "a1", "password": "pw", "role": "admin"},
        {"username": "a2", "password": "pw", "Role": "admin"},
        {"username": "a3", "password": "pw", "role": ["admin"]},
        {"username": "a4", "password": "pw", "user": {"role": "admin"}},
        {"username": "a5", "password": "pw", "ROLE": "admin"},
        {"username": "a6", "password": "pw", "role ": "admin"},
    ],
)
def test_a2_registration_body_cannot_carry_a_role(
    adv_client: TestClient, body: dict[str, Any]
) -> None:
    """CHARTER A2: none of these shapes may produce a non-clinician account."""
    r = adv_client.post("/api/v1/register/", json=body)
    assert r.status_code == 422, f"{body} -> {r.status_code} {r.text}"
    rows = _sql("SELECT username FROM users WHERE username = :u", u=body["username"])
    assert rows == [], f"account was created despite 422: {body}"


def test_a2_clean_registration_produces_a_clinician(adv_client: TestClient) -> None:
    r = adv_client.post("/api/v1/register/", json={"username": "clean", "password": "pw"})
    assert r.status_code == 200, r.text
    assert _sql("SELECT role FROM users WHERE username='clean'")[0][0] == "clinician"


# =============================================================================
# Probes A3 / A4 — promotion endpoint escalation surface
# =============================================================================


def test_a3_clinician_cannot_self_promote(adv_client: TestClient) -> None:
    """CHARTER A3: self-service escalation must 403 and change nothing."""
    _seed("selfpromo", "clinician")
    r = adv_client.patch(
        "/api/v1/admin/users/selfpromo/role",
        json={"role": "admin"},
        headers=_tok("selfpromo"),
    )
    assert r.status_code == 403, r.text
    assert _sql("SELECT role FROM users WHERE username='selfpromo'")[0][0] == "clinician"


def test_a3_medical_director_cannot_self_promote(adv_client: TestClient) -> None:
    _seed("md", "medical_director")
    r = adv_client.patch("/api/v1/admin/users/md/role", json={"role": "admin"}, headers=_tok("md"))
    assert r.status_code == 403, r.text
    assert _sql("SELECT role FROM users WHERE username='md'")[0][0] == "medical_director"


@pytest.mark.parametrize(
    "extra",
    [
        {"username": "hijacked"},
        {"hashed_password": "pwned"},
        {"id": 9999},
        {"is_admin": True},
    ],
)
def test_a4_mass_assignment_extra_fields_are_never_applied(
    adv_client: TestClient, extra: dict[str, Any]
) -> None:
    """CHARTER A4: extra body keys must be rejected or ignored — never applied."""
    _seed("boss", "admin")
    _seed("victim", "clinician")
    before = _sql("SELECT username, hashed_password, id FROM users WHERE username='victim'")[0]

    body: dict[str, Any] = {"role": "medical_director"}
    body.update(extra)
    r = adv_client.patch("/api/v1/admin/users/victim/role", json=body, headers=_tok("boss"))
    assert r.status_code in (200, 422), r.text

    after = _sql("SELECT username, hashed_password, id FROM users WHERE username='victim'")[0]
    assert after[0] == before[0], f"username mutated by {extra}"
    assert after[1] == before[1], f"hashed_password mutated by {extra}"
    assert after[2] == before[2], f"id mutated by {extra}"
    assert _sql("SELECT COUNT(*) FROM users WHERE username='hijacked'")[0][0] == 0


# =============================================================================
# Probe B5 — fail-closed on every corrupt stored role
# =============================================================================


@pytest.mark.parametrize(
    "stored",
    ["superuser", "ADMIN", "Admin", "admin ", " admin", "admin\n", "", "clinician,admin"],
)
def test_b5_corrupt_stored_role_fails_closed_on_every_guarded_tier(
    adv_client: TestClient, stored: str | None
) -> None:
    """CHARTER B5: an unparseable/NULL/whitespace role must 403 everywhere.

    Extends the Executor's matrix with trailing/leading whitespace, an
    embedded newline, and a comma-joined value — none of which appear in
    tests/unit/test_rbac.py.
    """
    _seed("corrupt", "clinician")
    _sql("UPDATE users SET role = :r WHERE username='corrupt'", r=stored)
    assert _sql("SELECT role FROM users WHERE username='corrupt'")[0][0] == stored

    h = _tok("corrupt")
    for path in (
        "/api/v1/admin/config",
        "/api/v1/sme-authoring/status",
        "/api/v1/authorizations/review-queue",
    ):
        r = adv_client.get(path, headers=h)
        assert r.status_code == 403, f"{stored!r} on {path} -> {r.status_code} {r.text}"


def test_b5_null_role_is_unstorable_at_the_schema_layer(adv_client: TestClient) -> None:
    """CHARTER B5 (NULL case, layer 1): migration 006's NOT NULL blocks it outright."""
    _seed("nulluser", "clinician")
    import sqlalchemy.exc

    with pytest.raises(sqlalchemy.exc.IntegrityError, match="NOT NULL constraint failed"):
        _sql("UPDATE users SET role = NULL WHERE username='nulluser'")


def test_b5_null_role_still_403s_if_the_not_null_constraint_is_bypassed(
    adv_client: TestClient,
) -> None:
    """CHARTER B5 (NULL case, layer 2): app-layer guard must deny NULL on its own.

    Rebuilds `users` WITHOUT the NOT NULL constraint (simulating a DB restored
    from a pre-006 dump, or a vendor tool that dropped the constraint) so the
    application-layer fail-closed behaviour is proven independently of the
    schema constraint. Defense in depth requires BOTH layers to hold.
    """
    _sql("DROP TABLE users")
    _sql(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, "
        "hashed_password VARCHAR, role VARCHAR(30))"
    )
    _sql("INSERT INTO users (username, hashed_password, role) VALUES ('nullrole', 'x', NULL)")
    assert _sql("SELECT role FROM users WHERE username='nullrole'")[0][0] is None

    h = _tok("nullrole")
    for path in ("/api/v1/admin/config", "/api/v1/sme-authoring/status"):
        r = adv_client.get(path, headers=h)
        assert r.status_code == 403, f"NULL role on {path} -> {r.status_code} {r.text}"


def test_b5_corrupt_role_also_denied_on_the_lowest_tier(adv_client: TestClient) -> None:
    """Even the clinician floor must deny an unparseable role (no silent default)."""
    _seed("corrupt2", "clinician")
    _sql("UPDATE users SET role='superuser' WHERE username='corrupt2'")
    r = adv_client.post(
        "/api/v1/authorizations/",
        json={"request_id": "x"},
        headers=_tok("corrupt2"),
    )
    assert r.status_code == 403, r.text


# =============================================================================
# Probe B6 — deleted user with a live token
# =============================================================================


def test_b6_deleted_user_with_live_token_gets_401_not_403(adv_client: TestClient) -> None:
    """CHARTER B6: token outliving the account -> 401 (no account to authorize)."""
    _seed("ghost", "admin")
    h = _tok("ghost")
    assert adv_client.get("/api/v1/admin/config", headers=h).status_code == 200
    _sql("DELETE FROM users WHERE username='ghost'")
    r = adv_client.get("/api/v1/admin/config", headers=h)
    assert r.status_code == 401, r.text


# =============================================================================
# Probe B7 — immediate demotion (the design's whole justification)
# =============================================================================


def test_b7_demotion_via_sql_takes_effect_on_the_very_next_request(
    adv_client: TestClient,
) -> None:
    """CHARTER B7: the SAME token must 403 immediately after an out-of-band demotion.

    This is the property the DB-lookup design was chosen for. If it fails,
    role is being read from the token or a cache and the design decision
    was violated.
    """
    _seed("demoted", "admin")
    h = _tok("demoted")
    assert adv_client.get("/api/v1/admin/config", headers=h).status_code == 200

    _sql("UPDATE users SET role='clinician' WHERE username='demoted'")

    r = adv_client.get("/api/v1/admin/config", headers=h)
    assert r.status_code == 403, f"demotion did not take effect: {r.status_code} {r.text}"


def test_b7_promotion_via_sql_also_takes_effect_immediately(
    adv_client: TestClient,
) -> None:
    """The converse — proves the lookup is genuinely per-request, not cached once."""
    _seed("rising", "clinician")
    h = _tok("rising")
    assert adv_client.get("/api/v1/admin/config", headers=h).status_code == 403
    _sql("UPDATE users SET role='admin' WHERE username='rising'")
    assert adv_client.get("/api/v1/admin/config", headers=h).status_code == 200


# =============================================================================
# Probe C11-ish — endpoint matrix at the boundary, with a REAL clinician
# =============================================================================


def test_boundary_clinician_allowed_on_clinician_floor_denied_above(
    adv_client: TestClient,
) -> None:
    _seed("c", "clinician")
    h = _tok("c")
    # allowed at its own floor (422 = past the guard, body validation failed)
    assert adv_client.post("/api/v1/authorizations/", json={}, headers=h).status_code == 422
    # denied everywhere above
    assert adv_client.get("/api/v1/authorizations/review-queue", headers=h).status_code == 403
    assert adv_client.post("/api/v1/authorizations/feedback", json={}, headers=h).status_code == 403
    assert adv_client.get("/api/v1/sme-authoring/status", headers=h).status_code == 403
    assert adv_client.get("/api/v1/admin/metrics", headers=h).status_code == 403


def test_boundary_medical_director_allowed_at_its_floor_denied_on_admin(
    adv_client: TestClient,
) -> None:
    _seed("md2", "medical_director")
    h = _tok("md2")
    assert adv_client.get("/api/v1/authorizations/review-queue", headers=h).status_code == 200
    assert adv_client.get("/api/v1/sme-authoring/sessions", headers=h).status_code == 200
    assert adv_client.get("/api/v1/admin/metrics", headers=h).status_code == 403
    assert adv_client.get("/api/v1/admin/config", headers=h).status_code == 403


# =============================================================================
# Probe D11 — last-admin guard leaves the DB genuinely unchanged
# =============================================================================


def test_d11_last_admin_409_leaves_role_unchanged_in_the_database(
    adv_client: TestClient,
) -> None:
    """CHARTER D11: a 409 returned AFTER the write already happened is a real bug."""
    _seed("onlyadmin", "admin")
    r = adv_client.patch(
        "/api/v1/admin/users/onlyadmin/role",
        json={"role": "clinician"},
        headers=_tok("onlyadmin"),
    )
    assert r.status_code == 409, r.text
    assert _sql("SELECT role FROM users WHERE username='onlyadmin'")[0][0] == "admin"
    # and the guard did not spuriously write an audit record for a refused change
    assert _sql("SELECT COUNT(*) FROM audit_logs WHERE action='user.role_changed'")[0][0] == 0


def test_d11_second_admin_makes_demotion_possible_again(adv_client: TestClient) -> None:
    _seed("admin1", "admin")
    _seed("admin2", "admin")
    r = adv_client.patch(
        "/api/v1/admin/users/admin2/role",
        json={"role": "clinician"},
        headers=_tok("admin1"),
    )
    assert r.status_code == 200, r.text
    assert _sql("SELECT role FROM users WHERE username='admin2'")[0][0] == "clinician"


# =============================================================================
# Probe D12 — audit record correctness
# =============================================================================


def test_d12_successful_role_change_writes_exactly_one_audit_record(
    adv_client: TestClient,
) -> None:
    """CHARTER D12: one `user.role_changed` record, actor = the ACTING admin."""
    _seed("actor", "admin")
    _seed("target", "clinician")
    r = adv_client.patch(
        "/api/v1/admin/users/target/role",
        json={"role": "medical_director"},
        headers=_tok("actor"),
    )
    assert r.status_code == 200, r.text
    rows = _sql(
        "SELECT actor, actor_type, details FROM audit_logs WHERE action='user.role_changed'"
    )
    assert len(rows) == 1, f"expected exactly 1 audit record, got {len(rows)}: {rows}"
    assert rows[0][0] == "actor", f"actor should be the acting admin, got {rows[0][0]!r}"
    assert rows[0][1] == "user"


def test_d12_failed_role_change_writes_no_audit_record(adv_client: TestClient) -> None:
    """404 on an unknown target must not leave an audit record behind."""
    _seed("actor2", "admin")
    r = adv_client.patch(
        "/api/v1/admin/users/nobody/role",
        json={"role": "admin"},
        headers=_tok("actor2"),
    )
    assert r.status_code == 404, r.text
    assert _sql("SELECT COUNT(*) FROM audit_logs WHERE action='user.role_changed'")[0][0] == 0


def test_finding_cli_create_admin_no_longer_resets_an_existing_users_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VALIDATOR FINDING, FIXED (was: undocumented deviation from design spec §3.3).

    §3.3 specifies only "Creates the user if absent; promotes to `admin` if
    present." It did not authorise a password rotation, but
    `_create_or_promote_admin` used to call
    `_assign_str(existing, "hashed_password", hashed)` unconditionally, so
    promoting an existing user ALSO overwrote their credential with the
    value the operator typed — the target's own password stopped working
    and the operator learned their new one.

    This test originally PINNED that behaviour (asserted the bug). Per its
    own comment ("if this now passes, the finding was fixed and this test
    should be inverted"), the assertions are now inverted: promotion must
    leave Alice's own password working and must NOT apply the password the
    operator typed for someone else's account.
    """
    from click.testing import CliRunner
    from sqlalchemy import create_engine as _ce

    from pacca.api.auth import verify_password
    from pacca.cli import pacca_cli

    db_path = tmp_path / "cli_pw.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    engine = _ce(f"sqlite:///{db_path}")
    AuthBase.metadata.create_all(engine)
    # DomainBase too: create-admin now writes a user.role_changed audit
    # record (FIX 3), which needs the audit_logs table to exist.
    DomainBase.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            user_module.User.__table__.insert().values(
                username="alice",
                hashed_password=get_password_hash("alice-original-pw"),
                role="clinician",
            )
        )
    engine.dispose()

    result = CliRunner().invoke(
        pacca_cli,
        ["create-admin", "--username", "alice", "--password", "operator-typed-this"],
    )
    assert result.exit_code == 0, result.output
    assert "Password NOT changed" in result.output, result.output

    engine = _ce(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        row = conn.execute(
            user_module.User.__table__.select().where(
                user_module.User.__table__.c.username == "alice"
            )
        ).first()
    engine.dispose()
    assert row is not None
    assert row.role == "admin"

    # The fix, asserted explicitly:
    assert verify_password("alice-original-pw", row.hashed_password) is True, (
        "alice's original password must still work — promotion must never "
        "touch an existing user's credential"
    )
    assert verify_password("operator-typed-this", row.hashed_password) is False, (
        "the password the operator typed must NOT have been applied to alice's account"
    )

    db_session_module._engine = None
    db_session_module._session_factory = None
    get_settings.cache_clear()


def test_finding_cli_admin_grant_now_writes_an_audit_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VALIDATOR FINDING, FIXED: the CLI privilege grant used to be unaudited.

    `PATCH /admin/users/{u}/role` writes a `user.role_changed` audit record.
    `pacca create-admin` — which mints the highest privilege in the system —
    used to write none. Design spec §3.3 did not require one, so this was
    an observation about the control's completeness, not a spec violation —
    but for a system whose CLAUDE.md calls audit integrity non-negotiable,
    the one privilege-granting path with no trail was not defensible.

    Now asserts the fix: exactly one `user.role_changed` record, actor
    identifying the out-of-band CLI grant (prefixed `cli:`, never a bare
    username — distinguishable from an in-app admin actor at a glance), and
    details recording the grant came via CLI bootstrap.
    """
    from click.testing import CliRunner
    from sqlalchemy import create_engine as _ce

    from pacca.cli import pacca_cli

    db_path = tmp_path / "cli_audit.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    engine = _ce(f"sqlite:///{db_path}")
    AuthBase.metadata.create_all(engine)
    DomainBase.metadata.create_all(engine)
    engine.dispose()

    result = CliRunner().invoke(
        pacca_cli, ["create-admin", "--username", "bootstrap", "--password", "pw-123456"]
    )
    assert result.exit_code == 0, result.output
    assert _sql("SELECT role FROM users WHERE username='bootstrap'")[0][0] == "admin"

    rows = _sql(
        "SELECT actor, actor_type, details FROM audit_logs WHERE action='user.role_changed'"
    )
    assert len(rows) == 1, (
        f"expected exactly one user.role_changed record for the CLI grant, got {rows}"
    )
    actor, actor_type, details_raw = rows[0]
    assert actor.startswith("cli:"), f"actor must identify the CLI grant, got {actor!r}"
    assert actor_type == "user"
    # Raw SQL via `_sql()` bypasses the JSON type decorator that applies at
    # the ORM layer, so `details` comes back as its stored JSON string.
    details = json.loads(details_raw) if isinstance(details_raw, str) else details_raw
    assert details["target"] == "bootstrap"
    assert details["to"] == "admin"
    assert details["via"] == "cli_bootstrap"

    db_session_module._engine = None
    db_session_module._session_factory = None
    get_settings.cache_clear()


def test_d12_invalid_role_value_is_422_and_unknown_user_is_404(
    adv_client: TestClient,
) -> None:
    _seed("actor3", "admin")
    _seed("t3", "clinician")
    h = _tok("actor3")
    assert (
        adv_client.patch(
            "/api/v1/admin/users/t3/role", json={"role": "superuser"}, headers=h
        ).status_code
        == 422
    )
    assert (
        adv_client.patch(
            "/api/v1/admin/users/t3/role", json={"role": "Admin"}, headers=h
        ).status_code
        == 422
    )
    assert _sql("SELECT role FROM users WHERE username='t3'")[0][0] == "clinician"
