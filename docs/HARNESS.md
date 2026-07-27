# PACCA Harness Architecture

> **Audience:** engineers contributing to PACCA, reviewers auditing iteration discipline, and readers studying the codebase as a reference implementation of observability-driven agent harness engineering applied to a healthcare domain.

> **Status (reconciled 2026-07-21): this document describes the _target_ harness
> architecture.** Several component types and commands below are the intended design,
> not the current build. As-built status and the authoritative limitations list live in
> `CLAUDE.md` (§Limitations, §Target architecture). Items called out as **(roadmap)**
> here are not yet implemented; do not assume a mount point, loader, or command exists
> because it appears in a table below — check `CLAUDE.md` or the tree first.

This document defines what the **harness** is in PACCA, the seven component types it contains, the file-level layout that makes each component independently editable, and the rules that govern how harness changes are proposed, recorded, and verified.

The methodology is adapted from Lin et al., *Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses* (arXiv:2604.25850, April 2026). The paper's central engineering finding — that file-level component decoupling plus falsifiable change manifests turn agent iteration from prose tinkering into auditable engineering — applies with particular force in healthcare, where audit, dispute, and rollback are first-class operational requirements.

---

## 1. What the Harness Is

In agent systems, the **harness** is everything around the model call that is *not* the model itself: prompts, tool descriptions, tool implementations, middleware hooks, skills, sub-agent configurations, and long-term memory. The base model is held fixed; the harness is the editable surface where engineering effort accumulates.

PACCA's harness mediates how Claude perceives a prior authorization request: which clinical context Claude sees, which tools Claude can invoke, which guard-rails fire before and after Claude's reasoning, which escalation paths are available, and which institutional lessons ride in the prompt context on every request.

The base model (Claude Sonnet 4) is upgraded by Anthropic on Anthropic's schedule. The harness is upgraded by us on our schedule, against measured outcomes.

---

## 2. The Seven Component Types

PACCA's harness is designed around seven orthogonal component types, each a separate
file (or set of files) at a known mount point so that adding or modifying one component
does not require editing any other.

> **As built (roadmap gap):** only **system prompt** (2 of 5 agents) and **long-term
> memory** (1 of 5 agents) exist on disk today. Tool descriptions (`*.tool.yaml`), tool
> implementations, middleware, skills, and sub-agents — and the per-agent `agent.yaml`
> loader — are **(roadmap)**. Agents are currently wired by direct Python import. The
> table below is the target layout.

| # | Component Type | PACCA Mount Point | Purpose |
|---|---------------|--------------------|---------|
| 1 | System prompt | `src/pacca/agents/<agent>/system_prompt.md` | Per-agent persona, behavioral rules, reasoning style |
| 2 | Tool description | `src/pacca/agents/<agent>/tool_descriptions/*.tool.yaml` | Interface contract Claude sees when deciding which tool to call |
| 3 | Tool implementation | `src/pacca/agents/<agent>/tools/*.py` | Python code executing the tool |
| 4 | Middleware | `src/pacca/agents/<agent>/middleware/*.py` | Cross-step hooks (before/after model, before/after tool) |
| 5 | Skill | `src/pacca/agents/<agent>/skills/<name>/SKILL.md` | On-demand workflow patterns loaded when relevant |
| 6 | Sub-agent | `src/pacca/agents/<agent>/sub_agents/<name>/agent.yaml` | Delegated specialized reasoning (e.g., Medical Director tier) |
| 7 | Long-term memory | `src/pacca/agents/<agent>/long_term_memory.md` | Persistent, human-readable cross-session lessons |

**(roadmap)** In the target design each agent's wiring is declared in
`src/pacca/agents/<agent>/agent.yaml`, which references the seven component types by file
path, so a change to any single component is a one-file diff with file-level rollback. No
`agent.yaml` loader exists yet; today the one-file-diff discipline is upheld by convention
and review.

### 2.1 PACCA-Specific Harness Surfaces

Beyond the seven NexAU-style component types, PACCA's harness has four additional editable surfaces specific to the healthcare prior authorization domain:

| Surface | Location | Purpose |
|---------|----------|---------|
| Escalation branch | `src/pacca/agents/orchestrator.py` (class `Orchestrator`) | The 7-branch deterministic escalation tree (4 pre-flight, 3 post-agent) |
| RAG collection | `src/pacca/integrations/vector_store.py` (`GuidelineRetriever`, `RAG_COLLECTIONS`) over `src/pacca/rag/pipeline.py` (`GuidelineVectorStore` / `RAGPipeline`) | Dual-collection and live: `nccn_guidelines` (authoritative) + `case_precedents` (institutional memory), both governed by the P-4 scope guard. The pipeline no longer names a collection — the retriever injects the governed one it holds |
| Prompt registry | `src/pacca/agents/prompts/templates.py` (PROMPT_REGISTRY) | Versioned prompt audit trail |
| Audit schema | `src/pacca/db/models.py` (class `AuditLogModel`, table `audit_logs`) | HIPAA audit log structure |

These four are treated as harness components for change-manifest purposes. Modifying the 7-branch escalation tree, for instance, requires the same manifest entry, predicted-fix list, and rollback granularity as modifying a system prompt.

---

## 3. Where Iteration Value Lives

The AHE paper's component ablation study (Lin et al., Table 3) reports the following single-component contributions when each is swapped into a minimal seed harness:

| Component | Single-component gain on Terminal-Bench 2 |
|-----------|-------------------------------------------|
| Long-term memory only | **+5.6 pp** |
| Tool implementation only | **+3.3 pp** |
| Middleware only | **+2.2 pp** |
| System prompt only | **−2.3 pp** (regression) |

Two findings transfer directly to PACCA:

**Tools, middleware, and long-term memory carry the gain.** When iterating PACCA, the highest-leverage changes go in those three component types.

**System prompts edited in isolation tend to regress.** Prompt edits are not useless — they encode behaviors that the other components depend on. But a prompt edit shipped without supporting changes in tools, middleware, or memory tends to underperform the unedited baseline. Treat prompt-only edits as a yellow flag during review.

This does not mean prompts are unimportant; it means prompts express *strategy*, while tools, middleware, and memory express *structure*, and structure transfers across cases more reliably than prose strategy.

---

## 4. Three Rules of Engagement

Three rules govern every harness change. They exist because they make changes auditable and rollback-able, which matters operationally (the eval suite catches regressions early) and matters for the healthcare domain (audit and dispute resolution require traceability that prose narrative cannot provide).

### Rule 1 — One change is one file diff

A behavioral change to PACCA touches exactly one component type. If a change appears to require edits across system prompt and tool description and middleware simultaneously, it is three changes, not one — split them. File-level diffs make `git revert <sha>` a real rollback option rather than a hopeful gesture.

### Rule 2 — Every behavioral change ships with a manifest entry

Every harness change includes a `change_manifest.json` entry naming:

- The failure pattern observed (with reference to specific cases or trajectory logs)
- The inferred root cause
- The predicted fixes (which cases this change should flip from failing to passing)
- The predicted risks (which cases this change might flip from passing to failing)
- The constraint level (which of the seven component types)
- Why that component type was chosen over alternatives

The manifest schema lives at `harness/manifests/change_manifest.schema.json`.

### Rule 3 — Predictions are verified against the next eval round

The next time the evaluation suite runs after a manifest entry lands, predicted fixes are compared against actual flipped cases, predicted risks are compared against actual regressions, and a verdict is recorded. Verdicts are append-only; rejected changes are reverted at file granularity.

The AHE paper found that self-attribution of fixes is reliable (~5x random baseline precision and recall), but self-attribution of regressions is barely above random (~2x). Translation: trust your "this should fix X" claims; distrust your "this won't break Y" claims, and let the evaluation suite catch you. If the eval suite isn't catching you, the eval suite isn't comprehensive enough.

---

## 5. Three Observability Pillars

The three rules above are made operational by three observability layers, each with a specific PACCA implementation.

### 5.1 Component Observability

*Goal:* every harness change maps to a single file diff with file-level rollback.

*PACCA implementation:* the directory layout in §2, plus `git tag harness-iter-N` on each iteration, plus the requirement that every behavioral commit uses the `chg-N:` prefix.

### 5.2 Experience Observability

*Goal:* every authorization request produces a structured trajectory that a reviewer can read in minutes, not hours.

*PACCA implementation:* OpenTelemetry spans (one per agent call) are emitted from
`agents/base.py` + `config/tracing.py`; **(roadmap)** the Langfuse exporter target and
per-case analysis records in PostgreSQL are intended but not yet verified end-to-end. The HIPAA audit log infrastructure required by 45 CFR 164.312(b) provides the primary persistence layer; the AHE-style trajectory extraction is built on top of it.

### 5.3 Decision Observability

*Goal:* every harness change is a falsifiable contract whose outcome is on record.

*PACCA implementation:* `harness/manifests/iter-N.json` for the current iteration's claims, `docs/DECISIONS.md` for the append-only decision log with verdicts, `docs/ITERATIONS.md` for the narrative log per iteration tag.

---

## 6. Component Editing Cheat Sheet

When a failure pattern is observed, the constraint level is chosen by asking which component type *owns* the behavior. The cheat sheet below is a starting point, not a rule.

| Symptom | Most likely constraint level | Rationale |
|---------|------------------------------|-----------|
| Agent reasoning style is wrong on every case | System prompt | Single-turn behavior, applies universally |
| Agent calls the wrong tool, or skips a tool it should call | Tool description | The contract Claude reads at decision time |
| Tool returns the right shape but wrong content | Tool implementation | Computational logic, not LLM behavior |
| Behavior depends on what happened earlier in the same case | Middleware | Cross-step state, single-turn rules can't see it |
| Same lesson keeps applying across many cases | Long-term memory | Persistent context, prompt-context-resident |
| Routine cases work, complex ones need specialist reasoning | Sub-agent | Tier escalation with isolated context |
| Specific workflow needed only on certain case types | Skill | On-demand loading, not always-on |

When in doubt, the AHE paper's heuristic applies: prefer tools, middleware, and memory over system prompts. The paper's data shows this preference earns its keep.

---

## 7. Adding a New Component

The four-step pattern for adding a new component (whichever type) is:

1. **Create the file.** Place at the correct mount point per §2.
2. **Register in the agent's `agent.yaml`.** Without registration, the framework will not load the file.
3. **Validate the manifest.** `python -m pacca.harness.validate_manifest harness/manifests/iter-N.json`
   (or `--all`) checks it against `change_manifest.schema.json` plus the `GC-\d{3}` case-id
   convention (exit 0 = valid). **(roadmap)** The `agent.yaml` loader and its
   `python -m pacca.harness.validate …` component checker do not exist yet.
4. **Write the manifest entry.** Before merging, draft the entry in
   `harness/manifests/iter-N.json` per the schema. The PR template *requires* the
   Standard-vs-Behavioral choice and prompts for the manifest, but nothing **blocks**
   merge today — CI enforcement (a `validate-manifests` job) is harness change P-6.

---

## 8. Cross-References

| Document | Purpose |
|----------|---------|
| `docs/ARCHITECTURE.md` | High-level system architecture; this document is the harness-layer detail |
| `docs/DECISIONS.md` | Append-only log of every harness change with manifest copy and verdict |
| `docs/ITERATIONS.md` | Narrative log per iteration tag; format borrowed from AHE paper Appendix C |
| `docs/EVALUATION.md` | Benchmark methodology, current scores, regression history |
| `harness/manifests/change_manifest.schema.json` | JSON Schema for manifest entries |
| `CHANGELOG.md` | Per-iteration changelog with eval delta and verified predictions |

---

## 9. References

- Lin, J., Liu, S., Pan, C., Lin, L., Dou, S., Huang, X., Yan, H., Han, Z., & Gui, T. (2026). *Agentic Harness Engineering: Observability-Driven Automatic Evolution of Coding-Agent Harnesses.* arXiv:2604.25850v3.
- AHE source: [github.com/china-qijizhifeng/agentic-harness-engineering](https://github.com/china-qijizhifeng/agentic-harness-engineering)
- NexAU framework: [github.com/nex-agi/NexAU](https://github.com/nex-agi/NexAU)
- Rajasekaran et al. (2025). *Effective context engineering for AI agents.* Anthropic Engineering.

---

*This document is part of PACCA's harness engineering documentation set. It is updated when the component layout changes; changes are themselves harness changes and ship with manifest entries.*
