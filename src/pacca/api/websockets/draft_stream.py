"""
WebSocket handler for live LLM drafting progress (PR-WUI-1).

The browser connects to `/api/v1/sme-authoring/sessions/{session_id}/draft-stream`,
authenticates via the first message (a JWT bearer token), and then
receives a stream of typed events as the LLM drafts the case.

DESIGN
======

- Authentication: first inbound message after connection MUST be
  `{"type": "auth", "token": "<JWT>"}`. If absent or invalid, the
  connection closes with code 4401 (custom: "unauthorized auth").
- Heartbeat: server emits `{"type": "heartbeat", ...}` every 15s to
  keep proxies happy.
- v1.1 simplification: the LLM call is synchronous (Claude SDK's
  streaming + tool-use combo is non-trivial). The server calls the
  agent, then emits a single `done` event with the full draft. The
  frontend achieves the "typewriter" UX by animating the received
  text client-side. Real token-by-token streaming is queued for v1.2.

- On any error, emit a `{"type": "error", ...}` event with
  `recoverable: false` and close. The frontend should display the
  error to the SME + offer a "retry" affordance.

CLOSE CODES
===========

- 1000 — Normal closure (draft completed successfully)
- 1011 — Internal error (LLM failure, session lookup error, etc.)
- 4401 — Custom: unauthorized (bad/missing token, or the token names an
  account that no longer exists — see rbac.get_current_user's 401 rationale)
- 4403 — Custom: authenticated, but below the medical_director floor RBAC
  requires for case authoring (RBAC — see `pacca.api.rbac`). Kept
  distinguishable from 4401 on purpose: a client that sees 4403 should not
  retry with the same account, whereas 4401 means "get a fresh token" (or,
  for a deleted account, "you need a different account entirely").
- 4404 — Custom: session not found
- 4400 — Custom: invalid request shape
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy import select

from pacca.agents.sme_authoring.agent import SMECaseAuthoringAgent
from pacca.agents.sme_authoring.file_router import route_case
from pacca.agents.sme_authoring.id_allocator import next_id, release_reservation
from pacca.agents.sme_authoring.models import CaseDraftRequest
from pacca.agents.sme_authoring.session import (
    SessionStorageError,
    load_session,
    save_session,
)
from pacca.api.auth import ALGORITHM, SECRET_KEY
from pacca.api.models.user import User
from pacca.api.rbac import Role, meets_minimum, parse_role
from pacca.api.routes.sme_authoring import _routing_placeholder
from pacca.db.session import get_session_context

HEARTBEAT_INTERVAL_SECONDS = 15.0


async def handle_draft_stream(websocket: WebSocket, session_id: str) -> None:  # noqa: PLR0911
    """
    WebSocket handler for streaming LLM draft progress.

    Args:
        websocket: The FastAPI WebSocket connection (already accepted by
            the route handler that mounts this).
        session_id: The session UUID from the URL path.
    """
    await websocket.accept()

    # Step 1: Authenticate
    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
    except (TimeoutError, WebSocketDisconnect):
        await _close_with_error(
            websocket, 4401, "Authentication timeout — send {type:'auth', token:'...'} within 10s"
        )
        return
    except json.JSONDecodeError:
        await _close_with_error(websocket, 4400, "First message must be JSON")
        return

    username = _authenticated_username(auth_msg)
    if username is None:
        await _close_with_error(websocket, 4401, "Invalid or missing JWT token")
        return

    # Step 1b: Authorize. The JWT only proves who the caller claims to be;
    # the database is the source of truth for role (see pacca.api.rbac module
    # docstring for why — a role claim in the token itself is never trusted).
    # A token naming an account that no longer exists is indistinguishable
    # from a bad token here, so it also closes 4401, not 4403.
    async with get_session_context() as db_session:
        result = await db_session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

    if user is None:
        await _close_with_error(websocket, 4401, "Invalid or missing JWT token")
        return

    # cast(), not a `# type: ignore` comment: whether `user.role` is seen as
    # `Column[str]` (full dependency set, no sqlalchemy mypy plugin) or as
    # `Any` (an isolated environment where sqlalchemy itself is an
    # unresolved import — e.g. the pre-commit mypy hook), an ignore comment
    # would be "needed" in one of those and "unused" in the other. cast is a
    # no-op in both.
    role = parse_role(cast("str | None", user.role))
    if not meets_minimum(role, Role.MEDICAL_DIRECTOR):
        await _close_with_error(websocket, 4403, "Insufficient role for this operation")
        return

    # Step 2: Load session
    try:
        state = load_session(session_id)
    except FileNotFoundError:
        await _close_with_error(websocket, 4404, f"Session {session_id} not found")
        return
    except SessionStorageError as exc:
        await _close_with_error(websocket, 1011, f"Session corrupt: {exc}")
        return

    if not state.scenario:
        await _close_with_error(
            websocket, 4400, "Session has no scenario; create with scenario first"
        )
        return

    # Step 3: Allocate ID + route
    allocated_id = next_id()
    placeholder = _routing_placeholder(allocated_id, state)
    routing = route_case(placeholder, state.scenario)

    # Step 4: Spawn heartbeat task + run agent
    heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket))

    try:
        agent = SMECaseAuthoringAgent()
        request = CaseDraftRequest(
            scenario=state.scenario,
            allocated_case_id=allocated_id,
            recommended_file=routing.target_file,
        )
        draft = await agent.run(request)
    except Exception as exc:
        release_reservation(allocated_id)
        heartbeat_task.cancel()
        await _send_event(
            websocket,
            {
                "type": "error",
                "message": f"LLM drafting failed: {exc}",
                "recoverable": True,
            },
        )
        await websocket.close(code=1011)
        return

    # Step 5: Persist + emit `done`
    state = state.model_copy(
        update={
            "draft": draft,
            "last_step": "drafted",
            "last_updated_at": datetime.now(UTC),
        }
    )
    save_session(state)

    heartbeat_task.cancel()
    await _send_event(
        websocket,
        {
            "type": "done",
            "draft": draft.model_dump(mode="json"),
            "allocated_case_id": allocated_id,
            "recommended_file": routing.target_file,
        },
    )
    await websocket.close(code=1000)


# =============================================================================
# Helpers (private)
# =============================================================================


def _authenticated_username(msg: Any) -> str | None:
    """Validate the first inbound message's JWT token; return its `sub`, or None."""
    if not isinstance(msg, dict):
        return None
    if msg.get("type") != "auth":
        return None
    token = msg.get("token")
    if not isinstance(token, str) or not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    username = payload.get("sub")
    return username if isinstance(username, str) and username else None


async def _send_event(websocket: WebSocket, event: dict[str, Any]) -> None:
    """Send a typed event to the client."""
    await websocket.send_json(event)


async def _close_with_error(websocket: WebSocket, code: int, message: str) -> None:
    """Send an error event then close with the given WebSocket close code."""
    with contextlib.suppress(Exception):
        await _send_event(
            websocket,
            {"type": "error", "message": message, "recoverable": False},
        )
    await websocket.close(code=code)


async def _heartbeat_loop(websocket: WebSocket) -> None:
    """Emit a heartbeat event every HEARTBEAT_INTERVAL_SECONDS."""
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await _send_event(
                websocket,
                {
                    "type": "heartbeat",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
    except (asyncio.CancelledError, WebSocketDisconnect):
        # Normal: either the parent task finished or the client disconnected
        raise
    except Exception:
        return
