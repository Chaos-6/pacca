# Agent lessons — PACCA-specific gotchas

> **Purpose.** Repo-local catalog of mistakes the agent has made while working on PACCA, with rules to prevent recurrence. Companion to the global `~/tasks/lessons.md` (which holds cross-project process discipline).
>
> **Read this file at session start when working on PACCA.** Update after any correction that exposes a PACCA-specific gotcha.
>
> **Why a separate file from `docs/DECISIONS.md`?** DECISIONS.md records *what the system does* (architectural choices, harness iterations). This file records *what the agent gets wrong about the system* (parser quirks, stale tests, environment surprises). Two different audiences, two different append-only logs.

---

## Rendering & docs

### P-001 · GitHub mermaid parser rejects semicolons in message bodies
**Symptom.** README shows "Unable to render rich display" on a `sequenceDiagram` block. The exact error in the GitHub blob view is `Parse error on line N: ...; <text>` with a caret under the character after `;`.

**Cause.** Mermaid treats `;` as a statement terminator in message bodies. After the semicolon, the parser expects an arrow operator (`->>`, `-->>`, etc.) and instead sees a newline, breaking the block.

**Rule.** No `;` in `participant ... : <message>` bodies. Use `,` or `.` or split into two messages.

**Other GitHub-mermaid rejections worth knowing:**
- `{` and `}` in message bodies — parsed as classDef syntax. `/sessions/{id}/validate` breaks; `/sessions/id/validate` works.
- `(` and `)` in **participant aliases** — `participant V as Validators (6)` breaks. Aliases must be bare tokens or multi-word without special chars.
- `<br/>` in `Note over` blocks — flaky across mermaid versions. Keep notes on one line or use mermaid's `<br>` (no slash, sometimes works).
- Reserved keywords (`end`, `loop`, `alt`, `opt`) as participant aliases.

**Validation.** Before pushing any mermaid edit, pipe the block through `mermaid-cli`:
```bash
# extract block from README
awk '/^```mermaid/{p=1;next} /^```$/{p=0} p' file.md > /tmp/block.mmd
# validate with the same parser GitHub uses
npx -p @mermaid-js/mermaid-cli mmdc -i /tmp/block.mmd -o /tmp/test.svg
```
Exit code 0 + a generated SVG = will render on GitHub.

---

## Logging & errors

### P-002 · `pacca.config.get_logger` accepts kwargs; stdlib `logging.getLogger` does not
**Symptom.** Endpoint crashes with `Logger._log() got an unexpected keyword argument 'error'` on what looks like a normal log call.

**Cause.** PACCA's `get_logger` returns a structlog-backed `BoundLogger` that accepts `logger.warning("event", error=str(e), key=val)`. The stdlib `logging.getLogger` returns a regular `Logger` that takes positional message + format args ONLY. Any module that imports `import logging; logger = logging.getLogger(__name__)` will crash on PACCA-style structured kwargs.

**Rule.** Every PACCA module that emits logs uses:
```python
from pacca.config import get_logger
logger = get_logger(__name__)
```
Never `import logging; logger = logging.getLogger(__name__)` in PACCA code. The CI/lint config does not catch this; it's a runtime bug.

---

## Tests & CI

### P-003 · mypy flags previously-unused `# type: ignore` once you re-touch the file
**Symptom.** Pre-commit fails with `error: Unused "type: ignore" comment [unused-ignore]` on a line you didn't change.

**Cause.** mypy re-checks the whole file when you edit it. Newer mypy understands frozen-dataclass attribute assignment natively, so older `# type: ignore[misc]` comments on `g.priority = 99` (for `@dataclass(frozen=True)` test cases) are now flagged as unused.

**Rule.** When the pre-commit `mypy` step fails on a file you touched, check first if the failing line is a pre-existing `# type: ignore` that's no longer needed. If so, remove it — the fix is one line and it cleans up stale technical debt as a side benefit. Don't add `# type: ignore[unused-ignore]` to suppress; just delete the original.

### P-004 · Test environment may lack `uuid_extensions` (CI has it; local conda may not)
**Symptom.** `pytest tests/unit/sme_authoring/` fails at collection with `ModuleNotFoundError: No module named 'uuid_extensions'` on `test_cli_commands.py`, `test_cli_new_subcommands.py`, `test_agent_with_mocked_llm.py`.

**Cause.** `src/pacca/agents/sme_authoring/cli_commands.py:46` imports `uuid_extensions` (a transitive dep of Click). CI installs it via `pip install -e ".[dev]"`. Local conda env may not have it if the dev extras weren't installed.

**Rule.** When local pytest collection errors with `uuid_extensions` missing, run `python -m pip install uuid_extensions` or fall back to running the tests that don't import `cli_commands`:
```bash
pytest tests/unit/sme_authoring/ \
  --ignore=tests/unit/sme_authoring/test_agent_with_mocked_llm.py \
  --ignore=tests/unit/sme_authoring/test_cli_commands.py \
  --ignore=tests/unit/sme_authoring/test_cli_new_subcommands.py
```

### P-005 · Contract change requires broad-grep, not just edits in the file you're already in
**Symptom.** CI fails with stale-test assertions after pushing a "fix" that updated only the test file you were already editing.

**Cause.** When `read_coverage()` was changed to fall back to on-disk file counting, two test files asserted the old "no-fallback" contract: `test_cli_new_subcommands.py` (which I was editing) and `test_gap_analyzer.py` (which I forgot to grep for).

**Rule.** Before pushing a contract change, grep ALL of `tests/` for assertions about the old contract:
```bash
grep -rn 'parsed_ok is False\|"not available"\|count == 0' tests/
```
Then update or delete every match. This applies any time you change: a function's return semantics, a default value, an error condition, an output format, a public API surface.

### P-007 · Touching a file surfaces its pre-existing mypy errors — annotate them, never blanket-disable
**Symptom.** Pre-commit `mypy` fails with `no-untyped-def` (missing return annotations) on test functions you didn't write, or `method-assign` on `obj.method = AsyncMock(...)` monkeypatches — surfacing only because you staged that file for an otherwise-unrelated change.

**Cause.** PACCA runs strict mypy (`disallow_untyped_defs = true`) on the staged files, **including tests**. A file that predates strict mode carries latent violations that stay invisible until you touch it and the hook re-checks it (the "py.typed cascade" — each change absorbs the type-debt of the files it touches).

**Rule.** Fix the touched file **in-file**: add `-> None` / proper return annotations to its functions; use a NARROW per-line `# type: ignore[method-assign]` only on the unavoidable mock method-assignment lines. **NEVER** make the hook pass by adding a module-level suppression like:
```toml
[[tool.mypy.overrides]]
module = ["tests.*"]
ignore_errors = true
```
That silently disables type-checking across the whole suite and erases the coverage other work earned. Suppressing a gate to pass it is not a fix — it's hiding the failure. (Related: P-003 covers the inverse — removing a stale *unused* ignore.)

### P-008 · `make test` is deterministic; the live golden-20 gate is `make test-clinical`
**Symptom.** Unsure how to "run the suite" or how to prove behavior preservation; clinical/accuracy tests show as `deselected`; or a routing change gets called "behavior-preserved" on the strength of `make test` alone.

**Cause.** `make test` runs the DETERMINISTIC suite with `-m "not clinical"`, which deselects the live LLM tests (`@pytest.mark.clinical`). The golden-20 accuracy gate (`tests/clinical/test_clinical_accuracy.py::TestFullClinicalEvaluation`) is clinical-marked: it makes real Claude API calls (~10 min) and runs ONLY via `make test-clinical`, which requires `ANTHROPIC_API_KEY` in the shell env.

**Rule.** For routine verification use `make test` (fast, deterministic, ~25s). For any change to decision routing or agent behavior, ALSO run the live gate **at the final merge HEAD** before claiming behavior preserved — `make test-clinical` (or `pytest tests/clinical/test_clinical_accuracy.py -m clinical`). Source the key from the gitignored `.env`, never hardcode or print it:
```bash
export ANTHROPIC_API_KEY=$(grep -m1 '^ANTHROPIC_API_KEY=' .env | cut -d= -f2- | tr -d '"')
```
Don't infer live clinical accuracy from the deterministic suite — they cover different things.

---

### P-009 · SQLite proves nothing about FK behaviour — a whole bug class is invisible to `make test`
**Symptom.** A change is green across the entire deterministic suite (890+ tests), green on SQLite-with-`PRAGMA foreign_keys=ON`, and **catastrophically broken on Postgres**. Observed 2026-07-28: moving audit writes onto an independently-committing session passed every local test, then on a real Postgres container a normal successful request persisted **0 of its 7 audit rows** while returning 200.

**Cause.** Two independent dialect divergences, both silent:
1. **SQLite disables FK enforcement by default.** `audit_logs.request_id` carries a `DEFERRABLE INITIALLY DEFERRED` FK (migration 003, Postgres-only); nothing local exercises it.
2. **Even with `PRAGMA foreign_keys=ON`, SQLAlchemy session state after a failed commit differs.** On SQLite a rolled-back instance stays re-insertable; on Postgres it is stamped with an identity key and left `detached=True`, so re-`add()`ing it emits an **UPDATE matching 0 rows** instead of an INSERT — silently swallowed by a blanket `except Exception`.

The second one is the trap: the agent *suspected* Postgres would differ, wrote a `PRAGMA`-forced SQLite test to check, and that test confirmed the wrong thing. A proxy for the production engine is not the production engine.

**Rule.** For **any** change touching transactions, sessions, commit boundaries, or FK-bearing tables:
- SQLite results are a smoke test, never evidence. Do not accept "I forced `PRAGMA foreign_keys=ON`" as a substitute.
- Run `tests/integration/ -m postgres` with `POSTGRES_TEST_URL` set, or stand up an ephemeral container:
  ```bash
  docker run -d --rm -e POSTGRES_PASSWORD=x -p 5433:5432 postgres:16
  DATABASE_URL=postgresql+asyncpg://postgres:x@localhost:5433/postgres alembic upgrade head
  ```
- That tier (2 tests, 187 lines) exists precisely for this and **it caught this bug when 890 unit tests did not**. It is the least-loved part of the suite and the highest-leverage.
- Also verify `alembic revision --autogenerate` is empty on Postgres — the `migration-drift` gate only runs there.

**Related.** P-008 (deterministic ≠ clinical) is the same shape one layer up: green on the cheap tier says nothing about the expensive one.

---

### P-010 · A regression test that has only ever passed is an assumption, not a test
**Symptom.** A guard, detector, or regression test ships green and is trusted — but nobody has ever seen it fail, so there is no evidence it *can*.

**Cause.** Tests are written after the fix, against the fixed code. A test that asserts the correct behaviour and a test that asserts nothing both pass on correct code.

**Real instances, all 2026-07-28:**
- An eval contamination guard was only believed once an adversarial reviewer injected a held-out case id into `src/` and watched it go red on both `.py` and `.md`.
- A route-enumeration probe reported "zero unguarded routes"; its detector had never been observed failing. Stripping the RBAC dependency from a real route in memory proved it goes red — *then* the zero meant something.
- A retry fix's guard set was assumed to be 4 tests; mutation testing showed only **3** actually fail when the predicate is reverted.

**Rule.** Before trusting any guard or regression test, **watch it fail**:
- Mutate the production code (revert the fix inline, strip the dependency, inject the contamination), confirm RED, restore, confirm GREEN — and paste both.
- **Never `git stash`** to do it (L-021: the stash list is shared across worktrees). Use `git show <sha>:<path> > <path>` with a backup, or patch in memory.
- Prefer a *permanent* mutation-style test (e.g. one that pins the old predicate via `patch()`) so the property stays checkable rather than being a one-time demonstration.

---

### P-011 · `MagicMock` implements `__float__` — an unconfigured mock can impersonate a real header value
**Symptom.** A test suite silently starts *sleeping*. Nothing fails; wall-clock creeps.

**Cause.** `float(MagicMock())` returns **1.0**. Code that reads a header and coerces it —
`float(response.headers.get("retry-after"))` — will "successfully" parse an unconfigured
`MagicMock(status_code=...)` as a genuine 1-second directive. Observed 2026-07-28 while adding
`retry-after` handling: every pre-existing test in `test_retry_and_tracing.py` builds fake
Anthropic errors as `response=MagicMock(...)` without setting `.headers`, so the new parser
would have introduced a real `asyncio.sleep(1.0)` into most of them.

**Rule.** When parsing a value that came from a mockable object, **type-guard before coercing**:
```python
if not isinstance(raw, (str, bytes)):
    continue          # a MagicMock is never str/bytes; real httpx headers are
```
Generalises past mocks: `float()`/`int()` on an unvalidated attribute accepts anything with a
dunder. Assert on the *computed value* rather than wall-clock, so a mock that slips through
shows up as a wrong number instead of a slow suite.

---

### P-012 · `--ignore-missing-imports` hides a real mypy error — use the canonical `make typecheck` invocation
**Symptom.** An agent reports "3 pre-existing mypy errors"; the true count is **4**. Happened in
**all three** parallel lanes on 2026-07-28, independently.

**Cause.** `mypy src/pacca --ignore-missing-imports` (the habitual form) suppresses
`src/pacca/harness/validate_manifest.py:31: Library stubs not installed for "jsonschema"
[import-untyped]`. The Makefile's `typecheck` target runs `mypy src/pacca/` with **no flag**, so
CI sees 4 and a local run sees 3.

**Rule.** Report the count from the canonical target (`make typecheck`, i.e. `mypy src/pacca/`),
never from an ad-hoc invocation with extra flags. If you must add flags to isolate something,
say which invocation produced the number. A count that disagrees with CI is worse than no count —
it trains the reader to discount the numbers that *are* load-bearing.

**Corollary.** The repo runs mypy in **two** environments with different visibility: the project
venv, and the pre-commit hook's isolated venv (thin `additional_dependencies`, no SQLAlchemy or
tenacity). An annotation that satisfies one can be flagged as unused in the other — see P-007.
Fix in-file (`cast()`, a real coercion) rather than suppressing in either.

---

### P-013 · Characterize before you change — a load-bearing claim about current behaviour must be measured, not asserted
**Symptom.** A change is well-designed, well-documented, well-tested, and wrong — because the premise it rests on was never checked. The tests pass because they were written against the same wrong premise.

**Three instances in one day (2026-07-28), all found late and all checkable in seconds:**

| Change | Load-bearing premise | Reality |
|---|---|---|
| chg-23 (audit durability) | "the FK-violation retry works" — verified on SQLite | Postgres leaves the instance detached; the retry emitted UPDATE-0-rows and lost the row silently |
| chg-24 (transaction boundaries) | "duplicate + no decision → resume" — the spec named two states | A third state existed, *in flight*: the race produced two decisions and then a permanent 500 |
| chg-25 (judge separation) | "substring matching reproduces the removed judge-prompt keyword check" | There was **no** Python check. `git show <base>:tests/clinical/evaluator.py \| grep must_include` returns only the two prompt-template lines — the old behaviour was the judge applying *semantic* judgment |

**Cause.** Claims about the *existing* system get asserted from reading, while claims about the *new* system get tested. So the riskiest statement in a change — "here is what it does today" — is the one nothing verifies.

**Rule.** Before modifying behaviour X, write a **characterization test** that captures X's CURRENT behaviour and **passes against the unmodified base**. Then change the code and let it fail.

```bash
# capture the baseline, in the base worktree (NOT via git stash — L-021)
git show <base-sha>:<path> > /tmp/base_copy.py     # or a scratch worktree
# run the characterization test against it: it must PASS
# then apply the change: it must FAIL
```

Run against the three above, each premise dies in about a minute:
- "assert the old pipeline flags GC-028 as a constraint violation" → fails on base; there is no such check.
- "assert a duplicate request_id returns 409" → fails on base with a 500; there is no contract at all.
- "assert audit rows survive a rollback on Postgres" → fails on base; you meet the FK before building on top of it.

**Relationship to P-010.** P-010 says *watch your new guard fail*. P-013 says *watch your characterization pass* — on the code you are about to replace. Together they bracket a change: measured where you started, proven you can detect where you are going. Neither alone is sufficient; chg-25 had P-010 and still shipped a false premise.

**For reviewers and validators.** Add this as a standing probe, not an instinct: *list every claim the change makes about pre-change behaviour, and verify each against the base commit by execution. An unverified baseline claim is a finding regardless of whether the implementation works.* Adversarial validation caught chg-23's; it missed chg-24's until it ran a race; and it never questioned chg-25's baseline claim at all.

**What does NOT work** (all three were tried and all three failed here): more tests on the new behaviour, careful code review, and relying on the validator to notice. Every one of these premises was plausible on a read.

---

## Git & PR workflow (PACCA-specific overlay on the global rule L-001)

### P-006 · PACCA defaults to branch-and-PR. No direct pushes to main.
**Pattern.** Per `~/.claude/projects/.../memory/pacca_pr_workflow.md` and CLAUDE.md, every change goes through a PR even for tiny doc fixes. The pre-commit hooks may reformat the file (ruff) — re-stage after a failed commit and re-run.

**Rule.**
1. Branch off main with a descriptive name (`fix/<thing>`, `docs/<thing>`, `feat/<thing>`).
2. Commit with a HEREDOC commit message that explains the *why*.
3. Run `git push -u origin <branch>` then `gh pr create --title "..." --body "..."`.
4. **Do not push follow-up commits to the same branch** (see global L-001). If the fix needs amending, branch off main again.
5. If a runbook prescribes direct push: flag the deviation to the user before doing it (per the saved policy).

---

## How to update this file

When the user corrects a PACCA-specific behavior (not a generic process flaw — those go in `~/tasks/lessons.md`), add a P-XXX entry with:

- **Symptom** — what the user or CI sees
- **Cause** — the underlying mechanism, in one or two sentences
- **Rule** — the actionable guard against recurrence
- Optionally: a validation snippet, a known-good code pattern, or related entries

Keep entries terse. The file is meant to be re-readable in two minutes at session start.

---

*Last updated: 2026-07-28.*
