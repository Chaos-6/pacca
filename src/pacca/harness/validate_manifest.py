"""Validate PACCA change manifests.

The real implementation of the command the harness workflow documents:

    python -m pacca.harness.validate_manifest harness/manifests/iter-N.json
    python -m pacca.harness.validate_manifest --all

The JSON Schema (`harness/manifests/change_manifest.schema.json`) already enforces required
fields and the ``constraint_level`` enum, so this tool runs the schema validation as the
real, documented CLI and adds the one cross-check the schema is too loose to express:

  * every ``GC-…`` case id referenced in a change matches the ``GC-\\d{3}`` convention.

(Note: the schema's ``constraint_level`` enum uses the historical harness-surface names —
``escalation_branch``, ``evaluation_harness``, ``instrumentation`` — which differ from the
reconciled PR-template taxonomy (``orchestrator``, ``eval_suite``). Reconciling the schema
enum to that taxonomy, and migrating the iter-0…6 constraint_level values, is a follow-up
governance change; this validator deliberately does not add a second, conflicting list.)

Exit code is 0 when every manifest is valid, 1 otherwise; a per-error report is printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema

# Case ids must look like GC-018 (exactly three digits).
_GC_TOKEN_RE = re.compile(r"GC-\d+")
_GC_VALID_RE = re.compile(r"^GC-\d{3}$")

# Manifests live next to their schema.
_MANIFEST_DIR = Path("harness/manifests")
_SCHEMA_NAME = "change_manifest.schema.json"

# The iteration at and after which `baseline` is required on `improvement`/`rollback`
# changes (D6, David's forward-only decision, 2026-07-29). Iterations 0-17 predate the
# field and are grandfathered, not backfilled. Kept as a named constant here so the error
# message and the schema's cutover stay readable as one decision, even though the cutover
# itself is enforced by the schema's `if/then` (see change_manifest.schema.json), not by
# this module -- this constant only has to agree with the schema for the *message*, never
# for the *decision* of whether an error fires.
_BASELINE_CUTOVER_ITERATION = 18

_BASELINE_WORKED_EXAMPLE = """\
    "baseline": {
      "claim": "The evaluator performs no Python keyword containment; \
reasoning_must_not_include is shown to the LLM judge under a 'hallucination markers' \
header and the judge decides semantically.",
      "verified_by": "git show fa71251:tests/clinical/evaluator.py | grep -n \
'must_include\\\\|must_not_include'",
      "result": "Only lines 359 and 362, both inside the judge-prompt template. No \
containment check exists in code."
    }"""


def _baseline_required_error(path: Path, manifest: dict[str, object], change_index: int) -> str:
    """A rich, actionable message for the one schema failure this module knows how to
    explain in depth: a required `baseline` field missing from an `improvement`/`rollback`
    change at iteration >= _BASELINE_CUTOVER_ITERATION.

    The *rejection* itself comes entirely from the schema's `if/then` conditional (per the
    D6 spec: "so validate_manifest.py enforces it through the existing schema path rather
    than a bespoke code branch") -- this function only recognizes that specific jsonschema
    error afterward and replaces its one-line message with something a human can act on at
    2am: which field, why it's required now, and what a good value looks like.
    """
    changes = manifest.get("changes")
    change = changes[change_index] if isinstance(changes, list) else {}
    change_id = (
        change.get("id", f"changes[{change_index}]")
        if isinstance(change, dict)
        else (f"changes[{change_index}]")
    )
    change_type = change.get("type", "?") if isinstance(change, dict) else "?"
    iteration = manifest.get("iteration", "?")
    return (
        f"{path}: changes[{change_index}] ({change_id}, type={change_type!r}): "
        f"missing required field 'baseline'.\n"
        f"  Why: iteration {iteration} is >= {_BASELINE_CUTOVER_ITERATION}, and every "
        f"'improvement' or 'rollback' change from iteration {_BASELINE_CUTOVER_ITERATION} "
        f"forward must record a baseline claim -- both types inherently assert something "
        f"about CURRENT behavior ('X is broken', 'X regressed'), and that assertion must be "
        f"measured, not remembered. This mechanises docs/AGENT_LESSONS.md P-013 "
        f"('characterize before you change'): three changes on 2026-07-28 shipped or nearly "
        f"shipped on a load-bearing claim about existing behavior that was never checked, "
        f"and each premise was falsifiable in under a minute once someone ran the command. "
        f"Iterations 0-{_BASELINE_CUTOVER_ITERATION - 1} predate this field and stay valid "
        f"without it -- this is a forward-only cutover, not a retroactive requirement.\n"
        f"  Add a 'baseline' object with 'claim' (what the system did BEFORE this change, "
        f"as the change depends on it), 'verified_by' (the reproducible command or "
        f"procedure that measured it), and 'result' (what that command actually produced). "
        f"A good value looks like:\n{_BASELINE_WORKED_EXAMPLE}"
    )


def _schema_path(manifest_path: Path) -> Path:
    """The schema sits alongside the manifests; resolve it relative to the given file."""
    return manifest_path.parent / _SCHEMA_NAME


def _iter_gc_tokens(value: object) -> list[str]:
    """Every ``GC-…`` token appearing anywhere in a JSON value (recursively)."""
    return _GC_TOKEN_RE.findall(json.dumps(value))


def validate_manifest(path: Path) -> list[str]:
    """Return a list of human-readable error strings for one manifest (empty == valid)."""
    errors: list[str] = []

    try:
        raw = path.read_text()
    except OSError as exc:
        return [f"{path}: cannot read file ({exc})"]

    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{path}: invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg}"]

    schema_file = _schema_path(path)
    try:
        schema = json.loads(schema_file.read_text())
    except OSError as exc:
        return [f"{path}: cannot read schema {schema_file} ({exc})"]

    # 1) JSON Schema validation — report every failure, not just the first.
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        err_path = list(err.path)
        if (
            err.validator == "required"
            and err.validator_value == ["baseline"]
            and len(err_path) == 2
            and err_path[0] == "changes"
            and isinstance(manifest, dict)
        ):
            errors.append(_baseline_required_error(path, manifest, err_path[1]))
        else:
            errors.append(f"{path}: schema: {loc}: {err.message}")

    # Case-id format cross-check (the schema does not constrain GC-id shape).
    # Guarded so a schema-invalid manifest shape still returns a non-zero result.
    changes = manifest.get("changes") if isinstance(manifest, dict) else None
    if isinstance(changes, list):
        for i, change in enumerate(changes):
            if not isinstance(change, dict):
                continue
            for token in _iter_gc_tokens(change):
                if not _GC_VALID_RE.match(token):
                    errors.append(
                        f"{path}: changes[{i}]: malformed case id {token!r} "
                        f"(expected GC-\\d{{3}}, e.g. GC-018)"
                    )

    return errors


def _resolve_targets(args: argparse.Namespace) -> list[Path]:
    if args.all:
        base = Path(args.dir) if args.dir else _MANIFEST_DIR
        return sorted(base.glob("iter-*.json"))
    return [Path(args.manifest)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pacca.harness.validate_manifest",
        description="Validate PACCA change manifest(s) against the schema + taxonomy/case-id rules.",
    )
    parser.add_argument("manifest", nargs="?", help="Path to a single iter-N.json manifest.")
    parser.add_argument(
        "--all", action="store_true", help="Validate every iter-*.json in the manifest dir."
    )
    parser.add_argument("--dir", help=f"Manifest directory for --all (default: {_MANIFEST_DIR}).")
    args = parser.parse_args(argv)

    if not args.all and not args.manifest:
        parser.error("provide a manifest path or --all")

    targets = _resolve_targets(args)
    if not targets:
        print("No manifests found to validate.", file=sys.stderr)
        return 1

    total_errors = 0
    for path in targets:
        errors = validate_manifest(path)
        if errors:
            total_errors += len(errors)
            for e in errors:
                print(e, file=sys.stderr)
        else:
            print(f"{path}: OK")

    if total_errors:
        print(
            f"\nFAILED — {total_errors} error(s) across {len(targets)} manifest(s).",
            file=sys.stderr,
        )
        return 1
    print(f"\nPASSED — {len(targets)} manifest(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
