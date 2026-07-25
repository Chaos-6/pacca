#!/usr/bin/env python3
"""
Generate PACCA's documentation SVGs.

Why a generator instead of hand-authored XML: these diagrams encode facts that
drift (iteration count, dataset size, escalation-branch names). Regenerating from
a script means a stale diagram is a one-command fix, and the numbers can be
sourced from the manifests rather than retyped.

Usage:
    python scripts/generate_assets.py            # writes docs/assets/*.svg

Design tokens below match the existing decision_trace.svg / architecture SVGs so
every figure reads as one system.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"

# ── Design tokens ────────────────────────────────────────────────────────────
BG = "#111318"
CARD = "#171b22"
EDGE = "#2a3242"
INK = "#e8edf5"
MUTED = "#9aa6b8"
DIM = "#6a7488"
RULE = "#222937"

MONO = "'JetBrains Mono','SFMono-Regular',Menlo,monospace"
SANS = "Arial, sans-serif"

TEAL = "#2BC0C8"
BLUE = "#7eb4f8"
ORANGE = "#F97316"
PURPLE = "#a78bfa"
GREEN = "#4ade80"
AMBER = "#fbbf24"
SLATE = "#5a6a86"

# chip palettes: (fill, stroke, text)
CHIP = {
    "ok": ("#143038", "#1f5d63", "#bfe9ec"),
    "green": ("#1b2b27", "#2c7350", "#bfe9cf"),
    "blue": ("#16243f", "#34507e", "#bcd3f3"),
    "amber": ("#2a1c10", "#7a4a1a", "#f6c98a"),
    "red": ("#2a1414", "#7a2a2a", "#f0a8a8"),
    "purple": ("#1f1b33", "#4c3f7a", "#cfc2f5"),
    "slate": ("#1b2030", "#46506a", "#aeb9cd"),
}

SURFACE = {
    "instrumentation": ("#253247", "#3d5378", "#a8c0e8"),
    "system_prompt": ("#2a2340", "#4c3f7a", "#cfc2f5"),
    "evaluation_harness": ("#14313a", "#1f5d63", "#a8dfe6"),
    "escalation_branch": ("#33240f", "#7a4a1a", "#e8b57a"),
    "long_term_memory": ("#16302a", "#2c7350", "#a8e0c0"),
    "tool_implementation": ("#331c26", "#7a2a45", "#e8a8bd"),
    "audit_schema": ("#2b2438", "#6b4a7a", "#dcb8ea"),
    "middleware": ("#2e2416", "#7a6a1a", "#e8d78a"),
}

VERDICT = {
    "KEEP": ("#17402a", "#2c7350", "#86efac"),
    "IMPROVE": ("#3d3113", "#7a6a1a", "#f0d78a"),
    "BASELINE": ("#253247", "#3d5378", "#a8c0e8"),
    "PENDING": ("#1f2937", "#46506a", "#9aa6b8"),
}


# ── Primitives ───────────────────────────────────────────────────────────────
def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(
    x: float,
    y: float,
    s: str,
    *,
    size: float = 11,
    fill: str = MUTED,
    weight: int = 400,
    font: str = SANS,
    anchor: str = "start",
    italic: bool = False,
) -> str:
    st = ' font-style="italic"' if italic else ""
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{font}" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}"{st}>{esc(s)}</text>'
    )


def rect(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = CARD,
    stroke: str | None = EDGE,
    rx: float = 11,
    sw: float = 1,
    dash: str | None = None,
) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"{st}{d}/>'


def card(x: float, y: float, w: float, h: float, accent: str) -> str:
    """Panel with a left accent bar — the house card style."""
    return (
        rect(x, y, w, h)
        + f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2.5" fill="{accent}"/>'
    )


def chip(
    x: float,
    y: float,
    label: str,
    kind: str = "ok",
    *,
    size: float = 9.5,
    pad: float = 13,
    h: float = 21,
    mono: bool = True,
) -> tuple[str, float]:
    """Rounded pill. Returns (svg, width) so callers can flow them."""
    fill, stroke, fg = CHIP[kind]
    cw = 5.75 if mono else 5.4
    w = len(label) * cw + pad * 2
    font = MONO if mono else SANS
    svg = rect(x, y, round(w, 1), h, fill=fill, stroke=stroke, rx=h / 2)
    svg += text(x + pad, y + h / 2 + 3.5, label, size=size, fill=fg, font=font)
    return svg, w


def check_chip(
    x: float, y: float, label: str, kind: str = "ok", *, h: float = 21
) -> tuple[str, float]:
    """Pill with a leading checkmark."""
    fill, stroke, fg = CHIP[kind]
    w = len(label) * 5.75 + 40
    svg = rect(x, y, round(w, 1), h, fill=fill, stroke=stroke, rx=h / 2)
    svg += (
        f'<path d="M{x + 11},{y + h / 2} l3,3 l6,-7" stroke="#67e8c3" stroke-width="2" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    svg += text(x + 25, y + h / 2 + 3.5, label, size=9.5, fill=fg, font=MONO)
    return svg, w


def arrow_down(cx: float, y1: float, y2: float, color: str = "#9ca3af") -> str:
    return (
        f'<line x1="{cx}" y1="{y1}" x2="{cx}" y2="{y2 - 7}" stroke="{color}" stroke-width="2"/>'
        f'<path d="M{cx - 5},{y2 - 8} L{cx + 5},{y2 - 8} L{cx},{y2} Z" fill="{color}"/>'
    )


def arrow_right(
    x1: float, x2: float, cy: float, color: str = "#9ca3af", dash: str | None = None
) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1}" y1="{cy}" x2="{x2 - 7}" y2="{cy}" stroke="{color}" stroke-width="2"{d}/>'
        f'<path d="M{x2 - 8},{cy - 5} L{x2 - 8},{cy + 5} L{x2},{cy} Z" fill="{color}"/>'
    )


def frame(w: float, h: float, title: str, subtitle: str) -> str:
    s = f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
    # Subtle outer border: on GitHub's dark canvas (#0d1117) the figure background
    # (#111318) is nearly identical, so without this the figure has no edge.
    s += rect(0, 0, w, h, fill=BG, stroke="#2c3444", rx=14)
    s += text(36, 46, title, size=24, fill=INK, weight=700)
    s += text(37, 70, subtitle, size=12.5, fill=MUTED)
    return s


def write(name: str, body: str) -> None:
    out = ASSETS / name
    out.write_text(body + "</svg>\n", encoding="utf-8")
    print(f"  wrote {out.relative_to(ROOT)}")


# ── Figure 1 · Trust chain (the governance spine, P-3 → P-4 → P-5) ───────────
def trust_chain() -> None:
    W, H = 1000, 664
    s = frame(
        W,
        H,
        "PACCA Trust Chain",
        "Three gates stand between the model's output and an automated decision. Any gate trips → a human decides.",
    )
    s += f'<line x1="684" y1="104" x2="684" y2="{H - 58}" stroke="{RULE}" stroke-width="1"/>'
    s += text(700, 122, "HUMAN REVIEW", size=11, fill=AMBER, weight=700, font=MONO)

    gates = [
        (
            TEAL,
            "1 · DECLARE",
            "P-3 · IntentRecord",
            "What is this run allowed to touch?",
            [
                "Every run opens by declaring its own scope — allowed collections,",
                "allowed actions, an opaque subject reference, expected effects.",
            ],
            "intent.declared",
            "emitted as the FIRST audit event",
            None,
        ),
        (
            ORANGE,
            "2 · CONFINE",
            "P-4 · Minimum-necessary scope guard",
            "Did it stay inside what it declared?",
            [
                "enforce_scope() wraps every DB write and RAG access — query AND",
                "precedent write. Unknown action / cross-case id / non-allowed collection → denied.",
            ],
            "EscalationReason.SCOPE_VIOLATION",
            "HIPAA minimum-necessary, expressed in code",
            "scope_violation",
        ),
        (
            PURPLE,
            "3 · VERIFY",
            "P-5 · Evidence-grounding detector",
            "Did it cite evidence that doesn't exist?",
            [
                "The decision must name the evidence ids it relied on. Any id absent",
                "from the submission is caught deterministically — no second LLM.",
            ],
            "EscalationReason.UNGROUNDED_EVIDENCE",
            "the anti-hallucination gate, at runtime",
            "ungrounded_evidence",
        ),
    ]

    y = 116
    for accent, step, name, question, body, code, note, offramp in gates:
        h = 118
        s += card(40, y, 616, h, accent)
        s += text(60, y + 22, f"{step}  ·  {name}", size=11, fill=accent, weight=700, font=MONO)
        s += text(60, y + 44, question, size=14.5, fill=INK, weight=700)
        s += text(60, y + 63, body[0], size=10.5, fill=MUTED)
        s += text(60, y + 78, body[1], size=10.5, fill=MUTED)
        c, cw = chip(60, y + 88, code, "slate")
        s += c
        s += text(60 + cw + 12, y + 103, note, size=9.5, fill=DIM, italic=True)

        mid = y + h / 2
        if offramp:
            s += arrow_right(658, 700, mid, color="#7a4a1a", dash="4 3")
            oc, _ = chip(704, mid - 10, offramp, "amber")
            s += oc
        else:
            s += text(704, mid + 4, "(record-only — no denial)", size=9.5, fill=DIM, italic=True)

        if y < 380:
            s += arrow_down(348, y + h, y + h + 18)
        y += h + 20

    s += text(700, 560, "every off-ramp lands here —", size=10, fill=DIM)
    s += text(700, 575, "fail-closed, never fail-open.", size=10, fill=DIM)
    s += text(700, 597, "A human deciding is the", size=10, fill=DIM, italic=True)
    s += text(700, 611, "designed outcome, not the", size=10, fill=DIM, italic=True)
    s += text(700, 625, "failure mode.", size=10, fill=DIM, italic=True)

    s += arrow_down(348, y - 2, y + 16)
    dy = y + 18
    s += card(40, dy, 616, 62, GREEN)
    s += text(60, dy + 24, "4 · DECIDE", size=11, fill=GREEN, weight=700, font=MONO)
    s += text(
        60,
        dy + 45,
        "All three gates clear — confidence routing decides the outcome",
        size=14,
        fill=INK,
        weight=700,
    )

    s += text(
        36,
        H - 26,
        "Gates 2 and 3 are deterministic — no model judges the model. In correct operation none of them "
        "fire; their value is what happens when something goes wrong.",
        size=10,
        fill=DIM,
    )
    write("trust_chain.svg", s)


# ── Figure 2 · Escalation tree (7 pre-flight + 3 confidence + 2 runtime) ──────
def escalation_tree() -> None:
    W, H = 1000, 606
    s = frame(
        W,
        H,
        "PACCA Escalation Tree",
        "Deterministic safety logic that overrides model confidence. 11 escalation reasons, each mapped to a branch.",
    )

    # Band A — pre-flight
    s += card(40, 104, 920, 150, TEAL)
    s += text(
        60,
        126,
        "A · PRE-FLIGHT  ·  ClinicalRiskDetector.evaluate()",
        size=11,
        fill=TEAL,
        weight=700,
        font=MONO,
    )
    s += text(
        60,
        147,
        "7 deterministic gates — no LLM, runs before any agent is called",
        size=14.5,
        fill=INK,
        weight=700,
    )
    gates = [
        "experimental_treatment",
        "rare_condition",
        "conflicting_guidelines",
        "prior_denial_same_service",
        "high_cost",
        "pediatric_complex",
        "adult_complex",
    ]
    x: float = 60
    yy: float = 160
    for g in gates:
        c, cw = check_chip(x, yy, g)
        s += c
        x += cw + 10
        if x > 720:
            x, yy = 60, yy + 28
    s += rect(60, 222, 420, 21, fill="#1d1812", stroke="#5a4326", rx=10.5, dash="3 3")
    s += text(
        72,
        236.5,
        "✕ any gate hit → escalation_pre_flight_triggered → IN_REVIEW",
        size=9.5,
        fill="#caa978",
        font=MONO,
    )

    s += arrow_down(500, 254, 274)

    # Band B — confidence routing
    s += card(40, 276, 920, 118, ORANGE)
    s += text(
        60,
        298,
        "B · TIER-1 CONFIDENCE ROUTING  ·  3 branches",
        size=11,
        fill=ORANGE,
        weight=700,
        font=MONO,
    )
    s += text(
        60,
        319,
        "The model proposes a confidence; the tree decides what that buys",
        size=14.5,
        fill=INK,
        weight=700,
    )
    bar_x, bar_w, bar_y = 60, 880, 332
    third = bar_w / 3
    s += rect(bar_x, bar_y, third, 26, fill="#5a2f2f", stroke=None, rx=4)
    s += rect(bar_x + third, bar_y, third, 26, fill="#6a5320", stroke=None, rx=0)
    s += rect(bar_x + 2 * third, bar_y, third, 26, fill="#27613f", stroke=None, rx=4)
    s += text(
        bar_x + third / 2,
        bar_y + 17,
        "< 0.90   →  IN_REVIEW",
        size=10,
        fill="#e8b9b9",
        weight=600,
        anchor="middle",
    )
    s += text(
        bar_x + third * 1.5,
        bar_y + 17,
        "0.90–0.95   →  MEDICAL DIRECTOR",
        size=10,
        fill="#f3dca2",
        weight=600,
        anchor="middle",
    )
    s += text(
        bar_x + third * 2.5,
        bar_y + 17,
        "≥ 0.95   →  AUTO-APPROVE",
        size=10,
        fill="#a7e8c0",
        weight=600,
        anchor="middle",
    )
    s += text(
        60,
        380,
        "confidence_below_threshold  ·  medical_director_required",
        size=9.5,
        fill=DIM,
        font=MONO,
    )

    s += arrow_down(500, 394, 414)

    # Band C — runtime short-circuits
    s += card(40, 416, 920, 118, PURPLE)
    s += text(
        60,
        438,
        "C · RUNTIME SAFETY SHORT-CIRCUITS  ·  governance rollout P-4 / P-5",
        size=11,
        fill=PURPLE,
        weight=700,
        font=MONO,
    )
    s += text(
        60,
        459,
        "Added 2026 — these fire before confidence routing and cannot be outvoted by it",
        size=14.5,
        fill=INK,
        weight=700,
    )
    c1, _ = chip(60, 474, "scope_violation — touched something outside the declared scope", "amber")
    s += c1
    c2, _ = chip(
        60, 502, "ungrounded_evidence — cited an evidence id absent from the submission", "purple"
    )
    s += c2

    s += text(
        36,
        H - 26,
        "Source of truth: EscalationReason in src/pacca/models/enums.py (11 members) and the branch "
        "labels in agents/orchestrator.py.",
        size=10,
        fill=DIM,
    )
    write("escalation_tree.svg", s)


# ── Figure 3 · Harness iteration ledger, regenerated through iter-10 ─────────
ITERS = [
    (
        0,
        "2026-04-15",
        "Baseline crystallization",
        "OpenTelemetry spans + trajectory logs + correlation-ID propagation. No behavioral change — the precondition for falsifiable verdicts.",
        ["instrumentation"],
        "BASELINE",
        "",
    ),
    (
        1,
        "2026-05-04",
        "Extract agent system prompts to file mount points",
        "Decision-Support and Medical-Director prompts become standalone versioned files, not hard-coded strings.",
        ["system_prompt"],
        "KEEP",
        "×1",
    ),
    (
        2,
        "2026-05-22",
        "Build the evaluation apparatus",
        "Per-case regression gate, baseline scoreboard, near-miss memory traps (GC-021/022), a doc-drift guard.",
        ["evaluation_harness", "instrumentation"],
        "KEEP",
        "×6",
    ),
    (
        3,
        "2026-05-24",
        "First clinical escalation branches + institutional memory",
        "Wire HIGH_COST + PEDIATRIC_COMPLEX triggers into the detector; land the first H2 memory entry (NSCLC pembrolizumab).",
        ["escalation_branch", "long_term_memory", "evaluation_harness"],
        "KEEP",
        "×3",
    ),
    (
        4,
        "2026-05-25",
        "Grow memory, cut dead weight",
        "Second H2 entry (first-line biologic DMARD for seropositive RA); delete 330 lines of dead decision_agent.py.",
        ["long_term_memory", "tool_implementation"],
        "KEEP",
        "×2",
    ),
    (
        5,
        "2026-05-25",
        "Heuristic → model: integer 1–5 complexity score",
        "Replace the iter-3 keyword heuristic with a complexity-score model; +3 pediatric cases; 3rd H2 entry (dupilumab).",
        ["escalation_branch", "evaluation_harness", "long_term_memory", "instrumentation"],
        "KEEP",
        "×3",
    ),
    (
        6,
        "2026-05-31",
        "Generalize the model to adults; first deny-class memory",
        "ADULT_COMPLEX reuses iter-5's score model byte-for-byte; first deny-class H2 entry; PROMPT_REGISTRY → v2.6.",
        ["escalation_branch", "long_term_memory", "evaluation_harness", "instrumentation"],
        "KEEP",
        "×4",
    ),
    (
        7,
        "2026-07-21",
        "Per-run IntentRecord as the first audit event",
        "A typed, record-only scope contract (allowed collections/actions, opaque subject_ref) emitted before anything else happens.",
        ["audit_schema"],
        "KEEP",
        "×1",
    ),
    (
        8,
        "2026-07-22",
        "Minimum-necessary scope guard, warn mode",
        "enforce_scope() wired at the RAG query and observed in warn mode before being trusted to deny.",
        ["middleware"],
        "KEEP",
        "×1",
    ),
    (
        9,
        "2026-07-22",
        "Scope guard warn → enforce, plus guarded persistence writes",
        "Promoted to fail-closed enforce mode at three call sites; a cross-case leak now routes to human review.",
        ["middleware"],
        "KEEP",
        "×1",
    ),
    (
        10,
        "2026-07-22",
        "Runtime evidence-grounding detector",
        "Any decision citing an evidence id absent from the submission is forced to human review; prompt → v2.7.",
        ["escalation_branch"],
        "KEEP",
        "×1",
    ),
    (
        11,
        "2026-07-23",
        "Server-side decision_id; legible integrity failures",
        "decision_id was model-generated into a unique column (B6) — now minted server-side, out of the tool schema; DB writes roll back so the real error surfaces.",
        ["tool_implementation"],
        "KEEP",
        "×2",
    ),
    (
        12,
        "2026-07-23",
        "Deferrable audit FK — fixes a Postgres-only FK violation",
        "audit_logs.request_id FK becomes DEFERRABLE INITIALLY DEFERRED (B3), so the pre-flight audit rows commit before the parent without reordering. Verified on real Postgres 16.",
        ["audit_schema"],
        "KEEP",
        "×1",
    ),
    (
        13,
        "2026-07-24",
        "Wire + govern the institutional-memory precedent collection",
        "Deterministic precedent id + upsert; the scope guard governs the two REAL RAG collections; the /feedback write is now intent-scoped, guarded, and audited-before-write.",
        ["tool_implementation", "middleware"],
        "KEEP",
        "×3",
    ),
]


def harness_timeline() -> None:
    pitch, ch = 120, 104
    top = 128
    H = top + pitch * len(ITERS) + 78
    W = 1100
    s = frame(
        W,
        H,
        "PACCA Harness — Iteration Ledger",
        "harness-iter-0 → harness-iter-13 · every behavioral change is a one-file diff with a falsifiable prediction;",
    )
    s += text(
        37,
        88,
        "the next iteration's evaluation returns a verdict — keep, improve, or rollback.",
        size=12.5,
        fill=MUTED,
    )

    rail = 176
    s += f'<line x1="{rail}" y1="{top}" x2="{rail}" y2="{top + pitch * (len(ITERS) - 1) + ch / 2}" stroke="{RULE}" stroke-width="2"/>'

    for i, (n, date, title, desc, surfaces, verdict, mult) in enumerate(ITERS):
        y = top + i * pitch
        cy = y + ch / 2
        dot = GREEN if verdict == "KEEP" else (AMBER if verdict == "IMPROVE" else BLUE)
        s += (
            f'<circle cx="{rail}" cy="{cy}" r="15" fill="#1b2030" stroke="{dot}" stroke-width="2"/>'
        )
        s += text(rail, cy + 4.5, str(n), size=12, fill=dot, weight=700, anchor="middle", font=MONO)
        s += text(rail - 26, cy - 22, "iter", size=9, fill=DIM, anchor="end", font=MONO)
        s += text(rail - 26, cy + 6, date, size=10, fill=MUTED, anchor="end", font=MONO)

        cx = 210
        cw = W - cx - 40
        s += card(cx, y, cw, ch, dot)
        s += text(cx + 20, y + 24, f"harness-iter-{n}", size=11.5, fill=TEAL, weight=700, font=MONO)
        s += text(cx + 20, y + 46, title, size=15, fill=INK, weight=700)
        s += text(cx + 20, y + 64, desc, size=10.5, fill=MUTED)

        x: float = cx + 20
        for sf in surfaces:
            fill, stroke, fg = SURFACE[sf]
            w = len(sf) * 5.75 + 24
            s += rect(x, y + 74, round(w, 1), 19, fill=fill, stroke=stroke, rx=9.5)
            s += text(x + 12, y + 87, sf, size=9.5, fill=fg, font=MONO)
            x += w + 8

        vf, vs, vfg = VERDICT[verdict]
        label = f"{verdict} {mult}".strip()
        vw = len(label) * 6.4 + 26
        s += rect(cx + cw - vw - 18, y + 14, round(vw, 1), 22, fill=vf, stroke=vs, rx=11)
        s += text(
            cx + cw - vw / 2 - 18, y + 29, label, size=10, fill=vfg, weight=700, anchor="middle"
        )

    fy = H - 44
    s += f'<line x1="36" y1="{fy - 20}" x2="{W - 36}" y2="{fy - 20}" stroke="{RULE}" stroke-width="1"/>'
    s += text(
        36,
        fy,
        "14 iterations · 31 changes · 0 rollbacks   |   H2 memory: 4 entries · escalation tree: "
        "7 pre-flight gates + 2 runtime short-circuits · golden gate: 20 · dataset: 105 cases",
        size=10.5,
        fill=BLUE,
    )
    write("harness_timeline.svg", s)


# ── Figure 4 · SME authoring, static (replaces the SMIL version) ─────────────
def sme_authoring_static() -> None:
    W, H = 1000, 560
    s = frame(
        W,
        H,
        "PACCA · SME Case Authoring",
        "A clinician describes a case in plain English. Six deterministic validators decide whether it may be written.",
    )

    steps = [
        ("1", "Scenario", "clinician describes the case in plain English"),
        ("2", "Drafting", "Claude drafts rationale, citations, judge criteria"),
        ("3", "Draft Review", "SME edits fields · adds guideline citation"),
        ("4", "Validators", "6 deterministic checks — all must pass"),
        ("5", "Attestation", "human sign-off recorded against the case"),
        ("6", "Commit", "AST emit → integrity test → rollback on fail"),
    ]
    y = 110
    for num, name, note in steps:
        s += rect(40, y, 380, 56, rx=10)
        s += f'<circle cx="70" cy="{y + 28}" r="14" fill="#1b2030" stroke="{TEAL}" stroke-width="1.5"/>'
        s += text(70, y + 32.5, num, size=11, fill=TEAL, weight=700, anchor="middle", font=MONO)
        s += text(96, y + 24, name, size=13.5, fill=INK, weight=700)
        s += text(96, y + 42, note, size=10, fill=MUTED)
        y += 64

    s += rect(444, 110, 516, 358, fill="#0d1014", rx=10)
    s += text(944, 130, "validators", size=10, fill=DIM, anchor="end", italic=True)
    s += text(464, 134, "$ pacca sme-author new", size=11, fill=GREEN, font=MONO)

    vals = [
        "no_phi",
        "guideline_citation",
        "schema_completeness",
        "outcome ↔ branch",
        "reasoning_specificity",
        "judge_criteria",
    ]
    vy = 154
    for i in range(0, len(vals), 2):
        c1, _ = check_chip(464, vy, vals[i])
        s += c1
        if i + 1 < len(vals):
            c2, _ = check_chip(700, vy, vals[i + 1])
            s += c2
        vy += 30

    s += rect(464, vy + 8, 476, 21, fill="#1b2b27", stroke="#2c7350", rx=10.5)
    s += text(476, vy + 22.5, "6 / 6 pass — write unblocked", size=9.5, fill="#bfe9cf", font=MONO)
    s += rect(464, vy + 38, 476, 21, fill="#1d1812", stroke="#5a4326", rx=10.5, dash="3 3")
    s += text(
        476,
        vy + 52.5,
        "✕ any FAIL → file mutation rolled back, SME revises",
        size=9.5,
        fill="#caa978",
        font=MONO,
    )

    s += text(
        464,
        vy + 92,
        "Attestation and provenance are written with the case —",
        size=10.5,
        fill=MUTED,
    )
    s += text(
        464,
        vy + 108,
        "docs/CASE_PROVENANCE.md gains one row naming the reviewer.",
        size=10.5,
        fill=MUTED,
    )

    s += text(
        36,
        H - 26,
        "Same library behind both surfaces: the CLI and the web wizard call src/pacca/agents/sme_authoring/. "
        "Synthetic data only — no PHI.",
        size=10,
        fill=DIM,
    )
    write("sme_authoring_static.svg", s)


# ── Figure 5 · Architecture v2.6 (incremental text refresh of the v2.5 asset) ─
# NOTE: docs/assets/architecture_v2.6.svg is a STATIC, hand-maintained asset
# (like docs/images/decision-hero.png) — it is not built here. Earlier versions
# were derived by find/replace on a hand-authored architecture_v2.5.svg template;
# that template has been removed and v2.6 is now the committed source of truth.
# Edit the SVG directly when the architecture changes; keep its "iter-0 … iter-N ·
# M changes" caption in sync with verify_facts() below.


def _iter_number(path: Path) -> int:
    """Sort key: the integer N in an ``iter-N.json`` manifest filename."""
    m = re.search(r"iter-(\d+)", path.name)
    if m is None:
        raise ValueError(f"unexpected manifest filename: {path.name}")
    return int(m.group(1))


def verify_facts() -> None:
    """Cross-check the numbers baked into the figures against the manifests."""
    files = sorted(
        (ROOT / "harness" / "manifests").glob("iter-*.json"),
        key=_iter_number,
    )
    iters = len(files)
    changes = sum(len(json.loads(f.read_text()).get("changes") or []) for f in files)
    rollbacks = sum(
        1
        for f in files
        for v in (json.loads(f.read_text()).get("verdicts") or [])
        if v.get("outcome") == "rollback"
    )
    assert iters == len(ITERS), f"manifest count {iters} != ITERS table {len(ITERS)}"
    print(f"  fact-check: {iters} iterations, {changes} changes, {rollbacks} rollbacks")
    if (iters, changes, rollbacks) != (14, 31, 0):
        print("  !! figures hardcode '14 iterations · 31 changes · 0 rollbacks' — update them")


if __name__ == "__main__":
    print("generating docs/assets/…")
    verify_facts()
    trust_chain()
    escalation_tree()
    harness_timeline()
    sme_authoring_static()
    print("done.")
