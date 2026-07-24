"""Baseline Git-object evidence policy (Task 19, review correction B-1).

The stamping migration evidence compares each rewritten checked-in payload
against the **actual committed blobs** at baseline commit ``6f92b53``.  Copying
those blobs into the repository as fixture data would defeat the point, so the
comparison genuinely needs Git history.

Before this correction the helper skipped whenever the baseline object was
unreachable.  Because ``actions/checkout`` defaults to ``fetch-depth: 1``, that
meant all 35 comparisons skipped silently in every hosted CI job -- the evidence
gate never actually ran.

Policy now:

- ``SIM_INTENT_REQUIRE_BASELINE_EVIDENCE=1`` (set by the required hosted jobs):
  an unavailable baseline object is a **hard failure**, never a skip.
- variable absent: an unavailable baseline object skips, so environments that
  intentionally exclude ``.git`` -- the runtime and ``ci`` container images --
  still run every other test.

Executed and skipped counts are reported in the pytest terminal summary so the
number of baseline comparisons that actually ran is visible in CI output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[1]

BASELINE_COMMIT = "6f92b5349d72fd7ef563293cd883c8b61fa3bbb5"
REQUIRE_BASELINE_ENV = "SIM_INTENT_REQUIRE_BASELINE_EVIDENCE"

BaselineAction = Literal["execute", "skip", "fail"]

_FALSEY = {"", "0", "false", "no", "off"}


@dataclass
class BaselineEvidenceCounters:
    """Visible accounting for the terminal summary."""

    executed: int = 0
    skipped: int = 0
    failed: int = 0

    def reset(self) -> None:
        self.executed = 0
        self.skipped = 0
        self.failed = 0


COUNTERS = BaselineEvidenceCounters()


def baseline_evidence_required(env: Mapping[str, str] | None = None) -> bool:
    """True when an unavailable baseline object must fail instead of skip."""

    source = os.environ if env is None else env
    raw = source.get(REQUIRE_BASELINE_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in _FALSEY


def baseline_commit_available(root: Path | None = None) -> bool:
    """True when the baseline commit object is reachable from this checkout."""

    base = ROOT if root is None else root
    git = shutil.which("git")
    if git is None or not (base / ".git").exists():
        return False
    result = subprocess.run(
        [git, "cat-file", "-e", f"{BASELINE_COMMIT}^{{commit}}"],
        cwd=base,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def read_baseline_object(relative: str, root: Path | None = None) -> str | None:
    """Return the baseline blob text, or ``None`` when it is unreachable."""

    base = ROOT if root is None else root
    git = shutil.which("git")
    if git is None or not (base / ".git").exists():
        return None
    result = subprocess.run(
        [git, "show", f"{BASELINE_COMMIT}:{relative}"],
        cwd=base,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


@dataclass(frozen=True)
class BaselineDecision:
    action: BaselineAction
    text: str | None
    reason: str


def resolve_baseline_evidence(
    relative: str,
    *,
    env: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> BaselineDecision:
    """Decide what to do about one baseline comparison.

    Kept free of pytest side effects so the policy itself is directly testable.
    """

    text = read_baseline_object(relative, root)
    if text is not None:
        return BaselineDecision("execute", text, "baseline object available")
    reason = (
        f"baseline Git object {BASELINE_COMMIT}:{relative} is unavailable in "
        "this checkout"
    )
    if baseline_evidence_required(env):
        return BaselineDecision(
            "fail",
            None,
            reason
            + f"; {REQUIRE_BASELINE_ENV} is set, so the stamping migration "
            "evidence must not be skipped. Check out full history "
            "(fetch-depth: 0) so the baseline commit is present.",
        )
    return BaselineDecision(
        "skip",
        None,
        reason + f"; set {REQUIRE_BASELINE_ENV}=1 to make this a hard failure",
    )


def require_baseline_object(
    relative: str,
    *,
    env: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> str:
    """Return the baseline blob text, applying the evidence policy.

    Only real evidence calls -- those using the ambient environment and
    checkout -- are counted.  The policy tests pass explicit ``env``/``root``
    probes, and those must not pollute the CI accounting.
    """

    counted = env is None and root is None
    decision = resolve_baseline_evidence(relative, env=env, root=root)
    if decision.action == "execute":
        if counted:
            COUNTERS.executed += 1
        assert decision.text is not None
        return decision.text
    if decision.action == "fail":
        if counted:
            COUNTERS.failed += 1
        pytest.fail(decision.reason)
    if counted:
        COUNTERS.skipped += 1
    pytest.skip(decision.reason)
    raise AssertionError("unreachable")  # pragma: no cover


def summary_line() -> str:
    required = baseline_evidence_required()
    return (
        "Task 19 baseline evidence: "
        f"executed={COUNTERS.executed} skipped={COUNTERS.skipped} "
        f"failed={COUNTERS.failed} "
        f"required={'yes' if required else 'no'} "
        f"baseline={BASELINE_COMMIT[:7]} "
        f"available={'yes' if baseline_commit_available() else 'no'}"
    )
